"""
Entry-point and Modal function definitions for running the agent in a sandboxed
environment and exposing lightweight HTTP endpoints.

Quickstart (CLI):
- run local_entrypoint: `modal run -m agent_sandbox.app` (runs the agent once in a short-lived Modal function)
- run run_agent_remote: `modal run -m agent_sandbox.app::run_agent_remote --question "..."`
- run run_claude_cli_remote: `modal run -m agent_sandbox.app::run_claude_cli_remote --prompt "..."`
- keep dev deployment running: `modal serve -m agent_sandbox.app`
- deploy to production: `modal deploy -m agent_sandbox.deploy`

Notes for future maintainers:
- This file defines a `modal.App` plus several `@app.function` entries. Functions
  annotated with `@modal.asgi_app` are exposed as HTTP endpoints when the
  app is served or deployed. See Modal docs for `modal.App`, `@app.function`,
  `modal.Sandbox`, and `@modal.asgi_app` for lifecycle and limits.
- We use a long-running `modal.Sandbox` process to host a FastAPI microservice
  (`agent_sandbox.controllers.controller`) and then call into it from a short-lived Modal
  function. This pattern keeps cold-start latency low for the model runtime
  while allowing us to keep the HTTP frontdoor responsive.

Prerequisite for curl testing:
- Start the dev server locally with `modal serve -m agent_sandbox.app` so the HTTP endpoint
  (see `http_app`) is reachable at a dev URL like
  `https://<org>--test-sandbox-http-app-dev.modal.run`.
"""

import inspect
import json
import logging
import mimetypes
import os
import pwd
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import anyio
import httpx
import modal
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from modal import exception as modal_exc
from starlette.responses import StreamingResponse

from agent_sandbox.config.settings import Settings, get_modal_secrets
from agent_sandbox.jobs import (
    JOB_QUEUE,
    bump_attempts,
    cancel_job,
    enqueue_job,
    get_job_record,
    get_job_status,
    is_job_due,
    job_workspace_root,
    normalize_job_id,
    resolve_job_artifact,
    should_skip_job,
    update_job,
)
from agent_sandbox.prompts.prompts import DEFAULT_QUESTION, SYSTEM_PROMPT
from agent_sandbox.schemas import (
    ArtifactListResponse,
    ClaudeCliPollResponse,
    ClaudeCliRequest,
    ClaudeCliResponse,
    JobStatusResponse,
    JobSubmitRequest,
    JobSubmitResponse,
    QueryBody,
)
from agent_sandbox.schemas.jobs import ArtifactEntry, ArtifactManifest
from agent_sandbox.schemas.responses import ClaudeCliSubmitResponse
from agent_sandbox.services.webhooks import build_headers, build_webhook_payload, serialize_payload

app = modal.App("test-sandbox")
_settings = Settings()
_logger = logging.getLogger(__name__)

_CLAUDE_CLI_USER = "claude"
_CLAUDE_CLI_HOME = Path("/home/claude")
_CLAUDE_CLI_PATH = f"{_CLAUDE_CLI_HOME}/.local/bin:{_CLAUDE_CLI_HOME}/.claude/bin"
_CLAUDE_CLI_APP_ROOT = _CLAUDE_CLI_HOME / "app"

web_app = FastAPI()

web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _base_anthropic_sdk_image() -> modal.Image:
    """Build a base image with Python, FastAPI, uvicorn, httpx and Claude SDK.

    - Uses Debian slim with Python 3.11
    - Installs `claude-agent-sdk` plus FastAPI/uvicorn/httpx
    - Installs Node.js and `@anthropic-ai/claude-agent-sdk` (Agent SDK dependency)
    - Installs Claude Code CLI via the official curl installer
    - Sets `/root/app` as the workdir and copies the local project into place
    """
    return (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install("claude-agent-sdk", "fastapi", "uvicorn", "httpx")
        .pip_install("uv")
        .apt_install("curl")
        .run_commands(
            "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
            "apt-get install -y nodejs",
            "npm install -g @anthropic-ai/claude-agent-sdk",  # Needed for Agent SDK
        )
        .env(
            {
                "AGENT_FS_ROOT": "/data",
                "PATH": (
                    "/root/.local/bin:/root/.claude/bin:"
                    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
                ),
            }
        )
        .workdir("/root/app")
        .add_local_dir(
            ".",
            remote_path="/root/app",
            copy=True,
            ignore=[".git", ".venv", "__pycache__", "*.pyc", ".DS_Store", "Makefile"],
        )
        .run_commands("cd /root/app && uv pip install -e . --system --no-cache")
    )


def _claude_cli_image() -> modal.Image:
    """Build a dedicated image for running the Claude Code CLI."""
    return (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install("claude-agent-sdk", "fastapi", "uvicorn", "httpx")
        .pip_install("uv")
        .apt_install("curl")
        .run_commands(
            "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
            "apt-get install -y nodejs",
            "npm install -g @anthropic-ai/claude-agent-sdk",  # Needed for Agent SDK
            "useradd -m -s /bin/bash -U claude",
            "su -l claude -c 'curl -fsSL https://claude.ai/install.sh | bash'",
            (
                "su -l claude -c "
                "'export PATH=/home/claude/.local/bin:/home/claude/.claude/bin:$PATH "
                "&& command -v claude'"
            ),
        )
        .env(
            {
                "AGENT_FS_ROOT": "/data",
                "PATH": (
                    "/root/.local/bin:/root/.claude/bin:"
                    "/home/claude/.local/bin:/home/claude/.claude/bin:"
                    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
                ),
            }
        )
        .workdir(str(_CLAUDE_CLI_APP_ROOT))
        .add_local_dir(
            ".",
            remote_path=str(_CLAUDE_CLI_APP_ROOT),
            copy=True,
            ignore=[".git", ".venv", "__pycache__", "*.pyc", ".DS_Store", "Makefile"],
        )
        .run_commands(
            f"chown -R {_CLAUDE_CLI_USER}:{_CLAUDE_CLI_USER} {_CLAUDE_CLI_APP_ROOT}",
            f"cd {_CLAUDE_CLI_APP_ROOT} && uv pip install -e . --system --no-cache",
        )
    )


def _autoscale_kwargs() -> dict[str, int]:
    """Build autoscaling kwargs for Modal functions when configured.

    Modal autoscaling parameters:
        - min_containers: Minimum always-warm containers (reduces cold starts)
        - max_containers: Maximum concurrent containers (cost/capacity limit)
        - buffer_containers: Extra warm containers beyond current demand
        - scaledown_window: Seconds to wait before scaling down idle containers

    See: https://modal.com/docs/guide/cold-start#scaling-settings
    """
    kwargs: dict[str, int] = {}
    if _settings.min_containers is not None:
        kwargs["min_containers"] = _settings.min_containers
    if _settings.max_containers is not None:
        kwargs["max_containers"] = _settings.max_containers
    if _settings.buffer_containers is not None:
        kwargs["buffer_containers"] = _settings.buffer_containers
    if _settings.scaledown_window is not None:
        kwargs["scaledown_window"] = _settings.scaledown_window
    return kwargs


def _claude_cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(_CLAUDE_CLI_HOME)
    env["USER"] = _CLAUDE_CLI_USER
    env["PATH"] = f"{_CLAUDE_CLI_PATH}:{env.get('PATH', '')}"
    return env


def _function_call_id(call: object) -> str | None:
    """Return a stable call identifier for Modal function calls."""
    for attr in ("object_id", "call_id", "id"):
        value = getattr(call, attr, None)
        if value:
            return str(value)
    return None


def _require_claude_cli_auth(env: dict[str, str]) -> None:
    """Ensure Claude CLI has credentials available."""
    if env.get("ANTHROPIC_API_KEY"):
        return
    raise RuntimeError(
        "ANTHROPIC_API_KEY is missing. Configure the 'anthropic-secret' "
        "Modal secret so Claude CLI can authenticate."
    )


def _claude_cli_ids() -> tuple[int, int]:
    try:
        entry = pwd.getpwnam(_CLAUDE_CLI_USER)
    except KeyError as exc:
        raise RuntimeError("Claude CLI user not found; rebuild the image to create it.") from exc
    return entry.pw_uid, entry.pw_gid


def _demote_to_claude():
    uid, gid = _claude_cli_ids()

    def _inner() -> None:
        os.setgid(gid)
        if hasattr(os, "setgroups"):
            os.setgroups([gid])
        os.setuid(uid)

    return _inner


def _maybe_chown_for_claude(path: Path) -> None:
    try:
        uid, gid = _claude_cli_ids()
    except RuntimeError:
        _logger.warning("Claude CLI user missing; skipping workspace chown")
        return
    try:
        os.chown(path, uid, gid)
        path.chmod(0o775)
    except PermissionError:
        _logger.warning("Unable to chown workspace for Claude CLI user", exc_info=True)


def _write_claude_cli_result(
    write_result_path: str | None,
    payload: dict,
    job_root: Path | None,
) -> None:
    if not write_result_path:
        return
    path = Path(write_result_path)
    if not path.is_absolute():
        base = job_root if job_root is not None else Path(_settings.agent_fs_root)
        path = base / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    _maybe_chown_for_claude(path)


def _function_resource_kwargs() -> dict[str, object]:
    """Build resource kwargs for Modal functions."""
    kwargs: dict[str, object] = {}
    if _settings.sandbox_cpu_limit is not None:
        kwargs["cpu"] = (_settings.sandbox_cpu, _settings.sandbox_cpu_limit)
    else:
        kwargs["cpu"] = _settings.sandbox_cpu

    if _settings.sandbox_memory_limit is not None:
        kwargs["memory"] = (_settings.sandbox_memory, _settings.sandbox_memory_limit)
    else:
        kwargs["memory"] = _settings.sandbox_memory

    ephemeral_disk = _validated_ephemeral_disk()
    if ephemeral_disk is not None:
        kwargs["ephemeral_disk"] = ephemeral_disk
    return kwargs


def _sandbox_resource_kwargs() -> dict[str, object]:
    """Build resource kwargs for Modal sandboxes."""
    kwargs: dict[str, object] = {}
    if _settings.sandbox_cpu_limit is not None:
        kwargs["cpu"] = (_settings.sandbox_cpu, _settings.sandbox_cpu_limit)
    else:
        kwargs["cpu"] = _settings.sandbox_cpu

    if _settings.sandbox_memory_limit is not None:
        kwargs["memory"] = (_settings.sandbox_memory, _settings.sandbox_memory_limit)
    else:
        kwargs["memory"] = _settings.sandbox_memory
    ephemeral_disk = _validated_ephemeral_disk()
    if ephemeral_disk is not None and _sandbox_supports_ephemeral_disk():
        kwargs["ephemeral_disk"] = ephemeral_disk
    return kwargs


def _sandbox_supports_ephemeral_disk() -> bool:
    """Return True if modal.Sandbox.create accepts ephemeral_disk."""
    try:
        return "ephemeral_disk" in inspect.signature(modal.Sandbox.create).parameters
    except (TypeError, ValueError):
        return False


def _validated_ephemeral_disk() -> int | None:
    """Validate ephemeral disk size against Modal limits."""
    if _settings.sandbox_ephemeral_disk is None:
        return None
    max_mib = 3145728
    if _settings.sandbox_ephemeral_disk <= 0:
        logging.getLogger(__name__).warning(
            "sandbox_ephemeral_disk=%s MiB must be positive; skipping",
            _settings.sandbox_ephemeral_disk,
        )
        return None
    if _settings.sandbox_ephemeral_disk > max_mib:
        logging.getLogger(__name__).warning(
            "sandbox_ephemeral_disk=%s MiB exceeds Modal maximum %s; skipping",
            _settings.sandbox_ephemeral_disk,
            max_mib,
        )
        return None
    return _settings.sandbox_ephemeral_disk


def _function_runtime_kwargs(
    *, include_retries: bool = True, include_autoscale: bool = True
) -> dict[str, object]:
    """Combine autoscaling and resource tuning for Modal functions."""
    kwargs: dict[str, object] = {}
    kwargs.update(_function_resource_kwargs())
    if include_autoscale:
        kwargs.update(_autoscale_kwargs())
    if include_retries:
        kwargs.update(_retry_kwargs())
    return kwargs


def _maybe_concurrent():
    """Return a concurrency decorator when configured, otherwise no-op."""
    if _settings.concurrent_max_inputs is None and _settings.concurrent_target_inputs is None:
        return lambda fn: fn
    return modal.concurrent(
        max_inputs=_settings.concurrent_max_inputs,
        target_inputs=_settings.concurrent_target_inputs,
    )


def _retry_policy() -> modal.Retries | None:
    """Build a Modal retry policy for transient failure recovery.

    Uses exponential backoff: delay = initial_delay * (backoff_coefficient ^ attempt)
    Delays are capped at max_delay to prevent unbounded waits.

    Defaults (when settings provided): 2x backoff, 1s initial, 60s max.
    Returns None if retry_max_attempts is not configured.

    See: https://modal.com/docs/guide/retries
    """
    if _settings.retry_max_attempts is None:
        return None
    return modal.Retries(
        max_retries=_settings.retry_max_attempts,
        # Exponential backoff: delay doubles each retry (2.0 coefficient)
        backoff_coefficient=_settings.retry_backoff_coefficient or 2.0,
        initial_delay=_settings.retry_initial_delay or 1.0,  # First retry after 1s
        max_delay=_settings.retry_max_delay or 60.0,  # Cap at 60s between retries
    )


def _retry_kwargs() -> dict[str, object]:
    policy = _retry_policy()
    if not policy:
        return {}
    return {"retries": policy}


def _job_queue_schedule() -> modal.Cron | None:
    cron = _settings.job_queue_cron
    if not cron:
        return None
    return modal.Cron(cron)


def _get_persist_volume() -> modal.Volume:
    """Return the configured persistent volume handle."""
    kwargs: dict[str, object] = {"create_if_missing": True}
    if _settings.persist_vol_version is not None:
        kwargs["version"] = _settings.persist_vol_version
    return modal.Volume.from_name(_settings.persist_vol_name, **kwargs)


def _reload_persist_volume() -> None:
    """Reload the persistent volume to see latest committed writes."""
    try:
        _get_persist_volume().reload()
    except Exception:
        logging.getLogger(__name__).warning("Failed to reload persistent volume", exc_info=True)


def _commit_persist_volume() -> None:
    """Commit pending writes to the persistent volume."""
    try:
        _get_persist_volume().commit()
    except RuntimeError as exc:
        message = str(exc)
        if "commit() can only be called" in message:
            return
        logging.getLogger(__name__).warning("Failed to commit persistent volume: %s", message)
    except Exception:
        logging.getLogger(__name__).warning("Failed to commit persistent volume", exc_info=True)


def _job_artifacts_root(job_id: str) -> Path:
    return job_workspace_root(_settings.agent_fs_root, job_id)


def _resolve_artifact_path(job_id: str, artifact_path: str) -> Path | None:
    """Resolve artifact path with security validation (HTTP endpoint helper).

    Convenience wrapper around resolve_job_artifact() for HTTP endpoints, using
    the configured agent_fs_root from settings. Validates that artifact_path
    stays within the job workspace.

    Args:
        job_id: Validated UUID job identifier (call _normalize_job_id_or_400 first)
        artifact_path: User-provided relative path from HTTP request
                      (e.g., from path parameter /jobs/{job_id}/artifacts/{path})

    Returns:
        Absolute Path to artifact if valid and within workspace, None if path
        traversal attempted or path escapes job boundary.

    Security:
        This function prevents directory traversal attacks. Always check return
        value for None before accessing filesystem.

    Usage in Endpoints:
        ```python
        @web_app.get("/jobs/{job_id}/artifacts/{artifact_path:path}")
        def download_artifact(job_id: str, artifact_path: str):
            job_id = _normalize_job_id_or_400(job_id)
            path = _resolve_artifact_path(job_id, artifact_path)
            if not path or not path.exists():
                raise HTTPException(404, "Artifact not found")
            return FileResponse(path)
        ```

    See Also:
        - resolve_job_artifact(): Full documentation of security model
        - _normalize_job_id_or_400(): Validate job_id before calling this
    """
    return resolve_job_artifact(_settings.agent_fs_root, job_id, artifact_path)


def _normalize_job_id_or_400(job_id: str) -> str:
    """Validate job_id from HTTP request and return 400 if invalid.

    Validates that job_id is a properly formatted UUID and raises HTTPException
    if validation fails. This function should be called at the beginning of all
    HTTP endpoints that accept job_id parameters.

    Args:
        job_id: Job ID from HTTP path parameter, query string, or request body

    Returns:
        Canonical UUID string if valid

    Raises:
        HTTPException: 400 Bad Request if job_id is not a valid UUID

    Security:
        Prevents injection attacks by validating job_id format before using it in:
        - Filesystem paths
        - Dict lookups
        - Database queries
        - HTTP responses

    Usage:
        ```python
        @web_app.get("/jobs/{job_id}")
        def get_job_status(job_id: str):
            job_id = _normalize_job_id_or_400(job_id)
            # Now safe to use job_id
            return get_job_status(job_id)
        ```

    Example Responses:
        Valid UUID:
        >>> _normalize_job_id_or_400("550e8400-e29b-41d4-a716-446655440000")
        '550e8400-e29b-41d4-a716-446655440000'

        Invalid inputs (raises 400):
        >>> _normalize_job_id_or_400("../../../etc/passwd")
        HTTPException(400, "Invalid job_id")

        >>> _normalize_job_id_or_400("not-a-uuid")
        HTTPException(400, "Invalid job_id")

    See Also:
        - normalize_job_id(): Core validation logic
    """
    normalized = normalize_job_id(job_id)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid job_id")
    return normalized


def _sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe use in HTTP Content-Disposition header.

    Removes path separators, control characters, and special characters that
    could enable HTTP header injection or path traversal attacks when the
    filename appears in the Content-Disposition header.

    Args:
        filename: The original filename from the filesystem (e.g., "report.pdf"
                 or potentially malicious input like "../../etc/passwd")

    Returns:
        Sanitized filename safe for HTTP headers, maximum 255 characters

    Security Model:
        This prevents two types of attacks:

        1. **Header Injection**: Control characters (\r, \n, \t) could inject
           additional HTTP headers if not sanitized:
           ```
           filename="file.txt\r\nX-Evil-Header: malicious"
           # Without sanitization becomes:
           Content-Disposition: attachment; filename="file.txt
           X-Evil-Header: malicious"
           ```

        2. **Path Traversal**: Path separators could cause browsers to save
           files in unexpected locations:
           ```
           filename="../../etc/passwd"  # Could save outside download directory
           ```

    Characters Removed:
        - Backslash (\\): Windows path separator
        - Forward slash (/): Unix path separator
        - Colon (:): Windows drive separator, NTFS stream separator
        - Asterisk (*): Wildcard character
        - Question mark (?): Wildcard character
        - Quote ("): Header delimiter
        - Less than (<): Angle bracket
        - Greater than (>): Angle bracket
        - Pipe (|): Command separator
        - Carriage return (\r): Line ending, header injection
        - Line feed (\n): Line ending, header injection
        - Tab (\t): Whitespace control character

        All replaced with underscore (_) to maintain readability

    Length Limit:
        Truncated to 255 characters to prevent:
        - Buffer overflows in legacy systems
        - Filesystem limitations (most filesystems have 255 byte filename limit)
        - DoS via excessively long headers

    Examples:
        >>> _sanitize_filename("report.pdf")
        'report.pdf'

        >>> _sanitize_filename("../../etc/passwd")
        '.._.._etc_passwd'

        >>> _sanitize_filename("file\r\nX-Evil: header.txt")
        'file__X-Evil_ header.txt'

        >>> _sanitize_filename("data:sensitive.csv")
        'data_sensitive.csv'

    Usage in File Download:
        ```python
        @web_app.get("/jobs/{job_id}/artifacts/{path}")
        def download_artifact(job_id: str, path: str):
            resolved = _resolve_artifact_path(job_id, path)
            if not resolved:
                raise HTTPException(404)

            safe_name = _sanitize_filename(resolved.name)
            return FileResponse(
                resolved,
                headers={"Content-Disposition": f'attachment; filename="{safe_name}"'}
            )
        ```

    See Also:
        - RFC 6266: Use of Content-Disposition Header Field
        - OWASP: HTTP Response Splitting
    """
    # Remove path separators and control characters that could enable attacks
    sanitized = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", filename)
    # Limit length to prevent excessively long filenames
    return sanitized[:255]


def _build_artifact_manifest(job_id: str) -> ArtifactManifest:
    """Build manifest of all artifacts created by a job.

    Recursively scans the job's workspace directory and collects metadata for
    all files, including size, MIME type, and timestamps. Used for artifact
    listing endpoint and job status responses.

    Args:
        job_id: Validated UUID job identifier

    Returns:
        ArtifactManifest containing:
            - root: Absolute path to job workspace
            - files: List of ArtifactEntry objects with metadata for each file

    Scanning Behavior:
        - Uses rglob("*") for recursive traversal of all subdirectories
        - Only includes files (not directories)
        - Relative paths computed from workspace root
        - Returns empty file list if workspace doesn't exist

    Metadata Collected:
        - path: Relative path from workspace root (e.g., "output.txt", "data/results.csv")
        - size_bytes: File size from os.stat().st_size
        - content_type: MIME type guessed from extension (e.g., "text/csv", "image/png")
        - created_at: Unix timestamp from st_birthtime (macOS/BSD) or None (Linux)
        - modified_at: Unix timestamp from st_mtime (all platforms)

    Platform-Specific Behavior:
        - macOS/BSD: created_at uses st_birthtime (true creation time)
        - Linux: created_at is None (most filesystems don't track creation time)
        - All platforms: modified_at available via st_mtime

    Example Output:
        >>> manifest = _build_artifact_manifest("550e8400-...")
        >>> manifest.root
        '/data/jobs/550e8400-.../
        >>> manifest.files
        [
            ArtifactEntry(
                path="output.txt",
                size_bytes=1024,
                content_type="text/plain",
                created_at=1704067200,
                modified_at=1704067300
            ),
            ArtifactEntry(
                path="results/data.csv",
                size_bytes=2048,
                content_type="text/csv",
                created_at=None,  # Linux system
                modified_at=1704067400
            )
        ]

    Usage:
        ```python
        # After job completion
        manifest = _build_artifact_manifest(job_id)
        update_job(job_id, {"artifacts": manifest.model_dump()})

        # In artifact listing endpoint
        @web_app.get("/jobs/{job_id}/artifacts")
        def list_artifacts(job_id: str):
            job_id = _normalize_job_id_or_400(job_id)
            manifest = _build_artifact_manifest(job_id)
            return ArtifactListResponse(job_id=job_id, artifacts=manifest)
        ```

    See Also:
        - ArtifactManifest: Schema definition
        - ArtifactEntry: Individual file metadata schema
    """
    root = _job_artifacts_root(job_id)
    files: list[ArtifactEntry] = []
    if root.exists():
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            stat = path.stat()
            content_type, _ = mimetypes.guess_type(str(path))
            created_at = getattr(stat, "st_birthtime", None)
            files.append(
                ArtifactEntry(
                    path=str(path.relative_to(root)),
                    size_bytes=stat.st_size,
                    content_type=content_type,
                    created_at=int(created_at) if created_at is not None else None,
                    modified_at=int(stat.st_mtime),
                )
            )
    return ArtifactManifest(root=str(root), files=files)


def _extract_job_metrics(result: dict) -> dict[str, object]:
    """Extract performance metrics from agent SDK response.

    Parses the agent run result to extract timing, cost, token usage, and other
    observability metrics. These metrics are stored in the job record and returned
    in job status responses.

    Args:
        result: Agent SDK response dict containing:
               - summary: Dict with duration_ms, total_cost_usd, usage, etc.
               - messages: List of conversation messages with tool_use blocks

    Returns:
        Dictionary of metrics with None values filtered out. Includes:
            - agent_duration_ms: Total agent execution time in milliseconds
            - agent_duration_api_ms: Time spent in API calls (subset of total)
            - total_cost_usd: Estimated cost in USD based on token usage
            - usage: Token usage dict (input_tokens, output_tokens, cache tokens)
            - num_turns: Number of conversation turns in the agent loop
            - session_id: Agent session identifier for resumption
            - tool_call_count: Number of tool invocations (counted from messages)
            - models: Sorted list of unique model IDs used (e.g., ["claude-sonnet-4"])

    Metric Sources:
        From summary dict (provided by Claude Agent SDK):
            - duration_ms, duration_api_ms: Timing metrics
            - total_cost_usd: Cost calculation
            - usage: Token counts
            - num_turns: Conversation length
            - session_id: Session identifier

        Computed from messages:
            - tool_call_count: Count of content blocks with type="tool_use"
            - models: Unique set of message.model values

    Example Input (result):
        {
            "summary": {
                "duration_ms": 1234,
                "duration_api_ms": 987,
                "total_cost_usd": 0.0045,
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "num_turns": 3,
                "session_id": "sess_abc123"
            },
            "messages": [
                {
                    "model": "claude-sonnet-4",
                    "content": [{"type": "tool_use", "name": "calculate"}]
                },
                {
                    "model": "claude-sonnet-4",
                    "content": [{"type": "text", "text": "result"}]
                }
            ]
        }

    Example Output:
        {
            "agent_duration_ms": 1234,
            "agent_duration_api_ms": 987,
            "total_cost_usd": 0.0045,
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "num_turns": 3,
            "session_id": "sess_abc123",
            "tool_call_count": 1,
            "models": ["claude-sonnet-4"]
        }

    Usage:
        ```python
        # After agent execution
        result = agent_client.run(prompt)
        metrics = _extract_job_metrics(result)

        # Store in job record
        update_job(job_id, {
            **metrics,
            "result": result,
            "status": "complete"
        })
        ```

    See Also:
        - JobStatusResponse: Schema including these metric fields
    """
    summary = result.get("summary") or {}
    messages = result.get("messages") or []
    tool_calls = 0
    models: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        model = message.get("model")
        if model:
            models.add(model)
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_calls += 1
    metrics: dict[str, object] = {
        "agent_duration_ms": summary.get("duration_ms"),
        "agent_duration_api_ms": summary.get("duration_api_ms"),
        "total_cost_usd": summary.get("total_cost_usd"),
        "usage": summary.get("usage"),
        "num_turns": summary.get("num_turns"),
        "session_id": summary.get("session_id"),
        "tool_call_count": tool_calls or None,
        "models": sorted(models) if models else None,
    }
    return {key: value for key, value in metrics.items() if value is not None}


def _webhook_retry_delay(settings: Settings, attempt: int) -> float:
    """Calculate exponential backoff delay for webhook retry attempts.

    Computes the delay before the next webhook delivery attempt using exponential
    backoff with a maximum cap. Each retry waits progressively longer to avoid
    overwhelming failing endpoints.

    Args:
        settings: Configuration object with webhook retry parameters
        attempt: Current attempt number (1-indexed, where 1 = first attempt)

    Returns:
        Delay in seconds before next retry attempt (float)

    Formula:
        delay = initial * (coefficient ^ (attempt - 1))
        capped_delay = min(delay, max_delay)

    Default Configuration:
        - initial_delay: 1.0 second
        - backoff_coefficient: 2.0 (doubles each time)
        - max_delay: 30.0 seconds

    Example Delay Sequence (with defaults):
        Attempt 1: 1.0 * (2.0 ^ 0) = 1.0s
        Attempt 2: 1.0 * (2.0 ^ 1) = 2.0s
        Attempt 3: 1.0 * (2.0 ^ 2) = 4.0s
        Attempt 4: 1.0 * (2.0 ^ 3) = 8.0s
        Attempt 5: 1.0 * (2.0 ^ 4) = 16.0s
        Attempt 6: 1.0 * (2.0 ^ 5) = 32.0s → capped to 30.0s
        Attempt 7+: 30.0s (all capped at max_delay)

    Rationale:
        - **Exponential Backoff**: Gives failing services time to recover without
          immediate hammering
        - **Max Delay Cap**: Prevents waiting excessively long (e.g., hours) for
          endpoints that will never recover
        - **First Attempt Immediate**: attempt=1 means delay=0 for first try
          (exponent is attempt-1)

    Usage in deliver_webhook:
        ```python
        for attempt in range(1, max_attempts + 1):
            try:
                response = httpx.post(url, ...)
                if response.ok:
                    return
            except Exception:
                pass

            if attempt < max_attempts:
                time.sleep(_webhook_retry_delay(settings, attempt))
        ```

    See Also:
        - Settings: webhook_retry_* configuration fields
        - deliver_webhook(): Uses this for retry timing
    """
    delay = settings.webhook_retry_initial_delay * (
        settings.webhook_retry_backoff_coefficient ** max(attempt - 1, 0)
    )
    return min(delay, settings.webhook_retry_max_delay)


def _maybe_trigger_webhook(job_id: str, event: str) -> None:
    """Trigger webhook delivery asynchronously if configured for the job.

    Checks if the job has a webhook configured and spawns an async Modal function
    to deliver the webhook notification. This function returns immediately without
    waiting for delivery completion.

    Args:
        job_id: UUID of the job that triggered the event
        event: Event type, typically "job.complete" or "job.failed"

    Behavior:
        - Checks job record exists
        - Checks webhook_config exists and has a URL
        - If both checks pass, spawns deliver_webhook.spawn(job_id, event)
        - Returns immediately (non-blocking)
        - If job or webhook not found, silently does nothing

    Async Execution:
        The webhook delivery runs in a separate Modal function invocation with:
        - Independent retry logic (handled by deliver_webhook function)
        - Status tracking in job record (webhook.attempts, webhook.last_status)
        - Exponential backoff between retries
        - Fresh signature generation for each attempt

    When Called:
        Automatically called after job completion/failure in process_job_queue:
        ```python
        try:
            result = execute_agent_job(job_id)
            update_job(job_id, {"status": "complete", "result": result})
            _maybe_trigger_webhook(job_id, "job.complete")
        except Exception as exc:
            update_job(job_id, {"status": "failed", "error": str(exc)})
            _maybe_trigger_webhook(job_id, "job.failed")
        ```

    Example Job Record with Webhook:
        {
            "job_id": "550e8400-...",
            "webhook_config": {
                "url": "https://example.com/webhook",
                "signing_secret": "wh_secret_123",
                "max_attempts": 3
            }
        }

    Example Job Record without Webhook:
        {
            "job_id": "550e8400-...",
            # No webhook_config field
        }
        # _maybe_trigger_webhook returns without doing anything

    See Also:
        - deliver_webhook(): Actual delivery implementation with retries
        - WebhookConfig: Schema for webhook configuration
        - WebhookStatus: Schema for delivery status tracking
    """
    record = get_job_record(job_id)
    if not record:
        return
    config = record.get("webhook_config")
    if not config or not config.get("url"):
        return
    deliver_webhook.spawn(job_id, event)


# Create image and secrets
agent_sdk_image = _base_anthropic_sdk_image()
claude_cli_image = _claude_cli_image()
agent_sdk_secrets = get_modal_secrets()


@app.function(
    image=agent_sdk_image,
    secrets=agent_sdk_secrets,
    volumes={_settings.agent_fs_root: _get_persist_volume()},
    **_function_runtime_kwargs(include_retries=False),
)
@_maybe_concurrent()
# requires_proxy_auth: When True, requests must include Modal workspace auth token.
# Protects public endpoints from unauthorized access. Set via require_proxy_auth setting.
# See: https://modal.com/docs/guide/webhooks#proxy-authentication
# custom_domains: Production-ready branding with custom domain names.
# See: https://modal.com/docs/guide/webhooks#custom-domains
@modal.asgi_app(
    requires_proxy_auth=_settings.require_proxy_auth,
    custom_domains=_settings.custom_domains or [],
)
def http_app():
    """ASGI app exposing HTTP endpoints for the agent service."""
    return web_app


@web_app.get("/health")
async def health():
    """Health check endpoint."""
    return {"ok": True}


@web_app.post("/query")
async def query_proxy(request: Request, body: QueryBody):
    """Proxy query requests to the background sandbox service."""
    # Use async getter to avoid blocking event loop
    sb, url = await get_or_start_background_sandbox_aio()

    # Optional: per-request connect token (verified in sandbox service)
    headers = {}
    settings = Settings()
    if settings.enforce_connect_token:
        creds = await sb.create_connect_token.aio(
            user_metadata={"ip": request.client.host or "unknown"}
        )
        headers = {"Authorization": f"Bearer {creds.token}"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        r = await client.post(
            f"{url.rstrip('/')}/query",
            json=body.model_dump(),
            headers=headers,
            timeout=httpx.Timeout(120.0, connect=30.0),
        )
        r.raise_for_status()
        return r.json()


@web_app.post("/query_stream")
async def query_stream(request: Request, body: QueryBody):
    """Stream query responses from the background sandbox service."""
    sb, url = await get_or_start_background_sandbox_aio()

    headers = {}
    settings = Settings()
    if settings.enforce_connect_token:
        creds = await sb.create_connect_token.aio(
            user_metadata={"ip": request.client.host or "unknown"}
        )
        headers = {"Authorization": f"Bearer {creds.token}"}

    async def sse_proxy():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", f"{url.rstrip('/')}/query_stream", json=body.model_dump(), headers=headers
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        sse_proxy(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@web_app.post("/claude_cli", response_model=ClaudeCliResponse)
async def claude_cli_proxy(request: Request, body: ClaudeCliRequest):
    """Run Claude Code CLI in the dedicated CLI environment."""
    allowed_tools = None
    if body.allowed_tools:
        allowed_tools = ",".join(body.allowed_tools)

    def _run():
        return run_claude_cli_remote.remote(
            prompt=body.prompt,
            allowed_tools=allowed_tools,
            dangerously_skip_permissions=body.dangerously_skip_permissions,
            output_format=body.output_format,
            timeout_seconds=body.timeout_seconds,
            max_turns=body.max_turns,
            job_id=body.job_id,
            debug=body.debug,
            probe=body.probe,
            write_result_path=body.write_result_path,
        )

    return await anyio.to_thread.run_sync(_run)


@web_app.post("/claude_cli/submit", response_model=ClaudeCliSubmitResponse)
async def claude_cli_submit(body: ClaudeCliRequest) -> ClaudeCliSubmitResponse:
    """Start a Claude CLI run asynchronously and return a call id for polling."""
    allowed_tools = None
    if body.allowed_tools:
        allowed_tools = ",".join(body.allowed_tools)

    def _spawn():
        return run_claude_cli_remote.spawn(
            prompt=body.prompt,
            allowed_tools=allowed_tools,
            dangerously_skip_permissions=body.dangerously_skip_permissions,
            output_format=body.output_format,
            timeout_seconds=body.timeout_seconds,
            max_turns=body.max_turns,
            job_id=body.job_id,
            debug=body.debug,
            probe=body.probe,
            write_result_path=body.write_result_path,
        )

    call = await anyio.to_thread.run_sync(_spawn)
    call_id = _function_call_id(call)
    if not call_id:
        raise HTTPException(status_code=500, detail="Unable to determine call id")
    return ClaudeCliSubmitResponse(ok=True, call_id=call_id)


@web_app.get("/claude_cli/result/{call_id}", response_model=ClaudeCliPollResponse)
async def claude_cli_result(call_id: str):
    """Poll a Claude CLI run by call id."""
    try:
        call = modal.FunctionCall.from_id(call_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Unknown call id") from exc

    try:
        result = await anyio.to_thread.run_sync(lambda: call.get(timeout=0))
    except modal_exc.TimeoutError:
        return JSONResponse(
            status_code=202,
            content=ClaudeCliPollResponse(ok=True, status="running").model_dump(),
        )
    except modal_exc.OutputExpiredError:
        return JSONResponse(
            status_code=410,
            content=ClaudeCliPollResponse(
                ok=False, status="expired", error="Result expired"
            ).model_dump(),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=ClaudeCliPollResponse(ok=False, status="failed", error=str(exc)).model_dump(),
        )

    return ClaudeCliPollResponse(ok=True, status="complete", result=result)


@web_app.post("/submit", response_model=JobSubmitResponse)
async def submit_job(body: JobSubmitRequest) -> JobSubmitResponse:
    """Enqueue a background job and return its id."""
    job_id = enqueue_job(
        body.question,
        tenant_id=body.tenant_id,
        user_id=body.user_id,
        schedule_at=body.schedule_at,
        webhook=body.webhook,
        metadata=body.metadata,
    )
    return JobSubmitResponse(job_id=job_id)


@web_app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def job_status(job_id: str) -> JobStatusResponse:
    """Fetch job status and result (if available)."""
    job_id = _normalize_job_id_or_400(job_id)
    status = get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    return status


@web_app.get("/jobs/{job_id}/artifacts", response_model=ArtifactListResponse)
async def job_artifacts(job_id: str) -> ArtifactListResponse:
    """List artifacts generated by a job."""
    job_id = _normalize_job_id_or_400(job_id)
    status = get_job_status(job_id)
    _reload_persist_volume()
    if not status:
        manifest = _build_artifact_manifest(job_id)
        if not manifest.files:
            raise HTTPException(status_code=404, detail="Job not found")
        return ArtifactListResponse(job_id=job_id, artifacts=manifest)
    manifest = status.artifacts or _build_artifact_manifest(job_id)
    return ArtifactListResponse(job_id=job_id, artifacts=manifest)


@web_app.get("/jobs/{job_id}/artifacts/{artifact_path:path}")
async def download_job_artifact(job_id: str, artifact_path: str):
    """Download a specific job artifact."""
    job_id = _normalize_job_id_or_400(job_id)
    status = get_job_status(job_id)
    _reload_persist_volume()
    resolved = _resolve_artifact_path(job_id, artifact_path)
    if not resolved or not resolved.exists() or not resolved.is_file():
        detail = "Artifact not found" if status else "Job not found"
        raise HTTPException(status_code=404, detail=detail)

    # Sanitize filename to prevent header injection attacks
    safe_filename = _sanitize_filename(resolved.name)

    # Use both ASCII fallback and RFC 2231 encoded filename for maximum compatibility
    return FileResponse(
        str(resolved),
        filename=safe_filename,
        headers={
            "Content-Disposition": f"attachment; filename=\"{safe_filename}\"; filename*=UTF-8''{quote(safe_filename)}"
        },
    )


@web_app.delete("/jobs/{job_id}", response_model=JobStatusResponse)
async def cancel_job_request(job_id: str) -> JobStatusResponse:
    """Cancel a queued job."""
    job_id = _normalize_job_id_or_400(job_id)
    status = cancel_job(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    return status


@web_app.get("/service_info")
async def service_info():
    """Get information about the background sandbox service."""
    sb, url = await get_or_start_background_sandbox_aio()
    return {"url": url, "sandbox_id": sb.object_id}


@app.function(image=agent_sdk_image, secrets=agent_sdk_secrets)
async def tail_logs(n: int = 200, timeout: float = 2.0) -> list[str]:
    """Tail logs from the background sandbox.

    Args:
        n: Maximum number of log lines to return.
        timeout: Timeout in seconds for log collection.

    Returns:
        List of log lines.
    """
    sb, _ = await get_or_start_background_sandbox_aio()
    from collections import deque

    buf = deque(maxlen=n)
    async with anyio.move_on_after(timeout):
        async for msg in sb.stdout.aio():
            for line in str(msg).splitlines():
                buf.append(line)
    return list(buf)


# Persistent registry for sandbox metadata (survives sandbox restarts).
# Keys are sandbox names (e.g., SANDBOX_NAME), values are dicts with:
#   - id: Sandbox object_id
#   - url: Service tunnel URL
#   - volume: Name of attached persistent volume
#   - created_at: Unix timestamp of creation
#   - tags: Dict of sandbox tags (role, app, port)
#   - status: Current status ("running", "missing")
SESSIONS = modal.Dict.from_name("sandbox-sessions", create_if_missing=True)

# Service sandbox identity and config (will be initialized from Settings)
SANDBOX_NAME = _settings.sandbox_name
SERVICE_PORT = _settings.service_port
PERSIST_VOL_NAME = _settings.persist_vol_name


# =============================================================================
# GLOBAL STATE MANAGEMENT
# =============================================================================
# These module-level globals store handles to the background sandbox and its URL.
#
# WHY THIS WORKS IN MODAL:
# - Each Modal worker process has its own isolated Python interpreter
# - Within a single worker, multiple requests share the same module state
# - This means subsequent requests in the same worker reuse the existing sandbox
#   connection instead of creating a new one (avoiding cold-start latency)
#
# IMPORTANT CAVEATS:
# - Different Modal workers will each have their own SANDBOX/SERVICE_URL
# - That's OK because they all discover the SAME sandbox via `from_name()`
# - If the sandbox dies, the next request will detect this and create a new one
# =============================================================================
SANDBOX: modal.Sandbox | None = None
SERVICE_URL: str | None = None


def _wait_for_service(url: str, timeout: int = 60, path: str = "/health_check") -> None:
    """Block until an HTTP health check returns 200 OK.

    Args:
        url: Base URL of the service (including scheme and host).
        timeout: Maximum time to wait in seconds.
        path: Health check path to append to URL.

    Raises:
        TimeoutError: If the service does not become healthy in time.
    """
    check_url = f"{url.rstrip('/')}{path}"
    start = time.time()
    delay = 0.5
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(check_url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            time.sleep(delay)
            delay = min(delay * 1.5, 3.0)
    raise TimeoutError(f"Service {check_url} did not become available within {timeout} seconds")


# Cron job that runs every 2 minutes to verify sandbox health and update SESSIONS metadata.
# If the sandbox has died, marks it as "missing" so get_or_start_background_sandbox()
# will create a new one on the next request.
@app.function(
    image=agent_sdk_image,
    secrets=agent_sdk_secrets,
    schedule=modal.Cron("*/2 * * * *"),
    **_retry_kwargs(),
)
def cleanup_sessions():
    """Verify sandbox health and update SESSIONS registry.

    Runs every 2 minutes via cron. Checks if the named sandbox is still alive
    by attempting to fetch its tunnel URLs. Updates SESSIONS status accordingly.
    """
    try:
        sb = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)
        _ = sb.tunnels()  # Will raise NotFoundError if sandbox is gone
        SESSIONS[SANDBOX_NAME] = {**SESSIONS.get(SANDBOX_NAME, {}), "status": "running"}
    except modal_exc.NotFoundError:
        SESSIONS[SANDBOX_NAME] = {"status": "missing"}
    except Exception:
        logging.getLogger(__name__).exception("Unexpected error cleaning up sessions")


def get_or_start_background_sandbox() -> tuple[modal.Sandbox, str]:
    """Return a running background sandbox and its encrypted service URL.

    Starts a daemonized sandbox running `uvicorn agent_sandbox.controllers.controller:app` if one is
    not already available, then discovers its encrypted tunnel URL on port
    8001. The function blocks until the `/health_check` endpoint responds.

    Returns:
        A pair of `(sandbox, service_url)`.
    """
    global SANDBOX, SERVICE_URL

    # STEP 1: Check if we already have a connection in this worker's memory
    if SANDBOX is not None and SERVICE_URL:
        return SANDBOX, SERVICE_URL

    # -------------------------------------------------------------------------
    # STEP 2: Try to find an EXISTING sandbox by name
    # -------------------------------------------------------------------------
    # Modal sandboxes can be given names. This allows multiple workers (or even
    # separate Modal function invocations) to discover and reuse the same
    # long-running sandbox. This is key to the "persistent service" pattern.
    # -------------------------------------------------------------------------
    try:
        sb = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)
        tunnels = sb.tunnels()
        if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
            SANDBOX = sb
            SERVICE_URL = tunnels[SERVICE_PORT].url
            _wait_for_service(SERVICE_URL)
            return SANDBOX, SERVICE_URL
    except Exception:
        pass  # Sandbox doesn't exist or isn't accessible; we'll create a new one

    # -------------------------------------------------------------------------
    # STEP 3: Create a NEW sandbox
    # -------------------------------------------------------------------------
    # If no existing sandbox was found, create one. This runs uvicorn inside
    # an isolated container with its own filesystem, network, and resources.
    # -------------------------------------------------------------------------
    svc_vol = _get_persist_volume()
    try:
        SANDBOX = modal.Sandbox.create(
            # Command to run inside the sandbox (uvicorn starts our FastAPI app)
            "uvicorn",
            "agent_sandbox.controllers.controller:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(SERVICE_PORT),
            # MODAL-SPECIFIC PARAMETERS EXPLAINED:
            app=app,  # Associates sandbox with this Modal App
            image=agent_sdk_image,  # Container image with all dependencies
            secrets=agent_sdk_secrets,  # Inject secrets (API keys) into environment
            workdir="/root/app",  # Working directory inside container
            name=SANDBOX_NAME,  # Named sandbox enables discovery via from_name()
            # encrypted_ports: Makes these ports accessible via Modal's secure tunnels.
            # Without this, the ports would only be accessible inside the sandbox.
            # Modal creates HTTPS URLs that tunnel traffic to these internal ports.
            # Supports multiple ports for multi-service architectures (API + frontend).
            encrypted_ports=_settings.service_ports,
            # volumes: Mount a Modal Volume at /data for persistent storage.
            # Files written here survive sandbox restarts (but only after termination).
            volumes={"/data": svc_vol},
            # Lifecycle settings:
            timeout=_settings.sandbox_timeout,  # Max lifetime (default: 12 hours)
            idle_timeout=_settings.sandbox_idle_timeout,  # Shutdown after idle (default: 10 min)
            **_sandbox_resource_kwargs(),
            verbose=True,
        )
    except modal_exc.AlreadyExistsError:
        SANDBOX = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)

    # Optional: set tags after creation (useful for filtering in Modal dashboard)
    SANDBOX.set_tags({"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)})

    # -------------------------------------------------------------------------
    # STEP 4: Discover the tunnel URL (polling loop)
    # -------------------------------------------------------------------------
    # Modal's encrypted_ports feature creates a secure tunnel to the sandbox.
    # However, the tunnel URL isn't immediately available - Modal needs a moment
    # to provision it. We poll `sandbox.tunnels()` until the URL appears.
    #
    # The returned URL looks like: https://xxxx.modal.run
    # This URL is publicly accessible and routes to port 8001 inside the sandbox.
    # -------------------------------------------------------------------------
    SERVICE_URL = None
    deadline = time.time() + 30  # 30-second timeout for tunnel discovery
    while time.time() < deadline:
        tunnels = SANDBOX.tunnels()
        if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
            SERVICE_URL = tunnels[SERVICE_PORT].url
            break
        time.sleep(0.5)

    if not SERVICE_URL:
        raise RuntimeError("Failed to start background sandbox or get service URL")

    _wait_for_service(SERVICE_URL)
    try:
        SESSIONS[SANDBOX_NAME] = {
            "id": SANDBOX.object_id,
            "url": SERVICE_URL,
            "volume": PERSIST_VOL_NAME,
            "created_at": int(time.time()),
            "tags": {"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)},
            "status": "running",
        }
    except modal_exc.Error as e:
        logging.getLogger(__name__).warning(
            "Failed to persist session metadata to Modal Dict: %s", e
        )
    except Exception:
        logging.getLogger(__name__).exception("Unexpected error persisting session metadata")

    return SANDBOX, SERVICE_URL


async def _wait_for_service_aio(url: str, timeout: int = 60, path: str = "/health_check") -> None:
    """Async version of _wait_for_service.

    Args:
        url: Base URL of the service.
        timeout: Maximum time to wait in seconds.
        path: Health check path to append to URL.

    Raises:
        TimeoutError: If the service does not become healthy in time.
    """
    check_url = f"{url.rstrip('/')}{path}"
    deadline = anyio.current_time() + timeout
    async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=2.0)) as client:
        while anyio.current_time() < deadline:
            try:
                r = await client.get(check_url)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            await anyio.sleep(1)
    raise TimeoutError(f"Service {check_url} did not become available within {timeout} seconds")


async def get_or_start_background_sandbox_aio() -> tuple[modal.Sandbox, str]:
    """Async version of get_or_start_background_sandbox.

    Returns:
        A pair of `(sandbox, service_url)`.
    """
    global SANDBOX, SERVICE_URL

    if SANDBOX and SERVICE_URL:
        return SANDBOX, SERVICE_URL

    # Attempt global reuse by name across workers/processes
    try:
        sb = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)
        # Poll tunnels until URL appears (mirrors sync behavior)
        deadline = anyio.current_time() + 30
        url = None
        while anyio.current_time() < deadline:
            tunnels = await sb.tunnels.aio()
            if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
                url = tunnels[SERVICE_PORT].url
                break
            await anyio.sleep(0.5)
        if url:
            SANDBOX, SERVICE_URL = sb, url
            await _wait_for_service_aio(SERVICE_URL)
            return SANDBOX, SERVICE_URL
    except Exception:
        pass

    # Create with persistent volume
    svc_vol = _get_persist_volume()
    try:
        SANDBOX = await modal.Sandbox.create.aio(
            "uvicorn",
            "agent_sandbox.controllers.controller:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(SERVICE_PORT),
            app=app,
            image=agent_sdk_image,
            secrets=agent_sdk_secrets,
            workdir="/root/app",
            name=SANDBOX_NAME,
            encrypted_ports=_settings.service_ports,
            volumes={"/data": svc_vol},
            timeout=_settings.sandbox_timeout,
            idle_timeout=_settings.sandbox_idle_timeout,
            **_sandbox_resource_kwargs(),
            verbose=True,
        )
    except modal_exc.AlreadyExistsError:
        SANDBOX = await modal.Sandbox.from_name.aio("test-sandbox", SANDBOX_NAME)

    # Optional: set tags after creation
    await SANDBOX.set_tags.aio(
        {"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)}
    )

    # Poll tunnels until URL appears
    deadline = anyio.current_time() + 30
    SERVICE_URL = None
    while anyio.current_time() < deadline:
        tunnels = await SANDBOX.tunnels.aio()
        if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
            SERVICE_URL = tunnels[SERVICE_PORT].url
            break
        await anyio.sleep(0.5)

    if not SERVICE_URL:
        raise RuntimeError("Failed to start background sandbox or get service URL")

    await _wait_for_service_aio(SERVICE_URL)

    # Persist session metadata
    try:
        SESSIONS[SANDBOX_NAME] = {
            "id": SANDBOX.object_id,
            "url": SERVICE_URL,
            "volume": PERSIST_VOL_NAME,
            "created_at": int(time.time()),
            "tags": {"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)},
            "status": "running",
        }
    except Exception:
        pass

    return SANDBOX, SERVICE_URL


@app.cls(
    image=agent_sdk_image,
    secrets=agent_sdk_secrets,
    volumes={"/data": _get_persist_volume()},
    enable_memory_snapshot=_settings.enable_memory_snapshot,
    **_function_runtime_kwargs(include_autoscale=False),
)
class AgentRunner:
    """Class-based agent runner with lifecycle hooks and optional memory snapshots.

    Memory Snapshot Lifecycle (when enable_memory_snapshot=True):
        1. First cold start: _snapshot_setup() runs and Modal captures memory state
        2. Subsequent starts: Container restores from snapshot, _post_restore() runs
        3. On termination: _cleanup() releases resources

    This pattern moves heavy initialization (MCP servers, tool registry) into the
    snapshot, dramatically reducing cold start latency for subsequent invocations.

    See: https://modal.com/docs/guide/memory-snapshot
    """

    system_prompt: str = modal.parameter(default=SYSTEM_PROMPT)

    @modal.enter(snap=True)
    def _snapshot_setup(self) -> None:
        """Initialize agent options and capture in memory snapshot.

        snap=True means this runs BEFORE the snapshot is taken. The initialized
        _options object will be serialized into the snapshot and restored on
        subsequent container starts, avoiding re-initialization overhead.
        """
        from agent_sandbox.agents.loop import build_agent_options
        from agent_sandbox.tools import get_allowed_tools, get_mcp_servers

        self._options = build_agent_options(
            get_mcp_servers(),
            get_allowed_tools(),
            self.system_prompt,
            max_turns=_settings.agent_max_turns,
        )

    @modal.enter(snap=False)
    def _post_restore(self) -> None:
        """Post-restore initialization after snapshot restore.

        snap=False means this runs AFTER restoring from snapshot. Used to
        reinitialize any state that can't be serialized (e.g., network connections).
        Also serves as fallback if snapshot wasn't taken or is corrupted.
        """
        if getattr(self, "_options", None) is None:
            from agent_sandbox.agents.loop import build_agent_options
            from agent_sandbox.tools import get_allowed_tools, get_mcp_servers

            self._options = build_agent_options(
                get_mcp_servers(),
                get_allowed_tools(),
                self.system_prompt,
                max_turns=_settings.agent_max_turns,
            )

    @modal.exit()
    def _cleanup(self) -> None:
        """Release resources when container shuts down."""
        self._options = None

    @modal.method()
    def run(self, question: str = DEFAULT_QUESTION) -> None:
        """Execute an agent query and stream responses to stdout."""
        import anyio
        from claude_agent_sdk import ClaudeSDKClient

        async def _run() -> None:
            async with ClaudeSDKClient(options=self._options) as client:
                await client.query(question)
                async for msg in client.receive_response():
                    print(msg)

        anyio.run(_run)


@app.function(
    image=agent_sdk_image,
    secrets=agent_sdk_secrets,
    volumes={"/data": _get_persist_volume()},
    **_function_runtime_kwargs(include_autoscale=False),
)
def run_agent_remote(question: str = DEFAULT_QUESTION) -> None:
    """Run the agent once in a short-lived Modal function.

    This is useful for synchronous, on-demand runs. For long-running, low-latency
    serving, prefer the background sandbox pattern used by `http_app`.

    Args:
        question: Natural-language query to send to the agent.
    """
    AgentRunner().run.remote(question)


@app.function(
    image=claude_cli_image,
    secrets=agent_sdk_secrets,
    volumes={"/data": _get_persist_volume()},
    timeout=60 * 60 * 24,
    **_function_runtime_kwargs(include_autoscale=False),
)
def run_claude_cli_remote(
    prompt: str = DEFAULT_QUESTION,
    allowed_tools: str | None = None,
    dangerously_skip_permissions: bool = True,
    output_format: str = "json",
    timeout_seconds: int = 120,
    max_turns: int | None = None,
    job_id: str | None = None,
    return_stdout: bool = False,
    debug: bool = False,
    probe: str | None = None,
    write_result_path: str | None = None,
) -> dict | str:
    """Run Claude Code CLI in a dedicated image and return the response."""
    tools_list = None
    if allowed_tools:
        tools_list = [tool.strip() for tool in allowed_tools.split(",") if tool.strip()]

    job_root = None
    normalized_job_id = normalize_job_id(job_id)
    if normalized_job_id:
        job_root = job_workspace_root(_settings.agent_fs_root, normalized_job_id)
        job_root.mkdir(parents=True, exist_ok=True)
        _maybe_chown_for_claude(job_root)

    cmd = ["claude", "-p", prompt, "--output-format", output_format]
    if dangerously_skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if tools_list:
        cmd.extend(["--allowedTools", ",".join(tools_list)])
    if max_turns is not None:
        cmd.extend(["--max-turns", str(max_turns)])

    probe_cmd: list[str] | None = None
    if probe:
        probe_value = probe.strip().lower()
        if probe_value == "version":
            probe_cmd = ["claude", "--version"]
        elif probe_value in {"help", "-h", "--help"}:
            probe_cmd = ["claude", "--help"]
        elif probe_value == "path":
            probe_cmd = ["/bin/sh", "-lc", "command -v claude && ls -l $(command -v claude)"]
        else:
            raise ValueError("probe must be one of: version, help, path")

    env = _claude_cli_env()
    _require_claude_cli_auth(env)

    cwd = str(job_root) if job_root is not None else str(_CLAUDE_CLI_APP_ROOT)
    _logger.info(
        "claude_cli.invoke",
        extra={
            "cmd": probe_cmd or cmd,
            "cwd": cwd,
            "path": env.get("PATH", ""),
            "home": env.get("HOME", ""),
            "user": env.get("USER", ""),
            "has_anthropic_api_key": bool(env.get("ANTHROPIC_API_KEY")),
            "output_format": output_format,
            "probe": probe_cmd is not None,
        },
    )

    try:
        result = subprocess.run(
            probe_cmd or cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            env=env,
            preexec_fn=_demote_to_claude(),
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        _logger.info(
            "claude_cli.complete",
            extra={
                "exit_code": result.returncode,
                "stdout_len": len(result.stdout or ""),
                "stderr_len": len(result.stderr or ""),
                "probe": probe_cmd is not None,
            },
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Claude CLI failed with exit code "
                f"{result.returncode}: {stderr or stdout or 'no output'}"
            )
        if probe_cmd is not None:
            payload = {
                "ok": True,
                "result": None,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
                "cmd": probe_cmd,
                "cwd": cwd,
                "path": env.get("PATH", ""),
                "home": env.get("HOME", ""),
                "user": env.get("USER", ""),
                "has_anthropic_api_key": bool(env.get("ANTHROPIC_API_KEY")),
                "probe": True,
            }
            _write_claude_cli_result(write_result_path, payload, job_root)
            if return_stdout:
                return json.dumps(payload)
            return payload

        parsed = stdout
        if output_format == "json":
            try:
                if stdout:
                    parsed = json.loads(stdout)
                elif stderr:
                    parsed = json.loads(stderr)
                else:
                    parsed = None
            except json.JSONDecodeError as exc:
                raise RuntimeError("Failed to parse Claude CLI JSON output") from exc
        payload = {
            "ok": True,
            "result": parsed,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
        if debug:
            payload.update(
                {
                    "cmd": probe_cmd or cmd,
                    "cwd": cwd,
                    "path": env.get("PATH", ""),
                    "home": env.get("HOME", ""),
                    "user": env.get("USER", ""),
                    "has_anthropic_api_key": bool(env.get("ANTHROPIC_API_KEY")),
                    "probe": probe_cmd is not None,
                }
            )
        _write_claude_cli_result(write_result_path, payload, job_root)
        if return_stdout:
            if stdout or stderr:
                return stdout or stderr
            return json.dumps(payload)
        return payload
    finally:
        _commit_persist_volume()


@app.function(image=agent_sdk_image, secrets=agent_sdk_secrets, timeout=600)
def load_test(num_queries: int = 10, question: str = DEFAULT_QUESTION) -> dict:
    """Run parallel queries to test scaling behavior.

    Spawns multiple agent queries in parallel using Modal's distributed execution
    and measures throughput. Useful for validating autoscaling configuration
    and measuring system performance under load.

    Usage:
        modal run -m agent_sandbox.app::load_test --num-queries 10
        modal run -m agent_sandbox.app::load_test --num-queries 100 --question "Hello"

    Args:
        num_queries: Number of parallel queries to spawn.
        question: Query to send to each agent instance.

    Returns:
        Dict with load test results:
            - total_queries: Number of queries executed
            - duration_seconds: Total time taken
            - throughput_per_second: Queries completed per second
    """
    start = time.time()

    # Spawn queries in parallel using Modal's distributed execution
    handles = [run_agent_remote.spawn(question) for _ in range(num_queries)]

    # Wait for all to complete
    for h in handles:
        h.get()

    duration = time.time() - start
    return {
        "total_queries": num_queries,
        "duration_seconds": round(duration, 2),
        "throughput_per_second": round(num_queries / duration, 3),
    }


@app.function(
    image=agent_sdk_image,
    volumes={"/data": _get_persist_volume()},
    timeout=60,
)
def read_job_artifact(job_id: str, artifact_path: str) -> str:
    """Read a job artifact from the persistent volume."""
    _reload_persist_volume()
    resolved = resolve_job_artifact(_settings.agent_fs_root, job_id, artifact_path)
    if resolved is None:
        raise FileNotFoundError(f"Artifact not found: {artifact_path}")
    return Path(resolved).read_text(encoding="utf-8")


@app.function(
    image=agent_sdk_image,
    secrets=agent_sdk_secrets,
    volumes={"/data": _get_persist_volume()},
    schedule=_job_queue_schedule(),
    **_function_runtime_kwargs(include_autoscale=False),
)
def process_job_queue() -> None:
    """Process queued agent jobs from JOB_QUEUE and persist results.

    Runs on a cron schedule (job_queue_cron setting) or can be invoked directly.
    Processes up to max_jobs_per_run jobs per invocation to bound runtime.

    Job Processing Flow:
        1. Pull job from JOB_QUEUE (2s timeout per poll)
        2. Check if job was canceled (skip if so)
        3. Defer job if schedule_at is in the future
        4. Increment attempt counter
        5. Set status to "running"
        6. Forward query to background sandbox service
        7. Update status to "complete" or "failed" with result/error
    """
    settings = Settings()
    jobs_processed = 0
    max_jobs = settings.max_jobs_per_run
    deferred_jobs: set[str] = set()

    while True:
        # Respect per-run job limit to bound execution time
        if max_jobs is not None and jobs_processed >= max_jobs:
            break
        # Non-blocking poll with 2s timeout - exit loop if queue empty
        job = JOB_QUEUE.get(timeout=2)
        if job is None:
            break
        job_id = job.get("job_id")
        question = job.get("question")
        if not job_id or not question:
            continue
        # Respect cancellation before processing
        if should_skip_job(job_id):
            update_job(job_id, {"status": "canceled"})
            continue
        # Respect scheduled execution time
        if not is_job_due(job_id):
            JOB_QUEUE.put(job)
            if job_id in deferred_jobs:
                break
            deferred_jobs.add(job_id)
            continue
        attempt = bump_attempts(job_id)
        started_at = int(time.time())
        record = get_job_record(job_id) or {}
        created_at = record.get("created_at")
        queue_latency_ms = None
        if created_at is not None:
            queue_latency_ms = max(0, (started_at - int(created_at)) * 1000)
        update_job(
            job_id,
            {
                "status": "running",
                "started_at": started_at,
                "queue_latency_ms": queue_latency_ms,
            },
        )
        try:
            sb, url = get_or_start_background_sandbox()
            update_job(job_id, {"sandbox_id": sb.object_id})
            _logger.info(
                "job.start",
                extra={"job_id": job_id, "attempt": attempt, "sandbox_id": sb.object_id},
            )
            headers = {}
            if settings.enforce_connect_token:
                creds = sb.create_connect_token(user_metadata={"job_id": job_id})
                headers = {"Authorization": f"Bearer {creds.token}"}
            r = httpx.post(
                f"{url.rstrip('/')}/query",
                json={"question": question, "job_id": job_id},
                headers=headers,
                timeout=httpx.Timeout(120.0, connect=30.0),
            )
            r.raise_for_status()
            result = r.json()
            _reload_persist_volume()
            manifest = _build_artifact_manifest(job_id)
            completed_at = int(time.time())
            duration_ms = max(0, (completed_at - started_at) * 1000)
            update_job(
                job_id,
                {
                    "status": "complete",
                    "result": result,
                    "artifacts": manifest.model_dump(),
                    "completed_at": completed_at,
                    "duration_ms": duration_ms,
                    **_extract_job_metrics(result),
                },
            )
            _logger.info(
                "job.complete",
                extra={
                    "job_id": job_id,
                    "attempt": attempt,
                    "duration_ms": duration_ms,
                    "sandbox_id": sb.object_id,
                },
            )
            _maybe_trigger_webhook(job_id, event="job.complete")
        except Exception as exc:
            completed_at = int(time.time())
            duration_ms = max(0, (completed_at - started_at) * 1000)
            update_job(
                job_id,
                {
                    "status": "failed",
                    "error": str(exc),
                    "completed_at": completed_at,
                    "duration_ms": duration_ms,
                },
            )
            _logger.info(
                "job.failed",
                extra={
                    "job_id": job_id,
                    "attempt": attempt,
                    "duration_ms": duration_ms,
                    "error": str(exc),
                },
            )
            _maybe_trigger_webhook(job_id, event="job.failed")
        jobs_processed += 1


@app.function(
    image=agent_sdk_image,
    secrets=agent_sdk_secrets,
    **_retry_kwargs(),
)
def deliver_webhook(job_id: str, event: str) -> None:
    """Deliver webhook notification with retry logic and signature regeneration.

    Sends HTTP POST request to webhook URL with signed payload containing job
    status and event information. Implements exponential backoff retry with
    fresh signature generation for each attempt. Tracks delivery status in job record.

    Args:
        job_id: UUID of the job that triggered the webhook
        event: Event type (typically "job.complete" or "job.failed")

    Retry Logic:
        - Max attempts: Configurable per webhook or global default (default: 3)
        - Exponential backoff between retries with formula:
          delay = initial * (coefficient ^ (attempt - 1))
        - Default backoff: 1s, 2s, 4s, 8s, 16s, ... (capped at 30s)
        - Continues from last recorded attempt (supports restarts)

    Signature Regeneration:
        **CRITICAL**: Headers are regenerated for each retry attempt with a fresh
        timestamp. This ensures:
        - Signatures remain valid even if retries span multiple minutes
        - Recipients can validate timestamp freshness (prevent replay attacks)
        - Each delivery attempt has its own unique signature

    Status Tracking:
        After each delivery attempt, updates job record with:
        - webhook.url: Endpoint URL
        - webhook.secret_ref: Reference to secret (if configured)
        - webhook.attempts: Current attempt number
        - webhook.last_status: HTTP status code from last attempt
        - webhook.last_error: Error message (truncated to 500 chars)
        - webhook.delivered_at: Unix timestamp (only on success)

    Success Criteria:
        HTTP status codes 200-299 are considered successful. Any 2xx response
        stops retries and marks webhook as delivered.

    Failure Handling:
        - Non-2xx responses: Records status code and response body (first 500 chars)
        - Exceptions (network errors, timeouts): Records exception message
        - After max attempts: Stops retrying, last error preserved in job record

    Example Workflow:
        ```python
        # Job completes
        update_job(job_id, {"status": "complete", "result": {...}})

        # Trigger webhook
        _maybe_trigger_webhook(job_id, "job.complete")

        # deliver_webhook.spawn(job_id, "job.complete") called
        # Attempt 1: POST https://example.com/webhook
        #   → 503 Service Unavailable
        #   → Wait 1s, regenerate signature
        # Attempt 2: POST with fresh signature
        #   → 503 Service Unavailable
        #   → Wait 2s, regenerate signature
        # Attempt 3: POST with fresh signature
        #   → 200 OK
        #   → Update job record with delivered_at
        #   → Return (success)
        ```

    Configuration:
        Per-webhook config (from WebhookConfig):
            - max_attempts: Override global default
            - timeout_seconds: HTTP timeout override

        Global defaults (from Settings):
            - webhook_default_max_attempts: 3
            - webhook_default_timeout: 10 seconds
            - webhook_signing_secret: Default signing secret
            - webhook_retry_initial_delay: 1.0 second
            - webhook_retry_backoff_coefficient: 2.0
            - webhook_retry_max_delay: 30.0 seconds

    Timeout Behavior:
        - Read timeout: Configured timeout value
        - Connect timeout: Min of configured timeout and 30s (prevents long hangs)
        - Example: timeout=60s → connect timeout = 30s, read timeout = 60s

    Modal Function Behavior:
        This function is decorated with Modal's retry policy (@app.function with
        **_retry_kwargs()). If the entire function fails (not just HTTP delivery),
        Modal will retry the whole function. The function tracks attempts internally
        to avoid duplication.

    Example Job Record After Delivery:
        {
            "job_id": "550e8400-...",
            "status": "complete",
            "result": {...},
            "webhook": {
                "url": "https://example.com/webhook",
                "secret_ref": "customer_webhook_secret",
                "attempts": 2,
                "last_status": 200,
                "delivered_at": 1704067890
            }
        }

    Example Job Record After Failed Delivery:
        {
            "job_id": "550e8400-...",
            "status": "complete",
            "result": {...},
            "webhook": {
                "url": "https://example.com/webhook",
                "attempts": 3,
                "last_status": 503,
                "last_error": "Service Temporarily Unavailable"
            }
        }

    See Also:
        - _maybe_trigger_webhook(): Spawns this function
        - _webhook_retry_delay(): Calculates backoff delay
        - build_webhook_payload(): Constructs event payload
        - build_headers(): Generates signed headers
        - WebhookConfig: Schema for webhook configuration
        - WebhookStatus: Schema for delivery status
    """
    settings = Settings()
    record = get_job_record(job_id)
    if not record:
        return
    config = record.get("webhook_config")
    if not config or not config.get("url"):
        return

    status = get_job_status(job_id)
    if not status:
        return

    payload = build_webhook_payload(event, status)
    serialized = serialize_payload(payload)

    max_attempts = int(config.get("max_attempts") or settings.webhook_default_max_attempts)
    timeout = float(config.get("timeout_seconds") or settings.webhook_default_timeout)

    webhook_status = record.get("webhook") or {}
    attempts_so_far = int(webhook_status.get("attempts", 0) or 0)
    if attempts_so_far >= max_attempts:
        return

    url = str(config.get("url"))
    for attempt in range(attempts_so_far + 1, max_attempts + 1):
        # Regenerate headers with fresh timestamp for each attempt to ensure valid signatures
        headers, _ = build_headers(
            config=config, payload=serialized, default_secret=settings.webhook_signing_secret
        )

        try:
            response = httpx.post(
                url,
                content=serialized,
                headers=headers,
                timeout=httpx.Timeout(timeout, connect=min(timeout, 30.0)),
            )
            if 200 <= response.status_code < 300:
                update_job(
                    job_id,
                    {
                        "webhook": {
                            "url": url,
                            "secret_ref": config.get("secret_ref"),
                            "attempts": attempt,
                            "last_status": response.status_code,
                            "delivered_at": int(time.time()),
                        }
                    },
                )
                return
            update_job(
                job_id,
                {
                    "webhook": {
                        "url": url,
                        "secret_ref": config.get("secret_ref"),
                        "attempts": attempt,
                        "last_status": response.status_code,
                        "last_error": response.text[:500],
                    }
                },
            )
        except Exception as exc:
            update_job(
                job_id,
                {
                    "webhook": {
                        "url": url,
                        "secret_ref": config.get("secret_ref"),
                        "attempts": attempt,
                        "last_error": str(exc),
                    }
                },
            )
        if attempt < max_attempts:
            time.sleep(_webhook_retry_delay(settings, attempt))


@app.function(
    image=agent_sdk_image,
    secrets=agent_sdk_secrets,
    **_retry_kwargs(),
)
def terminate_service_sandbox() -> dict:
    """Terminate the background sandbox to flush writes to the volume.

    Sandbox writes are synced when the sandbox terminates. If volume commits are enabled
    (via `volume_commit_interval`), writes may already be persisted without termination.
    Call this function after the agent has created files to ensure they are persisted.

    Returns:
        Dict with termination status
    """
    global SANDBOX
    try:
        sb, _ = get_or_start_background_sandbox()
        sb.terminate()
        SANDBOX = None  # Clear global so a new one will be created on next request
        return {"ok": True, "message": "Sandbox terminated, writes flushed to volume"}
    except modal_exc.NotFoundError as e:
        return {
            "ok": False,
            "error": "Sandbox not found",
            "detail": str(e),
            "type": "NotFoundError",
        }
    except modal_exc.SandboxTerminatedError:
        return {
            "ok": False,
            "error": "Sandbox already terminated",
            "type": "SandboxTerminatedError",
        }
    except modal_exc.TimeoutError as e:
        return {"ok": False, "error": "Sandbox termination timed out", "type": e.__class__.__name__}
    except modal_exc.Error as e:
        return {"ok": False, "error": str(e), "type": e.__class__.__name__}
    except Exception:
        logging.getLogger(__name__).exception("Unexpected error terminating sandbox")
        return {"ok": False, "error": "Unexpected error", "type": "UnexpectedException"}


@app.function(image=agent_sdk_image, secrets=agent_sdk_secrets, timeout=300, **_retry_kwargs())
def snapshot_service() -> dict:
    """Capture the sandbox filesystem as a reusable Modal Image.

    Creates a snapshot of the current sandbox filesystem state, which can be
    used to create new sandboxes with the same files/configuration. Useful for
    capturing agent-installed tools or downloaded artifacts.

    The snapshot metadata is persisted to SESSIONS for later retrieval.

    Returns:
        Dict with snapshot info: image_id (Modal Image ID), ts (timestamp),
        and base (sandbox name the snapshot was taken from).

    See: https://modal.com/docs/guide/sandbox#filesystem-snapshots
    """
    sb, _ = get_or_start_background_sandbox()
    img = sb.snapshot_filesystem()
    info = {"image_id": img.object_id, "ts": int(time.time()), "base": SANDBOX_NAME}
    try:
        SESSIONS[f"{SANDBOX_NAME}-snapshot"] = info
    except modal_exc.Error as e:
        logging.getLogger(__name__).warning(
            "Failed to persist snapshot metadata to Modal Dict: %s", e
        )
    except Exception:
        logging.getLogger(__name__).exception("Unexpected error persisting snapshot metadata")
    return info


# For 'modal run' command
@app.local_entrypoint()
def main():
    """Local entry point for `modal run -m agent_sandbox.app` during development.

    Spins up a short-lived sandbox, executes `agent_sandbox.agents.loop`, streams logs, and
    terminates the sandbox. Prefer `modal serve -m agent_sandbox.app` to keep endpoints and
    hot code reloading during development.
    """
    sb = modal.Sandbox.create(
        app=app,
        image=agent_sdk_image,
        secrets=agent_sdk_secrets,
        workdir="/root/app",
        timeout=60 * 10,  # 10 minutes
        **_sandbox_resource_kwargs(),
        verbose=True,
    )

    p = sb.exec("python", "-m", "agent_sandbox.agents.loop", timeout=60)

    print("=== STDOUT ===")
    for line in p.stdout:
        print(line, end="")
    print("\n=== STDERR ===")
    for line in p.stderr:
        print(line, end="")

    sb.terminate()
