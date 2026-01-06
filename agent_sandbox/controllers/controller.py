"""
FastAPI microservice that runs inside a long-lived Modal Sandbox.

This service exposes endpoints:
- `GET /health_check` used by the controller to know when the service is ready.
- `POST /query` which returns a response from the Claude Agent SDK.
- `POST /query_stream` which streams a response from the Claude Agent SDK.

This file is started inside the sandbox via `uvicorn agent_sandbox.controllers.controller:app`
(see `agent_sandbox.app.get_or_start_background_sandbox`). The sandbox is created with an
encrypted port (8001), and `agent_sandbox.app.http_app` proxies to these endpoints.

See Modal docs for details about `modal.Sandbox`, encrypted ports, and tunnel
discovery.

Important:
- To reach this service from outside (via `agent_sandbox.app.http_app`), make sure the
  app is running with `modal serve -m agent_sandbox.app` (dev) or has been deployed with
  `modal deploy -m agent_sandbox.deploy`.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

import modal
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)
from claude_agent_sdk.types import Message, ResultMessage
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from agent_sandbox.config.settings import get_settings
from agent_sandbox.controllers.middleware import RequestIdMiddleware
from agent_sandbox.controllers.serialization import (
    build_final_summary,
    iter_text_blocks,
    serialize_message,
)
from agent_sandbox.prompts.prompts import SYSTEM_PROMPT
from agent_sandbox.schemas import QueryBody
from agent_sandbox.schemas.responses import ErrorResponse, QueryResponse
from agent_sandbox.tools import get_allowed_tools, get_mcp_servers

app = FastAPI()
app.add_middleware(RequestIdMiddleware)
_settings = get_settings()
_logger = logging.getLogger(__name__)

# Tracks the last time we committed the persistent volume. Used to enforce
# minimum intervals between commits to avoid excessive I/O overhead.
# See: https://modal.com/docs/guide/volumes
_LAST_VOLUME_COMMIT_TS: float | None = None
SESSION_STORE = modal.Dict.from_name(_settings.session_store_name, create_if_missing=True)
SESSION_CACHE: dict[str, str] = {}


def _require_connect_token(request: Request) -> None:
    """Validate that the request includes a Modal connect token.

    When enforce_connect_token is enabled in settings, this ensures requests
    come through Modal's authenticated proxy rather than directly to the
    sandbox tunnel URL.

    Args:
        request: The incoming FastAPI request.

    Raises:
        HTTPException: 401 if token is missing and enforcement is enabled.
    """
    if _settings.enforce_connect_token:
        if not request.headers.get("X-Verified-User-Data"):
            raise HTTPException(status_code=401, detail="Missing or invalid connect token")


def _get_persist_volume() -> modal.Volume:
    """Get a handle to the persistent Modal Volume.

    Creates the volume if it doesn't exist. Optionally pins to a specific
    volume version if configured (v2 volumes support better concurrency).

    Returns:
        A modal.Volume handle for the configured persist_vol_name.

    See: https://modal.com/docs/guide/volumes
    """
    kwargs: dict[str, Any] = {"create_if_missing": True}
    if _settings.persist_vol_version is not None:
        kwargs["version"] = _settings.persist_vol_version
    return modal.Volume.from_name(_settings.persist_vol_name, **kwargs)


def _maybe_reload_volume() -> None:
    """Reload the persistent volume to see latest committed writes.

    Called before each query to ensure we have fresh data from other workers
    or previous requests. Only active when volume_commit_interval is configured.

    This is important because Modal Volumes are eventually consistent - without
    reload, we might read stale data cached from container startup.
    """
    if _settings.volume_commit_interval is None:
        return
    try:
        _get_persist_volume().reload()
    except Exception:
        _logger.warning("Failed to reload persistent volume", exc_info=True)


def _maybe_commit_volume(*, force: bool = False) -> None:
    """Commit pending volume writes if enough time has passed.

    Called after each query to persist any files written during execution.
    Uses _LAST_VOLUME_COMMIT_TS to enforce minimum intervals between commits,
    reducing I/O overhead for high-frequency requests.

    Commit behavior:
    - interval is None: No commits (writes persist on sandbox termination)
    - interval <= 0: Commit after every request
    - interval > 0: Commit only if that many seconds have passed
    """
    global _LAST_VOLUME_COMMIT_TS
    interval = _settings.volume_commit_interval
    if interval is None and not force:
        return
    now = time.time()
    should_commit = force
    if interval is not None:
        should_commit = should_commit or (
            _LAST_VOLUME_COMMIT_TS is None
            or interval <= 0
            or (now - _LAST_VOLUME_COMMIT_TS) >= interval
        )
    if should_commit:
        try:
            _get_persist_volume().commit()
            _LAST_VOLUME_COMMIT_TS = now
        except Exception:
            _logger.warning("Failed to commit persistent volume", exc_info=True)


def _job_workspace(job_id: str) -> Path:
    return Path(_settings.agent_fs_root) / "jobs" / job_id


def _ensure_job_workspace(job_id: str | None) -> Path | None:
    if not job_id:
        return None
    root = _job_workspace(job_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_session_id(body: QueryBody) -> str | None:
    """Resolve the session ID from the request body or stored mapping."""
    if body.session_id:
        return body.session_id
    if body.session_key:
        try:
            stored = SESSION_STORE.get(body.session_key)
            if stored:
                return stored
        except Exception:
            _logger.warning("Session store unavailable; falling back to memory cache")
        return SESSION_CACHE.get(body.session_key)
    return None


def _persist_session_id(session_key: str | None, session_id: str | None) -> None:
    """Persist session ID mapping when a session key is provided."""
    if not session_key or not session_id:
        return
    try:
        SESSION_STORE[session_key] = session_id
    except Exception:
        _logger.warning("Session store unavailable; persisting to memory cache only")
        SESSION_CACHE[session_key] = session_id


async def allow_web_only(
    tool_name: str,
    tool_input: dict[str, Any],
    ctx: ToolPermissionContext,
):
    """Permission handler that allows only web-related tools.

    Args:
        tool_name: Name of the tool being requested.
        tool_input: Input parameters for the tool.
        ctx: Permission context.

    Returns:
        PermissionResultAllow if tool is web-related, otherwise PermissionResultDeny.
    """
    if tool_name.startswith("WebSearch") or tool_name.startswith("WebFetch"):
        return PermissionResultAllow(updated_input=tool_input)
    return PermissionResultDeny(message=f"Tool {tool_name} is not allowed")


def _options(
    session_id: str | None = None,
    fork_session: bool = False,
    job_root: Path | None = None,
) -> ClaudeAgentOptions:
    """Build default `ClaudeAgentOptions` used by this service.

    Uses "acceptEdits" permission mode which auto-approves file edits but still
    requires tool permission checks via can_use_tool. This is safer than
    "bypassPermissions" while still enabling autonomous operation in the sandbox.

    Returns:
        A configured `ClaudeAgentOptions` instance using our local MCP servers,
        allowed tools, and `SYSTEM_PROMPT`.
    """
    system_prompt = SYSTEM_PROMPT
    if job_root is not None:
        system_prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            "This is a background job."
            f" Write all created files under {job_root} so they are persisted."
        )
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers=get_mcp_servers(),
        allowed_tools=get_allowed_tools(),
        can_use_tool=allow_web_only,
        # acceptEdits: Auto-approve file operations, but still check tools via can_use_tool.
        # bypassPermissions would skip all checks but isn't allowed with root access.
        permission_mode="acceptEdits",
        resume=session_id,
        fork_session=fork_session,
        max_turns=_settings.agent_max_turns,
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions with structured JSON response.

    Args:
        request: The incoming request.
        exc: The exception that was raised.

    Returns:
        JSONResponse with error details and request ID.
    """
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "request_id": request_id,
        },
    )


@app.get("/health_check")
def health_check():
    """Liveness/readiness probe.

    Uvicorn starts quickly, but downstream dependencies may still be warming
    up. We return a simple OK when the process is ready to receive traffic.

    Curl example (using the discovered `${SERVICE_URL}` from the sandbox):

        ```bash
        curl -sS "${SERVICE_URL}/health_check"
        ```
    """
    return {"ok": True}


@app.post(
    "/query",
    response_model=QueryResponse,
    responses={500: {"model": ErrorResponse}},
)
async def query_agent(body: QueryBody, request: Request) -> QueryResponse:
    """Run a single agent query and return structured messages.

    Args:
        body: `QueryBody` containing the question to ask the agent.

    Returns:
        QueryResponse with `ok`, `messages` list, and `summary`.

    Curl example (using the discovered `${SERVICE_URL}` from the sandbox):

        ```bash
        curl -X POST "${SERVICE_URL}/query" \
          -H 'Content-Type: application/json' \
          -d '{"question":"What is the capital of Canada?"}'
        ```
    """
    _require_connect_token(request)

    _maybe_reload_volume()
    job_root = _ensure_job_workspace(body.job_id)
    resolved_session_id = _resolve_session_id(body)
    request_id = getattr(request.state, "request_id", None)
    _logger.info(
        "agent.query.start",
        extra={"job_id": body.job_id, "request_id": request_id, "session_id": resolved_session_id},
    )
    try:
        messages: list[Message] = []
        result_message: ResultMessage | None = None
        async with ClaudeSDKClient(
            options=_options(
                session_id=resolved_session_id,
                fork_session=body.fork_session,
                job_root=job_root,
            )
        ) as client:
            await client.query(body.question)
            async for msg in client.receive_response():
                messages.append(msg)
                if isinstance(msg, ResultMessage):
                    result_message = msg

        text_blocks = iter_text_blocks(messages)
        final_text = None
        if result_message and result_message.result:
            final_text = result_message.result
        elif text_blocks:
            final_text = "\n".join(text_blocks)

        summary = build_final_summary(result_message, final_text)
        session_id = summary.get("session_id")
        _persist_session_id(body.session_key, session_id)
        _logger.info(
            "agent.query.complete",
            extra={
                "job_id": body.job_id,
                "request_id": request_id,
                "session_id": session_id,
                "duration_ms": summary.get("duration_ms"),
                "num_turns": summary.get("num_turns"),
            },
        )
        return {
            "ok": True,
            "messages": [serialize_message(message) for message in messages],
            "summary": summary,
            "session_id": session_id,
        }
    finally:
        _maybe_commit_volume(force=job_root is not None)


@app.post("/query_stream")
async def query_agent_stream(body: QueryBody, request: Request):
    """Stream agent responses as Server-Sent Events (SSE).

    Args:
        body: `QueryBody` containing the question to ask the agent.
        request: FastAPI request object.

    Returns:
        StreamingResponse with text/event-stream content type.
    """
    _require_connect_token(request)

    def _format_sse(event: str, data: dict[str, Any]) -> str:
        """Format a Server-Sent Event message.

        SSE format: "event: <type>\\ndata: <json>\\n\\n"
        Client receives events via EventSource API or curl with -N flag.
        """
        payload = json.dumps(data, ensure_ascii=True)
        return f"event: {event}\ndata: {payload}\n\n"

    async def sse():
        _maybe_reload_volume()
        job_root = _ensure_job_workspace(body.job_id)
        resolved_session_id = _resolve_session_id(body)
        messages: list[Message] = []
        result_message: ResultMessage | None = None
        request_id = getattr(request.state, "request_id", None)
        _logger.info(
            "agent.query_stream.start",
            extra={
                "job_id": body.job_id,
                "request_id": request_id,
                "session_id": resolved_session_id,
            },
        )
        try:
            async with ClaudeSDKClient(
                options=_options(
                    session_id=resolved_session_id,
                    fork_session=body.fork_session,
                    job_root=job_root,
                )
            ) as client:
                await client.query(body.question)
                async for msg in client.receive_response():
                    messages.append(msg)
                    if isinstance(msg, ResultMessage):
                        result_message = msg
                    serialized = serialize_message(msg)
                    yield _format_sse(serialized["type"], serialized)

            text_blocks = iter_text_blocks(messages)
            final_text = None
            if result_message and result_message.result:
                final_text = result_message.result
            elif text_blocks:
                final_text = "\n".join(text_blocks)

            summary = build_final_summary(result_message, final_text)
            _persist_session_id(body.session_key, summary.get("session_id"))
            _logger.info(
                "agent.query_stream.complete",
                extra={
                    "job_id": body.job_id,
                    "request_id": request_id,
                    "session_id": summary.get("session_id"),
                    "duration_ms": summary.get("duration_ms"),
                    "num_turns": summary.get("num_turns"),
                },
            )
            yield _format_sse("done", summary)
        finally:
            _maybe_commit_volume(force=job_root is not None)

    return StreamingResponse(sse(), media_type="text/event-stream")
