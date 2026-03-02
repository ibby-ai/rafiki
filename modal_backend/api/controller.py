"""FastAPI microservice running inside the long-lived Modal sandbox."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import modal
from agents import ItemHelpers, Runner
from agents.result import RunResultStreaming
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from modal_backend.agent_runtime import build_agent_options, ensure_session, get_agent_config
from modal_backend.api.middleware import RequestIdMiddleware
from modal_backend.api.serialization import (
    build_final_summary,
    iter_text_blocks,
    serialize_message,
)
from modal_backend.jobs import (
    acknowledge_session_cancellation,
    add_message_to_history,
    create_session_metadata,
    is_session_cancelled,
    job_workspace_root,
    normalize_job_id,
    record_session_end,
    record_session_start,
)
from modal_backend.mcp_tools.session_tools import reset_parent_context, set_parent_context
from modal_backend.models import QueryBody
from modal_backend.models.base import BaseSchema
from modal_backend.models.responses import ErrorResponse, QueryResponse
from modal_backend.security.cloudflare_auth import internal_auth_middleware
from modal_backend.security.runtime_hardening import (
    RuntimeHardeningReport,
    apply_runtime_hardening,
)
from modal_backend.settings.settings import get_settings
from modal_backend.tracing import langsmith_run_context

app = FastAPI()
app.add_middleware(RequestIdMiddleware)
app.middleware("http")(internal_auth_middleware)
_settings = get_settings()
_logger = logging.getLogger(__name__)

_LAST_VOLUME_COMMIT_TS: float | None = None
_FALLBACK_SESSION_DB_PATH = "/tmp/openai_agents_sessions.sqlite3"


@dataclass
class _ActiveClientState:
    """State tracked for an active or initializing session run."""

    stop_event: asyncio.Event
    run: RunResultStreaming | None = None
    immediate_cancel_requested: bool = False


# session_id -> active run state
ACTIVE_CLIENTS: dict[str, _ActiveClientState] = {}
_RUNTIME_HARDENING_REPORT: RuntimeHardeningReport | None = None


def _is_modal_auth_error(exc: Exception) -> bool:
    return exc.__class__.__name__ == "AuthError"


def _is_session_db_path_writable(db_path: str) -> bool:
    if db_path == ":memory:":
        return True
    candidate = Path(db_path)
    if candidate.exists():
        target = candidate
    else:
        target = candidate.parent
        # Walk to the nearest existing directory so nested-but-creatable paths
        # are treated as writable.
        while not target.exists():
            parent = target.parent
            if parent == target:
                return False
            target = parent
        if not target.is_dir():
            return False
    try:
        mode = os.W_OK if target == candidate else (os.W_OK | os.X_OK)
        return os.access(target, mode)
    except OSError:
        return False


def _ensure_openai_session_db_path_writable() -> None:
    current_path = _settings.openai_session_db_path
    if _is_session_db_path_writable(current_path):
        return

    _settings.openai_session_db_path = _FALLBACK_SESSION_DB_PATH
    _logger.warning(
        "controller.openai_session_db_fallback",
        extra={
            "db_path": current_path,
            "fallback_db_path": _FALLBACK_SESSION_DB_PATH,
        },
    )


def _record_session_start_best_effort(
    *,
    sandbox_type: str,
    job_id: str | None,
    user_id: str | None,
) -> None:
    try:
        record_session_start(
            sandbox_type=sandbox_type,
            job_id=job_id,
            user_id=user_id,
        )
    except Exception as exc:
        if _is_modal_auth_error(exc):
            _logger.warning(
                "Session start metrics skipped: Modal auth token unavailable in sandbox runtime",
                extra={"job_id": job_id, "user_id": user_id},
            )
            return
        raise


def _record_session_end_best_effort(
    *,
    sandbox_type: str,
    status: str,
    duration_ms: int,
) -> None:
    try:
        record_session_end(
            sandbox_type=sandbox_type,
            status=status,
            duration_ms=duration_ms,
        )
    except Exception as exc:
        if _is_modal_auth_error(exc):
            _logger.warning(
                "Session end metrics skipped: Modal auth token unavailable in sandbox runtime",
                extra={"status": status, "duration_ms": duration_ms},
            )
            return
        raise


@app.on_event("startup")
async def _apply_runtime_hardening_on_startup() -> None:
    """Apply startup hardening before handling query traffic."""
    global _RUNTIME_HARDENING_REPORT
    _RUNTIME_HARDENING_REPORT = apply_runtime_hardening(_settings.agent_fs_root)
    _ensure_openai_session_db_path_writable()
    _logger.info(
        "controller.runtime_hardening",
        extra={
            "privilege_status": _RUNTIME_HARDENING_REPORT.privilege_status,
            "initial_uid": _RUNTIME_HARDENING_REPORT.initial_uid,
            "final_uid": _RUNTIME_HARDENING_REPORT.final_uid,
            "scrubbed_keys": _RUNTIME_HARDENING_REPORT.scrubbed_keys,
            "warnings": _RUNTIME_HARDENING_REPORT.warnings,
        },
    )


@app.get("/runtime_hardening")
async def runtime_hardening_status() -> dict[str, object]:
    """Expose runtime hardening status for runbook verification."""
    if _RUNTIME_HARDENING_REPORT is None:
        return {"ok": False, "error": "Runtime hardening not initialized"}
    return {"ok": True, "report": _RUNTIME_HARDENING_REPORT.model_dump()}


def _preregister_active_client(session_id: str, stop_event: asyncio.Event) -> _ActiveClientState:
    """Ensure a session state exists before run initialization completes."""
    state = ACTIVE_CLIENTS.get(session_id)
    if state is None:
        state = _ActiveClientState(stop_event=stop_event)
        ACTIVE_CLIENTS[session_id] = state
        return state
    state.stop_event = stop_event
    return state


def _attach_run_to_active_client(
    session_id: str,
    run: RunResultStreaming,
    stop_event: asyncio.Event,
) -> _ActiveClientState:
    """Attach a run to session state and honor any queued immediate stop."""
    state = _preregister_active_client(session_id, stop_event)
    state.run = run
    if state.immediate_cancel_requested:
        run.cancel(mode="immediate")
    return state


def _is_tool_allowed(tool_name: str, allowed_tools: list[str]) -> bool:
    """Return whether a tool is allowed by exact or wildcard legacy patterns."""
    if tool_name in allowed_tools:
        return True

    for allowed in allowed_tools:
        if not allowed.endswith("(*)"):
            continue
        prefix = allowed[:-3]
        if tool_name == prefix or tool_name.startswith(f"{prefix}("):
            return True
    return False


# -----------------------------------------------------------------------------
# Shared filesystem helpers
# -----------------------------------------------------------------------------


def _require_connect_token(request: Request) -> None:
    if _settings.enforce_connect_token and not request.headers.get("X-Verified-User-Data"):
        raise HTTPException(status_code=401, detail="Missing or invalid connect token")


def _get_persist_volume() -> modal.Volume:
    kwargs: dict[str, Any] = {"create_if_missing": True}
    if _settings.persist_vol_version is not None:
        kwargs["version"] = _settings.persist_vol_version
    return modal.Volume.from_name(_settings.persist_vol_name, **kwargs)


def _maybe_reload_volume() -> None:
    if _settings.volume_commit_interval is None:
        return
    try:
        _get_persist_volume().reload()
    except RuntimeError as exc:
        if "reload() can only be called from within a running function" not in str(exc):
            _logger.warning("Failed to reload persistent volume: %s", exc)
    except Exception:
        _logger.warning("Failed to reload persistent volume", exc_info=True)


def _maybe_commit_volume(*, force: bool = False) -> None:
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

    if not should_commit:
        return

    try:
        _get_persist_volume().commit()
        _LAST_VOLUME_COMMIT_TS = now
    except RuntimeError as exc:
        if "commit() can only be called" not in str(exc):
            _logger.warning("Failed to commit persistent volume: %s", exc)
    except Exception:
        _logger.warning("Failed to commit persistent volume", exc_info=True)


def _job_workspace(job_id: str) -> Path:
    return job_workspace_root(_settings.agent_fs_root, job_id)


def _ensure_job_workspace(job_id: str | None) -> Path | None:
    if not job_id:
        return None
    normalized = normalize_job_id(job_id)
    if not normalized:
        return None
    root = _job_workspace(normalized)
    root.mkdir(parents=True, exist_ok=True)
    return root


# -----------------------------------------------------------------------------
# Agent run helpers
# -----------------------------------------------------------------------------


def _resolve_session_id(body: QueryBody) -> str | None:
    return body.session_id


def _resolve_trace_id(body: QueryBody, request_id: str | None) -> str:
    if body.trace_id:
        return body.trace_id
    if request_id:
        return request_id
    return str(uuid4())


def _build_system_prompt(agent_type: str, job_root: Path | None) -> str:
    config = get_agent_config(agent_type)
    system_prompt = config.system_prompt
    if job_root is not None:
        system_prompt = (
            f"{system_prompt}\n\n"
            "This is a background job."
            f" Write all created files under {job_root} so they are persisted."
        )
    return system_prompt


def _build_agent(agent_type: str, system_prompt: str):
    config = get_agent_config(agent_type)
    max_turns = config.max_turns or _settings.agent_max_turns or 50
    agent = build_agent_options(
        config.get_mcp_servers(),
        config.get_effective_allowed_tools(),
        system_prompt,
        subagents=config.get_subagents(),
    )
    return agent, max_turns


def _safe_json_loads(data: str | None) -> Any:
    if not data:
        return {}
    try:
        return json.loads(data)
    except Exception:
        return {"raw": data}


def _stringify_tool_output(output: Any) -> Any:
    if output is None:
        return ""
    if isinstance(output, str | int | float | bool | dict | list):
        return output
    return str(output)


def _usage_to_dict(run: RunResultStreaming) -> dict[str, Any] | None:
    requests_count = 0
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0

    raw_responses = getattr(run, "raw_responses", []) or []
    for response in raw_responses:
        usage = getattr(response, "usage", None)
        if usage is None:
            continue
        requests_count += int(getattr(usage, "requests", 0) or 0)
        input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        total_tokens += int(getattr(usage, "total_tokens", 0) or 0)

    if requests_count == 0 and input_tokens == 0 and output_tokens == 0 and total_tokens == 0:
        return None

    return {
        "requests": requests_count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _extract_openai_trace_id(run: RunResultStreaming | Any) -> str | None:
    """Best-effort extraction of OpenAI trace/request correlation id."""
    candidate_fields = (
        "openai_trace_id",
        "trace_id",
        "response_id",
        "openai_request_id",
        "request_id",
    )

    def _extract_from_obj(value: Any) -> str | None:
        if value is None:
            return None
        for field_name in candidate_fields:
            field_value = None
            if isinstance(value, dict):
                field_value = value.get(field_name)
            else:
                field_value = getattr(value, field_name, None)
            if isinstance(field_value, str) and field_value.strip():
                return field_value.strip()
        return None

    direct = _extract_from_obj(run)
    if direct:
        return direct

    raw_responses = getattr(run, "raw_responses", []) or []
    for response in raw_responses:
        response_trace_id = _extract_from_obj(response)
        if response_trace_id:
            return response_trace_id
    return None


def _json_safe_structured_output(value: Any) -> Any | None:
    """Return value only when it can be JSON-serialized as-is."""
    if value is None:
        return None
    if isinstance(value, str | int | float | bool | dict | list):
        try:
            json.dumps(value, ensure_ascii=True)
        except TypeError:
            return None
        return value
    return None


def _make_result_message(
    *,
    session_id: str,
    trace_id: str,
    duration_ms: int,
    final_output: Any,
    run: RunResultStreaming,
    is_error: bool,
    subtype: str,
    openai_trace_id: str | None = None,
) -> dict[str, Any]:
    usage = _usage_to_dict(run)
    final_text = (
        final_output
        if isinstance(final_output, str)
        else (str(final_output) if final_output else None)
    )
    return {
        "type": "result",
        "subtype": subtype,
        "duration_ms": duration_ms,
        "duration_api_ms": None,
        "is_error": is_error,
        "num_turns": getattr(run, "current_turn", None),
        "session_id": session_id,
        "trace_id": trace_id,
        "openai_trace_id": openai_trace_id,
        "total_cost_usd": None,
        "usage": usage,
        "result": final_text,
        "structured_output": (
            _json_safe_structured_output(final_output)
            if not isinstance(final_output, str)
            else None
        ),
    }


def _messages_from_run_event(
    event: Any,
    current_model: str,
    *,
    allowed_tools: list[str],
    session_id: str,
    trace_id: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    if getattr(event, "type", None) != "run_item_stream_event":
        return out

    name = getattr(event, "name", "")
    item = getattr(event, "item", None)
    item_type = getattr(item, "type", "")

    if name == "message_output_created" and item_type == "message_output_item":
        text = ItemHelpers.text_message_output(item)
        if text:
            out.append(
                {
                    "type": "assistant",
                    "content": [{"type": "text", "text": text}],
                    "model": current_model,
                    "parent_tool_use_id": None,
                    "error": None,
                    "session_id": session_id,
                    "trace_id": trace_id,
                }
            )
        return out

    if name == "tool_called" and item_type == "tool_call_item":
        raw = getattr(item, "raw_item", None)
        arguments = _safe_json_loads(getattr(raw, "arguments", None))
        call_id = getattr(raw, "call_id", None) or getattr(raw, "id", None)
        tool_name = getattr(raw, "name", "tool")
        if not _is_tool_allowed(tool_name, allowed_tools):
            out.append(
                {
                    "type": "assistant",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": f"Blocked tool call: {tool_name}",
                            "is_error": True,
                        }
                    ],
                    "model": current_model,
                    "parent_tool_use_id": call_id,
                    "error": "tool_not_allowed",
                    "session_id": session_id,
                    "trace_id": trace_id,
                }
            )
            return out
        out.append(
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": tool_name,
                        "input": arguments,
                    }
                ],
                "model": current_model,
                "parent_tool_use_id": None,
                "error": None,
                "session_id": session_id,
                "trace_id": trace_id,
            }
        )
        return out

    if name == "tool_output" and item_type == "tool_call_output_item":
        raw = getattr(item, "raw_item", None)
        call_id = getattr(raw, "call_id", None)
        output = _stringify_tool_output(getattr(item, "output", None))
        out.append(
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": output,
                        "is_error": False,
                    }
                ],
                "model": current_model,
                "parent_tool_use_id": call_id,
                "error": None,
                "session_id": session_id,
                "trace_id": trace_id,
            }
        )
        return out

    return out


async def _watch_for_cancellation(
    run: RunResultStreaming,
    session_id: str,
    stop_event: asyncio.Event,
) -> None:
    """Watch for graceful cancellation requests while a run is active."""
    cancelled = False
    while not run.is_complete:
        if stop_event.is_set() and not cancelled:
            run.cancel(mode="after_turn")
            cancelled = True

        if (
            _settings.enable_session_cancellation
            and is_session_cancelled(session_id)
            and not cancelled
        ):
            acknowledge_session_cancellation(session_id)
            run.cancel(mode="after_turn")
            cancelled = True

        await asyncio.sleep(0.2)


async def _execute_agent_query(
    question: str,
    session_id: str | None,
    fork_session: bool,
    job_root: Path | None,
    stop_event: asyncio.Event | None = None,
    agent_type: str = "default",
    trace_id: str = "",
    trace_metadata: dict[str, Any] | None = None,
    on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Execute an agent query and collect serialized message objects."""
    stop_event = stop_event or asyncio.Event()
    pre_registered_id: str | None = session_id if session_id and not fork_session else None
    pre_registered_state: _ActiveClientState | None = None
    if pre_registered_id:
        pre_registered_state = _preregister_active_client(pre_registered_id, stop_event)

    config = get_agent_config(agent_type)
    allowed_tools = config.get_effective_allowed_tools()
    system_prompt = _build_system_prompt(agent_type, job_root)
    agent, max_turns = _build_agent(agent_type, system_prompt)

    try:
        session, resolved_session_id = await ensure_session(
            session_id,
            fork_session=fork_session,
            db_path=_settings.openai_session_db_path,
        )

        if pre_registered_id and pre_registered_id != resolved_session_id:
            if ACTIVE_CLIENTS.get(pre_registered_id) is pre_registered_state:
                ACTIVE_CLIENTS.pop(pre_registered_id, None)
    except Exception:
        if pre_registered_id and ACTIVE_CLIENTS.get(pre_registered_id) is pre_registered_state:
            ACTIVE_CLIENTS.pop(pre_registered_id, None)
        raise

    messages: list[dict[str, Any]] = []
    started = time.time()
    current_model = str(getattr(agent, "model", _settings.openai_model_default))
    metadata = dict(trace_metadata or {})
    metadata.setdefault("trace_id", trace_id)
    metadata.setdefault("agent_type", agent_type)
    metadata.setdefault("session_id", resolved_session_id)
    run: RunResultStreaming | None = None
    error: Exception | None = None
    active_state: _ActiveClientState | None = None
    watcher: asyncio.Task[Any] | None = None
    try:
        with langsmith_run_context(metadata):
            run = Runner.run_streamed(
                agent,
                question,
                session=session,
                max_turns=max_turns,
            )
            active_state = _attach_run_to_active_client(resolved_session_id, run, stop_event)
            watcher = asyncio.create_task(
                _watch_for_cancellation(run, resolved_session_id, stop_event)
            )

            try:
                async for event in run.stream_events():
                    if getattr(event, "type", None) == "agent_updated_stream_event":
                        new_agent = getattr(event, "new_agent", None)
                        if new_agent is not None:
                            current_model = str(getattr(new_agent, "model", current_model))

                    event_messages = _messages_from_run_event(
                        event,
                        current_model,
                        allowed_tools=allowed_tools,
                        session_id=resolved_session_id,
                        trace_id=trace_id,
                    )
                    for msg in event_messages:
                        messages.append(msg)
                        if on_message is not None:
                            await on_message(msg)
            except Exception as exc:
                error = exc
            finally:
                if watcher is not None:
                    watcher.cancel()
                    with contextlib.suppress(Exception, asyncio.CancelledError):
                        await watcher
                if (
                    active_state is not None
                    and ACTIVE_CLIENTS.get(resolved_session_id) is active_state
                ):
                    ACTIVE_CLIENTS.pop(resolved_session_id, None)
    except Exception as exc:
        error = exc

    if run is None:
        run = SimpleNamespace(raw_responses=[], current_turn=None, final_output=None)

    duration_ms = int((time.time() - started) * 1000)

    if error is not None:
        openai_trace_id = _extract_openai_trace_id(run)
        result_message = _make_result_message(
            session_id=resolved_session_id,
            trace_id=trace_id,
            duration_ms=duration_ms,
            final_output=str(error),
            run=run,
            is_error=True,
            subtype="error_during_execution",
            openai_trace_id=openai_trace_id,
        )
    else:
        openai_trace_id = _extract_openai_trace_id(run)
        cancelled = stop_event.is_set() or (
            _settings.enable_session_cancellation and is_session_cancelled(resolved_session_id)
        )
        result_message = _make_result_message(
            session_id=resolved_session_id,
            trace_id=trace_id,
            duration_ms=duration_ms,
            final_output=getattr(run, "final_output", None),
            run=run,
            is_error=False,
            subtype="cancelled" if cancelled else "success",
            openai_trace_id=openai_trace_id,
        )

    messages.append(result_message)
    if on_message is not None:
        await on_message(result_message)

    return messages, result_message, resolved_session_id


# -----------------------------------------------------------------------------
# FastAPI handlers
# -----------------------------------------------------------------------------
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
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
    return {"ok": True}


@app.post(
    "/query",
    response_model=QueryResponse,
    responses={500: {"model": ErrorResponse}},
)
async def query_agent(body: QueryBody, request: Request) -> QueryResponse:
    _require_connect_token(request)

    _maybe_reload_volume()
    job_root = _ensure_job_workspace(body.job_id)
    resolved_session_id = _resolve_session_id(body)
    request_id = getattr(request.state, "request_id", None)
    trace_id = _resolve_trace_id(body, request_id)

    _logger.info(
        "agent.query.start",
        extra={
            "job_id": body.job_id,
            "request_id": request_id,
            "session_id": resolved_session_id,
            "trace_id": trace_id,
        },
    )

    _record_session_start_best_effort(
        sandbox_type="agent_sdk",
        job_id=body.job_id,
        user_id=body.user_id,
    )
    start_time = time.time()
    final_status = "failed"
    final_session_id: str | None = None

    stop_event = asyncio.Event()
    parent_context_token = set_parent_context(body.job_id or resolved_session_id)

    try:
        messages, result_message, final_session_id = await _execute_agent_query(
            question=body.question,
            session_id=resolved_session_id,
            fork_session=body.fork_session,
            job_root=job_root,
            stop_event=stop_event,
            agent_type=body.agent_type,
            trace_id=trace_id,
            trace_metadata={
                "trace_id": trace_id,
                "request_id": request_id,
                "session_id": resolved_session_id,
                "job_id": body.job_id,
                "user_id": body.user_id,
                "tenant_id": body.tenant_id,
            },
        )

        text_blocks = iter_text_blocks(messages)
        final_text = result_message.get("result") if result_message else None
        if not final_text and text_blocks:
            final_text = "\n".join(text_blocks)

        summary = build_final_summary(result_message, final_text)
        summary["trace_id"] = trace_id

        if _settings.enable_multiplayer_sessions and final_session_id:
            try:
                if not resolved_session_id and body.user_id:
                    create_session_metadata(final_session_id, owner_id=body.user_id)

                add_message_to_history(
                    session_id=final_session_id,
                    role="user",
                    content=body.question,
                    user_id=body.user_id,
                    turn_number=summary.get("num_turns"),
                )

                if final_text:
                    add_message_to_history(
                        session_id=final_session_id,
                        role="assistant",
                        content=final_text,
                        turn_number=summary.get("num_turns"),
                        tokens_used=(summary.get("usage") or {}).get("total_tokens")
                        if isinstance(summary.get("usage"), dict)
                        else None,
                    )
            except Exception as exc:
                _logger.warning(
                    "Session metadata operation failed - continuing without history",
                    extra={"session_id": final_session_id, "error": str(exc)},
                )

        final_status = "failed" if result_message.get("is_error") else "complete"
        return {
            "ok": True,
            "messages": [serialize_message(message) for message in messages],
            "summary": summary,
            "session_id": final_session_id,
        }
    finally:
        reset_parent_context(parent_context_token)
        duration_ms = int((time.time() - start_time) * 1000)
        _record_session_end_best_effort(
            sandbox_type="agent_sdk",
            status=final_status,
            duration_ms=duration_ms,
        )
        _maybe_commit_volume(force=job_root is not None)


@app.post("/query_stream")
async def query_agent_stream(body: QueryBody, request: Request):
    _require_connect_token(request)

    def _format_sse(event: str, data: dict[str, Any]) -> str:
        payload = json.dumps(data, ensure_ascii=True)
        return f"event: {event}\ndata: {payload}\n\n"

    async def sse():
        _maybe_reload_volume()
        job_root = _ensure_job_workspace(body.job_id)
        resolved_session_id = _resolve_session_id(body)

        request_id = getattr(request.state, "request_id", None)
        trace_id = _resolve_trace_id(body, request_id)
        _logger.info(
            "agent.query_stream.start",
            extra={
                "job_id": body.job_id,
                "request_id": request_id,
                "session_id": resolved_session_id,
                "trace_id": trace_id,
            },
        )

        _record_session_start_best_effort(
            sandbox_type="agent_sdk",
            job_id=body.job_id,
            user_id=body.user_id,
        )
        start_time = time.time()
        final_status = "failed"
        stop_event = asyncio.Event()

        messages: list[dict[str, Any]] = []
        result_message: dict[str, Any] | None = None
        final_session_id: str | None = None
        error_event_emitted = False

        parent_context_token = set_parent_context(body.job_id or resolved_session_id)
        try:
            stream_queue: asyncio.Queue[str | None] = asyncio.Queue()

            async def on_message(message: dict[str, Any]) -> None:
                nonlocal error_event_emitted
                messages.append(message)
                serialized = serialize_message(message)
                if serialized.get("type") == "result" and serialized.get("is_error"):
                    error_event_emitted = True
                    await stream_queue.put(
                        _format_sse(
                            "error",
                            {
                                "error": serialized.get("result") or "Agent execution failed",
                                "session_id": serialized.get("session_id"),
                                "subtype": serialized.get("subtype"),
                                "trace_id": trace_id,
                            },
                        )
                    )
                    return
                await stream_queue.put(_format_sse(serialized["type"], serialized))

            run_task = asyncio.create_task(
                _execute_agent_query(
                    question=body.question,
                    session_id=resolved_session_id,
                    fork_session=body.fork_session,
                    job_root=job_root,
                    stop_event=stop_event,
                    agent_type=body.agent_type,
                    trace_id=trace_id,
                    trace_metadata={
                        "trace_id": trace_id,
                        "request_id": request_id,
                        "session_id": resolved_session_id,
                        "job_id": body.job_id,
                        "user_id": body.user_id,
                        "tenant_id": body.tenant_id,
                    },
                    on_message=on_message,
                )
            )

            while True:
                if run_task.done() and stream_queue.empty():
                    break
                try:
                    chunk = await asyncio.wait_for(stream_queue.get(), timeout=0.1)
                except TimeoutError:
                    continue
                if chunk is None:
                    break
                yield chunk

            try:
                _, result_message, final_session_id = await run_task
            except Exception as exc:
                error_event_emitted = True
                yield _format_sse("error", {"error": str(exc), "trace_id": trace_id})
                return

            if result_message and result_message.get("is_error"):
                if not error_event_emitted:
                    error_event_emitted = True
                    yield _format_sse(
                        "error",
                        {
                            "error": result_message.get("result") or "Agent execution failed",
                            "session_id": result_message.get("session_id"),
                            "subtype": result_message.get("subtype"),
                            "trace_id": trace_id,
                        },
                    )
                return

            text_blocks = iter_text_blocks(messages)
            final_text = result_message.get("result") if result_message else None
            if not final_text and text_blocks:
                final_text = "\n".join(text_blocks)

            summary = build_final_summary(result_message, final_text)
            summary["trace_id"] = trace_id

            if _settings.enable_multiplayer_sessions and final_session_id:
                try:
                    if not resolved_session_id and body.user_id:
                        create_session_metadata(final_session_id, owner_id=body.user_id)

                    add_message_to_history(
                        session_id=final_session_id,
                        role="user",
                        content=body.question,
                        user_id=body.user_id,
                        turn_number=summary.get("num_turns"),
                    )

                    if final_text:
                        add_message_to_history(
                            session_id=final_session_id,
                            role="assistant",
                            content=final_text,
                            turn_number=summary.get("num_turns"),
                            tokens_used=(summary.get("usage") or {}).get("total_tokens")
                            if isinstance(summary.get("usage"), dict)
                            else None,
                        )
                except Exception as exc:
                    _logger.warning(
                        "Session metadata operation failed - continuing without history",
                        extra={"session_id": final_session_id, "error": str(exc)},
                    )

            final_status = "complete"
            yield _format_sse("done", summary)
        finally:
            reset_parent_context(parent_context_token)
            duration_ms = int((time.time() - start_time) * 1000)
            _record_session_end_best_effort(
                sandbox_type="agent_sdk",
                status=final_status,
                duration_ms=duration_ms,
            )
            _maybe_commit_volume(force=job_root is not None)

    return StreamingResponse(sse(), media_type="text/event-stream")


# -----------------------------------------------------------------------------
# Session stop/status endpoints
# -----------------------------------------------------------------------------
class StopSessionRequest(BaseSchema):
    mode: str = "graceful"  # "graceful" or "immediate"
    reason: str | None = None
    requested_by: str | None = None


class StopSessionResponse(BaseSchema):
    ok: bool
    session_id: str
    mode: str
    interrupted: bool = False
    stop_event_set: bool = False
    client_found: bool = False
    message: str | None = None


@app.post("/session/{session_id}/stop")
async def stop_session_internal(
    session_id: str,
    request: Request,
    body: StopSessionRequest | None = None,
) -> StopSessionResponse:
    _require_connect_token(request)

    mode = body.mode if body else "graceful"
    interrupted = False
    stop_event_set = False
    client_found = False

    client_state = ACTIVE_CLIENTS.get(session_id)
    if client_state:
        client_found = True

        if not client_state.stop_event.is_set():
            client_state.stop_event.set()
            stop_event_set = True

        if mode == "immediate":
            client_state.immediate_cancel_requested = True
            if client_state.run is not None:
                try:
                    client_state.run.cancel(mode="immediate")
                    interrupted = True
                    _logger.info(
                        "agent.session.interrupted",
                        extra={"session_id": session_id, "reason": body.reason if body else None},
                    )
                except Exception as exc:
                    _logger.warning(
                        "agent.session.interrupt_failed",
                        extra={"session_id": session_id, "error": str(exc)},
                    )

    if not client_found:
        _logger.info(
            "agent.session.stop_no_client",
            extra={"session_id": session_id, "mode": mode},
        )
        return StopSessionResponse(
            ok=True,
            session_id=session_id,
            mode=mode,
            interrupted=False,
            stop_event_set=False,
            client_found=False,
            message="No active client found for session (may have already completed)",
        )

    message = (
        f"Session stop requested (mode={mode}). "
        f"Interrupted: {interrupted}, Stop event set: {stop_event_set}"
    )
    return StopSessionResponse(
        ok=True,
        session_id=session_id,
        mode=mode,
        interrupted=interrupted,
        stop_event_set=stop_event_set,
        client_found=client_found,
        message=message,
    )


@app.get("/session/{session_id}/status")
async def get_session_status(session_id: str):
    client_state = ACTIVE_CLIENTS.get(session_id)
    if client_state:
        return {
            "ok": True,
            "session_id": session_id,
            "active": True,
            "stop_requested": client_state.stop_event.is_set(),
        }
    return {
        "ok": True,
        "session_id": session_id,
        "active": False,
        "stop_requested": False,
    }
