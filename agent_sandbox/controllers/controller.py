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

from agent_sandbox.agents.loop import build_agent_options
from agent_sandbox.config.settings import get_settings
from agent_sandbox.controllers.middleware import RequestIdMiddleware
from agent_sandbox.controllers.serialization import (
    build_final_summary,
    iter_text_blocks,
    serialize_message,
)
from agent_sandbox.jobs import (
    acknowledge_session_cancellation,
    add_message_to_history,
    create_session_metadata,
    is_session_cancelled,
    job_workspace_root,
    mark_session_executing,
    mark_session_idle,
    normalize_job_id,
    record_session_end,
    record_session_start,
)
from agent_sandbox.prompts.prompts import SYSTEM_PROMPT
from agent_sandbox.schemas import QueryBody
from agent_sandbox.schemas.responses import ErrorResponse, QueryResponse
from agent_sandbox.tools import get_allowed_tools, get_mcp_servers
from agent_sandbox.tools.session_tools import set_parent_context

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
    except RuntimeError as exc:
        message = str(exc)
        if "reload() can only be called from within a running function" in message:
            return
        _logger.warning("Failed to reload persistent volume: %s", message)
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
        except RuntimeError as exc:
            message = str(exc)
            # In sandbox context, commit() can only be called on a mounted volume
            if "commit() can only be called" in message:
                return
            _logger.warning("Failed to commit persistent volume: %s", message)
        except Exception:
            _logger.warning("Failed to commit persistent volume", exc_info=True)


def _job_workspace(job_id: str) -> Path:
    """Get the workspace directory path for a specific job.

    Returns the isolated filesystem path where a job should write artifacts,
    logs, and other output files. This is a lightweight wrapper around
    job_workspace_root() that uses the configured agent_fs_root from settings.

    Args:
        job_id: Unique job identifier (should be pre-validated with normalize_job_id,
                but this function does not perform validation itself)

    Returns:
        Path object pointing to the job's workspace directory:
        {agent_fs_root}/jobs/{job_id}/

        Example: /data/jobs/550e8400-e29b-41d4-a716-446655440000/

    Important Behavior:
        - Directory is NOT created automatically by this function
        - Caller must use _ensure_job_workspace() to create if needed
        - Path is returned even if directory doesn't exist yet

    Workspace Isolation:
        Each job gets its own isolated directory to:
        - Prevent cross-job file access
        - Enable safe artifact collection
        - Allow parallel job execution without conflicts
        - Support cleanup by removing entire job directory

    Volume Persistence:
        When agent_fs_root is a Modal persistent volume mount (typically /data):
        - Files written to workspace persist across sandbox restarts
        - Artifacts remain accessible after job completion
        - Volume commits (if configured) ensure durability

    Usage in Endpoints:
        ```python
        @app.post("/query")
        async def query_agent(body: QueryBody):
            job_root = _job_workspace(body.job_id)
            # job_root exists but may not be created yet
            # Use _ensure_job_workspace() to create if needed
        ```

    See Also:
        - _ensure_job_workspace(): Creates workspace directory
        - job_workspace_root(): Underlying implementation from jobs module
        - agent_fs_root in settings: Configured volume mount point
    """
    return job_workspace_root(_settings.agent_fs_root, job_id)


def _ensure_job_workspace(job_id: str | None) -> Path | None:
    """Create and return the workspace directory for a job, validating job_id.

    Validates the job_id, creates the workspace directory if it doesn't exist,
    and returns the Path. This is the primary function to call before job
    execution to ensure the workspace is ready for artifact writes.

    Args:
        job_id: Potential job identifier from user input (HTTP request, queue payload).
                Can be None (for non-job queries), invalid UUID, or valid UUID.

    Returns:
        - Path to created workspace directory if job_id is valid
        - None if job_id is None (non-job execution) or fails validation

    Security & Validation:
        This function performs security validation before filesystem operations:
        1. Checks if job_id is None → return None (non-job query)
        2. Validates job_id format using normalize_job_id()
        3. Rejects invalid UUIDs to prevent path traversal attacks
        4. Only creates directories for validated job IDs

    Directory Creation:
        Uses mkdir(parents=True, exist_ok=True) which:
        - Creates all intermediate directories (/data, /data/jobs, /data/jobs/{id})
        - Succeeds silently if directory already exists (exist_ok=True)
        - Raises PermissionError if filesystem is read-only (unlikely in Modal sandbox)

    When Called:
        This function is called at the start of every job-related query execution:
        - Before agent SDK query() is invoked
        - After volume reload (to see latest committed state)
        - In both /query and /query_stream endpoints

    Volume Interaction:
        If agent_fs_root is a Modal persistent volume:
        - Directory persists across sandbox restarts
        - Subsequent jobs reuse existing workspace (don't re-create)
        - Volume commits preserve directory and contents

    Examples:
        Valid job_id (directory created):
        >>> workspace = _ensure_job_workspace("550e8400-e29b-41d4-a716-446655440000")
        >>> workspace
        PosixPath('/data/jobs/550e8400-e29b-41d4-a716-446655440000')
        >>> workspace.exists()
        True

        None job_id (non-job query):
        >>> workspace = _ensure_job_workspace(None)
        >>> workspace
        None

        Invalid job_id (security rejection):
        >>> workspace = _ensure_job_workspace("../../../etc/passwd")
        >>> workspace
        None  # Path traversal blocked

        >>> workspace = _ensure_job_workspace("not-a-uuid")
        >>> workspace
        None  # Invalid UUID format

    Usage in Endpoints:
        ```python
        @app.post("/query")
        async def query_agent(body: QueryBody):
            # Validate and create workspace if job_id provided
            job_root = _ensure_job_workspace(body.job_id)

            # Pass to agent options to inform system prompt
            async with ClaudeSDKClient(options=_options(job_root=job_root)):
                # Agent writes to job_root if not None
                await client.query(body.question)

            # Force commit if job workspace was used
            _maybe_commit_volume(force=job_root is not None)
        ```

    System Prompt Integration:
        When job_root is not None, _options() extends the system prompt:
        "This is a background job. Write all created files under {job_root}
        so they are persisted."

        This guides the agent to write outputs to the correct location for
        artifact collection.

    Error Handling:
        - Invalid job_id → Returns None (caller should handle gracefully)
        - Filesystem errors → Raises exception (OSError, PermissionError)
        - Volume unavailable → May raise VolumeError

    See Also:
        - _job_workspace(): Get path without validation or creation
        - normalize_job_id(): UUID validation logic
        - job_workspace_root(): Path construction from jobs module
        - _options(): Uses job_root to extend system prompt
    """
    if not job_id:
        return None
    normalized = normalize_job_id(job_id)
    if not normalized:
        return None
    root = _job_workspace(normalized)
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


def _make_can_use_tool_handler(session_id: str | None = None):
    """Create a tool permission handler with session-aware cancellation checking.

    This factory function creates a closure that has access to the session_id,
    enabling the handler to check for cancellation requests before allowing tools.

    Args:
        session_id: The session ID to check for cancellation. If None,
            cancellation checking is skipped.

    Returns:
        An async function suitable for use as ClaudeAgentOptions.can_use_tool.

    Usage:
        ```python
        options = ClaudeAgentOptions(
            can_use_tool=_make_can_use_tool_handler(session_id="sess_123"),
            ...
        )
        ```
    """

    async def can_use_tool(
        tool_name: str,
        tool_input: dict[str, Any],
        ctx: ToolPermissionContext,
    ):
        """Permission handler that checks cancellation and allows web-related tools.

        This handler is called before each tool invocation. It:
        1. Checks if the session has been cancelled (via POST /session/{id}/stop)
        2. If cancelled, denies the tool call with a cancellation message
        3. Otherwise, allows only web-related tools (WebSearch, WebFetch)

        Args:
            tool_name: Name of the tool being requested.
            tool_input: Input parameters for the tool.
            ctx: Permission context from the Agent SDK.

        Returns:
            PermissionResultDeny if session is cancelled or tool is not allowed.
            PermissionResultAllow if tool is allowed and session is active.
        """
        # Check for session cancellation first
        if session_id and _settings.enable_session_cancellation:
            if is_session_cancelled(session_id):
                # Acknowledge the cancellation so it's tracked
                acknowledge_session_cancellation(session_id)
                _logger.info(
                    "agent.tool_denied.cancelled",
                    extra={"session_id": session_id, "tool_name": tool_name},
                )
                return PermissionResultDeny(
                    message=(
                        "Session has been cancelled by the user. "
                        "Please stop execution and summarize what was accomplished."
                    )
                )

        # Allow web-related tools
        if tool_name.startswith("WebSearch") or tool_name.startswith("WebFetch"):
            return PermissionResultAllow(updated_input=tool_input)

        return PermissionResultDeny(message=f"Tool {tool_name} is not allowed")

    return can_use_tool


async def allow_web_only(
    tool_name: str,
    tool_input: dict[str, Any],
    ctx: ToolPermissionContext,
):
    """Permission handler that allows only web-related tools (legacy, non-cancellable).

    This is the original handler without session cancellation support.
    Use _make_can_use_tool_handler() for sessions that need cancellation support.

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


def _is_session_resume_error(result_message: ResultMessage | None) -> bool:
    """Check if a result indicates a failed session resume attempt.

    The Claude SDK returns error_during_execution with num_turns=0 when
    trying to resume a session that doesn't exist. This helper detects
    that specific failure mode.

    Args:
        result_message: The ResultMessage from the SDK response.

    Returns:
        True if this looks like a session resume failure.
    """
    if not result_message:
        return False
    # Check for error_during_execution at turn 0 (failed before starting)
    return (
        getattr(result_message, "subtype", None) == "error_during_execution"
        and getattr(result_message, "num_turns", -1) == 0
        and getattr(result_message, "is_error", False) is True
    )


async def _execute_agent_query(
    question: str,
    session_id: str | None,
    fork_session: bool,
    job_root: Path | None,
) -> tuple[list[Message], ResultMessage | None]:
    """Execute an agent query and return messages.

    Args:
        question: The question to ask the agent.
        session_id: Optional session ID to resume.
        fork_session: Whether to fork the session.
        job_root: Optional job workspace root.

    Returns:
        Tuple of (messages list, result_message or None).
    """
    messages: list[Message] = []
    result_message: ResultMessage | None = None

    async with ClaudeSDKClient(
        options=_options(
            session_id=session_id,
            fork_session=fork_session,
            job_root=job_root,
        )
    ) as client:
        await client.query(question)
        async for msg in client.receive_response():
            messages.append(msg)
            if isinstance(msg, ResultMessage):
                result_message = msg

    return messages, result_message


def _options(
    session_id: str | None = None,
    fork_session: bool = False,
    job_root: Path | None = None,
) -> ClaudeAgentOptions:
    """Build default `ClaudeAgentOptions` used by this service.

    Uses "acceptEdits" permission mode which auto-approves file edits but still
    requires tool permission checks via can_use_tool. This is safer than
    "bypassPermissions" while still enabling autonomous operation in the sandbox.

    The can_use_tool handler is created via _make_can_use_tool_handler() to enable
    session cancellation support. When session_id is provided, the handler will
    check for cancellation before each tool call.

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
    options = build_agent_options(
        get_mcp_servers(),
        get_allowed_tools(),
        system_prompt,
        resume=session_id,
        fork_session=fork_session,
        max_turns=_settings.agent_max_turns,
    )
    # NOTE: claude_agent_sdk.query() requires AsyncIterable prompts when using can_use_tool.
    # For now, keep can_use_tool unset to avoid errors on string prompts.
    return options


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
    _logger.exception(
        "Uncaught exception in controller",
        extra={"request_id": request_id, "error_type": type(exc).__name__},
    )
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

    # Record session start for statistics tracking
    record_session_start(sandbox_type="agent_sdk", job_id=body.job_id, user_id=body.user_id)
    start_time = time.time()
    final_status = "failed"
    final_session_id: str | None = None

    # Mark session as executing (for prompt queue feature)
    # We mark using resolved_session_id initially; will update after SDK returns actual ID
    if resolved_session_id and _settings.enable_prompt_queue:
        mark_session_executing(resolved_session_id)

    try:
        # Enable child-session tools for this parent context
        set_parent_context(body.job_id or resolved_session_id)

        # Execute query, potentially with session resume
        messages, result_message = await _execute_agent_query(
            question=body.question,
            session_id=resolved_session_id,
            fork_session=body.fork_session,
            job_root=job_root,
        )

        # Check if session resume failed - retry without session_id
        if resolved_session_id and _is_session_resume_error(result_message):
            _logger.warning(
                "Session resume failed, retrying with new session",
                extra={
                    "original_session_id": resolved_session_id,
                    "request_id": request_id,
                },
            )
            # Retry without session_id (creates new session)
            messages, result_message = await _execute_agent_query(
                question=body.question,
                session_id=None,  # Don't try to resume
                fork_session=False,
                job_root=job_root,
            )

        text_blocks = iter_text_blocks(messages)
        final_text = None
        if result_message and result_message.result:
            final_text = result_message.result
        elif text_blocks:
            final_text = "\n".join(text_blocks)

        summary = build_final_summary(result_message, final_text)
        final_session_id = summary.get("session_id")
        _persist_session_id(body.session_key, final_session_id)

        # Track message history for multiplayer sessions
        if _settings.enable_multiplayer_sessions and final_session_id:
            try:
                # Create/update session metadata with owner if this is a new session
                if not resolved_session_id and body.user_id:
                    create_session_metadata(final_session_id, owner_id=body.user_id)

                # Record user message with attribution
                add_message_to_history(
                    session_id=final_session_id,
                    role="user",
                    content=body.question,
                    user_id=body.user_id,
                    turn_number=summary.get("num_turns"),
                )

                # Record assistant response
                if final_text:
                    add_message_to_history(
                        session_id=final_session_id,
                        role="assistant",
                        content=final_text,
                        turn_number=summary.get("num_turns"),
                        tokens_used=summary.get("tokens_used"),
                    )
            except Exception as e:
                _logger.warning(
                    "Session metadata operation failed - continuing without history",
                    extra={"session_id": final_session_id, "error": str(e)},
                )
                # Don't fail the query - metadata is optional

        _logger.info(
            "agent.query.complete",
            extra={
                "job_id": body.job_id,
                "request_id": request_id,
                "session_id": final_session_id,
                "duration_ms": summary.get("duration_ms"),
                "num_turns": summary.get("num_turns"),
            },
        )
        final_status = "complete"
        return {
            "ok": True,
            "messages": [serialize_message(message) for message in messages],
            "summary": summary,
            "session_id": final_session_id,
        }
    finally:
        set_parent_context(None)
        # Record session end for statistics tracking
        duration_ms = int((time.time() - start_time) * 1000)
        record_session_end(
            sandbox_type="agent_sdk",
            status=final_status,
            duration_ms=duration_ms,
        )
        # Mark session as idle (for prompt queue feature)
        if _settings.enable_prompt_queue:
            session_to_mark = final_session_id or resolved_session_id
            if session_to_mark:
                mark_session_idle(session_to_mark)
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
        final_session_id: str | None = None
        _logger.info(
            "agent.query_stream.start",
            extra={
                "job_id": body.job_id,
                "request_id": request_id,
                "session_id": resolved_session_id,
            },
        )

        # Record session start for statistics tracking
        record_session_start(sandbox_type="agent_sdk", job_id=body.job_id, user_id=body.user_id)
        start_time = time.time()
        final_status = "failed"

        # Mark session as executing (for prompt queue feature)
        if resolved_session_id and _settings.enable_prompt_queue:
            mark_session_executing(resolved_session_id)

        try:
            # Enable child-session tools for this parent context
            set_parent_context(body.job_id or resolved_session_id)

            # Track if we need to retry due to session resume failure
            session_to_use = resolved_session_id
            retry_needed = False

            async with ClaudeSDKClient(
                options=_options(
                    session_id=session_to_use,
                    fork_session=body.fork_session,
                    job_root=job_root,
                )
            ) as client:
                await client.query(body.question)
                async for msg in client.receive_response():
                    messages.append(msg)
                    if isinstance(msg, ResultMessage):
                        result_message = msg
                        # Check if this is a session resume error before yielding
                        if session_to_use and _is_session_resume_error(result_message):
                            retry_needed = True
                            break  # Don't yield error, we'll retry
                    if not retry_needed:
                        serialized = serialize_message(msg)
                        yield _format_sse(serialized["type"], serialized)

            # If session resume failed, retry with new session
            if retry_needed:
                _logger.warning(
                    "Session resume failed in stream, retrying with new session",
                    extra={
                        "original_session_id": session_to_use,
                        "request_id": request_id,
                    },
                )
                # Clear and retry
                messages = []
                result_message = None
                async with ClaudeSDKClient(
                    options=_options(
                        session_id=None,  # New session
                        fork_session=False,
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
            final_session_id = summary.get("session_id")
            _persist_session_id(body.session_key, final_session_id)

            # Track message history for multiplayer sessions
            if _settings.enable_multiplayer_sessions and final_session_id:
                try:
                    # Create/update session metadata with owner if this is a new session
                    if not resolved_session_id and body.user_id:
                        create_session_metadata(final_session_id, owner_id=body.user_id)

                    # Record user message with attribution
                    add_message_to_history(
                        session_id=final_session_id,
                        role="user",
                        content=body.question,
                        user_id=body.user_id,
                        turn_number=summary.get("num_turns"),
                    )

                    # Record assistant response
                    if final_text:
                        add_message_to_history(
                            session_id=final_session_id,
                            role="assistant",
                            content=final_text,
                            turn_number=summary.get("num_turns"),
                            tokens_used=summary.get("tokens_used"),
                        )
                except Exception as e:
                    _logger.warning(
                        "Session metadata operation failed - continuing without history",
                        extra={"session_id": final_session_id, "error": str(e)},
                    )
                    # Don't fail the query - metadata is optional

            _logger.info(
                "agent.query_stream.complete",
                extra={
                    "job_id": body.job_id,
                    "request_id": request_id,
                    "session_id": final_session_id,
                    "duration_ms": summary.get("duration_ms"),
                    "num_turns": summary.get("num_turns"),
                },
            )
            final_status = "complete"
            yield _format_sse("done", summary)
        finally:
            set_parent_context(None)
            # Record session end for statistics tracking
            duration_ms = int((time.time() - start_time) * 1000)
            record_session_end(
                sandbox_type="agent_sdk",
                status=final_status,
                duration_ms=duration_ms,
            )
            # Mark session as idle (for prompt queue feature)
            if _settings.enable_prompt_queue:
                session_to_mark = final_session_id or resolved_session_id
                if session_to_mark:
                    mark_session_idle(session_to_mark)
            _maybe_commit_volume(force=job_root is not None)

    return StreamingResponse(sse(), media_type="text/event-stream")
