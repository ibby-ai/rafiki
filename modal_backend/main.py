"""
Entry-point and Modal function definitions for running the agent in a sandboxed
environment and exposing lightweight HTTP endpoints.

Quickstart (CLI):
- run local_entrypoint: `modal run -m modal_backend.main` (runs the agent once in a short-lived Modal function)
- run run_agent_remote: `modal run -m modal_backend.main::run_agent_remote --question "..."`
- keep dev deployment running: `modal serve -m modal_backend.main`
- deploy to production: `modal deploy -m modal_backend.deploy`

Notes for future maintainers:
- This file defines a `modal.App` plus several `@app.function` entries. Functions
  annotated with `@modal.asgi_app` are exposed as HTTP endpoints when the
  app is served or deployed. See Modal docs for `modal.App`, `@app.function`,
  `modal.Sandbox`, and `@modal.asgi_app` for lifecycle and limits.
- We use a long-running `modal.Sandbox` process to host a FastAPI microservice
  (`modal_backend.api.controller`) and then call into it from a short-lived Modal
  function. This pattern keeps cold-start latency low for the model runtime
  while allowing us to keep the HTTP frontdoor responsive.

Prerequisite for curl testing:
- Start the dev server locally with `modal serve -m modal_backend.main` so the HTTP endpoint
  (see `http_app`) is reachable at a dev URL like
  `https://<org>--modal-backend-http-app-dev.modal.run`.
"""

import hashlib
import inspect
import json
import logging
import mimetypes
import re
import secrets as pysecrets
import time
import time as _time
import urllib.error
import urllib.request
from pathlib import Path
from threading import Lock
from typing import Literal
from urllib.parse import quote

import anyio
import httpx
import modal
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from modal import exception as modal_exc
from starlette.responses import StreamingResponse

from modal_backend.instructions.prompts import DEFAULT_QUESTION
from modal_backend.jobs import (
    JOB_QUEUE,
    DuplicateJobIdError,
    InvalidJobIdError,
    # Multiplayer session functions
    authorize_session_user,
    bump_attempts,
    cancel_job,
    cancel_session,
    claim_prewarm,
    claim_warm_sandbox,
    cleanup_stale_pool_entries,
    create_session_metadata,
    enqueue_job,
    generate_pool_sandbox_name,
    generate_warm_id,
    get_cancellation_status,
    # Image version tracking functions
    get_current_image_version,
    get_expired_pool_entries,
    get_image_deployed_at,
    get_job_record,
    get_job_status,
    get_multiplayer_status,
    get_prewarm,
    get_prewarm_status,
    get_session_cancellation,
    get_session_history,
    get_session_message_count,
    get_session_metadata,
    get_session_snapshot,
    get_session_users,
    get_stats,
    get_warm_pool_entries,
    get_warm_pool_status,
    # Workspace retention functions
    get_workspace_retention_status,
    is_job_due,
    job_workspace_root,
    list_workspaces_for_cleanup,
    mark_prewarm_failed,
    mark_workspace_deleted,
    normalize_job_id,
    register_prewarm,
    register_warm_sandbox,
    remove_from_pool,
    resolve_job_artifact,
    revoke_session_user,
    set_image_version,
    should_skip_job,
    should_snapshot_session,
    store_session_snapshot,
    update_job,
    update_prewarm_ready,
)
from modal_backend.models import (
    ArtifactListResponse,
    JobStatusResponse,
    JobSubmitRequest,
    JobSubmitResponse,
    # Multiplayer session schemas
    MessageHistoryEntry,
    MultiplayerStatusResponse,
    QueryBody,
    ScheduleCreateRequest,
    ScheduleDeleteResponse,
    ScheduleListResponse,
    ScheduleResponse,
    ScheduleUpdateRequest,
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
    # Workspace retention schemas
    WorkspaceCleanupRequest,
    WorkspaceCleanupResponse,
    WorkspaceDeleteResponse,
    WorkspaceRetentionStatusResponse,
)
from modal_backend.models.jobs import ArtifactEntry, ArtifactManifest
from modal_backend.platform_services.webhooks import (
    build_headers,
    build_webhook_payload,
    serialize_payload,
)
from modal_backend.schedules import (
    InvalidScheduleIdError,
    ScheduleError,
    ScheduleNotFoundError,
    create_schedule,
    delete_schedule,
    dispatch_due_schedules,
    get_schedule,
    list_schedules,
    normalize_schedule_id,
    update_schedule,
)
from modal_backend.security.artifact_access import (
    ArtifactTokenError,
    verify_artifact_access_token,
)
from modal_backend.security.cloudflare_auth import (
    SANDBOX_SESSION_AUTH_HEADER,
    build_scoped_sandbox_token,
    internal_auth_middleware,
)
from modal_backend.settings.settings import Settings, get_modal_secrets

# =============================================================================
# Image Version Tracking
# =============================================================================
# Generate unique version ID at module load time (i.e., on each deploy).
# This is used to track which image version sandboxes are running and
# invalidate warm pools when a new image is deployed.

_DEPLOY_TIMESTAMP = _time.time()
_IMAGE_VERSION_ID = hashlib.sha256(f"{_DEPLOY_TIMESTAMP}:{modal.__version__}".encode()).hexdigest()[
    :12
]

app = modal.App("modal-backend")
_settings = Settings()
_logger = logging.getLogger(__name__)
_SANDBOX_SESSION_SECRET_CACHE: dict[str, str] = {}
_SANDBOX_SESSION_SECRET_CACHE_MAX_ENTRIES = 64

web_app = FastAPI()
web_app.middleware("http")(internal_auth_middleware)

web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _generate_sandbox_session_secret() -> str:
    # 48 random bytes (~64 base64 chars) keeps HMAC key entropy high while remaining env-safe.
    return pysecrets.token_urlsafe(48)


def _sandbox_runtime_env(sandbox_session_secret: str) -> dict[str, str]:
    return {
        # Sandbox runtime no longer requires shared internal signing secret.
        "REQUIRE_INTERNAL_AUTH_SECRET": "false",
        # Enforce scoped gateway->sandbox auth token verification in controller middleware.
        "SANDBOX_SESSION_SECRET": sandbox_session_secret,
        "SANDBOX_SESSION_TOKEN_TTL_SECONDS": str(
            max(1, _settings.sandbox_session_token_ttl_seconds)
        ),
        # Runtime hardening defaults (Task 03).
        "SANDBOX_DROP_PRIVILEGES": "true",
        "SANDBOX_RUNTIME_UID": "65534",
        "SANDBOX_RUNTIME_GID": "65534",
        "SANDBOX_WRITABLE_ROOTS": f"{_settings.agent_fs_root},/tmp",
    }


def _remember_sandbox_session_secret(*, sandbox_id: str | None, secret: str | None) -> None:
    normalized_id = (sandbox_id or "").strip()
    normalized_secret = (secret or "").strip()
    if normalized_id and normalized_secret:
        _SANDBOX_SESSION_SECRET_CACHE[normalized_id] = normalized_secret
        while len(_SANDBOX_SESSION_SECRET_CACHE) > _SANDBOX_SESSION_SECRET_CACHE_MAX_ENTRIES:
            oldest_key = next(iter(_SANDBOX_SESSION_SECRET_CACHE))
            if oldest_key == normalized_id:
                break
            _SANDBOX_SESSION_SECRET_CACHE.pop(oldest_key, None)


def _forget_sandbox_session_secret(*, sandbox_id: str | None) -> None:
    normalized_id = (sandbox_id or "").strip()
    if normalized_id:
        _SANDBOX_SESSION_SECRET_CACHE.pop(normalized_id, None)


def _lookup_sandbox_session_secret(
    *,
    sandbox_id: str | None,
    prewarm_claimed: dict | None = None,
) -> str | None:
    if prewarm_claimed:
        claimed_sandbox_id = str(prewarm_claimed.get("sandbox_id") or "").strip()
        if not sandbox_id or not claimed_sandbox_id or claimed_sandbox_id == sandbox_id:
            value = prewarm_claimed.get("sandbox_session_secret")
            if isinstance(value, str) and value.strip():
                normalized = value.strip()
                _remember_sandbox_session_secret(
                    sandbox_id=sandbox_id or claimed_sandbox_id,
                    secret=normalized,
                )
                return normalized

    if sandbox_id:
        cached_value = _SANDBOX_SESSION_SECRET_CACHE.get(sandbox_id)
        if isinstance(cached_value, str) and cached_value.strip():
            return cached_value.strip()

        try:
            metadata = SESSIONS.get(SANDBOX_NAME)
        except Exception:
            metadata = None
        if isinstance(metadata, dict) and metadata.get("id") == sandbox_id:
            value = metadata.get("sandbox_session_secret")
            if isinstance(value, str) and value.strip():
                normalized = value.strip()
                _remember_sandbox_session_secret(sandbox_id=sandbox_id, secret=normalized)
                return normalized

        try:
            for entry in get_warm_pool_entries():
                if entry.get("sandbox_id") == sandbox_id:
                    value = entry.get("sandbox_session_secret")
                    if isinstance(value, str) and value.strip():
                        normalized = value.strip()
                        _remember_sandbox_session_secret(sandbox_id=sandbox_id, secret=normalized)
                        return normalized
                    break
        except Exception:
            pass
    return None


def _resolve_sandbox_session_secret(
    *,
    sandbox_id: str | None,
    secret: str | None,
) -> str | None:
    """Resolve a scoped sandbox secret while preserving existing metadata when possible."""
    normalized = (secret or "").strip()
    if normalized:
        _remember_sandbox_session_secret(sandbox_id=sandbox_id, secret=normalized)
        return normalized

    normalized_id = (sandbox_id or "").strip()
    if not normalized_id:
        return None

    try:
        metadata = SESSIONS.get(SANDBOX_NAME)
    except Exception:
        metadata = None

    if isinstance(metadata, dict) and str(metadata.get("id") or "").strip() == normalized_id:
        existing = str(metadata.get("sandbox_session_secret") or "").strip()
        if existing:
            _remember_sandbox_session_secret(sandbox_id=normalized_id, secret=existing)
            return existing

    return None


def _add_sandbox_auth_header(
    *,
    headers: dict[str, str],
    request_path: str,
    sandbox_id: str | None,
    session_id: str | None,
    prewarm_claimed: dict | None = None,
) -> None:
    secret = _lookup_sandbox_session_secret(
        sandbox_id=sandbox_id,
        prewarm_claimed=prewarm_claimed,
    )
    if secret:
        headers[SANDBOX_SESSION_AUTH_HEADER] = build_scoped_sandbox_token(
            secret,
            session_id=session_id,
            sandbox_id=sandbox_id,
            request_path=request_path,
            ttl_ms=max(1, _settings.sandbox_session_token_ttl_seconds) * 1000,
        )
        if sandbox_id:
            headers["X-Sandbox-Id"] = sandbox_id
        return

    missing_scope = f" for sandbox '{sandbox_id}'" if sandbox_id else " for current request"
    raise HTTPException(
        status_code=503,
        detail=f"Missing scoped sandbox auth secret{missing_scope}.",
    )


def _require_history_authority_header(request: Request) -> None:
    authority = (request.headers.get("X-Session-History-Authority") or "").strip()
    if authority and authority != "durable-object":
        raise HTTPException(status_code=409, detail="Unsupported session history authority")


def _normalize_query_upstream_error(raw_body: str) -> dict[str, object]:
    payload = {"ok": False, "error": "Background sandbox request failed"}
    text = raw_body.strip()
    if not text:
        return payload

    def _from_json_value(value: object) -> dict[str, object]:
        if isinstance(value, str):
            msg = value.strip()
            if msg:
                return {"ok": False, "error": msg}
            return payload

        if isinstance(value, list):
            return {
                "ok": False,
                "error": "Background sandbox validation error",
                "detail": value,
            }

        if not isinstance(value, dict):
            return payload

        detail = value.get("detail")

        nested_dict: dict[str, object] | None = None
        if isinstance(detail, str):
            nested_raw = detail.strip()
            if nested_raw:
                try:
                    nested = json.loads(nested_raw)
                except Exception:
                    nested = None
                if isinstance(nested, dict):
                    nested_dict = nested

        source = nested_dict or value
        request_id = source.get("request_id") or value.get("request_id")
        error_type = source.get("error_type") or value.get("error_type")
        error_value = source.get("error")
        message_value = source.get("message")
        detail_value = source.get("detail")

        if isinstance(error_value, str) and error_value.strip():
            message = error_value.strip()
        elif isinstance(message_value, str) and message_value.strip():
            message = message_value.strip()
        elif isinstance(detail_value, str) and detail_value.strip():
            message = detail_value.strip()
        elif isinstance(detail_value, list):
            message = "Background sandbox validation error"
        elif isinstance(detail, str) and detail.strip():
            message = detail.strip()
        elif isinstance(detail, list):
            message = "Background sandbox validation error"
        else:
            message = payload["error"]

        normalized: dict[str, object] = {"ok": False, "error": message}
        if isinstance(request_id, str) and request_id.strip():
            normalized["request_id"] = request_id.strip()
        if isinstance(error_type, str) and error_type.strip():
            normalized["error_type"] = error_type.strip()
        if isinstance(detail_value, list):
            normalized["detail"] = detail_value
        elif isinstance(detail, list):
            normalized["detail"] = detail
        return normalized

    try:
        parsed = json.loads(text)
    except Exception:
        return {"ok": False, "error": text}
    return _from_json_value(parsed)


ARTIFACT_ACCESS_HEADER = "X-Artifact-Access-Token"


def _verify_artifact_access_token(request: Request, *, job_id: str, artifact_path: str) -> dict:
    if not _settings.require_artifact_access_token:
        return {}

    raw_token = (request.headers.get(ARTIFACT_ACCESS_HEADER) or "").strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="Missing artifact access token")

    secret = (_settings.internal_auth_secret or "").strip()
    if not secret:
        raise HTTPException(status_code=500, detail="Internal auth secret not configured")
    requested_session = (request.headers.get("X-Session-Id") or "").strip()
    try:
        return verify_artifact_access_token(
            raw_token,
            secret=secret,
            expected_job_id=job_id,
            expected_artifact_path=artifact_path,
            expected_session_id=requested_session or None,
            max_ttl_seconds=_settings.artifact_access_token_max_ttl_seconds,
            is_revoked=lambda token_id: bool(ARTIFACT_ACCESS_REVOKED.get(token_id)),
        )
    except ArtifactTokenError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _enforce_job_actor_scope(request: Request, status: JobStatusResponse) -> None:
    """Enforce optional actor-scope headers against resolved job status."""
    session_id = (request.headers.get("X-Session-Id") or "").strip()
    if session_id and status.session_id and status.session_id != session_id:
        raise HTTPException(status_code=403, detail="Job session mismatch")

    user_id = (request.headers.get("X-User-Id") or "").strip()
    if user_id and status.user_id and status.user_id != user_id:
        raise HTTPException(status_code=403, detail="Job user mismatch")

    tenant_id = (request.headers.get("X-Tenant-Id") or "").strip()
    if tenant_id and status.tenant_id and status.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Job tenant mismatch")


def _base_openai_agents_image() -> modal.Image:
    """Build a base image with Python, FastAPI, uvicorn, httpx and openai-agents."""
    return (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install(
            "openai-agents==0.9.2",
            "fastapi",
            "uvicorn",
            "httpx",
            "langsmith[openai-agents]>=0.3.15",
        )
        .pip_install("uv")
        .env(
            {
                "AGENT_FS_ROOT": "/data",
                "PATH": "/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            }
        )
        .workdir("/root/app")
        .add_local_file("pyproject.toml", remote_path="/root/app/pyproject.toml", copy=True)
        .add_local_dir("modal_backend", remote_path="/root/app/modal_backend", copy=True)
        .run_commands("cd /root/app && uv pip install -e . --system --no-cache")
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
    """Return a stable call identifier for Modal function calls.

    Validates that the returned ID is a real value and not a placeholder
    like "null", "none", or empty string.
    """
    for attr in ("object_id", "call_id", "id"):
        value = getattr(call, attr, None)
        if value:
            str_val = str(value)
            # Filter out invalid/placeholder values
            lower_val = str_val.lower()
            if lower_val in ("null", "none", ""):
                continue
            # Also reject values that look like null representations
            if str_val.startswith("nul") and len(str_val) <= 5:
                continue
            return str_val
    return None


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


def _schedule_dispatcher_schedule() -> modal.Cron:
    return modal.Cron(_settings.schedule_cron)


def _get_persist_volume() -> modal.Volume:
    """Return the configured persistent volume handle."""
    kwargs: dict[str, object] = {"create_if_missing": True}
    if _settings.persist_vol_version is not None:
        kwargs["version"] = _settings.persist_vol_version
    return modal.Volume.from_name(_settings.persist_vol_name, **kwargs)


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


def _normalize_schedule_id_or_400(schedule_id: str) -> str:
    normalized = normalize_schedule_id(schedule_id)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid schedule_id")
    return normalized


def _request_actor_context(request: Request) -> tuple[str | None, str | None]:
    """Extract user/tenant scope from Cloudflare headers."""
    user_id = request.headers.get("X-User-Id") or None
    tenant_id = request.headers.get("X-Tenant-Id") or None
    return user_id, tenant_id


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
            - models: Sorted list of unique model IDs used (e.g., ["gpt-4.1"])

    Metric Sources:
        From summary dict (provided by the OpenAI Agents runtime):
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
                    "model": "gpt-4.1",
                    "content": [{"type": "tool_use", "name": "calculate"}]
                },
                {
                    "model": "gpt-4.1",
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
            "models": ["gpt-4.1"]
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
agent_sdk_image = _base_openai_agents_image()
function_runtime_secrets = get_modal_secrets(surface="function")
sandbox_runtime_secrets = get_modal_secrets(surface="sandbox")


def _http_app_volumes() -> dict[str, modal.Volume]:
    """Mount agent volume for HTTP endpoints that access artifacts."""
    return {_settings.agent_fs_root: _get_persist_volume()}


@app.function(
    image=agent_sdk_image,
    secrets=function_runtime_secrets,
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
    """Internal-only ASGI app exposing HTTP endpoints for the agent service."""
    return web_app


@web_app.get("/health")
async def health():
    """Health check endpoint."""
    return {"ok": True}


@web_app.post("/query")
async def query_proxy(request: Request, body: QueryBody):
    """Proxy query requests to the background sandbox service."""
    settings = Settings()
    _require_history_authority_header(request)

    # Resolve session_id for snapshot restoration (if resuming a session)
    resolved_session_id = body.session_id

    # Check for pre-warmed sandbox (from POST /warm)
    prewarm_claimed = None
    if body.warm_id and settings.enable_prewarm:
        prewarm_claimed = claim_prewarm(body.warm_id, resolved_session_id or "anonymous")
        if prewarm_claimed and prewarm_claimed.get("claimed"):
            _logger.info(
                "Query using pre-warmed sandbox",
                extra={
                    "warm_id": body.warm_id,
                    "sandbox_id": prewarm_claimed.get("sandbox_id"),
                    "prewarm_status": prewarm_claimed.get("status"),
                },
            )
        elif prewarm_claimed:
            # Claim failed - log reason and fall back to new sandbox
            _logger.info(
                "Pre-warm claim failed, falling back to new sandbox",
                extra={
                    "warm_id": body.warm_id,
                    "reason": prewarm_claimed.get("reason"),
                    "failure_reason": prewarm_claimed.get("failure_reason"),
                },
            )
            prewarm_claimed = None  # Clear so we don't try to use it

    sb = None
    url = None
    if prewarm_claimed and prewarm_claimed.get("sandbox_id") and prewarm_claimed.get("sandbox_url"):
        try:
            sb = modal.Sandbox.from_id(prewarm_claimed["sandbox_id"])
            url = prewarm_claimed["sandbox_url"]
            await _wait_for_service_or_raise_readiness_timeout_aio(
                sandbox=sb,
                service_url=url,
                timeout_seconds=max(int(settings.service_timeout), 1),
                phase="prewarm_claim_query",
                startup_attempt=1,
                recycle_allowed=True,
            )
        except _SandboxReadinessTimeoutError as timeout_exc:
            await _handle_readiness_timeout_async(timeout_exc)
            if body.warm_id:
                mark_prewarm_failed(body.warm_id, f"Readiness timeout: {timeout_exc}")
            sb = None
            url = None
            prewarm_claimed = None
        except Exception:
            if body.warm_id:
                mark_prewarm_failed(body.warm_id, "Pre-warm sandbox failed readiness probe")
            sb = None
            url = None
            prewarm_claimed = None

    # Use async getter with session_id for potential snapshot restoration
    if sb is None or url is None:
        sb, url = await get_or_start_background_sandbox_aio(session_id=resolved_session_id)

    # Optional: per-request connect token (verified in sandbox service)
    headers: dict[str, str] = {}
    if settings.enforce_connect_token:
        creds = await sb.create_connect_token.aio(
            user_metadata={"ip": request.client.host or "unknown"}
        )
        headers["Authorization"] = f"Bearer {creds.token}"

    _add_sandbox_auth_header(
        headers=headers,
        request_path="/query",
        sandbox_id=sb.object_id if sb else None,
        session_id=resolved_session_id,
        prewarm_claimed=prewarm_claimed,
    )

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        r = await client.post(
            f"{url.rstrip('/')}/query",
            json=body.model_dump(),
            headers=headers,
            timeout=httpx.Timeout(120.0, connect=30.0),
        )
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error_payload = _normalize_query_upstream_error(exc.response.text or "")
            _logger.error(
                "Background sandbox /query failed",
                extra={
                    "status_code": exc.response.status_code,
                    "error": error_payload.get("error"),
                    "request_id": error_payload.get("request_id"),
                    "error_type": error_payload.get("error_type"),
                },
            )
            return JSONResponse(
                status_code=exc.response.status_code,
                content=error_payload,
            )
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
                snapshot_session_state.spawn(result_session_id, sb.object_id)
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
    _require_history_authority_header(request)

    # Resolve session_id for snapshot restoration (if resuming a session)
    resolved_session_id = body.session_id

    # Check for pre-warmed sandbox (from POST /warm)
    prewarm_claimed = None
    if body.warm_id and settings.enable_prewarm:
        prewarm_claimed = claim_prewarm(body.warm_id, resolved_session_id or "anonymous")
        if prewarm_claimed and prewarm_claimed.get("claimed"):
            _logger.info(
                "Query stream using pre-warmed sandbox",
                extra={
                    "warm_id": body.warm_id,
                    "sandbox_id": prewarm_claimed.get("sandbox_id"),
                    "prewarm_status": prewarm_claimed.get("status"),
                },
            )
        elif prewarm_claimed:
            # Claim failed - log reason and fall back to new sandbox
            _logger.info(
                "Pre-warm claim failed for stream, falling back to new sandbox",
                extra={
                    "warm_id": body.warm_id,
                    "reason": prewarm_claimed.get("reason"),
                    "failure_reason": prewarm_claimed.get("failure_reason"),
                },
            )
            prewarm_claimed = None  # Clear so we don't try to use it

    sb = None
    url = None
    if prewarm_claimed and prewarm_claimed.get("sandbox_id") and prewarm_claimed.get("sandbox_url"):
        try:
            sb = modal.Sandbox.from_id(prewarm_claimed["sandbox_id"])
            url = prewarm_claimed["sandbox_url"]
            await _wait_for_service_or_raise_readiness_timeout_aio(
                sandbox=sb,
                service_url=url,
                timeout_seconds=max(int(settings.service_timeout), 1),
                phase="prewarm_claim_query_stream",
                startup_attempt=1,
                recycle_allowed=True,
            )
        except _SandboxReadinessTimeoutError as timeout_exc:
            await _handle_readiness_timeout_async(timeout_exc)
            if body.warm_id:
                mark_prewarm_failed(body.warm_id, f"Readiness timeout: {timeout_exc}")
            sb = None
            url = None
            prewarm_claimed = None
        except Exception:
            if body.warm_id:
                mark_prewarm_failed(body.warm_id, "Pre-warm sandbox failed readiness probe")
            sb = None
            url = None
            prewarm_claimed = None

    # Use async getter with session_id for potential snapshot restoration
    if sb is None or url is None:
        sb, url = await get_or_start_background_sandbox_aio(session_id=resolved_session_id)

    headers: dict[str, str] = {}
    if settings.enforce_connect_token:
        creds = await sb.create_connect_token.aio(
            user_metadata={"ip": request.client.host or "unknown"}
        )
        headers["Authorization"] = f"Bearer {creds.token}"

    _add_sandbox_auth_header(
        headers=headers,
        request_path="/query_stream",
        sandbox_id=sb.object_id if sb else None,
        session_id=resolved_session_id,
        prewarm_claimed=prewarm_claimed,
    )

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
                    snapshot_session_state.spawn(captured_session_id, sb.object_id)
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


@web_app.post("/submit", response_model=JobSubmitResponse)
async def submit_job(body: JobSubmitRequest) -> JobSubmitResponse:
    """Enqueue a background job and return its id."""
    try:
        job_id = enqueue_job(
            body.question,
            job_id=body.job_id,
            tenant_id=body.tenant_id,
            user_id=body.user_id,
            schedule_at=body.schedule_at,
            webhook=body.webhook,
            metadata=body.metadata,
        )
    except InvalidJobIdError as exc:
        raise HTTPException(status_code=400, detail="Invalid job_id") from exc
    except DuplicateJobIdError as exc:
        raise HTTPException(status_code=409, detail="Job already exists") from exc
    return JobSubmitResponse(job_id=job_id)


@web_app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def job_status(request: Request, job_id: str) -> JobStatusResponse:
    """Fetch job status and result (if available)."""
    job_id = _normalize_job_id_or_400(job_id)
    status = get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    _enforce_job_actor_scope(request, status)
    return status


@web_app.get("/jobs/{job_id}/artifacts", response_model=ArtifactListResponse)
async def job_artifacts(request: Request, job_id: str) -> ArtifactListResponse:
    """List artifacts generated by a job."""
    job_id = _normalize_job_id_or_400(job_id)
    status = get_job_status(job_id)
    _reload_persist_volume()
    if not status:
        manifest = _build_artifact_manifest(job_id)
        if not manifest.files:
            raise HTTPException(status_code=404, detail="Job not found")
        return ArtifactListResponse(job_id=job_id, artifacts=manifest)
    _enforce_job_actor_scope(request, status)
    manifest = status.artifacts or _build_artifact_manifest(job_id)
    return ArtifactListResponse(job_id=job_id, artifacts=manifest)


@web_app.get("/jobs/{job_id}/artifacts/{artifact_path:path}")
async def download_job_artifact(request: Request, job_id: str, artifact_path: str):
    """Download a specific job artifact."""
    job_id = _normalize_job_id_or_400(job_id)
    _verify_artifact_access_token(request, job_id=job_id, artifact_path=artifact_path)
    status = get_job_status(job_id)
    if status:
        _enforce_job_actor_scope(request, status)
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


@web_app.post("/internal/artifact_tokens/{token_id}/revoke")
async def revoke_artifact_token(token_id: str, reason: str | None = None) -> dict[str, object]:
    """Revoke a scoped artifact access token by token_id."""
    normalized = token_id.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="token_id is required")
    ARTIFACT_ACCESS_REVOKED[normalized] = {
        "revoked_at": int(time.time()),
        "reason": reason or "manual",
    }
    return {"ok": True, "token_id": normalized, "revoked": True}


@web_app.delete("/jobs/{job_id}", response_model=JobStatusResponse)
async def cancel_job_request(job_id: str) -> JobStatusResponse:
    """Cancel a queued job."""
    job_id = _normalize_job_id_or_400(job_id)
    status = cancel_job(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    return status


@web_app.post("/schedules", response_model=ScheduleResponse)
async def create_schedule_request(
    body: ScheduleCreateRequest, request: Request
) -> ScheduleResponse:
    """Create a new one-off or recurring schedule."""
    user_id, tenant_id = _request_actor_context(request)
    try:
        schedule = create_schedule(body, user_id=user_id, tenant_id=tenant_id)
    except ScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScheduleResponse(**schedule)


@web_app.get("/schedules", response_model=ScheduleListResponse)
async def list_schedules_request(
    request: Request,
    enabled: bool | None = None,
    schedule_type: Literal["one_off", "cron"] | None = None,
) -> ScheduleListResponse:
    """List schedules for the authenticated actor scope."""
    user_id, tenant_id = _request_actor_context(request)
    schedules = list_schedules(
        user_id=user_id,
        tenant_id=tenant_id,
        enabled=enabled,
        schedule_type=schedule_type,
    )
    return ScheduleListResponse(schedules=[ScheduleResponse(**item) for item in schedules])


@web_app.post("/schedules/dispatch")
async def dispatch_schedules_request() -> dict[str, int]:
    """Dispatch due schedules on demand (safe for dev E2E while using modal serve)."""
    result = dispatch_due_schedules()
    _logger.info("schedule.dispatch.manual", extra=result)
    return result


@web_app.get("/schedules/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule_request(schedule_id: str, request: Request) -> ScheduleResponse:
    """Fetch a single schedule."""
    schedule_id = _normalize_schedule_id_or_400(schedule_id)
    user_id, tenant_id = _request_actor_context(request)
    schedule = get_schedule(schedule_id, user_id=user_id, tenant_id=tenant_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return ScheduleResponse(**schedule)


@web_app.patch("/schedules/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule_request(
    schedule_id: str, body: ScheduleUpdateRequest, request: Request
) -> ScheduleResponse:
    """Update an existing schedule."""
    schedule_id = _normalize_schedule_id_or_400(schedule_id)
    user_id, tenant_id = _request_actor_context(request)
    try:
        schedule = update_schedule(schedule_id, body, user_id=user_id, tenant_id=tenant_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc
    except (InvalidScheduleIdError, ScheduleError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScheduleResponse(**schedule)


@web_app.delete("/schedules/{schedule_id}", response_model=ScheduleDeleteResponse)
async def delete_schedule_request(schedule_id: str, request: Request) -> ScheduleDeleteResponse:
    """Delete an existing schedule."""
    schedule_id = _normalize_schedule_id_or_400(schedule_id)
    user_id, tenant_id = _request_actor_context(request)
    deleted = delete_schedule(schedule_id, user_id=user_id, tenant_id=tenant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return ScheduleDeleteResponse(schedule_id=schedule_id, deleted=True)


# =============================================================================
# WORKSPACE RETENTION ENDPOINTS
# =============================================================================


def _delete_job_workspace(job_id: str, fs_root: str) -> tuple[bool, int]:
    """Delete a job workspace directory and return (deleted, bytes_freed).

    Deletes the workspace directory and all its contents. Does not raise
    exceptions on failure; returns (False, 0) if deletion fails.

    Args:
        job_id: Validated UUID job identifier
        fs_root: Root filesystem path (e.g., /data or /data-cli)

    Returns:
        Tuple of (was_deleted, bytes_freed)
    """
    import shutil

    workspace = job_workspace_root(fs_root, job_id)
    if not workspace.exists():
        return False, 0

    # Calculate size before deletion
    bytes_freed = 0
    try:
        for path in workspace.rglob("*"):
            if path.is_file():
                bytes_freed += path.stat().st_size
    except Exception:
        pass

    # Delete the workspace directory
    try:
        shutil.rmtree(str(workspace))
        mark_workspace_deleted(job_id)
        return True, bytes_freed
    except Exception as e:
        _logger.warning("Failed to delete workspace for %s: %s", job_id, e)
        return False, 0


def _cleanup_expired_workspaces(
    older_than_days: int | None = None,
    status_filter: list[str] | None = None,
    dry_run: bool = False,
) -> WorkspaceCleanupResponse:
    """Delete workspaces older than retention period.

    Scans tracked workspaces and deletes those that have exceeded retention.
    Returns cleanup statistics.

    Args:
        older_than_days: Override retention days (None = use settings)
        status_filter: Only clean up jobs with these statuses
        dry_run: If True, report what would be deleted without deleting

    Returns:
        WorkspaceCleanupResponse with cleanup statistics
    """
    # Calculate cutoff timestamp
    now = int(time.time())
    if older_than_days is not None:
        cutoff = now - (older_than_days * 86400)
    else:
        cutoff = None

    eligible = list_workspaces_for_cleanup(
        before_timestamp=cutoff,
        status_filter=status_filter,
    )

    response = WorkspaceCleanupResponse(
        ok=True,
        dry_run=dry_run,
        workspaces_checked=len(eligible),
        workspaces_deleted=0,
        bytes_freed=0,
        deleted_job_ids=[],
        errors=[],
    )

    for entry in eligible:
        job_id = entry.get("job_id")
        if not job_id:
            continue

        workspace_root = entry.get("workspace_root")
        if not workspace_root:
            continue

        if dry_run:
            # Count what would be deleted
            response.workspaces_deleted += 1
            response.bytes_freed += entry.get("size_bytes", 0) or 0
            response.deleted_job_ids.append(job_id)
        else:
            # Actually delete
            deleted, freed = _delete_job_workspace(job_id, _settings.agent_fs_root)
            if deleted:
                response.workspaces_deleted += 1
                response.bytes_freed += freed
                response.deleted_job_ids.append(job_id)
            else:
                response.errors.append(f"Failed to delete workspace for {job_id}")

    return response


@web_app.delete("/jobs/{job_id}/workspace", response_model=WorkspaceDeleteResponse)
async def delete_job_workspace_endpoint(job_id: str) -> WorkspaceDeleteResponse:
    """Delete a specific job's workspace directory.

    Permanently deletes the job's workspace directory and all artifacts.
    The job metadata in the job store is preserved.

    Args:
        job_id: UUID of the job whose workspace should be deleted

    Returns:
        WorkspaceDeleteResponse with deletion status and bytes freed
    """
    job_id = _normalize_job_id_or_400(job_id)
    deleted, bytes_freed = _delete_job_workspace(job_id, _settings.agent_fs_root)

    return WorkspaceDeleteResponse(
        ok=True,
        job_id=job_id,
        deleted=deleted,
        bytes_freed=bytes_freed,
    )


@web_app.get("/workspace/retention/status", response_model=WorkspaceRetentionStatusResponse)
async def workspace_retention_status_endpoint() -> WorkspaceRetentionStatusResponse:
    """Get workspace retention statistics.

    Returns an overview of workspace retention settings and current state,
    including counts, total size, and age statistics.

    Returns:
        WorkspaceRetentionStatusResponse with retention statistics
    """
    status = get_workspace_retention_status()
    return WorkspaceRetentionStatusResponse(**status)


@web_app.post("/workspace/cleanup", response_model=WorkspaceCleanupResponse)
async def workspace_cleanup_endpoint(
    body: WorkspaceCleanupRequest,
) -> WorkspaceCleanupResponse:
    """Trigger manual workspace cleanup.

    Deletes workspaces that have exceeded their retention period based on
    job status. Failed jobs have longer retention than completed jobs.

    Supports dry-run mode to preview what would be deleted without
    actually deleting anything.

    Args:
        body: Cleanup request with optional filters and dry_run flag

    Returns:
        WorkspaceCleanupResponse with cleanup statistics
    """
    return _cleanup_expired_workspaces(
        older_than_days=body.older_than_days,
        status_filter=body.status_filter,
        dry_run=body.dry_run,
    )


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
        curl 'https://<org>--modal-backend-http-app-dev.modal.run/stats?period_hours=48'
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
        curl 'https://<org>--modal-backend-http-app-dev.modal.run/pool/status'
        ```
    """
    if not _settings.enable_warm_pool:
        return {
            "ok": True,
            "enabled": False,
            "message": "Warm pool is disabled",
        }

    pool_status = get_warm_pool_status()
    entries = pool_status.get("entries", [])
    missing_scoped_secret_count = 0
    for entry in entries:
        secret = entry.get("sandbox_session_secret")
        if not isinstance(secret, str) or not secret.strip():
            missing_scoped_secret_count += 1

    return {
        "ok": True,
        "enabled": True,
        "target_size": _settings.warm_pool_size,
        "refresh_interval_seconds": _settings.warm_pool_refresh_interval,
        "sandbox_max_age_seconds": _settings.warm_pool_sandbox_max_age,
        "missing_scoped_secret_count": missing_scoped_secret_count,
        "scoped_secret_transition_stable": missing_scoped_secret_count == 0,
        **pool_status,
    }


@web_app.get("/image/version")
async def get_image_version_endpoint():
    """Return current image version info.

    Provides visibility into which image version is currently deployed
    and running. Useful for debugging and verifying deploys.

    Returns:
        - version_id: Short hash identifying this deploy
        - deployed_at: Unix timestamp when this module was loaded
        - stored_version: Last recorded version from deploy invalidation

    Example:
        ```
        curl 'https://<org>--modal-backend-http-app-dev.modal.run/image/version'
        ```
    """
    stored_version = get_current_image_version()
    return {
        "ok": True,
        "version_id": _IMAGE_VERSION_ID,
        "deployed_at": _DEPLOY_TIMESTAMP,
        "stored_version": stored_version,
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
    should be passed with the subsequent /query request.

    Args:
        body: Pre-warm request with sandbox_type and optional session/job IDs.

    Returns:
        WarmResponse with warm_id for correlation.

    Example:
        ```bash
        # Client calls when user focuses on input
        curl -X POST 'https://<org>--modal-backend-http-app.modal.run/warm' \\
          -H 'Content-Type: application/json' \\
          -d '{"sandbox_type": "agent_sdk", "session_id": "sess_123"}'

        # Response: {"warm_id": "abc-123", "status": "warming", ...}

        # Then pass warm_id with the actual query
        curl -X POST 'https://<org>--modal-backend-http-app.modal.run/query' \\
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
    prewarm_agent_sdk_sandbox.spawn(warm_id, body.session_id)

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
        curl 'https://<org>--modal-backend-http-app.modal.run/warm/abc-123'
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
        curl 'https://<org>--modal-backend-http-app.modal.run/warm/status'
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

    Requests termination of an active agent session. Supports two modes:
    - "graceful" (default): Sets cancellation flag, agent stops at next tool call
    - "immediate": Calls client.interrupt() for near-instant termination

    This is a "soft" stop - it doesn't forcibly terminate the sandbox, but
    signals to the agent that it should stop working.

    Args:
        session_id: The OpenAI Agents session ID to stop.
        body: Optional request body with mode, reason and requester info.

    Returns:
        SessionStopResponse with cancellation details and status.

    Example:
        ```bash
        # Basic stop (graceful)
        curl -X POST 'https://<org>--modal-backend-http-app.modal.run/session/sess_abc123/stop'

        # Immediate stop
        curl -X POST 'https://<org>--modal-backend-http-app.modal.run/session/sess_abc123/stop' \\
          -H 'Content-Type: application/json' \\
          -d '{"mode": "immediate", "reason": "User requested stop"}'
        ```
    """
    if not _settings.enable_session_cancellation:
        return SessionStopResponse(
            ok=False,
            session_id=session_id,
            status="disabled",
            message="Session cancellation is disabled in settings",
        )

    mode = body.mode if body else "graceful"
    reason = body.reason if body else None
    requested_by = body.requested_by if body else None

    # Always set cancellation in persistent store (for graceful mode).
    existing = get_session_cancellation(session_id)
    if not existing:
        entry = cancel_session(
            session_id=session_id,
            requested_by=requested_by,
            reason=reason,
        )
    else:
        entry = existing

    # For immediate mode, also call the controller's internal stop endpoint
    controller_response = None
    controller_error: str | None = None
    if mode == "immediate":
        try:
            sb, service_url = await get_or_start_background_sandbox_aio(session_id=session_id)
            headers: dict[str, str] = {}
            _add_sandbox_auth_header(
                headers=headers,
                request_path=f"/session/{session_id}/stop",
                sandbox_id=sb.object_id,
                session_id=session_id,
            )
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                r = await client.post(
                    f"{service_url.rstrip('/')}/session/{session_id}/stop",
                    json={"mode": "immediate", "reason": reason, "requested_by": requested_by},
                    headers=headers or None,
                )
                if r.status_code == 200:
                    controller_response = r.json()
                else:
                    controller_error = f"controller stop returned {r.status_code}" + (
                        f": {r.text}" if (r.text or "").strip() else ""
                    )
        except Exception as e:
            controller_error = str(e)
            _logger.warning(
                "Failed to call controller stop endpoint",
                extra={"session_id": session_id, "error": str(e)},
            )

    # Build response message
    if mode == "immediate" and controller_response and controller_response.get("interrupted"):
        message = "Session interrupted immediately."
    elif mode == "immediate" and controller_response and controller_response.get("stop_event_set"):
        message = "Session stop signaled. Agent will stop at next opportunity."
    elif mode == "immediate" and controller_error:
        message = (
            "Session stop requested, but immediate interrupt failed "
            f"({controller_error}). Agent will stop after current tool call."
        )
    elif existing:
        message = "Session stop already requested."
    else:
        message = "Session stop requested. Agent will stop after current tool call."

    return SessionStopResponse(
        ok=not (mode == "immediate" and controller_error),
        session_id=session_id,
        status=entry.get("status", "requested"),
        requested_at=entry.get("requested_at"),
        expires_at=entry.get("expires_at"),
        reason=entry.get("reason"),
        requested_by=entry.get("requested_by"),
        message=message,
    )


@web_app.get("/session/{session_id}/stop", response_model=SessionStopResponse)
async def get_session_stop_status(session_id: str) -> SessionStopResponse:
    """Check the cancellation status for a session.

    Returns the current cancellation status if one exists, or indicates
    that the session has no active cancellation.

    Args:
        session_id: The OpenAI Agents session ID to check.

    Returns:
        SessionStopResponse with current cancellation status.

    Example:
        ```bash
        curl 'https://<org>--modal-backend-http-app.modal.run/session/sess_abc123/stop'
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
        curl 'https://<org>--modal-backend-http-app.modal.run/session/cancellations/status'
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
        curl 'https://<org>--modal-backend-http-app.modal.run/session/sess_abc123/metadata'
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

    # Check for snapshot presence
    snapshot = get_session_snapshot(session_id)

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
        is_executing=False,
        has_snapshot=snapshot is not None,
    )


@web_app.get("/session/{session_id}/users", response_model=SessionUsersResponse)
async def get_session_users_endpoint(session_id: str) -> SessionUsersResponse:
    """Get list of users with access to a session.

    Returns the owner and all authorized users for a session.

    Example:
        ```bash
        curl 'https://<org>--modal-backend-http-app.modal.run/session/sess_abc123/users'
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
        curl -X POST 'https://<org>--modal-backend-http-app.modal.run/session/sess_abc123/share' \\
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
        curl -X POST 'https://<org>--modal-backend-http-app.modal.run/session/sess_abc123/unshare' \\
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
        curl 'https://<org>--modal-backend-http-app.modal.run/session/sess_abc123/history'

        # Get last 10 messages
        curl 'https://<org>--modal-backend-http-app.modal.run/session/sess_abc123/history?limit=10'
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
        curl 'https://<org>--modal-backend-http-app.modal.run/session/multiplayer/status'
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


@app.function(image=agent_sdk_image, secrets=function_runtime_secrets, timeout=120)
def prewarm_agent_sdk_sandbox(warm_id: str, session_id: str | None = None):
    """Background task to pre-warm an Agent SDK sandbox.

    Creates or claims a sandbox and updates the pre-warm entry when ready.
    This runs in the background after POST /warm returns.
    """
    try:
        # Get or create sandbox (will claim from pool if available)
        sb, url = get_or_start_background_sandbox(session_id=session_id)
        sandbox_session_secret = _lookup_sandbox_session_secret(sandbox_id=sb.object_id)

        # Update pre-warm entry with sandbox details
        updated = update_prewarm_ready(
            warm_id,
            sb.object_id,
            url,
            sandbox_session_secret=sandbox_session_secret,
        )
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
            # Entry expired or missing - mark as failed so it's not stuck at "warming"
            mark_prewarm_failed(warm_id, "Entry expired before sandbox ready")
            _logger.warning(
                "Pre-warm expired before sandbox ready",
                extra={"warm_id": warm_id, "sandbox_id": sb.object_id},
            )
    except Exception as exc:
        # Mark as failed so the entry doesn't stay stuck at "warming"
        mark_prewarm_failed(warm_id, str(exc))
        _logger.error(
            "Pre-warm failed (agent_sdk)",
            exc_info=True,
            extra={"warm_id": warm_id, "error": str(exc)},
        )


@app.function(image=agent_sdk_image, secrets=function_runtime_secrets)
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

    def _read() -> list[str]:
        deadline = time.time() + timeout
        for msg in sb.stdout:
            for line in str(msg).splitlines():
                buf.append(line)
            if time.time() > deadline:
                break
        return list(buf)

    return await anyio.to_thread.run_sync(_read)


# Persistent registry for sandbox metadata (survives sandbox restarts).
# Keys are sandbox names (e.g., SANDBOX_NAME), values are dicts with:
#   - id: Sandbox object_id
#   - url: Service tunnel URL
#   - volume: Name of attached persistent volume
#   - created_at: Unix timestamp of creation
#   - tags: Dict of sandbox tags (role, app, port)
#   - status: Current status ("running", "missing")
SESSIONS = modal.Dict.from_name("sandbox-sessions", create_if_missing=True)
ARTIFACT_ACCESS_REVOKED = modal.Dict.from_name(
    _settings.artifact_access_revocation_store_name,
    create_if_missing=True,
)

# Service sandbox identity and config (will be initialized from Settings)
SANDBOX_NAME = _settings.sandbox_name
SERVICE_PORT = _settings.service_port
PERSIST_VOL_NAME = _settings.persist_vol_name

# Dedicated deployed app name for sandbox management.
# Using App.lookup() ensures the app is deployed (not ephemeral), which is
# required for named sandboxes to work with Sandbox.from_name().
SANDBOX_APP_NAME = "sandbox-manager-app"


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
_SANDBOX_STATE_LOCK = Lock()
_SANDBOX_STARTUP_MAX_ATTEMPTS = 2


class _SandboxReadinessTimeoutError(TimeoutError):
    """Structured readiness timeout carrying sandbox lifecycle context."""

    def __init__(
        self,
        *,
        sandbox: modal.Sandbox | None,
        service_url: str,
        phase: str,
        startup_attempt: int,
        recycle_allowed: bool,
        from_warm_pool: bool = False,
    ) -> None:
        self.sandbox = sandbox
        self.sandbox_id = getattr(sandbox, "object_id", None) if sandbox else None
        self.service_url = service_url
        self.phase = phase
        self.startup_attempt = startup_attempt
        self.recycle_allowed = recycle_allowed
        self.from_warm_pool = from_warm_pool
        super().__init__(
            "Sandbox readiness timeout "
            f"(phase={phase}, sandbox_id={self.sandbox_id or 'unknown'}, attempt={startup_attempt})"
        )


class _SandboxStartupRetryableError(RuntimeError):
    """Retryable startup failure carrying sandbox lifecycle context."""

    def __init__(
        self,
        *,
        sandbox: modal.Sandbox | None,
        service_url: str | None,
        phase: str,
        startup_attempt: int,
        recycle_allowed: bool,
        from_warm_pool: bool = False,
        detail: str | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.sandbox_id = getattr(sandbox, "object_id", None) if sandbox else None
        self.service_url = service_url
        self.phase = phase
        self.startup_attempt = startup_attempt
        self.recycle_allowed = recycle_allowed
        self.from_warm_pool = from_warm_pool
        self.detail = detail
        super().__init__(
            (detail or "Retryable sandbox startup failure")
            + f" (phase={phase}, sandbox_id={self.sandbox_id or 'unknown'}, attempt={startup_attempt})"
        )


def _get_background_sandbox_state() -> tuple[modal.Sandbox | None, str | None]:
    """Read sandbox globals under lock for consistency."""
    with _SANDBOX_STATE_LOCK:
        return SANDBOX, SERVICE_URL


def _set_background_sandbox_state(
    sandbox: modal.Sandbox | None,
    service_url: str | None,
) -> None:
    """Set sandbox globals under lock for consistency."""
    global SANDBOX, SERVICE_URL
    with _SANDBOX_STATE_LOCK:
        previous_id = getattr(SANDBOX, "object_id", None) if SANDBOX else None
        next_id = getattr(sandbox, "object_id", None) if sandbox else None
        SANDBOX = sandbox
        SERVICE_URL = service_url
        if previous_id and previous_id != next_id:
            _forget_sandbox_session_secret(sandbox_id=previous_id)


def _clear_background_sandbox_state(*, expected_sandbox_id: str | None = None) -> bool:
    """Clear sandbox globals, optionally guarded by expected sandbox id."""
    global SANDBOX, SERVICE_URL
    with _SANDBOX_STATE_LOCK:
        current_id = getattr(SANDBOX, "object_id", None) if SANDBOX else None
        if expected_sandbox_id and current_id and current_id != expected_sandbox_id:
            return False
        _forget_sandbox_session_secret(sandbox_id=current_id)
        SANDBOX = None
        SERVICE_URL = None
        return True


def _collect_sandbox_readiness_diagnostics_sync(
    sandbox: modal.Sandbox | None,
) -> dict[str, object]:
    diagnostics: dict[str, object] = {}
    if sandbox is None:
        return diagnostics

    try:
        poll_result = sandbox.poll()
        diagnostics["poll_state"] = "running" if poll_result is None else f"exited:{poll_result}"
    except Exception as exc:
        diagnostics["poll_state"] = "error"
        diagnostics["poll_error"] = f"{type(exc).__name__}: {exc}"

    try:
        tunnels = sandbox.tunnels()
        tunnel = tunnels.get(SERVICE_PORT)
        diagnostics["tunnel_port_present"] = SERVICE_PORT in tunnels
        diagnostics["tunnel_url_present"] = bool(getattr(tunnel, "url", None)) if tunnel else False
    except Exception as exc:
        diagnostics["tunnel_port_present"] = False
        diagnostics["tunnel_error"] = f"{type(exc).__name__}: {exc}"

    return diagnostics


async def _collect_sandbox_readiness_diagnostics_async(
    sandbox: modal.Sandbox | None,
) -> dict[str, object]:
    diagnostics: dict[str, object] = {}
    if sandbox is None:
        return diagnostics

    try:
        poll_result = await sandbox.poll.aio()
        diagnostics["poll_state"] = "running" if poll_result is None else f"exited:{poll_result}"
    except Exception as exc:
        diagnostics["poll_state"] = "error"
        diagnostics["poll_error"] = f"{type(exc).__name__}: {exc}"

    try:
        tunnels = await sandbox.tunnels.aio()
        tunnel = tunnels.get(SERVICE_PORT)
        diagnostics["tunnel_port_present"] = SERVICE_PORT in tunnels
        diagnostics["tunnel_url_present"] = bool(getattr(tunnel, "url", None)) if tunnel else False
    except Exception as exc:
        diagnostics["tunnel_port_present"] = False
        diagnostics["tunnel_error"] = f"{type(exc).__name__}: {exc}"

    return diagnostics


def _wait_for_service_or_raise_readiness_timeout(
    *,
    sandbox: modal.Sandbox | None,
    service_url: str,
    timeout_seconds: int,
    phase: str,
    startup_attempt: int,
    recycle_allowed: bool,
    from_warm_pool: bool = False,
) -> None:
    wait_started_at = time.time()
    try:
        _wait_for_service(service_url, timeout=timeout_seconds)
        return
    except TimeoutError as exc:
        diagnostics = _collect_sandbox_readiness_diagnostics_sync(sandbox)
        _logger.warning(
            "Background sandbox readiness timeout",
            extra={
                "phase": phase,
                "startup_attempt": startup_attempt,
                "service_timeout_seconds": timeout_seconds,
                "elapsed_seconds": round(time.time() - wait_started_at, 2),
                "sandbox_id": getattr(sandbox, "object_id", None) if sandbox else None,
                "service_url": service_url,
                **diagnostics,
            },
        )
        raise _SandboxReadinessTimeoutError(
            sandbox=sandbox,
            service_url=service_url,
            phase=phase,
            startup_attempt=startup_attempt,
            recycle_allowed=recycle_allowed,
            from_warm_pool=from_warm_pool,
        ) from exc


async def _wait_for_service_or_raise_readiness_timeout_aio(
    *,
    sandbox: modal.Sandbox | None,
    service_url: str,
    timeout_seconds: int,
    phase: str,
    startup_attempt: int,
    recycle_allowed: bool,
    from_warm_pool: bool = False,
) -> None:
    wait_started_at = anyio.current_time()
    try:
        await _wait_for_service_aio(service_url, timeout=timeout_seconds)
        return
    except TimeoutError as exc:
        diagnostics = await _collect_sandbox_readiness_diagnostics_async(sandbox)
        _logger.warning(
            "Background sandbox readiness timeout (async)",
            extra={
                "phase": phase,
                "startup_attempt": startup_attempt,
                "service_timeout_seconds": timeout_seconds,
                "elapsed_seconds": round(anyio.current_time() - wait_started_at, 2),
                "sandbox_id": getattr(sandbox, "object_id", None) if sandbox else None,
                "service_url": service_url,
                **diagnostics,
            },
        )
        raise _SandboxReadinessTimeoutError(
            sandbox=sandbox,
            service_url=service_url,
            phase=phase,
            startup_attempt=startup_attempt,
            recycle_allowed=recycle_allowed,
            from_warm_pool=from_warm_pool,
        ) from exc


def _terminate_sandbox_best_effort(
    sandbox: modal.Sandbox | None,
    *,
    reason: str,
) -> None:
    if sandbox is None:
        return
    sandbox_id = getattr(sandbox, "object_id", None)
    try:
        sandbox.terminate()
    except (modal_exc.NotFoundError, modal_exc.SandboxTerminatedError):
        return
    except Exception:
        _logger.warning(
            "Failed to terminate sandbox during readiness recycle",
            exc_info=True,
            extra={"sandbox_id": sandbox_id, "reason": reason},
        )


async def _terminate_sandbox_best_effort_aio(
    sandbox: modal.Sandbox | None,
    *,
    reason: str,
) -> None:
    if sandbox is None:
        return
    sandbox_id = getattr(sandbox, "object_id", None)
    try:
        await sandbox.terminate.aio()
    except (modal_exc.NotFoundError, modal_exc.SandboxTerminatedError):
        return
    except Exception:
        _logger.warning(
            "Failed to terminate sandbox during readiness recycle (async)",
            exc_info=True,
            extra={"sandbox_id": sandbox_id, "reason": reason},
        )


def _handle_readiness_timeout_sync(
    timeout_exc: _SandboxReadinessTimeoutError | _SandboxStartupRetryableError,
) -> None:
    cleared = _clear_background_sandbox_state(expected_sandbox_id=timeout_exc.sandbox_id)

    if timeout_exc.from_warm_pool and timeout_exc.sandbox_id:
        try:
            remove_from_pool(timeout_exc.sandbox_id)
        except Exception:
            _logger.warning(
                "Failed to remove timed-out sandbox from warm pool",
                exc_info=True,
                extra={"sandbox_id": timeout_exc.sandbox_id},
            )

    if timeout_exc.recycle_allowed:
        _terminate_sandbox_best_effort(
            timeout_exc.sandbox,
            reason=f"{timeout_exc.phase}:attempt{timeout_exc.startup_attempt}",
        )

    _logger.warning(
        "Handled retryable sandbox startup failure",
        extra={
            "phase": timeout_exc.phase,
            "startup_attempt": timeout_exc.startup_attempt,
            "sandbox_id": timeout_exc.sandbox_id,
            "state_cleared": cleared,
            "recycle_allowed": timeout_exc.recycle_allowed,
            "from_warm_pool": timeout_exc.from_warm_pool,
        },
    )


async def _handle_readiness_timeout_async(
    timeout_exc: _SandboxReadinessTimeoutError | _SandboxStartupRetryableError,
) -> None:
    cleared = _clear_background_sandbox_state(expected_sandbox_id=timeout_exc.sandbox_id)

    if timeout_exc.from_warm_pool and timeout_exc.sandbox_id:
        try:
            remove_from_pool(timeout_exc.sandbox_id)
        except Exception:
            _logger.warning(
                "Failed to remove timed-out sandbox from warm pool (async)",
                exc_info=True,
                extra={"sandbox_id": timeout_exc.sandbox_id},
            )

    if timeout_exc.recycle_allowed:
        await _terminate_sandbox_best_effort_aio(
            timeout_exc.sandbox,
            reason=f"{timeout_exc.phase}:attempt{timeout_exc.startup_attempt}",
        )

    _logger.warning(
        "Handled retryable sandbox startup failure (async)",
        extra={
            "phase": timeout_exc.phase,
            "startup_attempt": timeout_exc.startup_attempt,
            "sandbox_id": timeout_exc.sandbox_id,
            "state_cleared": cleared,
            "recycle_allowed": timeout_exc.recycle_allowed,
            "from_warm_pool": timeout_exc.from_warm_pool,
        },
    )


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
    secrets=function_runtime_secrets,
    schedule=modal.Cron("*/2 * * * *"),
    **_retry_kwargs(),
)
def cleanup_sessions():
    """Verify sandbox health and update SESSIONS registry.

    Runs every 2 minutes via cron. Checks if the named sandbox is still alive
    by attempting to fetch its tunnel URLs. Updates SESSIONS status accordingly.
    """
    try:
        # Ensure sandbox-manager-app exists (required for from_name to work)
        modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
        sb = modal.Sandbox.from_name(SANDBOX_APP_NAME, SANDBOX_NAME)
        _ = sb.tunnels()  # Will raise NotFoundError if sandbox is gone
        SESSIONS[SANDBOX_NAME] = {**SESSIONS.get(SANDBOX_NAME, {}), "status": "running"}
    except modal_exc.NotFoundError:
        SESSIONS[SANDBOX_NAME] = {"status": "missing"}
    except Exception:
        logging.getLogger(__name__).exception("Unexpected error cleaning up sessions")


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
    sandbox_session_secret = _generate_sandbox_session_secret()
    svc_vol = _get_persist_volume()
    sandbox_app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)

    try:
        sb = modal.Sandbox.create(
            "uvicorn",
            "modal_backend.api.controller:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(SERVICE_PORT),
            app=sandbox_app,
            image=agent_sdk_image,
            secrets=sandbox_runtime_secrets,
            env=_sandbox_runtime_env(sandbox_session_secret),
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
            "app": "modal-backend",
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
    register_warm_sandbox(
        sandbox_id,
        pool_name,
        sandbox_session_secret=sandbox_session_secret,
    )
    _logger.info(
        "Created warm pool sandbox",
        extra={"sandbox_id": sandbox_id, "sandbox_name": pool_name, "url": service_url},
    )

    return sb, sandbox_id, pool_name


@app.function(
    image=agent_sdk_image,
    secrets=function_runtime_secrets,
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
    secrets=function_runtime_secrets,
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

    # Step 3b: Invalidate sandboxes created before current deploy
    deploy_invalidated = 0
    if _settings.enable_image_version_tracking:
        current_deploy_time = get_image_deployed_at()
        if current_deploy_time:
            for entry in get_warm_pool_entries():
                if entry.get("status") == "warm":
                    created_at = entry.get("created_at", 0)
                    if created_at < current_deploy_time:
                        sandbox_id = entry.get("sandbox_id")
                        if sandbox_id:
                            try:
                                sb = modal.Sandbox.from_id(sandbox_id)
                                sb.terminate()
                            except Exception:
                                pass
                            remove_from_pool(sandbox_id)
                            deploy_invalidated += 1
            if deploy_invalidated > 0:
                _logger.info(
                    "Invalidated pre-deploy pool sandboxes",
                    extra={"count": deploy_invalidated},
                )

    # Step 4: Replenish pool
    replenish_result = replenish_warm_pool.local()

    pool_status = get_warm_pool_status()
    return {
        "status": "ok",
        "stale_removed": stale_removed,
        "expired_terminated": expired_count,
        "deploy_invalidated": deploy_invalidated,
        "replenished": replenish_result.get("created", 0),
        "pool_warm": pool_status["warm"],
        "pool_claimed": pool_status["claimed"],
        "pool_total": pool_status["total"],
    }


# =============================================================================
# DEPLOY INVALIDATION
# =============================================================================
# This function records the new image version and invalidates warm pool
# sandboxes running old images. Call it from CI/CD after `modal deploy`.


@app.function(
    secrets=function_runtime_secrets,
    timeout=120,
)
def on_deploy_invalidate_pools():
    """Record new image version and invalidate old warm pool sandboxes.

    Called after deploy to record the current image version and terminate
    all warm pool sandboxes that were created with the previous image.
    This ensures sandboxes always run the latest deployed code.

    Should be invoked from CI/CD after `modal deploy` completes:
        modal run -m modal_backend.main::on_deploy_invalidate_pools

    Returns:
        Dict with status, version_id, deployed_at, and invalidated count.

    Example CI/CD step:
        ```bash
        modal deploy -m modal_backend.deploy
        modal run -m modal_backend.main::on_deploy_invalidate_pools
        ```
    """
    if not _settings.enable_image_version_tracking:
        return {"status": "disabled", "version_id": _IMAGE_VERSION_ID}

    # Record this deploy
    set_image_version(_IMAGE_VERSION_ID, _DEPLOY_TIMESTAMP)

    # Invalidate all existing warm pool entries (they run old image)
    invalidated = 0

    # Agent SDK pool
    for entry in get_warm_pool_entries():
        if entry.get("status") == "warm":
            sandbox_id = entry.get("sandbox_id")
            if sandbox_id:
                try:
                    sb = modal.Sandbox.from_id(sandbox_id)
                    sb.terminate()
                except Exception:
                    pass
                remove_from_pool(sandbox_id)
                invalidated += 1

    _logger.info(
        "Deploy invalidation complete",
        extra={"version_id": _IMAGE_VERSION_ID, "invalidated": invalidated},
    )

    return {
        "status": "ok",
        "version_id": _IMAGE_VERSION_ID,
        "deployed_at": _DEPLOY_TIMESTAMP,
        "invalidated": invalidated,
    }


# =============================================================================
# WORKSPACE RETENTION SCHEDULED TASK
# =============================================================================


@app.function(
    image=agent_sdk_image,
    secrets=function_runtime_secrets,
    schedule=modal.Cron(
        f"0 */{max(_settings.workspace_cleanup_interval_seconds // 3600, 1)} * * *"
    ),
    timeout=1800,
    **_retry_kwargs(),
)
def maintain_workspace_retention():
    """Periodic cleanup of expired job workspaces.

    Runs on a schedule to delete workspaces that have exceeded their retention
    period. Completed jobs are cleaned up after workspace_retention_days,
    failed jobs after failed_job_retention_days.

    The schedule is derived from workspace_cleanup_interval_seconds setting.
    Default: every hour.
    """
    if not _settings.enable_workspace_retention:
        return {"status": "disabled"}

    _logger.info("Running workspace retention maintenance")

    result = _cleanup_expired_workspaces(dry_run=False)

    _logger.info(
        "Workspace cleanup completed",
        extra={
            "workspaces_checked": result.workspaces_checked,
            "workspaces_deleted": result.workspaces_deleted,
            "bytes_freed": result.bytes_freed,
        },
    )

    return {
        "status": "ok",
        "workspaces_checked": result.workspaces_checked,
        "workspaces_deleted": result.workspaces_deleted,
        "bytes_freed": result.bytes_freed,
        "deleted_job_ids": result.deleted_job_ids,
        "errors": result.errors,
    }


def get_or_start_background_sandbox(
    session_id: str | None = None,
) -> tuple[modal.Sandbox, str]:
    """Return a running background sandbox and its encrypted service URL.

    Starts a daemonized sandbox running `uvicorn modal_backend.api.controller:app` if one is
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
        3. The OpenAI SQLite session memory is resumed via `session_id`
           (handled in the controller)
    """
    timeout_seconds = max(int(_settings.service_timeout), 1)

    for startup_attempt in range(1, _SANDBOX_STARTUP_MAX_ATTEMPTS + 1):
        try:
            # STEP 1: Check if we already have a connection in this worker's memory
            cached_sb, cached_url = _get_background_sandbox_state()
            if cached_sb is not None and cached_url:
                return cached_sb, cached_url

            # -----------------------------------------------------------------
            # STEP 2: Try to find an EXISTING sandbox by name
            # -----------------------------------------------------------------
            try:
                modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
                sb = modal.Sandbox.from_name(SANDBOX_APP_NAME, SANDBOX_NAME)
                tunnels = sb.tunnels()
                if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
                    service_url = tunnels[SERVICE_PORT].url
                    _wait_for_service_or_raise_readiness_timeout(
                        sandbox=sb,
                        service_url=service_url,
                        timeout_seconds=timeout_seconds,
                        phase="reuse_by_name",
                        startup_attempt=startup_attempt,
                        recycle_allowed=False,
                    )
                    reused_secret = _resolve_sandbox_session_secret(
                        sandbox_id=sb.object_id,
                        secret=_lookup_sandbox_session_secret(sandbox_id=sb.object_id),
                    )
                    if not reused_secret:
                        _logger.warning(
                            "Reused sandbox missing scoped session secret; creating replacement sandbox",
                            extra={"sandbox_id": sb.object_id},
                        )
                    else:
                        _set_background_sandbox_state(sb, service_url)
                        return sb, service_url
            except (_SandboxReadinessTimeoutError, _SandboxStartupRetryableError):
                raise
            except Exception:
                pass  # Sandbox doesn't exist or isn't accessible; we'll create a new one

            # -----------------------------------------------------------------
            # STEP 3: Determine image for new sandbox (snapshot restoration)
            # -----------------------------------------------------------------
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

            # -----------------------------------------------------------------
            # STEP 3.5: Try to claim from warm pool (if enabled and no snapshot)
            # -----------------------------------------------------------------
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
                                    tunnels = pool_sb.tunnels()
                                    if SERVICE_PORT in tunnels and getattr(
                                        tunnels[SERVICE_PORT], "url", None
                                    ):
                                        pool_url = tunnels[SERVICE_PORT].url
                                        _wait_for_service_or_raise_readiness_timeout(
                                            sandbox=pool_sb,
                                            service_url=pool_url,
                                            timeout_seconds=timeout_seconds,
                                            phase="warm_pool_claim",
                                            startup_attempt=startup_attempt,
                                            recycle_allowed=True,
                                            from_warm_pool=True,
                                        )
                                        _set_background_sandbox_state(pool_sb, pool_url)
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
                                                "app": "modal-backend",
                                                "port": str(SERVICE_PORT),
                                            }
                                        )
                                        # Trigger async pool replenishment
                                        try:
                                            replenish_warm_pool.spawn()
                                        except Exception:
                                            pass  # Non-critical: pool replenished by maintainer
                                        claimed_secret = _resolve_sandbox_session_secret(
                                            sandbox_id=sandbox_id,
                                            secret=claimed.get("sandbox_session_secret"),
                                        )
                                        if not claimed_secret:
                                            raise _SandboxStartupRetryableError(
                                                sandbox=pool_sb,
                                                service_url=pool_url,
                                                phase="warm_pool_missing_scoped_secret",
                                                startup_attempt=startup_attempt,
                                                recycle_allowed=True,
                                                from_warm_pool=True,
                                                detail="Warm pool sandbox missing scoped session secret",
                                            )
                                        try:
                                            SESSIONS[SANDBOX_NAME] = {
                                                "id": sandbox_id,
                                                "url": pool_url,
                                                "volume": PERSIST_VOL_NAME,
                                                "created_at": int(time.time()),
                                                "tags": {
                                                    "role": "service",
                                                    "app": "modal-backend",
                                                    "port": str(SERVICE_PORT),
                                                },
                                                "status": "running",
                                                "sandbox_session_secret": claimed_secret,
                                            }
                                        except Exception:
                                            _logger.warning(
                                                "Failed to persist claimed sandbox session secret metadata",
                                                exc_info=True,
                                                extra={"sandbox_id": sandbox_id},
                                            )
                                        return pool_sb, pool_url
                            except (_SandboxReadinessTimeoutError, _SandboxStartupRetryableError):
                                raise
                            except Exception:
                                _logger.warning(
                                    "Failed to use claimed pool sandbox, will create new",
                                    exc_info=True,
                                    extra={"sandbox_id": sandbox_id},
                                )
                                # Remove the bad entry from pool
                                remove_from_pool(sandbox_id)
                except (_SandboxReadinessTimeoutError, _SandboxStartupRetryableError):
                    raise
                except Exception:
                    _logger.warning(
                        "Error checking warm pool, will create new sandbox", exc_info=True
                    )

            # -----------------------------------------------------------------
            # STEP 4: Create a NEW sandbox
            # -----------------------------------------------------------------
            svc_vol = _get_persist_volume()
            sandbox_app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
            sandbox_session_secret = _generate_sandbox_session_secret()
            attached_existing = False
            try:
                sandbox = modal.Sandbox.create(
                    "uvicorn",
                    "modal_backend.api.controller:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    str(SERVICE_PORT),
                    app=sandbox_app,
                    image=sandbox_image,
                    secrets=sandbox_runtime_secrets,
                    env=_sandbox_runtime_env(sandbox_session_secret),
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
                attached_existing = True
                sandbox = modal.Sandbox.from_name(SANDBOX_APP_NAME, SANDBOX_NAME)
                sandbox_session_secret = _lookup_sandbox_session_secret(
                    sandbox_id=sandbox.object_id
                )

            sandbox_session_secret = _resolve_sandbox_session_secret(
                sandbox_id=sandbox.object_id if sandbox else None,
                secret=sandbox_session_secret,
            )
            if attached_existing and not sandbox_session_secret:
                raise _SandboxStartupRetryableError(
                    sandbox=sandbox,
                    service_url=None,
                    phase="attach_missing_scoped_secret",
                    startup_attempt=startup_attempt,
                    recycle_allowed=True,
                    detail="Attached sandbox missing scoped session secret",
                )

            _set_background_sandbox_state(sandbox, None)
            _remember_sandbox_session_secret(
                sandbox_id=sandbox.object_id if sandbox else None,
                secret=sandbox_session_secret,
            )

            # Optional: set tags after creation (useful for filtering in Modal dashboard)
            sandbox.set_tags({"role": "service", "app": "modal-backend", "port": str(SERVICE_PORT)})

            # Poll tunnels until URL appears
            service_url = None
            deadline = time.time() + 30
            while time.time() < deadline:
                tunnels = sandbox.tunnels()
                if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
                    service_url = tunnels[SERVICE_PORT].url
                    break
                time.sleep(0.5)

            if not service_url:
                _clear_background_sandbox_state(expected_sandbox_id=sandbox.object_id)
                raise _SandboxStartupRetryableError(
                    sandbox=sandbox,
                    service_url=None,
                    phase="tunnel_discovery",
                    startup_attempt=startup_attempt,
                    recycle_allowed=True,
                    detail="Failed to start background sandbox or get service URL",
                )

            _set_background_sandbox_state(sandbox, service_url)
            _wait_for_service_or_raise_readiness_timeout(
                sandbox=sandbox,
                service_url=service_url,
                timeout_seconds=timeout_seconds,
                phase="create_or_attach",
                startup_attempt=startup_attempt,
                recycle_allowed=True,
            )

            try:
                session_metadata: dict = {
                    "id": sandbox.object_id,
                    "url": service_url,
                    "volume": PERSIST_VOL_NAME,
                    "created_at": int(time.time()),
                    "tags": {"role": "service", "app": "modal-backend", "port": str(SERVICE_PORT)},
                    "status": "running",
                }
                if sandbox_session_secret:
                    session_metadata["sandbox_session_secret"] = sandbox_session_secret
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
                logging.getLogger(__name__).exception(
                    "Unexpected error persisting session metadata"
                )

            return sandbox, service_url
        except (_SandboxReadinessTimeoutError, _SandboxStartupRetryableError) as timeout_exc:
            _handle_readiness_timeout_sync(timeout_exc)
            if startup_attempt < _SANDBOX_STARTUP_MAX_ATTEMPTS:
                _logger.warning(
                    "Retrying background sandbox startup after retryable failure",
                    extra={
                        "startup_attempt": startup_attempt,
                        "next_attempt": startup_attempt + 1,
                        "phase": timeout_exc.phase,
                        "sandbox_id": timeout_exc.sandbox_id,
                    },
                )
                continue
            raise TimeoutError(
                "Background sandbox startup failed after "
                f"{_SANDBOX_STARTUP_MAX_ATTEMPTS} attempts"
            ) from timeout_exc

    raise RuntimeError("Unreachable background sandbox startup state")


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
    timeout_seconds = max(int(_settings.service_timeout), 1)

    for startup_attempt in range(1, _SANDBOX_STARTUP_MAX_ATTEMPTS + 1):
        try:
            cached_sb, cached_url = _get_background_sandbox_state()
            if cached_sb is not None and cached_url:
                return cached_sb, cached_url

            # Attempt global reuse by name across workers/processes.
            try:
                modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
                sb = modal.Sandbox.from_name(SANDBOX_APP_NAME, SANDBOX_NAME)
                deadline = anyio.current_time() + 30
                url = None
                while anyio.current_time() < deadline:
                    tunnels = await sb.tunnels.aio()
                    if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
                        url = tunnels[SERVICE_PORT].url
                        break
                    await anyio.sleep(0.5)

                if url:
                    await _wait_for_service_or_raise_readiness_timeout_aio(
                        sandbox=sb,
                        service_url=url,
                        timeout_seconds=timeout_seconds,
                        phase="reuse_by_name",
                        startup_attempt=startup_attempt,
                        recycle_allowed=False,
                    )
                    reused_secret = _resolve_sandbox_session_secret(
                        sandbox_id=sb.object_id,
                        secret=_lookup_sandbox_session_secret(sandbox_id=sb.object_id),
                    )
                    if not reused_secret:
                        _logger.warning(
                            "Reused sandbox missing scoped session secret; creating replacement sandbox (async)",
                            extra={"sandbox_id": sb.object_id},
                        )
                    else:
                        _set_background_sandbox_state(sb, url)
                        return sb, url
            except (_SandboxReadinessTimeoutError, _SandboxStartupRetryableError):
                raise
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
                                if await pool_sb.poll.aio() is None:
                                    tunnels = await pool_sb.tunnels.aio()
                                    if SERVICE_PORT in tunnels and getattr(
                                        tunnels[SERVICE_PORT], "url", None
                                    ):
                                        pool_url = tunnels[SERVICE_PORT].url
                                        await _wait_for_service_or_raise_readiness_timeout_aio(
                                            sandbox=pool_sb,
                                            service_url=pool_url,
                                            timeout_seconds=timeout_seconds,
                                            phase="warm_pool_claim",
                                            startup_attempt=startup_attempt,
                                            recycle_allowed=True,
                                            from_warm_pool=True,
                                        )
                                        _set_background_sandbox_state(pool_sb, pool_url)
                                        _logger.info(
                                            "Claimed sandbox from warm pool (async)",
                                            extra={
                                                "sandbox_id": sandbox_id,
                                                "sandbox_name": sandbox_name,
                                                "session_id": session_id,
                                            },
                                        )
                                        await pool_sb.set_tags.aio(
                                            {
                                                "pool": "agent_sdk",
                                                "status": "claimed",
                                                "role": "service",
                                                "app": "modal-backend",
                                                "port": str(SERVICE_PORT),
                                            }
                                        )
                                        try:
                                            replenish_warm_pool.spawn()
                                        except Exception:
                                            pass  # Non-critical
                                        claimed_secret = _resolve_sandbox_session_secret(
                                            sandbox_id=sandbox_id,
                                            secret=claimed.get("sandbox_session_secret"),
                                        )
                                        if not claimed_secret:
                                            raise _SandboxStartupRetryableError(
                                                sandbox=pool_sb,
                                                service_url=pool_url,
                                                phase="warm_pool_missing_scoped_secret",
                                                startup_attempt=startup_attempt,
                                                recycle_allowed=True,
                                                from_warm_pool=True,
                                                detail="Warm pool sandbox missing scoped session secret",
                                            )
                                        try:
                                            SESSIONS[SANDBOX_NAME] = {
                                                "id": sandbox_id,
                                                "url": pool_url,
                                                "volume": PERSIST_VOL_NAME,
                                                "created_at": int(time.time()),
                                                "tags": {
                                                    "role": "service",
                                                    "app": "modal-backend",
                                                    "port": str(SERVICE_PORT),
                                                },
                                                "status": "running",
                                                "sandbox_session_secret": claimed_secret,
                                            }
                                        except Exception:
                                            _logger.warning(
                                                "Failed to persist claimed sandbox session secret metadata (async)",
                                                exc_info=True,
                                                extra={"sandbox_id": sandbox_id},
                                            )
                                        return pool_sb, pool_url
                            except (_SandboxReadinessTimeoutError, _SandboxStartupRetryableError):
                                raise
                            except Exception:
                                _logger.warning(
                                    "Failed to use claimed pool sandbox (async), will create new",
                                    exc_info=True,
                                    extra={"sandbox_id": sandbox_id},
                                )
                                remove_from_pool(sandbox_id)
                except (_SandboxReadinessTimeoutError, _SandboxStartupRetryableError):
                    raise
                except Exception:
                    _logger.warning(
                        "Error checking warm pool (async), will create new sandbox",
                        exc_info=True,
                    )

            # Create with persistent volume.
            svc_vol = _get_persist_volume()
            sandbox_app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
            sandbox_session_secret = _generate_sandbox_session_secret()
            attached_existing = False
            try:
                sandbox = await modal.Sandbox.create.aio(
                    "uvicorn",
                    "modal_backend.api.controller:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    str(SERVICE_PORT),
                    app=sandbox_app,
                    image=sandbox_image,
                    secrets=sandbox_runtime_secrets,
                    env=_sandbox_runtime_env(sandbox_session_secret),
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
                attached_existing = True
                sandbox = await modal.Sandbox.from_name.aio(SANDBOX_APP_NAME, SANDBOX_NAME)
                sandbox_session_secret = _lookup_sandbox_session_secret(
                    sandbox_id=sandbox.object_id
                )

            sandbox_session_secret = _resolve_sandbox_session_secret(
                sandbox_id=sandbox.object_id if sandbox else None,
                secret=sandbox_session_secret,
            )
            if attached_existing and not sandbox_session_secret:
                raise _SandboxStartupRetryableError(
                    sandbox=sandbox,
                    service_url=None,
                    phase="attach_missing_scoped_secret",
                    startup_attempt=startup_attempt,
                    recycle_allowed=True,
                    detail="Attached sandbox missing scoped session secret",
                )

            _set_background_sandbox_state(sandbox, None)
            _remember_sandbox_session_secret(
                sandbox_id=sandbox.object_id if sandbox else None,
                secret=sandbox_session_secret,
            )

            await sandbox.set_tags.aio(
                {"role": "service", "app": "modal-backend", "port": str(SERVICE_PORT)}
            )

            deadline = anyio.current_time() + 30
            service_url = None
            while anyio.current_time() < deadline:
                tunnels = await sandbox.tunnels.aio()
                if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
                    service_url = tunnels[SERVICE_PORT].url
                    break
                await anyio.sleep(0.5)

            if not service_url:
                _clear_background_sandbox_state(expected_sandbox_id=sandbox.object_id)
                raise _SandboxStartupRetryableError(
                    sandbox=sandbox,
                    service_url=None,
                    phase="tunnel_discovery",
                    startup_attempt=startup_attempt,
                    recycle_allowed=True,
                    detail="Failed to start background sandbox or get service URL",
                )

            _set_background_sandbox_state(sandbox, service_url)
            await _wait_for_service_or_raise_readiness_timeout_aio(
                sandbox=sandbox,
                service_url=service_url,
                timeout_seconds=timeout_seconds,
                phase="create_or_attach",
                startup_attempt=startup_attempt,
                recycle_allowed=True,
            )

            try:
                session_metadata: dict = {
                    "id": sandbox.object_id,
                    "url": service_url,
                    "volume": PERSIST_VOL_NAME,
                    "created_at": int(time.time()),
                    "tags": {"role": "service", "app": "modal-backend", "port": str(SERVICE_PORT)},
                    "status": "running",
                }
                if sandbox_session_secret:
                    session_metadata["sandbox_session_secret"] = sandbox_session_secret
                if restored_from_snapshot and session_id:
                    session_metadata["restored_from_session"] = session_id
                    session_metadata["restored_from_snapshot"] = True
                SESSIONS[SANDBOX_NAME] = session_metadata
            except Exception:
                _logger.warning(
                    "Failed to persist session metadata to Modal Dict (async)",
                    exc_info=True,
                    extra={"sandbox_id": sandbox.object_id if sandbox else None},
                )

            return sandbox, service_url
        except (_SandboxReadinessTimeoutError, _SandboxStartupRetryableError) as timeout_exc:
            await _handle_readiness_timeout_async(timeout_exc)
            if startup_attempt < _SANDBOX_STARTUP_MAX_ATTEMPTS:
                _logger.warning(
                    "Retrying background sandbox startup after retryable failure (async)",
                    extra={
                        "startup_attempt": startup_attempt,
                        "next_attempt": startup_attempt + 1,
                        "phase": timeout_exc.phase,
                        "sandbox_id": timeout_exc.sandbox_id,
                    },
                )
                continue
            raise TimeoutError(
                "Background sandbox startup failed after "
                f"{_SANDBOX_STARTUP_MAX_ATTEMPTS} attempts"
            ) from timeout_exc

    raise RuntimeError("Unreachable background sandbox startup state (async)")


@app.cls(
    image=agent_sdk_image,
    secrets=function_runtime_secrets,
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

    agent_type: str = modal.parameter(default="default")
    system_prompt: str = modal.parameter(default="")

    @modal.enter(snap=True)
    def _snapshot_setup(self) -> None:
        """Initialize agent options and capture in memory snapshot.

        snap=True means this runs BEFORE the snapshot is taken. The initialized
        _options object will be serialized into the snapshot and restored on
        subsequent container starts, avoiding re-initialization overhead.
        """
        from modal_backend.agent_runtime import build_agent_options, get_agent_config

        config = get_agent_config(self.agent_type)
        system_prompt = self.system_prompt or config.system_prompt
        max_turns = config.max_turns or _settings.agent_max_turns

        self._options = build_agent_options(
            config.get_mcp_servers(),
            config.get_effective_allowed_tools(),
            system_prompt,
            subagents=config.get_subagents(),
        )
        self._max_turns = max_turns

    @modal.enter(snap=False)
    def _post_restore(self) -> None:
        """Post-restore initialization after snapshot restore.

        snap=False means this runs AFTER restoring from snapshot. Used to
        reinitialize any state that can't be serialized (e.g., network connections).
        Also serves as fallback if snapshot wasn't taken or is corrupted.
        """
        if getattr(self, "_options", None) is None:
            from modal_backend.agent_runtime import build_agent_options, get_agent_config

            config = get_agent_config(self.agent_type)
            system_prompt = self.system_prompt or config.system_prompt
            max_turns = config.max_turns or _settings.agent_max_turns

            self._options = build_agent_options(
                config.get_mcp_servers(),
                config.get_effective_allowed_tools(),
                system_prompt,
                subagents=config.get_subagents(),
            )
            self._max_turns = max_turns

    @modal.exit()
    def _cleanup(self) -> None:
        """Release resources when container shuts down."""
        self._options = None

    @modal.method()
    def run(self, question: str = DEFAULT_QUESTION) -> None:
        """Execute an agent query and stream responses to stdout."""
        import anyio
        from agents import Runner

        from modal_backend.agent_runtime import ensure_session

        async def _run() -> None:
            session, session_id = await ensure_session(
                None,
                fork_session=False,
                db_path=_settings.openai_session_db_path,
            )
            result = await Runner.run(
                self._options,
                question,
                session=session,
                max_turns=self._max_turns or 50,
            )
            print(f"session_id={session_id}")
            print(result.final_output)

        anyio.run(_run)


@app.function(
    image=agent_sdk_image,
    secrets=function_runtime_secrets,
    schedule=_schedule_dispatcher_schedule(),
    timeout=120,
)
def schedule_dispatcher() -> dict[str, int]:
    """Dispatch due schedules into the job queue."""
    result = dispatch_due_schedules()
    _logger.info("schedule.dispatch", extra=result)
    return result


@app.function(
    image=agent_sdk_image,
    secrets=function_runtime_secrets,
    volumes={"/data": _get_persist_volume()},
    **_function_runtime_kwargs(include_autoscale=False),
)
def run_agent_remote(
    question: str = DEFAULT_QUESTION,
    agent_type: str = "default",
) -> None:
    """Run the agent once in a short-lived Modal function.

    This is useful for synchronous, on-demand runs. For long-running, low-latency
    serving, prefer the background sandbox pattern used by `http_app`.

    Args:
        question: Natural-language query to send to the agent.
        agent_type: Agent type to use (e.g., "default", "marketing", "research").
    """
    AgentRunner(agent_type=agent_type).run.remote(question)


@app.function(image=agent_sdk_image, secrets=function_runtime_secrets, timeout=600)
def load_test(num_queries: int = 10, question: str = DEFAULT_QUESTION) -> dict:
    """Run parallel queries to test scaling behavior.

    Spawns multiple agent queries in parallel using Modal's distributed execution
    and measures throughput. Useful for validating autoscaling configuration
    and measuring system performance under load.

    Usage:
        modal run -m modal_backend.main::load_test --num-queries 10
        modal run -m modal_backend.main::load_test --num-queries 100 --question "Hello"

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
    secrets=function_runtime_secrets,
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
            headers: dict[str, str] = {}
            if settings.enforce_connect_token:
                creds = sb.create_connect_token(user_metadata={"job_id": job_id})
                headers = {"Authorization": f"Bearer {creds.token}"}
            _add_sandbox_auth_header(
                headers=headers,
                request_path="/query",
                sandbox_id=sb.object_id,
                session_id=record.get("session_id"),
            )
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
    secrets=function_runtime_secrets,
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
    secrets=function_runtime_secrets,
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
    try:
        sb, _ = get_or_start_background_sandbox()
        sb.terminate()
        _clear_background_sandbox_state(expected_sandbox_id=getattr(sb, "object_id", None))
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


@app.function(
    image=agent_sdk_image, secrets=function_runtime_secrets, timeout=300, **_retry_kwargs()
)
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


@app.function(
    image=agent_sdk_image, secrets=function_runtime_secrets, timeout=300, **_retry_kwargs()
)
def snapshot_session_state(session_id: str, sandbox_id: str | None = None) -> dict:
    """Capture sandbox filesystem state for a specific agent session.

    Creates a snapshot tied to a session_id, enabling session restoration when
    the user resumes a session after the sandbox has timed out. This enables
    "leave and come back" workflows where the agent's installed tools, downloaded
    files, and other filesystem state are preserved.

    Unlike snapshot_service() which creates a global snapshot, this function
    stores the snapshot reference keyed by session_id in SESSION_SNAPSHOTS,
    allowing per-session restoration.

    Args:
        session_id: The OpenAI Agents session ID to associate with this snapshot.

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
        if sandbox_id:
            sb = modal.Sandbox.from_id(sandbox_id)
        else:
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


# For 'modal run' command
@app.local_entrypoint()
def main():
    """Local entry point for `modal run -m modal_backend.main` during development.

    Spins up a short-lived sandbox, executes `modal_backend.agent_runtime.loop`, streams logs, and
    terminates the sandbox. Prefer `modal serve -m modal_backend.main` to keep endpoints and
    hot code reloading during development.
    """
    sb = modal.Sandbox.create(
        app=app,
        image=agent_sdk_image,
        secrets=sandbox_runtime_secrets,
        env={"REQUIRE_INTERNAL_AUTH_SECRET": "false"},
        workdir="/root/app",
        timeout=60 * 10,  # 10 minutes
        **_sandbox_resource_kwargs(),
        verbose=True,
    )

    p = sb.exec("python", "-m", "modal_backend.agent_runtime.loop", timeout=60)

    print("=== STDOUT ===")
    for line in p.stdout:
        print(line, end="")
    print("\n=== STDERR ===")
    for line in p.stderr:
        print(line, end="")

    sb.terminate()
