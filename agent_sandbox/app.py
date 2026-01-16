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
import re
import time
import urllib.error
import urllib.request
import uuid
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
    # Multiplayer session functions
    authorize_session_user,
    bump_attempts,
    cancel_job,
    cancel_session,
    claim_cli_warm_sandbox,
    claim_prewarm,
    claim_warm_sandbox,
    cleanup_stale_cli_pool_entries,
    cleanup_stale_pool_entries,
    clear_session_queue,
    create_session_metadata,
    enqueue_job,
    generate_cli_pool_sandbox_name,
    generate_pool_sandbox_name,
    generate_warm_id,
    get_cancellation_status,
    get_cli_job_snapshot,
    get_cli_warm_pool_status,
    get_expired_cli_pool_entries,
    get_expired_pool_entries,
    get_job_record,
    get_job_status,
    get_multiplayer_status,
    get_prewarm,
    get_prewarm_status,
    get_prompt_queue_status,
    get_queue_size,
    # Ralph control functions
    get_ralph_checkpoint,
    get_ralph_control_status,
    get_ralph_iteration_snapshot,
    get_ralph_snapshot_status,
    get_session_cancellation,
    get_session_history,
    get_session_message_count,
    get_session_metadata,
    get_session_queue,
    get_session_snapshot,
    get_session_users,
    get_stats,
    get_warm_pool_status,
    is_job_due,
    is_session_executing,
    job_workspace_root,
    list_ralph_iteration_snapshots,
    mark_ralph_resumed,
    normalize_job_id,
    queue_prompt,
    register_cli_warm_sandbox,
    register_prewarm,
    register_warm_sandbox,
    remove_from_cli_pool,
    remove_from_pool,
    remove_queued_prompt,
    request_ralph_pause,
    resolve_job_artifact,
    revoke_session_user,
    should_skip_job,
    should_snapshot_cli_job,
    should_snapshot_session,
    store_cli_job_snapshot,
    store_session_snapshot,
    update_job,
    update_prewarm_ready,
)
from agent_sandbox.prompts.prompts import DEFAULT_QUESTION, SYSTEM_PROMPT
from agent_sandbox.ralph.feedback import validate_feedback_commands
from agent_sandbox.ralph.schemas import (
    RalphExecuteRequest,
    RalphIterationSnapshotEntry,
    RalphLoopResult,
    RalphPauseRequest,
    RalphPauseResponse,
    RalphResumeRequest,
    RalphResumeResponse,
    RalphRollbackRequest,
    RalphRollbackResponse,
    RalphSnapshotListResponse,
    RalphStartRequest,
    RalphStartResponse,
    RalphStatusResponse,
)
from agent_sandbox.ralph.status import read_status
from agent_sandbox.schemas import (
    ArtifactListResponse,
    ClaudeCliCancelResponse,
    ClaudeCliPollResponse,
    ClaudeCliRequest,
    ClaudeCliResponse,
    JobStatusResponse,
    JobSubmitRequest,
    JobSubmitResponse,
    # Multiplayer session schemas
    MessageHistoryEntry,
    MultiplayerStatusResponse,
    PromptQueueClearResponse,
    PromptQueueListResponse,
    PromptQueueStatusResponse,
    QueryBody,
    QueuedPromptEntry,
    QueuePromptRequest,
    QueuePromptResponse,
    SessionCancellationStatusResponse,
    SessionHistoryResponse,
    SessionMetadataResponse,
    SessionShareRequest,
    SessionShareResponse,
    SessionStopRequest,
    SessionStopResponse,
    SessionUnshareRequest,
    SessionUnshareResponse,
    SessionUsersResponse,
    WarmRequest,
    WarmResponse,
    WarmStatusResponse,
)
from agent_sandbox.schemas.jobs import ArtifactEntry, ArtifactManifest
from agent_sandbox.schemas.responses import ClaudeCliSubmitResponse
from agent_sandbox.services.webhooks import build_headers, build_webhook_payload, serialize_payload
from agent_sandbox.utils.cli import (
    CLAUDE_CLI_APP_ROOT,
    CLAUDE_CLI_USER,
    claude_cli_env,
    maybe_chown_for_claude,
    require_claude_cli_auth,
)

app = modal.App("test-sandbox")
_settings = Settings()
_logger = logging.getLogger(__name__)

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
        .apt_install("curl", "git")
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
                "AGENT_FS_ROOT": _settings.claude_cli_fs_root,
                "CLAUDE_CLI_FS_ROOT": _settings.claude_cli_fs_root,
                "PATH": (
                    "/root/.local/bin:/root/.claude/bin:"
                    "/home/claude/.local/bin:/home/claude/.claude/bin:"
                    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
                ),
            }
        )
        .workdir(str(CLAUDE_CLI_APP_ROOT))
        .add_local_dir(
            ".",
            remote_path=str(CLAUDE_CLI_APP_ROOT),
            copy=True,
            ignore=[".git", ".venv", "__pycache__", "*.pyc", ".DS_Store", "Makefile"],
        )
        .run_commands(
            f"chown -R {CLAUDE_CLI_USER}:{CLAUDE_CLI_USER} {CLAUDE_CLI_APP_ROOT}",
            f"cd {CLAUDE_CLI_APP_ROOT} && uv pip install -e . --system --no-cache",
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


def _function_call_id(call: object) -> str | None:
    """Return a stable call identifier for Modal function calls."""
    for attr in ("object_id", "call_id", "id"):
        value = getattr(call, attr, None)
        if value:
            return str(value)
    return None


def _write_claude_cli_result(
    write_result_path: str | None,
    payload: dict,
    job_root: Path | None,
    job_id: str | None = None,
) -> None:
    if not write_result_path:
        return
    path = Path(write_result_path)
    if not path.is_absolute():
        job_token = job_id or (job_root.name if job_root is not None else None)
        if job_token and path.parts[:2] == ("jobs", job_token):
            path = Path(_settings.claude_cli_fs_root) / path
        else:
            base = job_root if job_root is not None else Path(_settings.claude_cli_fs_root)
            path = base / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    maybe_chown_for_claude(path)


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


def _cli_sandbox_resource_kwargs() -> dict[str, object]:
    """Build resource kwargs for Claude CLI sandboxes."""
    kwargs: dict[str, object] = {}
    if _settings.claude_cli_sandbox_cpu_limit is not None:
        kwargs["cpu"] = (
            _settings.claude_cli_sandbox_cpu,
            _settings.claude_cli_sandbox_cpu_limit,
        )
    else:
        kwargs["cpu"] = _settings.claude_cli_sandbox_cpu

    if _settings.claude_cli_sandbox_memory_limit is not None:
        kwargs["memory"] = (
            _settings.claude_cli_sandbox_memory,
            _settings.claude_cli_sandbox_memory_limit,
        )
    else:
        kwargs["memory"] = _settings.claude_cli_sandbox_memory

    ephemeral_disk = _validated_cli_ephemeral_disk()
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


def _validated_cli_ephemeral_disk() -> int | None:
    """Validate Claude CLI ephemeral disk size against Modal limits."""
    if _settings.claude_cli_sandbox_ephemeral_disk is None:
        return None
    max_mib = 3145728
    if _settings.claude_cli_sandbox_ephemeral_disk <= 0:
        logging.getLogger(__name__).warning(
            "claude_cli_sandbox_ephemeral_disk=%s MiB must be positive; skipping",
            _settings.claude_cli_sandbox_ephemeral_disk,
        )
        return None
    if _settings.claude_cli_sandbox_ephemeral_disk > max_mib:
        logging.getLogger(__name__).warning(
            "claude_cli_sandbox_ephemeral_disk=%s MiB exceeds Modal maximum %s; skipping",
            _settings.claude_cli_sandbox_ephemeral_disk,
            max_mib,
        )
        return None
    return _settings.claude_cli_sandbox_ephemeral_disk


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


def _cli_sandbox_timeout(timeout_seconds: int | None) -> int:
    """Return a sandbox timeout with a small buffer over CLI execution."""
    if timeout_seconds is None:
        return _settings.claude_cli_sandbox_timeout
    return min(_settings.claude_cli_sandbox_timeout, timeout_seconds + 300)


def _build_cli_sandbox_name(suffix: str | None) -> str:
    base = _settings.claude_cli_sandbox_name
    token = suffix or uuid.uuid4().hex[:8]
    name = f"{base}-{token}"
    return name[:63]


def _create_claude_cli_sandbox(
    *,
    job_id: str | None = None,
    timeout_seconds: int | None = None,
) -> modal.Sandbox:
    """Create a dedicated sandbox for Claude CLI execution."""
    kwargs: dict[str, object] = {
        "app": app,
        "image": claude_cli_image,
        "secrets": agent_sdk_secrets,
        "workdir": str(CLAUDE_CLI_APP_ROOT),
        "volumes": {_settings.claude_cli_fs_root: _get_claude_cli_volume()},
        "timeout": _cli_sandbox_timeout(timeout_seconds),
        "idle_timeout": _settings.claude_cli_sandbox_idle_timeout,
        "verbose": True,
    }
    kwargs.update(_cli_sandbox_resource_kwargs())

    name = _build_cli_sandbox_name(job_id) if _settings.claude_cli_sandbox_name else None
    if name:
        kwargs["name"] = name

    try:
        sb = modal.Sandbox.create(**kwargs)
    except modal_exc.AlreadyExistsError:
        if "name" not in kwargs:
            raise
        kwargs["name"] = _build_cli_sandbox_name(uuid.uuid4().hex[:12])
        sb = modal.Sandbox.create(**kwargs)

    try:
        tags = {"role": "claude-cli", "app": "test-sandbox"}
        if job_id:
            tags["job_id"] = job_id
        sb.set_tags(tags)
    except Exception:
        _logger.debug("Failed to set Claude CLI sandbox tags", exc_info=True)
    return sb


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


def _get_claude_cli_volume() -> modal.Volume:
    """Return the configured persistent volume handle for Claude CLI."""
    kwargs: dict[str, object] = {"create_if_missing": True}
    if _settings.persist_vol_version is not None:
        kwargs["version"] = _settings.persist_vol_version
    return modal.Volume.from_name(_settings.claude_cli_persist_vol_name, **kwargs)


def _reload_persist_volume(max_retries: int = 3) -> None:
    """Reload the persistent volume to see latest committed writes.

    Modal volumes appear empty during an active reload operation. This function
    retries with exponential backoff to handle transient reload failures,
    especially for larger volumes.

    Args:
        max_retries: Maximum number of retry attempts (default: 3)
    """
    logger = logging.getLogger(__name__)
    for attempt in range(max_retries):
        try:
            _get_persist_volume().reload()
            return
        except Exception:
            if attempt == max_retries - 1:
                logger.warning(
                    "Failed to reload persistent volume after %d attempts",
                    max_retries,
                    exc_info=True,
                )
                return
            # Exponential backoff: 0.5s, 1.0s, 1.5s, ...
            time.sleep(0.5 * (attempt + 1))


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


def _reload_claude_cli_volume(max_retries: int = 3) -> None:
    """Reload the Claude CLI volume to see latest committed writes."""
    logger = logging.getLogger(__name__)
    for attempt in range(max_retries):
        try:
            _get_claude_cli_volume().reload()
            return
        except Exception:
            if attempt == max_retries - 1:
                logger.warning(
                    "Failed to reload Claude CLI volume after %d attempts",
                    max_retries,
                    exc_info=True,
                )
                return
            time.sleep(0.5 * (attempt + 1))


def _commit_claude_cli_volume() -> None:
    """Commit pending writes to the Claude CLI volume."""
    try:
        _get_claude_cli_volume().commit()
    except RuntimeError as exc:
        message = str(exc)
        if "commit() can only be called" in message:
            return
        logging.getLogger(__name__).warning("Failed to commit Claude CLI volume: %s", message)
    except Exception:
        logging.getLogger(__name__).warning("Failed to commit Claude CLI volume", exc_info=True)


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


def _http_app_volumes() -> dict[str, modal.Volume]:
    """Mount agent and CLI volumes for HTTP endpoints that access artifacts."""
    volumes: dict[str, modal.Volume] = {_settings.agent_fs_root: _get_persist_volume()}
    if _settings.claude_cli_fs_root != _settings.agent_fs_root:
        volumes[_settings.claude_cli_fs_root] = _get_claude_cli_volume()
    return volumes


@app.function(
    image=agent_sdk_image,
    secrets=agent_sdk_secrets,
    volumes=_http_app_volumes(),
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
    settings = Settings()

    # Resolve session_id for snapshot restoration (if resuming a session)
    resolved_session_id = body.session_id
    # Note: We could also look up session_key -> session_id here if needed

    # Check for pre-warmed sandbox (from POST /warm)
    prewarm_claimed = None
    if body.warm_id and settings.enable_prewarm:
        prewarm_claimed = claim_prewarm(body.warm_id, resolved_session_id or "anonymous")
        if prewarm_claimed:
            _logger.info(
                "Query using pre-warmed sandbox",
                extra={
                    "warm_id": body.warm_id,
                    "sandbox_id": prewarm_claimed.get("sandbox_id"),
                    "prewarm_status": prewarm_claimed.get("status"),
                },
            )

    # Use async getter with session_id for potential snapshot restoration
    # If pre-warm was claimed, the sandbox should already be ready in globals
    sb, url = await get_or_start_background_sandbox_aio(session_id=resolved_session_id)

    # Optional: per-request connect token (verified in sandbox service)
    headers = {}
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
        result = r.json()

    # Trigger session snapshot asynchronously after successful query
    # This captures filesystem state for session restoration on resume
    if settings.enable_session_snapshots:
        result_session_id = result.get("session_id")
        if result_session_id and should_snapshot_session(
            result_session_id, settings.snapshot_min_interval_seconds
        ):
            try:
                # Fire-and-forget: spawn snapshot in background
                snapshot_session_state.spawn(result_session_id)
                _logger.debug(
                    "Spawned session snapshot",
                    extra={"session_id": result_session_id},
                )
            except Exception:
                _logger.warning(
                    "Failed to spawn session snapshot",
                    exc_info=True,
                    extra={"session_id": result_session_id},
                )

    return result


@web_app.post("/query_stream")
async def query_stream(request: Request, body: QueryBody):
    """Stream query responses from the background sandbox service."""
    settings = Settings()

    # Resolve session_id for snapshot restoration (if resuming a session)
    resolved_session_id = body.session_id

    # Check for pre-warmed sandbox (from POST /warm)
    prewarm_claimed = None
    if body.warm_id and settings.enable_prewarm:
        prewarm_claimed = claim_prewarm(body.warm_id, resolved_session_id or "anonymous")
        if prewarm_claimed:
            _logger.info(
                "Query stream using pre-warmed sandbox",
                extra={
                    "warm_id": body.warm_id,
                    "sandbox_id": prewarm_claimed.get("sandbox_id"),
                    "prewarm_status": prewarm_claimed.get("status"),
                },
            )

    # Use async getter with session_id for potential snapshot restoration
    # If pre-warm was claimed, the sandbox should already be ready in globals
    sb, url = await get_or_start_background_sandbox_aio(session_id=resolved_session_id)

    headers = {}
    if settings.enforce_connect_token:
        creds = await sb.create_connect_token.aio(
            user_metadata={"ip": request.client.host or "unknown"}
        )
        headers = {"Authorization": f"Bearer {creds.token}"}

    # Track session_id from stream for post-completion snapshot
    captured_session_id: str | None = None

    async def sse_proxy():
        nonlocal captured_session_id
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", f"{url.rstrip('/')}/query_stream", json=body.model_dump(), headers=headers
            ) as response:
                response.raise_for_status()
                buffer = ""
                async for chunk in response.aiter_bytes():
                    yield chunk
                    # Parse SSE to capture session_id from "done" event
                    # Format: "event: done\ndata: {...}\n\n"
                    try:
                        buffer += chunk.decode("utf-8", errors="ignore")
                        if "event: done" in buffer:
                            # Extract session_id from the done event data
                            for line in buffer.split("\n"):
                                if line.startswith("data:") and "session_id" in line:
                                    data_str = line[5:].strip()
                                    data = json.loads(data_str)
                                    captured_session_id = data.get("session_id")
                                    break
                    except Exception:
                        pass  # Best effort parsing, don't fail stream

        # Trigger snapshot after stream completes
        if settings.enable_session_snapshots and captured_session_id:
            if should_snapshot_session(captured_session_id, settings.snapshot_min_interval_seconds):
                try:
                    snapshot_session_state.spawn(captured_session_id)
                    _logger.debug(
                        "Spawned session snapshot after stream",
                        extra={"session_id": captured_session_id},
                    )
                except Exception:
                    _logger.warning(
                        "Failed to spawn session snapshot after stream",
                        exc_info=True,
                        extra={"session_id": captured_session_id},
                    )

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
            warm_id=body.warm_id,
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
            warm_id=body.warm_id,
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


@web_app.delete("/claude_cli/{call_id}", response_model=ClaudeCliCancelResponse)
async def claude_cli_cancel(call_id: str):
    """Cancel a running Claude CLI invocation.

    This endpoint attempts to cancel a running function call. If the call
    has already completed, it returns a status indicating the call is
    already finished.
    """
    try:
        call = modal.FunctionCall.from_id(call_id)
    except Exception as exc:
        _logger.warning("Cancel request for unknown call_id: %s", call_id, exc_info=exc)
        return JSONResponse(
            status_code=404,
            content=ClaudeCliCancelResponse(
                ok=False,
                status="not_found",
                message="Unknown call id",
            ).model_dump(),
        )

    try:
        # Check if already complete before cancelling
        _result = call.get(timeout=0)  # noqa: F841
        # If we get here, the call already completed
        return ClaudeCliCancelResponse(
            ok=True,
            status="already_completed",
            message="Call has already completed",
        )
    except modal_exc.TimeoutError:
        # Call is still running, proceed to cancel
        pass
    except modal_exc.OutputExpiredError:
        return ClaudeCliCancelResponse(
            ok=True,
            status="already_completed",
            message="Call result has expired",
        )
    except Exception:
        # Some other error, try to cancel anyway
        pass

    try:
        await anyio.to_thread.run_sync(call.cancel)
        return ClaudeCliCancelResponse(
            ok=True,
            status="cancelled",
            message="Call cancellation requested",
        )
    except Exception as exc:
        _logger.warning("Failed to cancel call %s: %s", call_id, exc)
        return JSONResponse(
            status_code=500,
            content=ClaudeCliCancelResponse(
                ok=False,
                status="not_found",
                message=f"Failed to cancel: {exc}",
            ).model_dump(),
        )


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


# =============================================================================
# RALPH WIGGUM ENDPOINTS
# =============================================================================


@web_app.post("/ralph/start", response_model=RalphStartResponse)
async def start_ralph(body: RalphStartRequest) -> RalphStartResponse:
    """Start a Ralph autonomous coding loop (async).

    Starts a long-running Modal function that works through the provided PRD
    until all tasks are complete or max iterations is reached.

    Returns a job_id and call_id that can be used to poll for status.
    """
    # Validate feedback commands early to fail fast
    if body.feedback_commands:
        validate_feedback_commands(body.feedback_commands)

    import uuid

    job_id = str(uuid.uuid4())

    call = run_ralph_remote.spawn(
        job_id=job_id,
        prd_json=body.prd.model_dump_json(),
        workspace_source_json=body.workspace_source.model_dump_json(),
        prompt_template=body.prompt_template,
        max_iterations=body.max_iterations,
        timeout_per_iteration=body.timeout_per_iteration,
        allowed_tools=",".join(body.allowed_tools),
        feedback_commands=",".join(body.feedback_commands),
        feedback_timeout=body.feedback_timeout,
        auto_commit=body.auto_commit,
        max_consecutive_failures=body.max_consecutive_failures,
    )

    call_id = _function_call_id(call)
    if not call_id:
        raise HTTPException(status_code=500, detail="Unable to determine call id")

    return RalphStartResponse(job_id=job_id, call_id=call_id)


@web_app.get("/ralph/{job_id}", response_model=RalphStatusResponse)
async def get_ralph_status(job_id: str, call_id: str) -> RalphStatusResponse:
    """Get Ralph loop status by job_id.

    Polls the running Ralph loop for current status. Returns live progress
    from the workspace status.json file if available, otherwise checks the
    Modal function call status.

    Args:
        job_id: The Ralph job ID returned from /ralph/start
        call_id: The Modal call ID returned from /ralph/start
    """
    job_id = _normalize_job_id_or_400(job_id)

    # Try to read live status from workspace first
    workspace = Path(_settings.claude_cli_fs_root) / "jobs" / job_id
    _reload_claude_cli_volume()
    live_status = read_status(workspace)

    if live_status and live_status.get("status") == "running":
        return RalphStatusResponse(
            job_id=job_id,
            status=live_status["status"],
            current_iteration=live_status["current_iteration"],
            max_iterations=live_status["max_iterations"],
            tasks_completed=live_status["tasks_completed"],
            tasks_total=live_status["tasks_total"],
            current_task=live_status.get("current_task"),
            result=None,
        )

    # Check Modal call status
    try:
        call = modal.FunctionCall.from_id(call_id)
        try:
            result = call.get(timeout=0)
            return RalphStatusResponse(
                job_id=job_id,
                status=result["status"],
                current_iteration=result["iterations_completed"],
                max_iterations=result["iterations_max"],
                tasks_completed=result["tasks_completed"],
                tasks_total=result["tasks_total"],
                current_task=None,
                result=RalphLoopResult(**result),
            )
        except modal_exc.TimeoutError:
            # Still running but no status file yet
            return RalphStatusResponse(
                job_id=job_id,
                status="running",
                current_iteration=0,
                max_iterations=0,
                tasks_completed=0,
                tasks_total=0,
                current_task=None,
                result=None,
            )
        except modal_exc.OutputExpiredError:
            return JSONResponse(
                status_code=410,
                content={"job_id": job_id, "status": "expired", "error": "Result expired"},
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# =============================================================================
# RALPH CONTROL ENDPOINTS (PAUSE/RESUME/SNAPSHOTS)
# =============================================================================


@web_app.post("/ralph/{job_id}/pause", response_model=RalphPauseResponse)
async def pause_ralph(job_id: str, body: RalphPauseRequest) -> RalphPauseResponse:
    """Request a Ralph loop to pause at the next safe point.

    The pause won't take effect immediately - the loop will complete its
    current iteration and then pause before starting the next one.

    Args:
        job_id: The Ralph job ID to pause.

    Returns:
        RalphPauseResponse with pause request status.
    """
    job_id = _normalize_job_id_or_400(job_id)

    if not _settings.enable_ralph_control:
        return RalphPauseResponse(
            ok=False,
            job_id=job_id,
            status="disabled",
            message="Ralph control is not enabled",
        )

    result = request_ralph_pause(
        job_id=job_id,
        requested_by=body.requested_by,
        reason=body.reason,
    )

    return RalphPauseResponse(
        ok=True,
        job_id=job_id,
        status=result.get("status", "pause_requested"),
        paused_at=result.get("paused_at"),
        reason=body.reason,
        message=result.get("message"),
    )


@web_app.post("/ralph/{job_id}/resume", response_model=RalphResumeResponse)
async def resume_ralph(job_id: str, body: RalphResumeRequest) -> RalphResumeResponse:
    """Resume a paused Ralph loop from its checkpoint.

    Spawns a new Modal function call that continues from the saved checkpoint.

    Args:
        job_id: The Ralph job ID to resume.

    Returns:
        RalphResumeResponse with new call_id for polling.
    """
    import json as json_module

    job_id = _normalize_job_id_or_400(job_id)

    if not _settings.enable_ralph_control:
        return RalphResumeResponse(
            ok=False,
            job_id=job_id,
            status="disabled",
            message="Ralph control is not enabled",
        )

    # Get checkpoint
    checkpoint = get_ralph_checkpoint(job_id)
    if not checkpoint:
        return RalphResumeResponse(
            ok=False,
            job_id=job_id,
            status="not_paused",
            message="Ralph loop is not paused or checkpoint expired",
        )

    # Mark as resumed
    mark_ralph_resumed(job_id, requested_by=body.requested_by)

    # Spawn resumed loop with checkpoint data
    # The checkpoint contains prd_json (serialized PRD state)
    prd_json = checkpoint.get("prd_json", "{}")

    # Build workspace_source_json - empty source since workspace already exists
    workspace_source_json = json_module.dumps({"type": "empty"})

    call = run_ralph_remote.spawn(
        job_id=job_id,
        prd_json=prd_json,
        workspace_source_json=workspace_source_json,
        max_iterations=checkpoint.get("max_iterations", 10),
        resume_checkpoint_json=json_module.dumps(checkpoint),
    )

    call_id = _function_call_id(call)
    if not call_id:
        raise HTTPException(status_code=500, detail="Unable to determine call id")

    return RalphResumeResponse(
        ok=True,
        job_id=job_id,
        status="resumed",
        call_id=call_id,
        message="Ralph loop resumed from checkpoint",
    )


@web_app.get("/ralph/{job_id}/control")
async def get_ralph_control(job_id: str):
    """Get the current control status for a Ralph job.

    Args:
        job_id: The Ralph job ID to check.

    Returns:
        Control status including pause state and checkpoint info.
    """
    job_id = _normalize_job_id_or_400(job_id)

    status = get_ralph_control_status(job_id)
    if not status:
        return {"ok": True, "job_id": job_id, "status": "running", "paused": False}

    return {
        "ok": True,
        "job_id": job_id,
        "status": status.get("status"),
        "paused": status.get("status") in ("pause_requested", "paused"),
        "pause_requested_at": status.get("pause_requested_at"),
        "paused_at": status.get("paused_at"),
        "resumed_at": status.get("resumed_at"),
        "reason": status.get("reason"),
        "has_checkpoint": status.get("checkpoint") is not None,
    }


@web_app.get("/ralph/{job_id}/snapshots", response_model=RalphSnapshotListResponse)
async def list_ralph_snapshots(job_id: str) -> RalphSnapshotListResponse:
    """List all available iteration snapshots for a Ralph job.

    Args:
        job_id: The Ralph job ID.

    Returns:
        List of iteration snapshots available for rollback.
    """
    job_id = _normalize_job_id_or_400(job_id)

    if not _settings.enable_ralph_iteration_snapshots:
        return RalphSnapshotListResponse(
            ok=False,
            job_id=job_id,
            snapshots=[],
            total=0,
        )

    snapshots = list_ralph_iteration_snapshots(job_id)
    entries = [RalphIterationSnapshotEntry(**s) for s in snapshots]

    return RalphSnapshotListResponse(
        ok=True,
        job_id=job_id,
        snapshots=entries,
        total=len(entries),
    )


@web_app.post("/ralph/{job_id}/rollback/{iteration}", response_model=RalphRollbackResponse)
async def rollback_ralph(
    job_id: str, iteration: int, body: RalphRollbackRequest
) -> RalphRollbackResponse:
    """Rollback a Ralph job to a previous iteration's state.

    This restores the filesystem state from the specified iteration's snapshot
    and starts a new Ralph loop from that point.

    Args:
        job_id: The Ralph job ID.
        iteration: The iteration number to rollback to.

    Returns:
        RalphRollbackResponse with rollback status.
    """
    job_id = _normalize_job_id_or_400(job_id)

    if not _settings.enable_ralph_iteration_snapshots:
        return RalphRollbackResponse(
            ok=False,
            job_id=job_id,
            iteration=iteration,
            status="disabled",
            message="Ralph iteration snapshots are not enabled",
        )

    # Get the snapshot
    snapshot = get_ralph_iteration_snapshot(job_id, iteration)
    if not snapshot:
        return RalphRollbackResponse(
            ok=False,
            job_id=job_id,
            iteration=iteration,
            status="snapshot_not_found",
            message=f"No snapshot found for iteration {iteration}",
        )

    # Note: Actual rollback requires creating a new sandbox from the snapshot image
    # This is a complex operation that requires the CLI sandbox to be recreated
    # For now, we return the snapshot info and let the caller handle the rollback

    return RalphRollbackResponse(
        ok=True,
        job_id=job_id,
        iteration=iteration,
        status="snapshot_available",
        message=f"Snapshot available for iteration {iteration}. "
        f"Image ID: {snapshot.get('image_id')}. "
        "Use this to restore the sandbox state.",
    )


@web_app.get("/ralph/snapshots/status")
async def ralph_snapshot_status():
    """Get overall status of Ralph iteration snapshots.

    Returns statistics about snapshot storage and configuration.
    """
    return get_ralph_snapshot_status()


@web_app.get("/service_info")
async def service_info():
    """Get information about the background sandbox service."""
    sb, url = await get_or_start_background_sandbox_aio()
    return {"url": url, "sandbox_id": sb.object_id}


@web_app.get("/stats")
async def stats_endpoint(
    period_hours: int = 24,
    include_time_series: bool = False,
):
    """Get aggregated statistics for agent sessions.

    Provides visibility into agent effectiveness and usage patterns across
    both Agent SDK and CLI sandboxes.

    Query Parameters:
        period_hours: Hours to include (default 24, max 720)
        include_time_series: Include hourly breakdown (default false)

    Returns:
        StatsResponse with aggregate statistics by sandbox type.

    Example:
        ```
        curl 'https://<org>--test-sandbox-http-app-dev.modal.run/stats?period_hours=48'
        ```
    """
    period_hours = min(max(period_hours, 1), 720)  # Clamp to valid range
    return get_stats(period_hours=period_hours, include_time_series=include_time_series)


@web_app.get("/pool/status")
async def pool_status_endpoint():
    """Get current status of the warm sandbox pool.

    Returns pool statistics including:
    - enabled: Whether warm pool is enabled
    - target_size: Configured pool size
    - warm: Number of available warm sandboxes
    - claimed: Number of currently claimed sandboxes
    - total: Total sandboxes in pool
    - entries: List of pool entries with metadata

    Example:
        ```
        curl 'https://<org>--test-sandbox-http-app-dev.modal.run/pool/status'
        ```
    """
    if not _settings.enable_warm_pool:
        return {
            "ok": True,
            "enabled": False,
            "message": "Warm pool is disabled",
        }

    pool_status = get_warm_pool_status()
    return {
        "ok": True,
        "enabled": True,
        "target_size": _settings.warm_pool_size,
        "refresh_interval_seconds": _settings.warm_pool_refresh_interval,
        "sandbox_max_age_seconds": _settings.warm_pool_sandbox_max_age,
        **pool_status,
    }


@web_app.get("/cli/pool/status")
async def cli_pool_status_endpoint():
    """Get current status of the CLI warm sandbox pool.

    Returns pool statistics including:
    - enabled: Whether CLI warm pool is enabled
    - target_size: Configured pool size
    - warm: Number of available warm CLI sandboxes
    - claimed: Number of currently claimed sandboxes
    - total: Total sandboxes in pool
    - entries: List of pool entries with metadata

    Example:
        ```
        curl 'https://<org>--test-sandbox-http-app-dev.modal.run/cli/pool/status'
        ```
    """
    if not _settings.enable_cli_warm_pool:
        return {
            "ok": True,
            "enabled": False,
            "message": "CLI warm pool is disabled",
        }

    pool_status = get_cli_warm_pool_status()
    return {
        "ok": True,
        "enabled": True,
        "target_size": _settings.cli_warm_pool_size,
        "refresh_interval_seconds": _settings.cli_warm_pool_refresh_interval,
        "sandbox_max_age_seconds": _settings.cli_warm_pool_sandbox_max_age,
        **pool_status,
    }


# =============================================================================
# Pre-warm API Endpoints
# =============================================================================
# These endpoints support speculative sandbox pre-warming for reduced latency.
# Clients call POST /warm when users start typing to begin sandbox preparation
# before the actual query arrives. The returned warm_id is passed with the
# subsequent query for correlation.
# =============================================================================


@web_app.post("/warm", response_model=WarmResponse)
async def prewarm_sandbox(body: WarmRequest) -> WarmResponse:
    """Pre-warm a sandbox for reduced latency on subsequent queries.

    Call this endpoint when users start typing to speculatively prepare
    a sandbox before the actual query arrives. Returns a warm_id that
    should be passed with the subsequent /query or /claude_cli request.

    Args:
        body: Pre-warm request with sandbox_type and optional session/job IDs.

    Returns:
        WarmResponse with warm_id for correlation.

    Example:
        ```bash
        # Client calls when user focuses on input
        curl -X POST 'https://<org>--test-sandbox-http-app.modal.run/warm' \\
          -H 'Content-Type: application/json' \\
          -d '{"sandbox_type": "agent_sdk", "session_id": "sess_123"}'

        # Response: {"warm_id": "abc-123", "status": "warming", ...}

        # Then pass warm_id with the actual query
        curl -X POST 'https://<org>--test-sandbox-http-app.modal.run/query' \\
          -H 'Content-Type: application/json' \\
          -d '{"question": "...", "warm_id": "abc-123", "session_id": "sess_123"}'
        ```
    """
    if not _settings.enable_prewarm:
        return WarmResponse(
            warm_id="",
            status="error",
            sandbox_type=body.sandbox_type,
            expires_at=0,
            message="Pre-warm API is disabled",
        )

    # Generate correlation ID
    warm_id = generate_warm_id()

    # Register the pre-warm request
    entry = register_prewarm(
        warm_id=warm_id,
        sandbox_type=body.sandbox_type,
        session_id=body.session_id,
        job_id=body.job_id,
    )

    # Spawn background task to warm the sandbox
    # This runs async and updates the pre-warm entry when ready
    if body.sandbox_type == "agent_sdk":
        prewarm_agent_sdk_sandbox.spawn(warm_id, body.session_id)
    else:
        prewarm_cli_sandbox.spawn(warm_id, body.job_id)

    _logger.info(
        "Pre-warm request registered",
        extra={
            "warm_id": warm_id,
            "sandbox_type": body.sandbox_type,
            "session_id": body.session_id,
            "job_id": body.job_id,
        },
    )

    return WarmResponse(
        warm_id=warm_id,
        status="warming",
        sandbox_type=body.sandbox_type,
        expires_at=entry["expires_at"],
        message="Sandbox warming started",
    )


@web_app.get("/warm/{warm_id}")
async def get_prewarm_status_by_id(warm_id: str):
    """Get status of a specific pre-warm request.

    Args:
        warm_id: The pre-warm correlation ID from POST /warm.

    Returns:
        Pre-warm entry details or 404 if not found/expired.

    Example:
        ```bash
        curl 'https://<org>--test-sandbox-http-app.modal.run/warm/abc-123'
        ```
    """
    entry = get_prewarm(warm_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Pre-warm not found or expired")
    return {"ok": True, **entry}


@web_app.get("/warm/status", response_model=WarmStatusResponse)
async def prewarm_status_endpoint() -> WarmStatusResponse:
    """Get current status of the pre-warm store.

    Returns statistics about pre-warm requests including counts of
    warming, ready, claimed, and expired entries.

    Example:
        ```bash
        curl 'https://<org>--test-sandbox-http-app.modal.run/warm/status'
        ```
    """
    if not _settings.enable_prewarm:
        return WarmStatusResponse(
            enabled=False,
            total=0,
            warming=0,
            ready=0,
            claimed=0,
            expired=0,
            timeout_seconds=_settings.prewarm_timeout_seconds,
        )

    status = get_prewarm_status()
    return WarmStatusResponse(
        enabled=True,
        total=status["total"],
        warming=status["warming"],
        ready=status["ready"],
        claimed=status["claimed"],
        expired=status["expired"],
        timeout_seconds=_settings.prewarm_timeout_seconds,
    )


# =============================================================================
# Session Stop/Cancel Endpoints
# =============================================================================
# These endpoints allow graceful termination of agent sessions mid-execution.
# When a session is stopped, the cancellation flag is checked by the agent's
# can_use_tool handler before each tool call, causing the agent to stop.
# =============================================================================


@web_app.post("/session/{session_id}/stop", response_model=SessionStopResponse)
async def stop_session(
    session_id: str,
    body: SessionStopRequest | None = None,
) -> SessionStopResponse:
    """Stop an agent session mid-execution.

    Requests graceful termination of an active agent session. The agent will
    finish its current tool call, then be denied further tool calls, causing
    it to stop execution and return a summary.

    This is a "soft" stop - it doesn't forcibly terminate the sandbox, but
    signals to the agent that it should stop working.

    Args:
        session_id: The Claude Agent SDK session ID to stop.
        body: Optional request body with reason and requester info.

    Returns:
        SessionStopResponse with cancellation details and status.

    Example:
        ```bash
        # Basic stop
        curl -X POST 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/stop'

        # With reason
        curl -X POST 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/stop' \\
          -H 'Content-Type: application/json' \\
          -d '{"reason": "User requested stop", "requested_by": "user_123"}'
        ```
    """
    if not _settings.enable_session_cancellation:
        return SessionStopResponse(
            ok=False,
            session_id=session_id,
            status="disabled",
            message="Session cancellation is disabled in settings",
        )

    # Check if session is already cancelled
    existing = get_session_cancellation(session_id)
    if existing:
        return SessionStopResponse(
            ok=True,
            session_id=session_id,
            status=existing.get("status", "requested"),
            requested_at=existing.get("requested_at"),
            expires_at=existing.get("expires_at"),
            reason=existing.get("reason"),
            requested_by=existing.get("requested_by"),
            message="Session stop already requested",
        )

    # Request cancellation
    reason = body.reason if body else None
    requested_by = body.requested_by if body else None
    entry = cancel_session(
        session_id=session_id,
        requested_by=requested_by,
        reason=reason,
    )

    return SessionStopResponse(
        ok=True,
        session_id=session_id,
        status=entry["status"],
        requested_at=entry["requested_at"],
        expires_at=entry["expires_at"],
        reason=entry.get("reason"),
        requested_by=entry.get("requested_by"),
        message="Session stop requested. Agent will stop after current tool call.",
    )


@web_app.get("/session/{session_id}/stop", response_model=SessionStopResponse)
async def get_session_stop_status(session_id: str) -> SessionStopResponse:
    """Check the cancellation status for a session.

    Returns the current cancellation status if one exists, or indicates
    that the session has no active cancellation.

    Args:
        session_id: The Claude Agent SDK session ID to check.

    Returns:
        SessionStopResponse with current cancellation status.

    Example:
        ```bash
        curl 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/stop'
        ```
    """
    if not _settings.enable_session_cancellation:
        return SessionStopResponse(
            ok=False,
            session_id=session_id,
            status="disabled",
            message="Session cancellation is disabled in settings",
        )

    entry = get_session_cancellation(session_id)
    if not entry:
        return SessionStopResponse(
            ok=True,
            session_id=session_id,
            status="not_found",
            message="No active cancellation for this session",
        )

    return SessionStopResponse(
        ok=True,
        session_id=session_id,
        status=entry.get("status", "requested"),
        requested_at=entry.get("requested_at"),
        expires_at=entry.get("expires_at"),
        reason=entry.get("reason"),
        requested_by=entry.get("requested_by"),
        message=None,
    )


@web_app.get("/session/cancellations/status", response_model=SessionCancellationStatusResponse)
async def get_cancellation_status_endpoint() -> SessionCancellationStatusResponse:
    """Get current status of session cancellations across all sessions.

    Returns statistics about cancellation requests including counts of
    requested, acknowledged, and expired cancellations.

    Example:
        ```bash
        curl 'https://<org>--test-sandbox-http-app.modal.run/session/cancellations/status'
        ```
    """
    if not _settings.enable_session_cancellation:
        return SessionCancellationStatusResponse(
            enabled=False,
            total=0,
            requested=0,
            acknowledged=0,
            expired=0,
            expiry_seconds=_settings.cancellation_expiry_seconds,
        )

    status = get_cancellation_status()
    return SessionCancellationStatusResponse(
        enabled=True,
        total=status["total"],
        requested=status["requested"],
        acknowledged=status["acknowledged"],
        expired=status["expired"],
        expiry_seconds=_settings.cancellation_expiry_seconds,
    )


# =============================================================================
# Prompt Queue API
# =============================================================================
# These endpoints manage per-session follow-up prompt queues.
# When a session is executing, prompts can be queued instead of rejected.
# Queued prompts are stored until the session becomes idle.
# =============================================================================


@web_app.get("/session/{session_id}/queue", response_model=PromptQueueListResponse)
async def get_session_queue_endpoint(session_id: str) -> PromptQueueListResponse:
    """Get all pending prompts in a session's queue.

    Returns the list of queued prompts waiting to be processed,
    along with the session's current execution status.

    Args:
        session_id: The session ID to get queue for.

    Returns:
        PromptQueueListResponse with queue contents and status.

    Example:
        ```bash
        curl 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/queue'
        ```
    """
    if not _settings.enable_prompt_queue:
        return PromptQueueListResponse(
            ok=False,
            session_id=session_id,
            is_executing=False,
            queue_size=0,
            prompts=[],
            max_queue_size=_settings.max_queued_prompts_per_session,
        )

    prompts = get_session_queue(session_id)
    is_exec = is_session_executing(session_id)

    # Add position numbers to prompts
    prompt_entries = [
        QueuedPromptEntry(
            prompt_id=p["prompt_id"],
            question=p["question"],
            user_id=p.get("user_id"),
            queued_at=p["queued_at"],
            expires_at=p["expires_at"],
            position=i + 1,
        )
        for i, p in enumerate(prompts)
    ]

    return PromptQueueListResponse(
        ok=True,
        session_id=session_id,
        is_executing=is_exec,
        queue_size=len(prompts),
        prompts=prompt_entries,
        max_queue_size=_settings.max_queued_prompts_per_session,
    )


@web_app.post("/session/{session_id}/queue", response_model=QueuePromptResponse)
async def queue_prompt_endpoint(
    session_id: str,
    body: QueuePromptRequest,
) -> QueuePromptResponse:
    """Queue a follow-up prompt for a session.

    If the session is currently executing, the prompt is queued and will
    be available for processing after the current query completes.
    If the session is idle, the prompt is still queued (client can then
    decide to process it immediately via the normal /query endpoint).

    Args:
        session_id: The session ID to queue the prompt for.
        body: QueuePromptRequest containing the prompt text.

    Returns:
        QueuePromptResponse with queue status and prompt details.

    Example:
        ```bash
        curl -X POST 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/queue' \\
          -H 'Content-Type: application/json' \\
          -d '{"question": "Follow-up question here"}'
        ```
    """
    if not _settings.enable_prompt_queue:
        return QueuePromptResponse(
            ok=False,
            queued=False,
            session_id=session_id,
            error="Prompt queue feature is disabled",
        )

    result = queue_prompt(
        session_id=session_id,
        question=body.question,
        user_id=body.user_id,
    )

    if result.get("queued"):
        is_exec = is_session_executing(session_id)
        message = "Prompt queued"
        if is_exec:
            message = "Prompt queued. Session is executing, will process after current query."
        else:
            message = "Prompt queued. Session is idle, ready for processing."

        return QueuePromptResponse(
            ok=True,
            queued=True,
            session_id=session_id,
            prompt_id=result.get("prompt_id"),
            position=result.get("position"),
            queue_size=result.get("queue_size", 0),
            expires_at=result.get("expires_at"),
            message=message,
        )
    else:
        return QueuePromptResponse(
            ok=False,
            queued=False,
            session_id=session_id,
            queue_size=result.get("queue_size", 0),
            error=result.get("error", "Failed to queue prompt"),
        )


@web_app.delete("/session/{session_id}/queue", response_model=PromptQueueClearResponse)
async def clear_session_queue_endpoint(session_id: str) -> PromptQueueClearResponse:
    """Clear all pending prompts from a session's queue.

    Args:
        session_id: The session ID to clear queue for.

    Returns:
        PromptQueueClearResponse with number of prompts cleared.

    Example:
        ```bash
        curl -X DELETE 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/queue'
        ```
    """
    if not _settings.enable_prompt_queue:
        return PromptQueueClearResponse(
            ok=False,
            session_id=session_id,
            cleared_count=0,
            message="Prompt queue feature is disabled",
        )

    count = clear_session_queue(session_id)
    return PromptQueueClearResponse(
        ok=True,
        session_id=session_id,
        cleared_count=count,
        message=f"Cleared {count} queued prompt(s)" if count > 0 else "Queue was already empty",
    )


@web_app.delete("/session/{session_id}/queue/{prompt_id}")
async def remove_queued_prompt_endpoint(session_id: str, prompt_id: str) -> JSONResponse:
    """Remove a specific prompt from the queue by its ID.

    Args:
        session_id: The session ID.
        prompt_id: The prompt ID to remove.

    Returns:
        JSONResponse with removal status.

    Example:
        ```bash
        curl -X DELETE 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/queue/prompt_xyz'
        ```
    """
    if not _settings.enable_prompt_queue:
        return JSONResponse(
            {"ok": False, "removed": False, "error": "Prompt queue feature is disabled"},
            status_code=503,
        )

    removed = remove_queued_prompt(session_id, prompt_id)
    if removed:
        return JSONResponse(
            {"ok": True, "removed": True, "session_id": session_id, "prompt_id": prompt_id}
        )
    else:
        return JSONResponse(
            {
                "ok": False,
                "removed": False,
                "session_id": session_id,
                "prompt_id": prompt_id,
                "error": "Prompt not found in queue",
            },
            status_code=404,
        )


@web_app.get("/session/{session_id}/executing")
async def is_session_executing_endpoint(session_id: str) -> JSONResponse:
    """Check if a session is currently executing a query.

    Useful for clients to decide whether to queue a prompt or
    submit it directly.

    Args:
        session_id: The session ID to check.

    Returns:
        JSONResponse with execution status.

    Example:
        ```bash
        curl 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/executing'
        ```
    """
    is_exec = is_session_executing(session_id) if _settings.enable_prompt_queue else False
    queue_size = get_queue_size(session_id) if _settings.enable_prompt_queue else 0
    return JSONResponse(
        {
            "ok": True,
            "session_id": session_id,
            "is_executing": is_exec,
            "queue_size": queue_size,
            "queue_enabled": _settings.enable_prompt_queue,
        }
    )


@web_app.get("/session/queue/status", response_model=PromptQueueStatusResponse)
async def get_prompt_queue_status_endpoint() -> PromptQueueStatusResponse:
    """Get current status of prompt queues across all sessions.

    Returns statistics about queued prompts including counts of
    active, expired, and total queued prompts.

    Example:
        ```bash
        curl 'https://<org>--test-sandbox-http-app.modal.run/session/queue/status'
        ```
    """
    if not _settings.enable_prompt_queue:
        return PromptQueueStatusResponse(
            enabled=False,
            sessions_with_queues=0,
            total_queued_prompts=0,
            active_prompts=0,
            expired_prompts=0,
            max_queue_size=_settings.max_queued_prompts_per_session,
            entry_expiry_seconds=_settings.prompt_queue_entry_expiry_seconds,
        )

    status = get_prompt_queue_status()
    return PromptQueueStatusResponse(
        enabled=True,
        sessions_with_queues=status["sessions_with_queues"],
        total_queued_prompts=status["total_queued_prompts"],
        active_prompts=status["active_prompts"],
        expired_prompts=status["expired_prompts"],
        max_queue_size=status["max_queue_size"],
        entry_expiry_seconds=status["entry_expiry_seconds"],
    )


# =============================================================================
# Multiplayer Session Endpoints
# =============================================================================
# These endpoints support multiplayer session collaboration where multiple users
# can interact with the same session. Sessions track ownership, authorized users,
# and message history with user attribution.
# =============================================================================


@web_app.get("/session/{session_id}/metadata", response_model=SessionMetadataResponse)
async def get_session_metadata_endpoint(session_id: str) -> SessionMetadataResponse:
    """Get metadata for a session including ownership and access info.

    Returns session metadata including owner, authorized users, and message count.
    Returns a 404-like response if session metadata doesn't exist.

    Example:
        ```bash
        curl 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/metadata'
        ```
    """
    if not _settings.enable_multiplayer_sessions:
        return SessionMetadataResponse(
            ok=False,
            session_id=session_id,
            message="Multiplayer sessions are disabled",
        )

    metadata = get_session_metadata(session_id)
    if not metadata:
        return SessionMetadataResponse(
            ok=False,
            session_id=session_id,
            message="Session metadata not found",
        )

    # Check for snapshot and execution state
    snapshot = get_session_snapshot(session_id)
    is_exec = is_session_executing(session_id)

    return SessionMetadataResponse(
        ok=True,
        session_id=session_id,
        owner_id=metadata.get("owner_id"),
        created_at=metadata.get("created_at"),
        updated_at=metadata.get("updated_at"),
        name=metadata.get("name"),
        description=metadata.get("description"),
        authorized_users=metadata.get("authorized_users", []),
        message_count=len(metadata.get("messages", [])),
        is_shared=bool(metadata.get("authorized_users")),
        is_executing=is_exec,
        has_snapshot=snapshot is not None,
    )


@web_app.get("/session/{session_id}/users", response_model=SessionUsersResponse)
async def get_session_users_endpoint(session_id: str) -> SessionUsersResponse:
    """Get list of users with access to a session.

    Returns the owner and all authorized users for a session.

    Example:
        ```bash
        curl 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/users'
        ```
    """
    if not _settings.enable_multiplayer_sessions:
        return SessionUsersResponse(
            ok=False,
            session_id=session_id,
            authorized_users=[],
            total_users=0,
            message="Multiplayer sessions are disabled",
        )

    users = get_session_users(session_id)
    if not users:
        return SessionUsersResponse(
            ok=False,
            session_id=session_id,
            authorized_users=[],
            total_users=0,
            message="Session metadata not found",
        )

    return SessionUsersResponse(
        ok=True,
        session_id=session_id,
        owner_id=users.get("owner_id"),
        authorized_users=users.get("authorized_users", []),
        total_users=users.get("total_users", 0),
    )


@web_app.post("/session/{session_id}/share", response_model=SessionShareResponse)
async def share_session_endpoint(
    session_id: str, body: SessionShareRequest
) -> SessionShareResponse:
    """Share a session with another user.

    Adds a user to the authorized_users list, granting them access to query
    and interact with the session.

    Example:
        ```bash
        curl -X POST 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/share' \\
          -H 'Content-Type: application/json' \\
          -d '{"user_id": "user_456", "requested_by": "user_123"}'
        ```
    """
    if not _settings.enable_multiplayer_sessions:
        return SessionShareResponse(
            ok=False,
            session_id=session_id,
            shared_with=body.user_id,
            authorized_users=[],
            message="Multiplayer sessions are disabled",
        )

    # Check if session exists, auto-create if not
    metadata = get_session_metadata(session_id)
    if not metadata:
        # Create session metadata with requester as owner
        metadata = create_session_metadata(session_id, owner_id=body.requested_by)

    # Authorize the user
    result = authorize_session_user(session_id, body.user_id, authorized_by=body.requested_by)
    if not result:
        return SessionShareResponse(
            ok=False,
            session_id=session_id,
            shared_with=body.user_id,
            authorized_users=metadata.get("authorized_users", []),
            message="Failed to authorize user (max users limit may be reached)",
        )

    return SessionShareResponse(
        ok=True,
        session_id=session_id,
        shared_with=body.user_id,
        authorized_users=result.get("authorized_users", []),
        message=f"Session shared with {body.user_id}",
    )


@web_app.post("/session/{session_id}/unshare", response_model=SessionUnshareResponse)
async def unshare_session_endpoint(
    session_id: str, body: SessionUnshareRequest
) -> SessionUnshareResponse:
    """Revoke a user's access to a session.

    Removes a user from the authorized_users list.

    Example:
        ```bash
        curl -X POST 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/unshare' \\
          -H 'Content-Type: application/json' \\
          -d '{"user_id": "user_456", "requested_by": "user_123"}'
        ```
    """
    if not _settings.enable_multiplayer_sessions:
        return SessionUnshareResponse(
            ok=False,
            session_id=session_id,
            revoked_from=body.user_id,
            authorized_users=[],
            message="Multiplayer sessions are disabled",
        )

    result = revoke_session_user(session_id, body.user_id, revoked_by=body.requested_by)
    if not result:
        return SessionUnshareResponse(
            ok=False,
            session_id=session_id,
            revoked_from=body.user_id,
            authorized_users=[],
            message="Session metadata not found",
        )

    return SessionUnshareResponse(
        ok=True,
        session_id=session_id,
        revoked_from=body.user_id,
        authorized_users=result.get("authorized_users", []),
        message=f"Access revoked from {body.user_id}",
    )


@web_app.get("/session/{session_id}/history", response_model=SessionHistoryResponse)
async def get_session_history_endpoint(
    session_id: str,
    limit: int | None = None,
    offset: int = 0,
) -> SessionHistoryResponse:
    """Get message history for a session.

    Returns the conversation history with user attribution.
    Supports pagination with limit and offset parameters.

    Example:
        ```bash
        # Get all history
        curl 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/history'

        # Get last 10 messages
        curl 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/history?limit=10'
        ```
    """
    if not _settings.enable_multiplayer_sessions:
        return SessionHistoryResponse(
            ok=False,
            session_id=session_id,
            message_count=0,
            messages=[],
            message="Multiplayer sessions are disabled",
        )

    total_count = get_session_message_count(session_id)
    messages = get_session_history(session_id, limit=limit, offset=offset)

    # Convert to schema objects
    message_entries = [
        MessageHistoryEntry(
            message_id=m["message_id"],
            role=m["role"],
            content=m["content"],
            user_id=m.get("user_id"),
            timestamp=m["timestamp"],
            turn_number=m.get("turn_number"),
            tokens_used=m.get("tokens_used"),
        )
        for m in messages
    ]

    # Check if there are more messages
    has_more = (offset + len(messages)) < total_count if limit else False

    return SessionHistoryResponse(
        ok=True,
        session_id=session_id,
        message_count=total_count,
        messages=message_entries,
        has_more=has_more,
    )


@web_app.get("/session/multiplayer/status", response_model=MultiplayerStatusResponse)
async def get_multiplayer_status_endpoint() -> MultiplayerStatusResponse:
    """Get current status of multiplayer sessions across the system.

    Returns statistics about sessions with metadata, shared sessions,
    and message counts.

    Example:
        ```bash
        curl 'https://<org>--test-sandbox-http-app.modal.run/session/multiplayer/status'
        ```
    """
    if not _settings.enable_multiplayer_sessions:
        return MultiplayerStatusResponse(
            enabled=False,
            total_sessions=0,
            shared_sessions=0,
            total_messages=0,
            max_history_per_session=_settings.max_message_history_per_session,
        )

    status = get_multiplayer_status()
    return MultiplayerStatusResponse(
        enabled=True,
        total_sessions=status["total_sessions"],
        shared_sessions=status["shared_sessions"],
        total_messages=status["total_messages"],
        max_history_per_session=status["max_history_per_session"],
    )


@app.function(image=agent_sdk_image, secrets=agent_sdk_secrets, timeout=120)
def prewarm_agent_sdk_sandbox(warm_id: str, session_id: str | None = None):
    """Background task to pre-warm an Agent SDK sandbox.

    Creates or claims a sandbox and updates the pre-warm entry when ready.
    This runs in the background after POST /warm returns.
    """
    try:
        # Get or create sandbox (will claim from pool if available)
        sb, url = get_or_start_background_sandbox(session_id=session_id)

        # Update pre-warm entry with sandbox details
        updated = update_prewarm_ready(warm_id, sb.object_id, url)
        if updated:
            _logger.info(
                "Pre-warm ready (agent_sdk)",
                extra={
                    "warm_id": warm_id,
                    "sandbox_id": sb.object_id,
                    "url": url,
                },
            )
        else:
            _logger.warning(
                "Pre-warm expired before sandbox ready",
                extra={"warm_id": warm_id},
            )
    except Exception as exc:
        _logger.error(
            "Pre-warm failed (agent_sdk)",
            exc_info=True,
            extra={"warm_id": warm_id, "error": str(exc)},
        )


@app.function(image=agent_sdk_image, secrets=agent_sdk_secrets, timeout=120)
def prewarm_cli_sandbox(warm_id: str, job_id: str | None = None):
    """Background task to pre-warm a CLI sandbox.

    Creates or claims a sandbox and updates the pre-warm entry when ready.
    This runs in the background after POST /warm returns.
    """
    try:
        # Get or create CLI sandbox (will claim from pool if available)
        sb, url = get_or_start_cli_sandbox(job_id=job_id)

        # Update pre-warm entry with sandbox details
        updated = update_prewarm_ready(warm_id, sb.object_id, url)
        if updated:
            _logger.info(
                "Pre-warm ready (cli)",
                extra={
                    "warm_id": warm_id,
                    "sandbox_id": sb.object_id,
                    "url": url,
                },
            )
        else:
            _logger.warning(
                "Pre-warm expired before sandbox ready",
                extra={"warm_id": warm_id},
            )
    except Exception as exc:
        _logger.error(
            "Pre-warm failed (cli)",
            exc_info=True,
            extra={"warm_id": warm_id, "error": str(exc)},
        )


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
CLI_SANDBOX_NAME = _settings.claude_cli_sandbox_name
CLI_SERVICE_PORT = _settings.claude_cli_service_port
CLI_SERVICE_PORTS = _settings.claude_cli_service_ports
CLI_PERSIST_VOL_NAME = _settings.claude_cli_persist_vol_name


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
CLI_SANDBOX: modal.Sandbox | None = None
CLI_SERVICE_URL: str | None = None


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

    try:
        cli_sb = modal.Sandbox.from_name("test-sandbox", CLI_SANDBOX_NAME)
        _ = cli_sb.tunnels()
        SESSIONS[CLI_SANDBOX_NAME] = {**SESSIONS.get(CLI_SANDBOX_NAME, {}), "status": "running"}
    except modal_exc.NotFoundError:
        SESSIONS[CLI_SANDBOX_NAME] = {"status": "missing"}
    except Exception:
        logging.getLogger(__name__).exception("Unexpected error cleaning up Claude CLI sessions")


# =============================================================================
# WARM POOL MANAGEMENT
# =============================================================================
# Functions for maintaining a pool of pre-warmed Agent SDK sandboxes.
# The pool reduces cold-start latency by keeping sandboxes ready for use.
# Pool sandboxes run uvicorn with the same configuration as the main service.


def _create_warm_sandbox_sync() -> tuple[modal.Sandbox, str, str] | None:
    """Create a single warm sandbox and add it to the pool.

    Creates a new sandbox with uvicorn running, waits for it to become healthy,
    registers it in the pool, and returns the sandbox details.

    Returns:
        Tuple of (sandbox, sandbox_id, sandbox_name) if successful, None if failed.
    """
    pool_name = generate_pool_sandbox_name()
    svc_vol = _get_persist_volume()

    try:
        sb = modal.Sandbox.create(
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
            name=pool_name,
            encrypted_ports=_settings.service_ports,
            volumes={"/data": svc_vol},
            timeout=_settings.sandbox_timeout,
            idle_timeout=_settings.sandbox_idle_timeout,
            **_sandbox_resource_kwargs(),
            verbose=False,
        )
    except Exception:
        _logger.warning("Failed to create warm pool sandbox", exc_info=True)
        return None

    # Set pool tags for tracking
    sb.set_tags(
        {
            "pool": "agent_sdk",
            "status": "warm",
            "app": "test-sandbox",
            "port": str(SERVICE_PORT),
        }
    )

    # Wait for tunnel URL
    deadline = time.time() + 30
    service_url = None
    while time.time() < deadline:
        tunnels = sb.tunnels()
        if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
            service_url = tunnels[SERVICE_PORT].url
            break
        time.sleep(0.5)

    if not service_url:
        _logger.warning("Failed to get tunnel URL for warm pool sandbox")
        try:
            sb.terminate()
        except Exception:
            pass
        return None

    # Wait for health check
    try:
        _wait_for_service(service_url, timeout=30)
    except TimeoutError:
        _logger.warning("Warm pool sandbox health check failed")
        try:
            sb.terminate()
        except Exception:
            pass
        return None

    # Register in pool
    sandbox_id = sb.object_id
    register_warm_sandbox(sandbox_id, pool_name)
    _logger.info(
        "Created warm pool sandbox",
        extra={"sandbox_id": sandbox_id, "sandbox_name": pool_name, "url": service_url},
    )

    return sb, sandbox_id, pool_name


@app.function(
    image=agent_sdk_image,
    secrets=agent_sdk_secrets,
    timeout=600,
    **_retry_kwargs(),
)
def replenish_warm_pool():
    """Add sandboxes to the warm pool up to the configured size.

    Called after a sandbox is claimed from the pool to replenish it.
    Also called by the pool maintainer on a schedule.
    """
    if not _settings.enable_warm_pool:
        return {"status": "disabled", "created": 0}

    target_size = _settings.warm_pool_size
    pool_status = get_warm_pool_status()
    warm_count = pool_status["warm"]
    needed = target_size - warm_count

    _logger.info(
        "Replenishing warm pool",
        extra={"target": target_size, "current_warm": warm_count, "needed": needed},
    )

    created = 0
    for _ in range(needed):
        result = _create_warm_sandbox_sync()
        if result:
            created += 1
        else:
            # Don't keep trying if creation fails
            break

    return {"status": "ok", "created": created, "target": target_size, "warm_count": warm_count}


@app.function(
    image=agent_sdk_image,
    secrets=agent_sdk_secrets,
    schedule=modal.Cron(f"*/{max(_settings.warm_pool_refresh_interval // 60, 1)} * * * *"),
    timeout=600,
    **_retry_kwargs(),
)
def maintain_warm_pool():
    """Periodic maintenance of the warm sandbox pool.

    Runs on a schedule to:
    1. Clean up stale pool entries for sandboxes that no longer exist
    2. Expire old sandboxes (beyond max age) to pick up image changes
    3. Replenish the pool to maintain target size

    The schedule is derived from warm_pool_refresh_interval setting.
    """
    if not _settings.enable_warm_pool:
        return {"status": "disabled"}

    _logger.info("Running warm pool maintenance")

    # Step 1: Find live pool sandboxes via Modal API
    live_sandbox_ids: set[str] = set()
    try:
        for sb in modal.Sandbox.list(tags={"pool": "agent_sdk"}):
            # Verify sandbox is still running
            if sb.poll() is None:
                live_sandbox_ids.add(sb.object_id)
            else:
                # Sandbox has exited, remove from pool
                remove_from_pool(sb.object_id)
    except Exception:
        _logger.warning("Failed to list pool sandboxes", exc_info=True)

    # Step 2: Clean up stale entries (entries for sandboxes that no longer exist)
    stale_removed = cleanup_stale_pool_entries(live_sandbox_ids)
    if stale_removed > 0:
        _logger.info("Removed stale pool entries", extra={"count": stale_removed})

    # Step 3: Expire old sandboxes
    expired_entries = get_expired_pool_entries(_settings.warm_pool_sandbox_max_age)
    expired_count = 0
    for entry in expired_entries:
        sandbox_id = entry.get("sandbox_id")
        if sandbox_id:
            try:
                sb = modal.Sandbox.from_id(sandbox_id)
                sb.terminate()
                _logger.info("Terminated expired pool sandbox", extra={"sandbox_id": sandbox_id})
            except Exception:
                pass
            remove_from_pool(sandbox_id)
            expired_count += 1
    if expired_count > 0:
        _logger.info("Expired old pool sandboxes", extra={"count": expired_count})

    # Step 4: Replenish pool
    replenish_result = replenish_warm_pool.local()

    pool_status = get_warm_pool_status()
    return {
        "status": "ok",
        "stale_removed": stale_removed,
        "expired_terminated": expired_count,
        "replenished": replenish_result.get("created", 0),
        "pool_warm": pool_status["warm"],
        "pool_claimed": pool_status["claimed"],
        "pool_total": pool_status["total"],
    }


# =============================================================================
# CLI WARM POOL MANAGEMENT
# =============================================================================
# Functions for maintaining a pool of pre-warmed CLI sandboxes.
# The pool reduces cold-start latency by keeping CLI sandboxes ready for use.
# Pool sandboxes run uvicorn with the same configuration as the CLI service.


def _create_cli_warm_sandbox_sync() -> tuple[modal.Sandbox, str, str] | None:
    """Create a single warm CLI sandbox and add it to the pool.

    Creates a new CLI sandbox with uvicorn running, waits for it to become healthy,
    registers it in the pool, and returns the sandbox details.

    Returns:
        Tuple of (sandbox, sandbox_id, sandbox_name) if successful, None if failed.
    """
    pool_name = generate_cli_pool_sandbox_name()
    cli_vol = _get_claude_cli_volume()

    try:
        sb = modal.Sandbox.create(
            "uvicorn",
            "agent_sandbox.controllers.cli_controller:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(CLI_SERVICE_PORT),
            app=app,
            image=claude_cli_image,
            secrets=agent_sdk_secrets,
            workdir=str(CLAUDE_CLI_APP_ROOT),
            name=pool_name,
            encrypted_ports=CLI_SERVICE_PORTS,
            volumes={_settings.claude_cli_fs_root: cli_vol},
            timeout=_settings.claude_cli_sandbox_timeout,
            idle_timeout=_settings.claude_cli_sandbox_idle_timeout,
            **_cli_sandbox_resource_kwargs(),
            verbose=False,
        )
    except Exception:
        _logger.warning("Failed to create CLI warm pool sandbox", exc_info=True)
        return None

    # Set pool tags for tracking
    sb.set_tags(
        {
            "pool": "cli",
            "status": "warm",
            "app": "test-sandbox",
            "port": str(CLI_SERVICE_PORT),
        }
    )

    # Wait for tunnel URL
    deadline = time.time() + 30
    service_url = None
    while time.time() < deadline:
        tunnels = sb.tunnels()
        if CLI_SERVICE_PORT in tunnels and getattr(tunnels[CLI_SERVICE_PORT], "url", None):
            service_url = tunnels[CLI_SERVICE_PORT].url
            break
        time.sleep(0.5)

    if not service_url:
        _logger.warning("Failed to get tunnel URL for CLI warm pool sandbox")
        try:
            sb.terminate()
        except Exception:
            pass
        return None

    # Wait for health check
    try:
        _wait_for_service(service_url, timeout=30)
    except TimeoutError:
        _logger.warning("CLI warm pool sandbox health check failed")
        try:
            sb.terminate()
        except Exception:
            pass
        return None

    # Register in pool
    sandbox_id = sb.object_id
    register_cli_warm_sandbox(sandbox_id, pool_name)
    _logger.info(
        "Created CLI warm pool sandbox",
        extra={"sandbox_id": sandbox_id, "sandbox_name": pool_name, "url": service_url},
    )

    return sb, sandbox_id, pool_name


@app.function(
    image=claude_cli_image,
    secrets=agent_sdk_secrets,
    timeout=600,
    **_retry_kwargs(),
)
def replenish_cli_warm_pool():
    """Add CLI sandboxes to the warm pool up to the configured size.

    Called after a sandbox is claimed from the pool to replenish it.
    Also called by the pool maintainer on a schedule.
    """
    if not _settings.enable_cli_warm_pool:
        return {"status": "disabled", "created": 0}

    target_size = _settings.cli_warm_pool_size
    pool_status = get_cli_warm_pool_status()
    warm_count = pool_status["warm"]
    needed = target_size - warm_count

    _logger.info(
        "Replenishing CLI warm pool",
        extra={"target": target_size, "current_warm": warm_count, "needed": needed},
    )

    created = 0
    for _ in range(needed):
        result = _create_cli_warm_sandbox_sync()
        if result:
            created += 1
        else:
            # Don't keep trying if creation fails
            break

    return {"status": "ok", "created": created, "target": target_size, "warm_count": warm_count}


@app.function(
    image=claude_cli_image,
    secrets=agent_sdk_secrets,
    schedule=modal.Cron(f"*/{max(_settings.cli_warm_pool_refresh_interval // 60, 1)} * * * *"),
    timeout=600,
    **_retry_kwargs(),
)
def maintain_cli_warm_pool():
    """Periodic maintenance of the CLI warm sandbox pool.

    Runs on a schedule to:
    1. Clean up stale pool entries for sandboxes that no longer exist
    2. Expire old sandboxes (beyond max age) to pick up image changes
    3. Replenish the pool to maintain target size

    The schedule is derived from cli_warm_pool_refresh_interval setting.
    """
    if not _settings.enable_cli_warm_pool:
        return {"status": "disabled"}

    _logger.info("Running CLI warm pool maintenance")

    # Step 1: Find live pool sandboxes via Modal API
    live_sandbox_ids: set[str] = set()
    try:
        for sb in modal.Sandbox.list(tags={"pool": "cli"}):
            # Verify sandbox is still running
            if sb.poll() is None:
                live_sandbox_ids.add(sb.object_id)
            else:
                # Sandbox has exited, remove from pool
                remove_from_cli_pool(sb.object_id)
    except Exception:
        _logger.warning("Failed to list CLI pool sandboxes", exc_info=True)

    # Step 2: Clean up stale entries (entries for sandboxes that no longer exist)
    stale_removed = cleanup_stale_cli_pool_entries(live_sandbox_ids)
    if stale_removed > 0:
        _logger.info("Removed stale CLI pool entries", extra={"count": stale_removed})

    # Step 3: Expire old sandboxes
    expired_entries = get_expired_cli_pool_entries(_settings.cli_warm_pool_sandbox_max_age)
    expired_count = 0
    for entry in expired_entries:
        sandbox_id = entry.get("sandbox_id")
        if sandbox_id:
            try:
                sb = modal.Sandbox.from_id(sandbox_id)
                sb.terminate()
                _logger.info(
                    "Terminated expired CLI pool sandbox", extra={"sandbox_id": sandbox_id}
                )
            except Exception:
                pass
            remove_from_cli_pool(sandbox_id)
            expired_count += 1
    if expired_count > 0:
        _logger.info("Expired old CLI pool sandboxes", extra={"count": expired_count})

    # Step 4: Replenish pool
    replenish_result = replenish_cli_warm_pool.local()

    pool_status = get_cli_warm_pool_status()
    return {
        "status": "ok",
        "stale_removed": stale_removed,
        "expired_terminated": expired_count,
        "replenished": replenish_result.get("created", 0),
        "pool_warm": pool_status["warm"],
        "pool_claimed": pool_status["claimed"],
        "pool_total": pool_status["total"],
    }


def get_or_start_background_sandbox(
    session_id: str | None = None,
) -> tuple[modal.Sandbox, str]:
    """Return a running background sandbox and its encrypted service URL.

    Starts a daemonized sandbox running `uvicorn agent_sandbox.controllers.controller:app` if one is
    not already available, then discovers its encrypted tunnel URL on port
    8001. The function blocks until the `/health_check` endpoint responds.

    Args:
        session_id: Optional session ID for snapshot restoration. When provided
            and a snapshot exists for this session, creates the sandbox from
            the snapshot image to restore filesystem state from a previous session.

    Returns:
        A pair of `(sandbox, service_url)`.

    Warm Pool:
        When enabled, the function first tries to claim a pre-warmed sandbox
        from the pool. Pool sandboxes have uvicorn already running and
        health-checked, eliminating cold-start latency.

    Session Snapshot Restoration:
        When resuming a session after sandbox timeout, pass the session_id to
        check for stored snapshots. If a snapshot exists:
        1. The snapshot image is used instead of the base agent_sdk_image
        2. This restores installed packages, downloaded files, and other
           filesystem changes from the previous session
        3. The Claude Agent SDK session is resumed via its resume= parameter
           (handled in the controller)
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
    # STEP 3: Determine image for new sandbox (snapshot restoration)
    # -------------------------------------------------------------------------
    # Check if we have a stored snapshot for this session. If so, use the
    # snapshot image to restore filesystem state from the previous session.
    # This enables "leave and come back" workflows where agent-installed tools
    # and downloaded files are preserved across sandbox restarts.
    # -------------------------------------------------------------------------
    sandbox_image = agent_sdk_image
    restored_from_snapshot = False
    use_warm_pool = _settings.enable_warm_pool

    if session_id and _settings.enable_session_snapshots:
        snapshot = get_session_snapshot(session_id)
        if snapshot and snapshot.get("image_id"):
            try:
                snapshot_image = modal.Image.from_id(snapshot["image_id"])
                sandbox_image = snapshot_image
                restored_from_snapshot = True
                # Don't use warm pool when restoring from snapshot - need specific image
                use_warm_pool = False
                _logger.info(
                    "Restoring sandbox from session snapshot",
                    extra={
                        "session_id": session_id,
                        "snapshot_image_id": snapshot["image_id"],
                        "snapshot_created_at": snapshot.get("created_at"),
                    },
                )
            except Exception:
                _logger.warning(
                    "Failed to restore from snapshot, using base image",
                    exc_info=True,
                    extra={"session_id": session_id, "snapshot": snapshot},
                )

    # -------------------------------------------------------------------------
    # STEP 3.5: Try to claim from warm pool (if enabled and no snapshot)
    # -------------------------------------------------------------------------
    # The warm pool contains pre-created sandboxes with uvicorn already running
    # and health-checked. Claiming from pool avoids sandbox creation overhead.
    # Pool is only used when not restoring from snapshot (need base image).
    # -------------------------------------------------------------------------
    if use_warm_pool:
        try:
            claimed = claim_warm_sandbox(session_id=session_id)
            if claimed:
                sandbox_id = claimed.get("sandbox_id")
                sandbox_name = claimed.get("sandbox_name")
                if sandbox_id:
                    try:
                        pool_sb = modal.Sandbox.from_id(sandbox_id)
                        # Verify sandbox is still running
                        if pool_sb.poll() is None:
                            # Get tunnel URL
                            tunnels = pool_sb.tunnels()
                            if SERVICE_PORT in tunnels and getattr(
                                tunnels[SERVICE_PORT], "url", None
                            ):
                                pool_url = tunnels[SERVICE_PORT].url
                                # Verify health
                                _wait_for_service(pool_url)
                                SANDBOX = pool_sb
                                SERVICE_URL = pool_url
                                _logger.info(
                                    "Claimed sandbox from warm pool",
                                    extra={
                                        "sandbox_id": sandbox_id,
                                        "sandbox_name": sandbox_name,
                                        "session_id": session_id,
                                    },
                                )
                                # Update tags to reflect active use
                                pool_sb.set_tags(
                                    {
                                        "pool": "agent_sdk",
                                        "status": "claimed",
                                        "role": "service",
                                        "app": "test-sandbox",
                                        "port": str(SERVICE_PORT),
                                    }
                                )
                                # Trigger async pool replenishment
                                try:
                                    replenish_warm_pool.spawn()
                                except Exception:
                                    pass  # Non-critical: pool will be replenished by maintainer
                                return SANDBOX, SERVICE_URL
                    except Exception:
                        _logger.warning(
                            "Failed to use claimed pool sandbox, will create new",
                            exc_info=True,
                            extra={"sandbox_id": sandbox_id},
                        )
                        # Remove the bad entry from pool
                        remove_from_pool(sandbox_id)
        except Exception:
            _logger.warning("Error checking warm pool, will create new sandbox", exc_info=True)

    # -------------------------------------------------------------------------
    # STEP 4: Create a NEW sandbox
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
            image=sandbox_image,  # Container image (base or snapshot for restoration)
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
        try:
            SANDBOX = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)
        except modal_exc.NotFoundError:
            # In dev mode, from_name doesn't work since app isn't deployed.
            raise modal_exc.AlreadyExistsError(
                f"Sandbox '{SANDBOX_NAME}' already exists but cannot be looked up in dev mode"
            )

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
        session_metadata: dict = {
            "id": SANDBOX.object_id,
            "url": SERVICE_URL,
            "volume": PERSIST_VOL_NAME,
            "created_at": int(time.time()),
            "tags": {"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)},
            "status": "running",
        }
        # Track snapshot restoration for observability
        if restored_from_snapshot and session_id:
            session_metadata["restored_from_session"] = session_id
            session_metadata["restored_from_snapshot"] = True
        SESSIONS[SANDBOX_NAME] = session_metadata
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


async def get_or_start_background_sandbox_aio(
    session_id: str | None = None,
) -> tuple[modal.Sandbox, str]:
    """Async version of get_or_start_background_sandbox.

    Args:
        session_id: Optional session ID for snapshot restoration.

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

    # Determine image for new sandbox (snapshot restoration)
    sandbox_image = agent_sdk_image
    restored_from_snapshot = False
    use_warm_pool = _settings.enable_warm_pool

    if session_id and _settings.enable_session_snapshots:
        snapshot = get_session_snapshot(session_id)
        if snapshot and snapshot.get("image_id"):
            try:
                snapshot_image = modal.Image.from_id(snapshot["image_id"])
                sandbox_image = snapshot_image
                restored_from_snapshot = True
                # Don't use warm pool when restoring from snapshot - need specific image
                use_warm_pool = False
                _logger.info(
                    "Restoring sandbox from session snapshot (async)",
                    extra={
                        "session_id": session_id,
                        "snapshot_image_id": snapshot["image_id"],
                        "snapshot_created_at": snapshot.get("created_at"),
                    },
                )
            except Exception:
                _logger.warning(
                    "Failed to restore from snapshot, using base image",
                    exc_info=True,
                    extra={"session_id": session_id, "snapshot": snapshot},
                )

    # Try to claim from warm pool (if enabled and no snapshot)
    if use_warm_pool:
        try:
            claimed = claim_warm_sandbox(session_id=session_id)
            if claimed:
                sandbox_id = claimed.get("sandbox_id")
                sandbox_name = claimed.get("sandbox_name")
                if sandbox_id:
                    try:
                        pool_sb = modal.Sandbox.from_id(sandbox_id)
                        # Verify sandbox is still running
                        if pool_sb.poll() is None:
                            # Get tunnel URL
                            tunnels = await pool_sb.tunnels.aio()
                            if SERVICE_PORT in tunnels and getattr(
                                tunnels[SERVICE_PORT], "url", None
                            ):
                                pool_url = tunnels[SERVICE_PORT].url
                                # Verify health
                                await _wait_for_service_aio(pool_url)
                                SANDBOX = pool_sb
                                SERVICE_URL = pool_url
                                _logger.info(
                                    "Claimed sandbox from warm pool (async)",
                                    extra={
                                        "sandbox_id": sandbox_id,
                                        "sandbox_name": sandbox_name,
                                        "session_id": session_id,
                                    },
                                )
                                # Update tags to reflect active use
                                await pool_sb.set_tags.aio(
                                    {
                                        "pool": "agent_sdk",
                                        "status": "claimed",
                                        "role": "service",
                                        "app": "test-sandbox",
                                        "port": str(SERVICE_PORT),
                                    }
                                )
                                # Trigger async pool replenishment
                                try:
                                    replenish_warm_pool.spawn()
                                except Exception:
                                    pass  # Non-critical
                                return SANDBOX, SERVICE_URL
                    except Exception:
                        _logger.warning(
                            "Failed to use claimed pool sandbox (async), will create new",
                            exc_info=True,
                            extra={"sandbox_id": sandbox_id},
                        )
                        remove_from_pool(sandbox_id)
        except Exception:
            _logger.warning(
                "Error checking warm pool (async), will create new sandbox", exc_info=True
            )

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
            image=sandbox_image,  # Use snapshot image if available
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
        try:
            SANDBOX = await modal.Sandbox.from_name.aio("test-sandbox", SANDBOX_NAME)
        except modal_exc.NotFoundError:
            # In dev mode, from_name doesn't work since app isn't deployed.
            raise modal_exc.AlreadyExistsError(
                f"Sandbox '{SANDBOX_NAME}' already exists but cannot be looked up in dev mode"
            )

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
        session_metadata: dict = {
            "id": SANDBOX.object_id,
            "url": SERVICE_URL,
            "volume": PERSIST_VOL_NAME,
            "created_at": int(time.time()),
            "tags": {"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)},
            "status": "running",
        }
        # Track snapshot restoration for observability
        if restored_from_snapshot and session_id:
            session_metadata["restored_from_session"] = session_id
            session_metadata["restored_from_snapshot"] = True
        SESSIONS[SANDBOX_NAME] = session_metadata
    except Exception:
        pass

    return SANDBOX, SERVICE_URL


def get_or_start_cli_sandbox(
    job_id: str | None = None,
) -> tuple[modal.Sandbox, str]:
    """Return a running Claude CLI sandbox and its encrypted service URL.

    Args:
        job_id: Optional job ID for snapshot restoration. When provided
            and a snapshot exists for this job, creates the sandbox from
            the snapshot image to restore filesystem state from a previous job.

    Returns:
        A pair of `(sandbox, service_url)`.

    Warm Pool:
        When enabled, the function first tries to claim a pre-warmed CLI sandbox
        from the pool. Pool sandboxes have uvicorn already running and
        health-checked, eliminating cold-start latency.

    CLI Job Snapshot Restoration:
        When resuming a job after sandbox timeout, pass the job_id to
        check for stored snapshots. If a snapshot exists:
        1. The snapshot image is used instead of the base claude_cli_image
        2. This restores installed packages, downloaded files, and other
           filesystem changes from the previous job execution
    """
    global CLI_SANDBOX, CLI_SERVICE_URL

    if CLI_SANDBOX is not None and CLI_SERVICE_URL:
        return CLI_SANDBOX, CLI_SERVICE_URL

    try:
        sb = modal.Sandbox.from_name("test-sandbox", CLI_SANDBOX_NAME)
        tunnels = sb.tunnels()
        if CLI_SERVICE_PORT in tunnels and getattr(tunnels[CLI_SERVICE_PORT], "url", None):
            CLI_SANDBOX = sb
            CLI_SERVICE_URL = tunnels[CLI_SERVICE_PORT].url
            _wait_for_service(CLI_SERVICE_URL)
            return CLI_SANDBOX, CLI_SERVICE_URL
    except Exception:
        pass

    # -------------------------------------------------------------------------
    # Determine image for new sandbox (snapshot restoration)
    # -------------------------------------------------------------------------
    # Check if we have a stored snapshot for this job. If so, use the
    # snapshot image to restore filesystem state from the previous execution.
    # This enables "leave and come back" workflows where CLI-installed tools
    # and downloaded files are preserved across sandbox restarts.
    # -------------------------------------------------------------------------
    sandbox_image = claude_cli_image
    restored_from_snapshot = False
    use_cli_warm_pool = _settings.enable_cli_warm_pool

    if job_id and _settings.enable_cli_job_snapshots:
        snapshot = get_cli_job_snapshot(job_id)
        if snapshot and snapshot.get("image_id"):
            try:
                snapshot_image = modal.Image.from_id(snapshot["image_id"])
                sandbox_image = snapshot_image
                restored_from_snapshot = True
                # Don't use warm pool when restoring from snapshot - need specific image
                use_cli_warm_pool = False
                _logger.info(
                    "Restoring CLI sandbox from job snapshot",
                    extra={
                        "job_id": job_id,
                        "snapshot_image_id": snapshot["image_id"],
                        "snapshot_created_at": snapshot.get("created_at"),
                    },
                )
            except Exception:
                _logger.warning(
                    "Failed to restore CLI sandbox from snapshot, using base image",
                    exc_info=True,
                    extra={"job_id": job_id, "snapshot": snapshot},
                )

    # -------------------------------------------------------------------------
    # Try to claim from CLI warm pool (if enabled and no snapshot)
    # -------------------------------------------------------------------------
    # The CLI warm pool contains pre-created sandboxes with uvicorn already running
    # and health-checked. Claiming from pool avoids sandbox creation overhead.
    # Pool is only used when not restoring from snapshot (need base image).
    # -------------------------------------------------------------------------
    if use_cli_warm_pool:
        try:
            claimed = claim_cli_warm_sandbox(job_id=job_id)
            if claimed:
                sandbox_id = claimed.get("sandbox_id")
                sandbox_name = claimed.get("sandbox_name")
                if sandbox_id:
                    try:
                        pool_sb = modal.Sandbox.from_id(sandbox_id)
                        # Verify sandbox is still running
                        if pool_sb.poll() is None:
                            # Get tunnel URL
                            tunnels = pool_sb.tunnels()
                            if CLI_SERVICE_PORT in tunnels and getattr(
                                tunnels[CLI_SERVICE_PORT], "url", None
                            ):
                                pool_url = tunnels[CLI_SERVICE_PORT].url
                                # Verify health
                                _wait_for_service(pool_url)
                                CLI_SANDBOX = pool_sb
                                CLI_SERVICE_URL = pool_url
                                _logger.info(
                                    "Claimed CLI sandbox from warm pool",
                                    extra={
                                        "sandbox_id": sandbox_id,
                                        "sandbox_name": sandbox_name,
                                        "job_id": job_id,
                                    },
                                )
                                # Update tags to reflect active use
                                pool_sb.set_tags(
                                    {
                                        "pool": "cli",
                                        "status": "claimed",
                                        "role": "claude-cli-service",
                                        "app": "test-sandbox",
                                        "port": str(CLI_SERVICE_PORT),
                                    }
                                )
                                # Trigger async pool replenishment
                                try:
                                    replenish_cli_warm_pool.spawn()
                                except Exception:
                                    pass  # Non-critical: pool will be replenished by maintainer
                                return CLI_SANDBOX, CLI_SERVICE_URL
                    except Exception:
                        _logger.warning(
                            "Failed to use claimed CLI pool sandbox, will create new",
                            exc_info=True,
                            extra={"sandbox_id": sandbox_id},
                        )
                        # Remove the bad entry from pool
                        remove_from_cli_pool(sandbox_id)
        except Exception:
            _logger.warning("Error checking CLI warm pool, will create new sandbox", exc_info=True)

    cli_vol = _get_claude_cli_volume()
    try:
        CLI_SANDBOX = modal.Sandbox.create(
            "uvicorn",
            "agent_sandbox.controllers.cli_controller:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(CLI_SERVICE_PORT),
            app=app,
            image=sandbox_image,  # Use snapshot image if available
            secrets=agent_sdk_secrets,
            workdir=str(CLAUDE_CLI_APP_ROOT),
            name=CLI_SANDBOX_NAME,
            encrypted_ports=CLI_SERVICE_PORTS,
            volumes={_settings.claude_cli_fs_root: cli_vol},
            timeout=_settings.claude_cli_sandbox_timeout,
            idle_timeout=_settings.claude_cli_sandbox_idle_timeout,
            **_cli_sandbox_resource_kwargs(),
            verbose=True,
        )
    except modal_exc.AlreadyExistsError:
        try:
            CLI_SANDBOX = modal.Sandbox.from_name("test-sandbox", CLI_SANDBOX_NAME)
        except modal_exc.NotFoundError:
            # In dev mode, from_name doesn't work since app isn't deployed.
            raise modal_exc.AlreadyExistsError(
                f"Sandbox '{CLI_SANDBOX_NAME}' already exists but cannot be looked up in dev mode"
            )

    CLI_SANDBOX.set_tags(
        {"role": "claude-cli-service", "app": "test-sandbox", "port": str(CLI_SERVICE_PORT)}
    )

    CLI_SERVICE_URL = None
    deadline = time.time() + 30
    while time.time() < deadline:
        tunnels = CLI_SANDBOX.tunnels()
        if CLI_SERVICE_PORT in tunnels and getattr(tunnels[CLI_SERVICE_PORT], "url", None):
            CLI_SERVICE_URL = tunnels[CLI_SERVICE_PORT].url
            break
        time.sleep(0.5)

    if not CLI_SERVICE_URL:
        raise RuntimeError("Failed to start Claude CLI sandbox or get service URL")

    _wait_for_service(CLI_SERVICE_URL)
    try:
        session_metadata: dict = {
            "id": CLI_SANDBOX.object_id,
            "url": CLI_SERVICE_URL,
            "volume": CLI_PERSIST_VOL_NAME,
            "created_at": int(time.time()),
            "tags": {
                "role": "claude-cli-service",
                "app": "test-sandbox",
                "port": str(CLI_SERVICE_PORT),
            },
            "status": "running",
        }
        # Track snapshot restoration for observability
        if restored_from_snapshot and job_id:
            session_metadata["restored_from_job"] = job_id
            session_metadata["restored_from_snapshot"] = True
        SESSIONS[CLI_SANDBOX_NAME] = session_metadata
    except modal_exc.Error as exc:
        logging.getLogger(__name__).warning(
            "Failed to persist Claude CLI session metadata: %s", exc
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Unexpected error persisting Claude CLI session metadata"
        )

    return CLI_SANDBOX, CLI_SERVICE_URL


async def get_or_start_cli_sandbox_aio(
    job_id: str | None = None,
) -> tuple[modal.Sandbox, str]:
    """Async version of get_or_start_cli_sandbox.

    Args:
        job_id: Optional job ID for snapshot restoration.

    Returns:
        A pair of `(sandbox, service_url)`.
    """
    global CLI_SANDBOX, CLI_SERVICE_URL

    if CLI_SANDBOX and CLI_SERVICE_URL:
        return CLI_SANDBOX, CLI_SERVICE_URL

    try:
        sb = modal.Sandbox.from_name("test-sandbox", CLI_SANDBOX_NAME)
        deadline = anyio.current_time() + 30
        url = None
        while anyio.current_time() < deadline:
            tunnels = await sb.tunnels.aio()
            if CLI_SERVICE_PORT in tunnels and getattr(tunnels[CLI_SERVICE_PORT], "url", None):
                url = tunnels[CLI_SERVICE_PORT].url
                break
            await anyio.sleep(0.5)
        if url:
            CLI_SANDBOX, CLI_SERVICE_URL = sb, url
            await _wait_for_service_aio(CLI_SERVICE_URL)
            return CLI_SANDBOX, CLI_SERVICE_URL
    except Exception:
        pass

    # Determine image for new sandbox (snapshot restoration)
    sandbox_image = claude_cli_image
    restored_from_snapshot = False
    use_cli_warm_pool = _settings.enable_cli_warm_pool

    if job_id and _settings.enable_cli_job_snapshots:
        snapshot = get_cli_job_snapshot(job_id)
        if snapshot and snapshot.get("image_id"):
            try:
                snapshot_image = modal.Image.from_id(snapshot["image_id"])
                sandbox_image = snapshot_image
                restored_from_snapshot = True
                # Don't use warm pool when restoring from snapshot - need specific image
                use_cli_warm_pool = False
                _logger.info(
                    "Restoring CLI sandbox from job snapshot (async)",
                    extra={
                        "job_id": job_id,
                        "snapshot_image_id": snapshot["image_id"],
                        "snapshot_created_at": snapshot.get("created_at"),
                    },
                )
            except Exception:
                _logger.warning(
                    "Failed to restore CLI sandbox from snapshot, using base image",
                    exc_info=True,
                    extra={"job_id": job_id, "snapshot": snapshot},
                )

    # -------------------------------------------------------------------------
    # Try to claim from CLI warm pool (if enabled and no snapshot)
    # -------------------------------------------------------------------------
    if use_cli_warm_pool:
        try:
            claimed = claim_cli_warm_sandbox(job_id=job_id)
            if claimed:
                sandbox_id = claimed.get("sandbox_id")
                sandbox_name = claimed.get("sandbox_name")
                if sandbox_id:
                    try:
                        pool_sb = modal.Sandbox.from_id(sandbox_id)
                        # Verify sandbox is still running
                        if pool_sb.poll() is None:
                            # Get tunnel URL
                            deadline = anyio.current_time() + 5
                            pool_url = None
                            while anyio.current_time() < deadline:
                                tunnels = await pool_sb.tunnels.aio()
                                if CLI_SERVICE_PORT in tunnels and getattr(
                                    tunnels[CLI_SERVICE_PORT], "url", None
                                ):
                                    pool_url = tunnels[CLI_SERVICE_PORT].url
                                    break
                                await anyio.sleep(0.25)
                            if pool_url:
                                # Verify health
                                await _wait_for_service_aio(pool_url)
                                CLI_SANDBOX = pool_sb
                                CLI_SERVICE_URL = pool_url
                                _logger.info(
                                    "Claimed CLI sandbox from warm pool (async)",
                                    extra={
                                        "sandbox_id": sandbox_id,
                                        "sandbox_name": sandbox_name,
                                        "job_id": job_id,
                                    },
                                )
                                # Update tags to reflect active use
                                await pool_sb.set_tags.aio(
                                    {
                                        "pool": "cli",
                                        "status": "claimed",
                                        "role": "claude-cli-service",
                                        "app": "test-sandbox",
                                        "port": str(CLI_SERVICE_PORT),
                                    }
                                )
                                # Trigger async pool replenishment
                                try:
                                    replenish_cli_warm_pool.spawn()
                                except Exception:
                                    pass
                                return CLI_SANDBOX, CLI_SERVICE_URL
                    except Exception:
                        _logger.warning(
                            "Failed to use claimed CLI pool sandbox (async), will create new",
                            exc_info=True,
                            extra={"sandbox_id": sandbox_id},
                        )
                        remove_from_cli_pool(sandbox_id)
        except Exception:
            _logger.warning(
                "Error checking CLI warm pool (async), will create new sandbox", exc_info=True
            )

    cli_vol = _get_claude_cli_volume()
    try:
        CLI_SANDBOX = await modal.Sandbox.create.aio(
            "uvicorn",
            "agent_sandbox.controllers.cli_controller:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(CLI_SERVICE_PORT),
            app=app,
            image=sandbox_image,  # Use snapshot image if available
            secrets=agent_sdk_secrets,
            workdir=str(CLAUDE_CLI_APP_ROOT),
            name=CLI_SANDBOX_NAME,
            encrypted_ports=CLI_SERVICE_PORTS,
            volumes={_settings.claude_cli_fs_root: cli_vol},
            timeout=_settings.claude_cli_sandbox_timeout,
            idle_timeout=_settings.claude_cli_sandbox_idle_timeout,
            **_cli_sandbox_resource_kwargs(),
            verbose=True,
        )
    except modal_exc.AlreadyExistsError:
        try:
            CLI_SANDBOX = await modal.Sandbox.from_name.aio("test-sandbox", CLI_SANDBOX_NAME)
        except modal_exc.NotFoundError:
            # In dev mode, from_name doesn't work since app isn't deployed.
            raise modal_exc.AlreadyExistsError(
                f"Sandbox '{CLI_SANDBOX_NAME}' already exists but cannot be looked up in dev mode"
            )

    await CLI_SANDBOX.set_tags.aio(
        {"role": "claude-cli-service", "app": "test-sandbox", "port": str(CLI_SERVICE_PORT)}
    )

    deadline = anyio.current_time() + 30
    CLI_SERVICE_URL = None
    while anyio.current_time() < deadline:
        tunnels = await CLI_SANDBOX.tunnels.aio()
        if CLI_SERVICE_PORT in tunnels and getattr(tunnels[CLI_SERVICE_PORT], "url", None):
            CLI_SERVICE_URL = tunnels[CLI_SERVICE_PORT].url
            break
        await anyio.sleep(0.5)

    if not CLI_SERVICE_URL:
        raise RuntimeError("Failed to start Claude CLI sandbox or get service URL")

    await _wait_for_service_aio(CLI_SERVICE_URL)
    try:
        session_metadata: dict = {
            "id": CLI_SANDBOX.object_id,
            "url": CLI_SERVICE_URL,
            "volume": CLI_PERSIST_VOL_NAME,
            "created_at": int(time.time()),
            "tags": {
                "role": "claude-cli-service",
                "app": "test-sandbox",
                "port": str(CLI_SERVICE_PORT),
            },
            "status": "running",
        }
        # Track snapshot restoration for observability
        if restored_from_snapshot and job_id:
            session_metadata["restored_from_job"] = job_id
            session_metadata["restored_from_snapshot"] = True
        SESSIONS[CLI_SANDBOX_NAME] = session_metadata
    except Exception:
        pass

    return CLI_SANDBOX, CLI_SERVICE_URL


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
    warm_id: str | None = None,
) -> dict | str:
    """Run Claude Code CLI in a dedicated sandbox and return the response."""
    tools_list = None
    if allowed_tools:
        tools_list = [tool.strip() for tool in allowed_tools.split(",") if tool.strip()]

    normalized_job_id = normalize_job_id(job_id)
    env = claude_cli_env()
    require_claude_cli_auth(env)

    request_body = ClaudeCliRequest(
        prompt=prompt,
        allowed_tools=tools_list,
        dangerously_skip_permissions=dangerously_skip_permissions,
        output_format=output_format,
        timeout_seconds=timeout_seconds,
        max_turns=max_turns,
        job_id=normalized_job_id,
        debug=debug,
        probe=probe,
        write_result_path=write_result_path,
    )

    _logger.info(
        "claude_cli.invoke",
        extra={
            "job_id": normalized_job_id,
            "output_format": output_format,
            "probe": probe is not None,
            "warm_id": warm_id,
        },
    )

    # Check for pre-warmed sandbox (from POST /warm)
    settings = Settings()
    if warm_id and settings.enable_prewarm:
        prewarm_claimed = claim_prewarm(warm_id, normalized_job_id or "anonymous")
        if prewarm_claimed:
            _logger.info(
                "CLI using pre-warmed sandbox",
                extra={
                    "warm_id": warm_id,
                    "sandbox_id": prewarm_claimed.get("sandbox_id"),
                    "prewarm_status": prewarm_claimed.get("status"),
                },
            )

    # Pass job_id for potential snapshot restoration
    # If pre-warm was claimed, the sandbox should already be ready in globals
    sb, url = get_or_start_cli_sandbox(job_id=normalized_job_id)
    headers: dict[str, str] = {}
    settings = Settings()
    if settings.enforce_connect_token:
        creds = sb.create_connect_token(user_metadata={"job_id": normalized_job_id or "unknown"})
        headers = {"Authorization": f"Bearer {creds.token}"}

    try:
        timeout = httpx.Timeout(timeout_seconds + 60, connect=30.0)
        response = httpx.post(
            f"{url.rstrip('/')}/execute",
            json=request_body.model_dump(),
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        try:
            payload = exc.response.json()
        except Exception:
            payload = {}
        message = payload.get("error") or exc.response.text or str(exc)
        raise RuntimeError(message) from exc
    except Exception as exc:
        raise RuntimeError(f"Claude CLI sandbox request failed: {exc}") from exc

    if payload and payload.get("ok") is False:
        message = payload.get("error") or payload.get("stderr") or "Claude CLI sandbox run failed"
        raise RuntimeError(message)

    # Trigger CLI job snapshot after successful execution (fire-and-forget)
    if (
        normalized_job_id
        and settings.enable_cli_job_snapshots
        and should_snapshot_cli_job(normalized_job_id, settings.cli_snapshot_min_interval_seconds)
    ):
        try:
            snapshot_cli_job_state.spawn(normalized_job_id)
        except Exception:
            _logger.debug(
                "Failed to spawn CLI job snapshot",
                exc_info=True,
                extra={"job_id": normalized_job_id},
            )

    if return_stdout:
        cli_stdout = (payload.get("stdout") or "").strip()
        cli_stderr = (payload.get("stderr") or "").strip()
        if cli_stdout or cli_stderr:
            return cli_stdout or cli_stderr
        return json.dumps(payload)

    return payload


# =============================================================================
# RALPH WIGGUM MODAL FUNCTION
# =============================================================================


@app.function(
    image=claude_cli_image,
    secrets=agent_sdk_secrets,
    timeout=86400,  # 24 hours
)
def run_ralph_remote(
    job_id: str,
    prd_json: str,
    workspace_source_json: str,
    prompt_template: str | None = None,
    max_iterations: int = 10,
    timeout_per_iteration: int = 300,
    allowed_tools: str = "Read,Write,Bash,Glob,Grep",
    feedback_commands: str = "",
    feedback_timeout: int = 120,
    auto_commit: bool = True,
    max_consecutive_failures: int = 3,
    resume_checkpoint_json: str | None = None,
) -> dict:
    """Run Ralph autonomous coding loop inside a dedicated Claude CLI sandbox.

    Args:
        job_id: Unique job identifier.
        prd_json: JSON-serialized PRD.
        workspace_source_json: JSON-serialized workspace source config.
        prompt_template: Custom prompt template.
        max_iterations: Maximum iterations.
        timeout_per_iteration: CLI timeout per iteration.
        allowed_tools: Comma-separated list of allowed CLI tools.
        feedback_commands: Comma-separated list of feedback commands.
        feedback_timeout: Timeout for feedback commands.
        auto_commit: Whether to auto-commit changes.
        max_consecutive_failures: Max failures before stopping.
        resume_checkpoint_json: JSON-serialized checkpoint for resuming a paused loop.
    """
    normalized_job_id = normalize_job_id(job_id)
    if not normalized_job_id:
        raise ValueError("job_id must be a valid UUID")

    env = claude_cli_env()
    require_claude_cli_auth(env)

    tools_list = [t.strip() for t in allowed_tools.split(",") if t.strip()]
    feedback_list = [c.strip() for c in feedback_commands.split(",") if c.strip()]

    # Build request body
    request_dict = {
        "job_id": normalized_job_id,
        "prd": json.loads(prd_json),
        "workspace_source": json.loads(workspace_source_json),
        "prompt_template": prompt_template,
        "max_iterations": max_iterations,
        "timeout_per_iteration": timeout_per_iteration,
        "allowed_tools": tools_list,
        "feedback_commands": feedback_list,
        "feedback_timeout": feedback_timeout,
        "auto_commit": auto_commit,
        "max_consecutive_failures": max_consecutive_failures,
    }

    # Add resume checkpoint if provided
    if resume_checkpoint_json:
        request_dict["resume_checkpoint"] = json.loads(resume_checkpoint_json)

    request_body = RalphExecuteRequest(**request_dict)

    estimated_runtime = max_iterations * timeout_per_iteration
    # Pass job_id for potential snapshot restoration
    sb, url = get_or_start_cli_sandbox(job_id=normalized_job_id)
    headers: dict[str, str] = {}
    settings = Settings()
    if settings.enforce_connect_token:
        creds = sb.create_connect_token(user_metadata={"job_id": normalized_job_id})
        headers = {"Authorization": f"Bearer {creds.token}"}

    try:
        timeout = httpx.Timeout(estimated_runtime + 300, connect=30.0)
        response = httpx.post(
            f"{url.rstrip('/')}/ralph/execute",
            json=request_body.model_dump(),
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        try:
            payload = exc.response.json()
        except Exception:
            payload = {}
        message = payload.get("error") or exc.response.text or str(exc)
        raise RuntimeError(message) from exc
    except Exception as exc:
        raise RuntimeError(f"Ralph sandbox request failed: {exc}") from exc

    # Trigger CLI job snapshot after Ralph execution (fire-and-forget)
    if settings.enable_cli_job_snapshots and should_snapshot_cli_job(
        normalized_job_id, settings.cli_snapshot_min_interval_seconds
    ):
        try:
            snapshot_cli_job_state.spawn(normalized_job_id)
        except Exception:
            _logger.debug(
                "Failed to spawn CLI job snapshot for Ralph",
                exc_info=True,
                extra={"job_id": normalized_job_id},
            )

    return payload


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


@app.function(image=agent_sdk_image, secrets=agent_sdk_secrets, timeout=300, **_retry_kwargs())
def snapshot_session_state(session_id: str) -> dict:
    """Capture sandbox filesystem state for a specific agent session.

    Creates a snapshot tied to a session_id, enabling session restoration when
    the user resumes a session after the sandbox has timed out. This enables
    "leave and come back" workflows where the agent's installed tools, downloaded
    files, and other filesystem state are preserved.

    Unlike snapshot_service() which creates a global snapshot, this function
    stores the snapshot reference keyed by session_id in SESSION_SNAPSHOTS,
    allowing per-session restoration.

    Args:
        session_id: The Claude Agent SDK session ID to associate with this snapshot.

    Returns:
        Dict with snapshot info:
        - ok: True if snapshot succeeded
        - session_id: The session ID this snapshot is associated with
        - image_id: Modal Image object_id for restoration
        - sandbox_name: Name of the sandbox that was snapshotted
        - created_at: Unix timestamp

    Usage:
        Called after agent query completes when session snapshots are enabled:
        ```python
        if should_snapshot_session(session_id, min_interval_seconds=60):
            result = snapshot_session_state.remote(session_id)
        ```

    Restoration:
        When resuming a session after sandbox timeout, use get_session_snapshot()
        to retrieve the image_id and create a new sandbox from it.

    See: https://modal.com/docs/guide/sandbox#filesystem-snapshots
    """
    if not session_id:
        return {"ok": False, "error": "session_id is required"}

    # Check if we should skip snapshotting (throttling)
    if not should_snapshot_session(session_id, _settings.snapshot_min_interval_seconds):
        existing = get_session_snapshot(session_id)
        return {
            "ok": True,
            "skipped": True,
            "reason": "Recent snapshot exists",
            "session_id": session_id,
            "existing_snapshot": existing,
        }

    try:
        sb, _ = get_or_start_background_sandbox()
        img = sb.snapshot_filesystem()
        snapshot_info = store_session_snapshot(
            session_id=session_id,
            image_id=img.object_id,
            sandbox_name=SANDBOX_NAME,
        )
        return {
            "ok": True,
            "session_id": session_id,
            "image_id": img.object_id,
            "sandbox_name": SANDBOX_NAME,
            "created_at": snapshot_info["created_at"],
        }
    except modal_exc.SandboxTerminatedError:
        return {
            "ok": False,
            "error": "Sandbox terminated, cannot snapshot",
            "type": "SandboxTerminatedError",
        }
    except modal_exc.Error as e:
        _logger.warning("Failed to snapshot session %s: %s", session_id, e)
        return {"ok": False, "error": str(e), "type": e.__class__.__name__}
    except Exception:
        _logger.exception("Unexpected error snapshotting session %s", session_id)
        return {"ok": False, "error": "Unexpected error", "type": "UnexpectedException"}


@app.function(image=claude_cli_image, secrets=agent_sdk_secrets, timeout=300, **_retry_kwargs())
def snapshot_cli_job_state(job_id: str) -> dict:
    """Capture CLI sandbox filesystem state for a specific job.

    Creates a snapshot tied to a job_id, enabling job state restoration when
    resuming a job after the CLI sandbox has timed out. This enables
    "leave and come back" workflows where the CLI's installed tools, downloaded
    files, and other filesystem state are preserved.

    Unlike snapshot_service() which creates a global snapshot, this function
    stores the snapshot reference keyed by job_id in CLI_JOB_SNAPSHOTS,
    allowing per-job restoration.

    Args:
        job_id: The CLI job ID (UUID) to associate with this snapshot.

    Returns:
        Dict with snapshot info:
        - ok: True if snapshot succeeded
        - job_id: The job ID this snapshot is associated with
        - image_id: Modal Image object_id for restoration
        - sandbox_name: Name of the sandbox that was snapshotted
        - created_at: Unix timestamp

    Usage:
        Called after CLI job completes when job snapshots are enabled:
        ```python
        if should_snapshot_cli_job(job_id, min_interval_seconds=60):
            result = snapshot_cli_job_state.spawn(job_id)
        ```

    Restoration:
        When resuming a job after sandbox timeout, use get_cli_job_snapshot()
        to retrieve the image_id and create a new sandbox from it.

    See: https://modal.com/docs/guide/sandbox#filesystem-snapshots
    """
    if not job_id:
        return {"ok": False, "error": "job_id is required"}

    # Check if we should skip snapshotting (throttling)
    if not should_snapshot_cli_job(job_id, _settings.cli_snapshot_min_interval_seconds):
        existing = get_cli_job_snapshot(job_id)
        return {
            "ok": True,
            "skipped": True,
            "reason": "Recent snapshot exists",
            "job_id": job_id,
            "existing_snapshot": existing,
        }

    try:
        sb, _ = get_or_start_cli_sandbox()
        img = sb.snapshot_filesystem()
        snapshot_info = store_cli_job_snapshot(
            job_id=job_id,
            image_id=img.object_id,
            sandbox_name=CLI_SANDBOX_NAME,
        )
        return {
            "ok": True,
            "job_id": job_id,
            "image_id": img.object_id,
            "sandbox_name": CLI_SANDBOX_NAME,
            "created_at": snapshot_info["created_at"],
        }
    except modal_exc.SandboxTerminatedError:
        return {
            "ok": False,
            "error": "CLI sandbox terminated, cannot snapshot",
            "type": "SandboxTerminatedError",
        }
    except modal_exc.Error as e:
        _logger.warning("Failed to snapshot CLI job %s: %s", job_id, e)
        return {"ok": False, "error": str(e), "type": e.__class__.__name__}
    except Exception:
        _logger.exception("Unexpected error snapshotting CLI job %s", job_id)
        return {"ok": False, "error": "Unexpected error", "type": "UnexpectedException"}


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
