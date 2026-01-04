"""
FastAPI microservice that runs inside a long-lived Modal Sandbox.

This service exposes endpoints:
- `GET /health_check` used by the controller to know when the service is ready.
- `POST /query` which returns a response from the configured agent provider.
- `POST /query_stream` which streams a response from the configured agent provider.

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
from typing import Any

import modal
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from agent_sandbox.config.settings import get_settings
from agent_sandbox.controllers.middleware import RequestIdMiddleware
from agent_sandbox.prompts.prompts import SYSTEM_PROMPT
from agent_sandbox.providers import get_provider
from agent_sandbox.schemas import QueryBody
from agent_sandbox.schemas.responses import ErrorResponse, QueryResponse

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


def _maybe_commit_volume() -> None:
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
    if interval is None:
        return
    now = time.time()
    # Commit if: first commit, interval=0 (always), or enough time elapsed
    if (
        _LAST_VOLUME_COMMIT_TS is None
        or interval <= 0
        or (now - _LAST_VOLUME_COMMIT_TS) >= interval
    ):
        try:
            _get_persist_volume().commit()
            _LAST_VOLUME_COMMIT_TS = now
        except Exception:
            _logger.warning("Failed to commit persistent volume", exc_info=True)


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


def _resolve_provider_id(body: QueryBody) -> str:
    provider_id = body.provider or _settings.agent_provider
    if body.provider and body.provider != _settings.agent_provider:
        raise HTTPException(
            status_code=400,
            detail=f"Provider override '{body.provider}' is not enabled for this deployment",
        )
    return provider_id


def _build_options(body: QueryBody, session_id: str | None) -> tuple[str, Any, Any]:
    provider_id = _resolve_provider_id(body)
    try:
        provider = get_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    mcp_servers = provider.get_mcp_servers()
    allowed_tools = provider.get_allowed_tools()
    permission_handler = None
    if hasattr(provider, "default_tool_permission_handler"):
        permission_handler = provider.default_tool_permission_handler()
    merged_config: dict[str, Any] | None = None
    if _settings.agent_provider_options or body.provider_config:
        merged_config = {**_settings.agent_provider_options, **(body.provider_config or {})}
    options = provider.build_options(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
        session_id=session_id,
        fork_session=body.fork_session,
        max_turns=_settings.agent_max_turns,
        provider_config=merged_config,
        permission_mode="acceptEdits",
        can_use_tool=permission_handler,
    )
    return provider_id, provider, options


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
    resolved_session_id = _resolve_session_id(body)
    provider_id, provider, options = _build_options(body, resolved_session_id)
    try:
        messages: list[Any] = []
        async with provider.create_client(options) as client:
            await client.query(body.question)
            async for msg in client.receive_response():
                messages.append(msg)

        summary = provider.build_summary(messages)
        session_id = summary.get("session_id")
        _persist_session_id(body.session_key, session_id)
        return {
            "ok": True,
            "messages": [provider.serialize_message(message) for message in messages],
            "summary": summary,
            "session_id": session_id,
            "provider": provider_id,
            "provider_payload": summary.pop("provider_payload", None),
        }
    finally:
        _maybe_commit_volume()


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
        resolved_session_id = _resolve_session_id(body)
        provider_id, provider, options = _build_options(body, resolved_session_id)
        messages: list[Any] = []
        try:
            async with provider.create_client(options) as client:
                await client.query(body.question)
                async for msg in client.receive_response():
                    messages.append(msg)
                    serialized = provider.serialize_message(msg)
                    yield _format_sse(serialized["type"], serialized)

            summary = provider.build_summary(messages)
            _persist_session_id(body.session_key, summary.get("session_id"))
            provider_payload = summary.pop("provider_payload", None)
            done_payload = {
                **summary,
                "provider": provider_id,
                "provider_payload": provider_payload,
            }
            yield _format_sse("done", done_payload)
        finally:
            _maybe_commit_volume()

    return StreamingResponse(sse(), media_type="text/event-stream")
