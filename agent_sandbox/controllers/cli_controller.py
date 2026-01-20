"""
FastAPI microservice that runs inside a long-lived Claude CLI sandbox.

This service exposes endpoints:
- `GET /health_check` used by the controller to know when the service is ready.
- `POST /execute` which runs Claude Code CLI in the CLI sandbox.
- `POST /ralph/execute` which runs the Ralph loop in the CLI sandbox.

The sandbox is created with a dedicated CLI image and volume mounted at
`claude_cli_fs_root` (default: /data-cli). The app is started via:

    uvicorn agent_sandbox.controllers.cli_controller:app
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import anyio
import modal
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from modal import exception as modal_exc
from starlette.responses import StreamingResponse

from agent_sandbox.config.settings import get_settings
from agent_sandbox.controllers.middleware import RequestIdMiddleware
from agent_sandbox.jobs import (
    build_artifact_manifest,
    job_workspace_root,
    normalize_job_id,
    register_job_workspace,
    update_job,
    update_workspace_metadata,
)
from agent_sandbox.ralph.loop import resume_ralph_loop, run_ralph_loop, run_ralph_loop_streaming
from agent_sandbox.ralph.schemas import RalphCheckpoint, RalphExecuteRequest, RalphStreamEvent
from agent_sandbox.schemas import ClaudeCliRequest
from agent_sandbox.tools.session_tools import set_parent_context
from agent_sandbox.utils.cli import (
    CLAUDE_CLI_APP_ROOT,
    claude_cli_env,
    demote_to_claude,
    maybe_chown_for_claude,
    require_claude_cli_auth,
)

app = FastAPI()
app.add_middleware(RequestIdMiddleware)
_settings = get_settings()
_logger = logging.getLogger(__name__)


def _require_connect_token(request: Request) -> None:
    """Validate that the request includes a Modal connect token when required."""
    if _settings.enforce_connect_token:
        if not request.headers.get("X-Verified-User-Data"):
            raise HTTPException(status_code=401, detail="Missing or invalid connect token")


def _get_cli_volume() -> modal.Volume:
    kwargs: dict[str, Any] = {"create_if_missing": True}
    if _settings.persist_vol_version is not None:
        kwargs["version"] = _settings.persist_vol_version
    return modal.Volume.from_name(_settings.claude_cli_persist_vol_name, **kwargs)


def _maybe_reload_cli_volume() -> None:
    """Reload the CLI volume to see latest committed writes."""
    try:
        _get_cli_volume().reload()
    except RuntimeError as exc:
        message = str(exc)
        if "reload() can only be called from within a running function" in message:
            return
        _logger.warning("Failed to reload Claude CLI volume: %s", message)
    except modal_exc.AuthError:
        # Expected when running inside a sandbox without Modal credentials
        return
    except Exception:
        _logger.warning("Failed to reload Claude CLI volume", exc_info=True)


def _commit_cli_volume() -> None:
    """Commit pending writes to the Claude CLI volume."""
    try:
        _get_cli_volume().commit()
    except RuntimeError as exc:
        message = str(exc)
        if "commit() can only be called" in message:
            return
        _logger.warning("Failed to commit Claude CLI volume: %s", message)
    except modal_exc.AuthError:
        # Expected when running inside a sandbox without Modal credentials
        return
    except Exception:
        _logger.warning("Failed to commit Claude CLI volume", exc_info=True)


def _write_result(
    write_result_path: str | None,
    payload: dict[str, Any],
    job_root: Path | None,
    base_root: Path,
    job_id: str | None,
) -> None:
    if not write_result_path:
        return
    path = Path(write_result_path)
    if not path.is_absolute():
        if job_id and path.parts[:2] == ("jobs", job_id):
            path = base_root / path
        else:
            base = job_root if job_root is not None else base_root
            path = base / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    maybe_chown_for_claude(path)


def _build_cli_command(body: ClaudeCliRequest) -> tuple[list[str], list[str] | None]:
    cmd = ["claude", "-p", body.prompt, "--output-format", body.output_format]
    if body.dangerously_skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if body.allowed_tools:
        cmd.extend(["--allowedTools", ",".join(body.allowed_tools)])
    if body.max_turns is not None:
        cmd.extend(["--max-turns", str(body.max_turns)])

    probe_cmd: list[str] | None = None
    if body.probe:
        if body.probe == "version":
            probe_cmd = ["claude", "--version"]
        elif body.probe == "help":
            probe_cmd = ["claude", "--help"]
        elif body.probe == "path":
            probe_cmd = ["/bin/sh", "-lc", "command -v claude && ls -l $(command -v claude)"]

    return cmd, probe_cmd


@app.get("/health_check")
async def health_check() -> dict[str, bool]:
    return {"ok": True}


@app.post("/execute")
async def execute_claude_cli(body: ClaudeCliRequest, request: Request) -> JSONResponse:
    """Execute Claude Code CLI in the dedicated CLI sandbox."""
    _require_connect_token(request)
    _maybe_reload_cli_volume()

    normalized_job_id = normalize_job_id(body.job_id)
    if body.job_id and not normalized_job_id:
        raise HTTPException(status_code=400, detail="job_id must be a valid UUID")

    job_root = None
    if normalized_job_id:
        job_root = job_workspace_root(_settings.claude_cli_fs_root, normalized_job_id)
        job_root.mkdir(parents=True, exist_ok=True)
        maybe_chown_for_claude(job_root)
        # Register workspace for retention tracking
        try:
            register_job_workspace(normalized_job_id, str(job_root), job_status="running")
        except Exception as e:
            _logger.warning("Failed to register workspace for %s: %s", normalized_job_id, e)

    cmd, probe_cmd = _build_cli_command(body)

    def _run() -> subprocess.CompletedProcess[str]:
        env = claude_cli_env()
        require_claude_cli_auth(env)
        return subprocess.run(
            probe_cmd or cmd,
            capture_output=True,
            text=True,
            timeout=body.timeout_seconds,
            cwd=str(job_root) if job_root is not None else str(CLAUDE_CLI_APP_ROOT),
            stdin=subprocess.DEVNULL,
            env=env,
            preexec_fn=demote_to_claude(),
        )

    payload: dict[str, Any] = {}
    status_code = 200
    try:
        set_parent_context(normalized_job_id)
        result = await anyio.to_thread.run_sync(_run)
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        if result.returncode != 0:
            status_code = 500
            payload = {
                "ok": False,
                "error": stderr or stdout or "Claude CLI failed",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
            }
        elif probe_cmd is not None:
            payload = {
                "ok": True,
                "result": None,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
                "cmd": probe_cmd,
                "cwd": str(job_root) if job_root is not None else str(CLAUDE_CLI_APP_ROOT),
                "path": claude_cli_env().get("PATH", ""),
                "home": claude_cli_env().get("HOME", ""),
                "user": claude_cli_env().get("USER", ""),
                "has_anthropic_api_key": bool(claude_cli_env().get("ANTHROPIC_API_KEY")),
                "probe": True,
            }
        else:
            parsed: object = stdout
            if body.output_format == "json":
                try:
                    if stdout:
                        parsed = json.loads(stdout)
                    elif stderr:
                        parsed = json.loads(stderr)
                    else:
                        parsed = None
                except json.JSONDecodeError as exc:
                    status_code = 500
                    payload = {
                        "ok": False,
                        "error": "Failed to parse Claude CLI JSON output",
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "exit_code": 1,
                        "parse_error": str(exc),
                    }
                else:
                    payload = {
                        "ok": True,
                        "result": parsed,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "exit_code": result.returncode,
                    }
            else:
                payload = {
                    "ok": True,
                    "result": parsed,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.returncode,
                }
    except subprocess.TimeoutExpired:
        status_code = 500
        payload = {
            "ok": False,
            "error": f"Claude CLI timed out after {body.timeout_seconds}s",
            "stdout": None,
            "stderr": None,
            "exit_code": 124,
        }
    except Exception as exc:  # pragma: no cover - unexpected runtime errors
        status_code = 500
        payload = {
            "ok": False,
            "error": str(exc),
            "stdout": None,
            "stderr": None,
            "exit_code": 1,
        }
    finally:
        set_parent_context(None)
        if body.debug:
            payload.update(
                {
                    "cmd": probe_cmd or cmd,
                    "cwd": str(job_root) if job_root is not None else str(CLAUDE_CLI_APP_ROOT),
                    "path": claude_cli_env().get("PATH", ""),
                    "home": claude_cli_env().get("HOME", ""),
                    "user": claude_cli_env().get("USER", ""),
                    "has_anthropic_api_key": bool(claude_cli_env().get("ANTHROPIC_API_KEY")),
                    "probe": probe_cmd is not None,
                }
            )
        _write_result(
            body.write_result_path,
            payload,
            job_root,
            base_root=Path(_settings.claude_cli_fs_root),
            job_id=normalized_job_id,
        )
        _commit_cli_volume()

        # Record artifact manifest after successful execution
        if normalized_job_id and job_root and status_code == 200:
            try:
                manifest = build_artifact_manifest(str(job_root))
                job_status = "complete" if payload.get("ok") else "failed"
                update_job(normalized_job_id, {"artifacts": manifest.model_dump()})
                update_workspace_metadata(
                    normalized_job_id, job_status=job_status, recalculate_size=True
                )
            except Exception as e:
                _logger.warning(
                    "Failed to record artifact manifest for %s: %s", normalized_job_id, e
                )

    return JSONResponse(status_code=status_code, content=payload)


@app.post("/ralph/execute")
async def execute_ralph(body: RalphExecuteRequest, request: Request) -> JSONResponse:
    """Execute the Ralph loop inside the CLI sandbox.

    If resume_checkpoint is provided, resumes from the checkpoint instead of
    starting fresh.
    """
    _require_connect_token(request)
    _maybe_reload_cli_volume()

    normalized_job_id = normalize_job_id(body.job_id)
    if not normalized_job_id:
        raise HTTPException(status_code=400, detail="job_id must be a valid UUID")

    workspace = job_workspace_root(_settings.claude_cli_fs_root, normalized_job_id)
    workspace.mkdir(parents=True, exist_ok=True)
    maybe_chown_for_claude(workspace)
    # Register workspace for retention tracking
    try:
        register_job_workspace(normalized_job_id, str(workspace), job_status="running")
    except Exception as e:
        _logger.warning("Failed to register workspace for %s: %s", normalized_job_id, e)

    env = claude_cli_env()
    require_claude_cli_auth(env)

    def _run():
        # Check if resuming from checkpoint
        if body.resume_checkpoint:
            checkpoint = RalphCheckpoint(**body.resume_checkpoint)
            return resume_ralph_loop(
                job_id=normalized_job_id,
                workspace=workspace,
                checkpoint=checkpoint,
                workspace_source=body.workspace_source,
                prompt_template=body.prompt_template,
                timeout_per_iteration=body.timeout_per_iteration,
                first_iteration_timeout=body.first_iteration_timeout,
                allowed_tools=body.allowed_tools or None,
                feedback_commands=body.feedback_commands or None,
                feedback_timeout=body.feedback_timeout,
                auto_commit=body.auto_commit,
                max_consecutive_failures=body.max_consecutive_failures,
            )
        else:
            return run_ralph_loop(
                job_id=normalized_job_id,
                prd=body.prd,
                workspace=workspace,
                workspace_source=body.workspace_source,
                prompt_template=body.prompt_template,
                max_iterations=body.max_iterations,
                timeout_per_iteration=body.timeout_per_iteration,
                first_iteration_timeout=body.first_iteration_timeout,
                allowed_tools=body.allowed_tools or None,
                feedback_commands=body.feedback_commands or None,
                feedback_timeout=body.feedback_timeout,
                auto_commit=body.auto_commit,
                max_consecutive_failures=body.max_consecutive_failures,
            )

    try:
        set_parent_context(normalized_job_id)
        result = await anyio.to_thread.run_sync(_run)
        payload = result.model_dump()
        status_code = 200
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
        status_code = 500
    finally:
        set_parent_context(None)
        _commit_cli_volume()

        # Record artifact manifest after execution
        try:
            manifest = build_artifact_manifest(str(workspace))
            job_status = "complete" if payload.get("ok") else "failed"
            update_job(normalized_job_id, {"artifacts": manifest.model_dump()})
            update_workspace_metadata(
                normalized_job_id, job_status=job_status, recalculate_size=True
            )
        except Exception as e:
            _logger.warning("Failed to record artifact manifest for %s: %s", normalized_job_id, e)

    return JSONResponse(status_code=status_code, content=payload)


@app.post("/ralph/execute_stream")
async def execute_ralph_stream(body: RalphExecuteRequest, request: Request):
    """Execute the Ralph loop with SSE streaming of iteration events.

    Returns a Server-Sent Events stream with events for each iteration.
    Event types:
    - iteration_start: Beginning of an iteration
    - iteration_complete: Successful iteration completion
    - iteration_failed: Failed iteration
    - paused: Loop was paused by user request
    - done: Loop completed (includes final result)
    """
    _require_connect_token(request)
    _maybe_reload_cli_volume()

    normalized_job_id = normalize_job_id(body.job_id)
    if not normalized_job_id:
        raise HTTPException(status_code=400, detail="job_id must be a valid UUID")

    workspace = job_workspace_root(_settings.claude_cli_fs_root, normalized_job_id)
    workspace.mkdir(parents=True, exist_ok=True)
    maybe_chown_for_claude(workspace)
    # Register workspace for retention tracking
    try:
        register_job_workspace(normalized_job_id, str(workspace), job_status="running")
    except Exception as e:
        _logger.warning("Failed to register workspace for %s: %s", normalized_job_id, e)

    env = claude_cli_env()
    require_claude_cli_auth(env)

    def _format_sse(event: RalphStreamEvent) -> str:
        """Format a Server-Sent Event message."""
        return f"event: {event.event_type}\ndata: {event.model_dump_json()}\n\n"

    def _run_streaming():
        """Generator that runs Ralph loop and yields SSE-formatted events."""
        _logger.debug("Starting Ralph streaming for job %s", normalized_job_id)
        job_status = "running"
        try:
            set_parent_context(normalized_job_id)
            gen = run_ralph_loop_streaming(
                job_id=normalized_job_id,
                prd=body.prd,
                workspace=workspace,
                workspace_source=body.workspace_source,
                prompt_template=body.prompt_template,
                max_iterations=body.max_iterations,
                timeout_per_iteration=body.timeout_per_iteration,
                first_iteration_timeout=body.first_iteration_timeout,
                allowed_tools=body.allowed_tools or None,
                feedback_commands=body.feedback_commands or None,
                feedback_timeout=body.feedback_timeout,
                auto_commit=body.auto_commit,
                max_consecutive_failures=body.max_consecutive_failures,
            )

            # Iterate through all events
            for event in gen:
                _logger.debug("Yielding SSE event: %s", event.event_type)
                yield _format_sse(event)

        except Exception as exc:
            # Yield error event
            error_event = RalphStreamEvent(
                event_type="error",
                job_id=normalized_job_id,
                error=str(exc),
            )
            yield _format_sse(error_event)
            job_status = "failed"
        else:
            job_status = "complete"
        finally:
            set_parent_context(None)
            _commit_cli_volume()
            # Record artifact manifest after streaming completes
            try:
                manifest = build_artifact_manifest(str(workspace))
                update_job(normalized_job_id, {"artifacts": manifest.model_dump()})
                update_workspace_metadata(
                    normalized_job_id, job_status=job_status, recalculate_size=True
                )
            except Exception as e:
                _logger.warning(
                    "Failed to record artifact manifest for %s: %s", normalized_job_id, e
                )

    async def sse_generator():
        """Async wrapper that streams events as they are produced.

        Uses a queue to bridge the synchronous generator running in a thread
        with the async generator that yields to the HTTP response.
        """
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        exception_holder: list[Exception] = []

        # Capture the event loop before entering the thread
        loop = asyncio.get_running_loop()

        def _run_streaming_to_queue():
            """Run the synchronous generator and push events to queue."""
            _logger.debug("Thread started for Ralph streaming job %s", normalized_job_id)
            try:
                for event in _run_streaming():
                    _logger.debug("Queueing SSE event to async bridge")
                    # Use call_soon_threadsafe to safely put items from thread
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as exc:
                exception_holder.append(exc)
            finally:
                # Signal completion
                loop.call_soon_threadsafe(queue.put_nowait, None)

        # Start the synchronous generator in a thread pool
        task = loop.run_in_executor(None, _run_streaming_to_queue)

        # Yield events as they arrive in the queue
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item

        # Wait for the thread to complete and propagate any exception
        await task
        if exception_holder:
            raise exception_holder[0]

    return StreamingResponse(sse_generator(), media_type="text/event-stream")
