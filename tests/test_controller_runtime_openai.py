"""Runtime compatibility tests for OpenAI controller internals."""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from modal_backend.api import controller
from modal_backend.api.controller import StopSessionRequest, stop_session_internal
from modal_backend.models import QueryBody


class _FakeRun:
    def __init__(self) -> None:
        self.cancel_modes: list[str] = []
        self.is_complete = False

    def cancel(self, mode: str) -> None:
        self.cancel_modes.append(mode)
        self.is_complete = True


def _request(method: str = "POST") -> Request:
    return Request({"type": "http", "method": method, "path": "/", "headers": []})


@pytest.mark.asyncio
async def test_stop_session_internal_immediate_cancels_active_run() -> None:
    session_id = "sess-immediate"
    run = _FakeRun()
    stop_event = asyncio.Event()
    controller.ACTIVE_CLIENTS[session_id] = controller._ActiveClientState(
        stop_event=stop_event,
        run=run,
    )
    try:
        response = await stop_session_internal(
            session_id,
            _request(),
            StopSessionRequest(mode="immediate", reason="test"),
        )
    finally:
        controller.ACTIVE_CLIENTS.pop(session_id, None)

    assert response.ok is True
    assert response.client_found is True
    assert response.stop_event_set is True
    assert response.interrupted is True
    assert run.cancel_modes == ["immediate"]


@pytest.mark.asyncio
async def test_stop_session_internal_graceful_sets_stop_event() -> None:
    session_id = "sess-graceful"
    run = _FakeRun()
    stop_event = asyncio.Event()
    controller.ACTIVE_CLIENTS[session_id] = controller._ActiveClientState(
        stop_event=stop_event,
        run=run,
    )
    try:
        response = await stop_session_internal(
            session_id,
            _request(),
            StopSessionRequest(mode="graceful"),
        )
    finally:
        controller.ACTIVE_CLIENTS.pop(session_id, None)

    assert response.ok is True
    assert response.client_found is True
    assert response.stop_event_set is True
    assert response.interrupted is False
    assert run.cancel_modes == []


@pytest.mark.asyncio
async def test_stop_session_internal_immediate_sets_pending_when_run_not_ready() -> None:
    session_id = "sess-pending-immediate"
    stop_event = asyncio.Event()
    state = controller._ActiveClientState(stop_event=stop_event, run=None)
    controller.ACTIVE_CLIENTS[session_id] = state
    try:
        response = await stop_session_internal(
            session_id,
            _request(),
            StopSessionRequest(mode="immediate", reason="queue"),
        )
    finally:
        controller.ACTIVE_CLIENTS.pop(session_id, None)

    assert response.ok is True
    assert response.client_found is True
    assert response.stop_event_set is True
    assert response.interrupted is False
    assert state.immediate_cancel_requested is True


def test_attach_run_to_active_client_applies_queued_immediate_cancel() -> None:
    session_id = "sess-attach"
    run = _FakeRun()
    stop_event = asyncio.Event()
    state = controller._ActiveClientState(
        stop_event=stop_event,
        run=None,
        immediate_cancel_requested=True,
    )
    controller.ACTIVE_CLIENTS[session_id] = state
    try:
        attached = controller._attach_run_to_active_client(session_id, run, stop_event)
    finally:
        controller.ACTIVE_CLIENTS.pop(session_id, None)

    assert attached is state
    assert attached.run is run
    assert run.cancel_modes == ["immediate"]


@pytest.mark.asyncio
async def test_watch_for_cancellation_uses_after_turn_mode() -> None:
    run = _FakeRun()
    stop_event = asyncio.Event()
    stop_event.set()

    watcher = asyncio.create_task(
        controller._watch_for_cancellation(run, "sess-after-turn", stop_event)
    )
    await asyncio.sleep(0.3)
    watcher.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watcher
    assert run.cancel_modes == ["after_turn"]


def test_usage_to_dict_aggregates_raw_response_usage() -> None:
    run = SimpleNamespace(
        raw_responses=[
            SimpleNamespace(
                usage=SimpleNamespace(requests=1, input_tokens=10, output_tokens=4, total_tokens=14)
            ),
            SimpleNamespace(
                usage=SimpleNamespace(requests=2, input_tokens=20, output_tokens=6, total_tokens=26)
            ),
        ]
    )

    usage = controller._usage_to_dict(run)
    assert usage == {
        "requests": 3,
        "input_tokens": 30,
        "output_tokens": 10,
        "total_tokens": 40,
    }


def test_make_result_message_omits_non_json_structured_output() -> None:
    class _NonSerializable:
        pass

    run = SimpleNamespace(raw_responses=[], current_turn=1)
    message = controller._make_result_message(
        session_id="sess-json-safe",
        duration_ms=42,
        final_output=_NonSerializable(),
        run=run,
        is_error=False,
        subtype="success",
    )

    assert message["result"] is not None
    assert message["structured_output"] is None


def test_messages_from_run_event_maps_tool_events() -> None:
    tool_called_event = SimpleNamespace(
        type="run_item_stream_event",
        name="tool_called",
        item=SimpleNamespace(
            type="tool_call_item",
            raw_item=SimpleNamespace(
                arguments='{"expression":"2+2"}',
                call_id="call_123",
                name="mcp__utilities__calculate",
            ),
        ),
    )
    tool_messages = controller._messages_from_run_event(tool_called_event, "gpt-4.1")
    assert tool_messages == [
        {
            "type": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_123",
                    "name": "mcp__utilities__calculate",
                    "input": {"expression": "2+2"},
                }
            ],
            "model": "gpt-4.1",
            "parent_tool_use_id": None,
            "error": None,
        }
    ]

    tool_output_event = SimpleNamespace(
        type="run_item_stream_event",
        name="tool_output",
        item=SimpleNamespace(
            type="tool_call_output_item",
            output="Result: 4",
            raw_item=SimpleNamespace(call_id="call_123"),
        ),
    )
    output_messages = controller._messages_from_run_event(tool_output_event, "gpt-4.1")
    assert output_messages == [
        {
            "type": "assistant",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_123",
                    "content": "Result: 4",
                    "is_error": False,
                }
            ],
            "model": "gpt-4.1",
            "parent_tool_use_id": "call_123",
            "error": None,
        }
    ]


def test_messages_from_run_event_maps_assistant_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller.ItemHelpers, "text_message_output", lambda _item: "hello there")

    event = SimpleNamespace(
        type="run_item_stream_event",
        name="message_output_created",
        item=SimpleNamespace(type="message_output_item"),
    )
    messages = controller._messages_from_run_event(event, "gpt-4.1-mini")
    assert messages == [
        {
            "type": "assistant",
            "content": [{"type": "text", "text": "hello there"}],
            "model": "gpt-4.1-mini",
            "parent_tool_use_id": None,
            "error": None,
        }
    ]


@pytest.mark.asyncio
async def test_execute_agent_query_suppresses_watcher_cancelled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeStreamingRun:
        def __init__(self) -> None:
            self.cancel_modes: list[str] = []
            self.is_complete = False
            self.final_output = "ok"
            self.current_turn = 1
            self.raw_responses: list[object] = []

        def cancel(self, mode: str) -> None:
            self.cancel_modes.append(mode)

        async def stream_events(self):
            yield SimpleNamespace(type="unknown_event")

    fake_run = _FakeStreamingRun()
    fake_agent = SimpleNamespace(model="gpt-4.1")

    async def fake_ensure_session(
        session_id: str | None,
        fork_session: bool,
        db_path: str,
    ):
        return object(), session_id or "sess-watcher-cancel"

    monkeypatch.setattr(controller, "ensure_session", fake_ensure_session)
    monkeypatch.setattr(controller, "_build_system_prompt", lambda *_args, **_kwargs: "prompt")
    monkeypatch.setattr(controller, "_build_agent", lambda *_args, **_kwargs: (fake_agent, 5))
    monkeypatch.setattr(controller.Runner, "run_streamed", lambda *args, **kwargs: fake_run)

    messages, result_message, session_id = await controller._execute_agent_query(
        question="hello",
        session_id="sess-watcher-cancel",
        fork_session=False,
        job_root=None,
    )

    assert session_id == "sess-watcher-cancel"
    assert result_message["is_error"] is False
    assert messages[-1]["type"] == "result"
    assert session_id not in controller.ACTIVE_CLIENTS


@pytest.mark.asyncio
async def test_query_stream_emits_error_event_without_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_agent_query(**kwargs):
        on_message = kwargs["on_message"]
        await on_message(
            {
                "type": "assistant",
                "content": [{"type": "text", "text": "partial output"}],
                "model": "gpt-4.1",
                "parent_tool_use_id": None,
                "error": None,
            }
        )
        result_message = {
            "type": "result",
            "subtype": "error_during_execution",
            "duration_ms": 5,
            "duration_api_ms": None,
            "is_error": True,
            "num_turns": 1,
            "session_id": "sess-stream-error",
            "total_cost_usd": None,
            "usage": None,
            "result": "boom",
            "structured_output": None,
        }
        await on_message(result_message)
        return [], result_message, "sess-stream-error"

    monkeypatch.setattr(controller, "_execute_agent_query", fake_execute_agent_query)
    monkeypatch.setattr(controller, "_maybe_reload_volume", lambda: None)
    monkeypatch.setattr(controller, "_maybe_commit_volume", lambda **_kwargs: None)
    monkeypatch.setattr(controller, "record_session_start", lambda **_kwargs: None)
    monkeypatch.setattr(controller, "record_session_end", lambda **_kwargs: None)
    monkeypatch.setattr(controller._settings, "enable_multiplayer_sessions", False)

    response = await controller.query_agent_stream(
        QueryBody(question="hello", session_id="sess-stream-error"),
        _request(),
    )

    chunks: list[str] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8"))
        else:
            chunks.append(chunk)

    payload = "".join(chunks)
    assert "event: assistant" in payload
    assert "event: error" in payload
    assert '"error": "boom"' in payload
    assert "event: done" not in payload
