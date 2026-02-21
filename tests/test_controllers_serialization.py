"""Tests for provider-neutral serialization helpers."""

from __future__ import annotations

import pytest

from modal_backend.api.serialization import (
    build_final_summary,
    iter_text_blocks,
    serialize_content_block,
    serialize_message,
)


class _ModelLike:
    def __init__(self, payload: dict):
        self._payload = payload

    def model_dump(self) -> dict:
        return self._payload


def test_serialize_content_block_text() -> None:
    block = {"type": "text", "text": "hello"}
    assert serialize_content_block(block) == {"type": "text", "text": "hello"}


def test_serialize_content_block_tool_use() -> None:
    block = {
        "type": "tool_use",
        "id": "tool_1",
        "name": "mcp__utilities__calculate",
        "input": {"expression": "2+2"},
    }
    assert serialize_content_block(block) == block


def test_serialize_content_block_tool_result() -> None:
    block = {
        "type": "tool_result",
        "tool_use_id": "tool_1",
        "content": "Result: 4",
        "is_error": False,
    }
    assert serialize_content_block(block) == block


def test_serialize_content_block_model_like() -> None:
    block = _ModelLike({"type": "text", "text": "from-model-dump"})
    assert serialize_content_block(block) == {"type": "text", "text": "from-model-dump"}


def test_serialize_content_block_unknown_object() -> None:
    block = object()
    result = serialize_content_block(block)
    assert result["type"] == "unknown"
    assert "object object" in result["value"]


def test_serialize_message_dict_preserves_shape() -> None:
    message = {
        "type": "assistant",
        "content": [{"type": "text", "text": "hello"}],
        "model": "gpt-4.1",
        "parent_tool_use_id": None,
        "error": None,
    }
    assert serialize_message(message) == message


def test_serialize_message_model_like() -> None:
    message = _ModelLike({"type": "result", "subtype": "success", "session_id": "sess_1"})
    assert serialize_message(message) == {
        "type": "result",
        "subtype": "success",
        "session_id": "sess_1",
    }


def test_serialize_message_unsupported_raises() -> None:
    with pytest.raises(TypeError, match="Unsupported message type"):
        serialize_message("plain-string-message")


def test_iter_text_blocks_extracts_assistant_text_only() -> None:
    messages = [
        {"type": "assistant", "content": [{"type": "text", "text": "one"}], "model": "gpt-4.1"},
        {"type": "assistant", "content": [{"type": "tool_use", "id": "t1"}], "model": "gpt-4.1"},
        {"type": "user", "content": "ignored"},
        {
            "type": "assistant",
            "content": [{"type": "text", "text": "two"}],
            "model": "gpt-4.1-mini",
        },
    ]
    assert iter_text_blocks(messages) == ["one", "two"]


def test_build_final_summary_without_result_message() -> None:
    summary = build_final_summary(None, "partial")
    assert summary == {"text": "partial", "is_complete": False}


def test_build_final_summary_with_result_message() -> None:
    result_message = {
        "type": "result",
        "subtype": "success",
        "duration_ms": 123,
        "duration_api_ms": None,
        "is_error": False,
        "num_turns": 2,
        "session_id": "sess_123",
        "total_cost_usd": None,
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "requests": 1},
        "result": "final answer",
        "structured_output": {"ok": True},
    }
    summary = build_final_summary(result_message, "final answer")
    assert summary["text"] == "final answer"
    assert summary["is_complete"] is True
    assert summary["subtype"] == "success"
    assert summary["session_id"] == "sess_123"
    assert summary["usage"]["total_tokens"] == 15
    assert summary["structured_output"] == {"ok": True}
