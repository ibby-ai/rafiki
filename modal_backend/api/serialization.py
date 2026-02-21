"""Serialization helpers for provider-neutral agent messages."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if value is None:
        return None
    if isinstance(value, str | int | float | bool):
        return value
    return str(value)


def serialize_content_block(block: Any) -> dict[str, Any]:
    """Convert a content block into a JSON-serializable dict."""
    if isinstance(block, dict):
        block_type = block.get("type")
        if block_type == "text":
            return {"type": "text", "text": block.get("text", "")}
        if block_type == "thinking":
            return {
                "type": "thinking",
                "thinking": block.get("thinking", ""),
                "signature": block.get("signature", ""),
            }
        if block_type == "tool_use":
            return {
                "type": "tool_use",
                "id": _safe_scalar(block.get("id")),
                "name": _safe_scalar(block.get("name")),
                "input": block.get("input", {}),
            }
        if block_type == "tool_result":
            return {
                "type": "tool_result",
                "tool_use_id": _safe_scalar(block.get("tool_use_id")),
                "content": _safe_scalar(block.get("content")),
                "is_error": bool(block.get("is_error", False)),
            }
        return dict(block)

    # Fallback for unknown objects
    if hasattr(block, "model_dump"):
        dumped = block.model_dump()
        if isinstance(dumped, dict):
            return dumped
    return {"type": "unknown", "value": str(block)}


def _serialize_content(value: Any) -> Any:
    if isinstance(value, list):
        return [serialize_content_block(block) for block in value]
    return value


def serialize_message(message: Any) -> dict[str, Any]:
    """Convert a runtime message into a JSON-serializable dict.

    For OpenAI migration we keep wire compatibility by accepting already-serialized dicts.
    """
    if isinstance(message, dict):
        msg = dict(message)
        if msg.get("content") is not None:
            msg["content"] = _serialize_content(msg.get("content"))
        for field in (
            "trace_id",
            "openai_trace_id",
            "session_id",
            "parent_tool_use_id",
            "request_id",
            "agent_type",
        ):
            if field in msg:
                msg[field] = _safe_scalar(msg.get(field))
        return msg

    if hasattr(message, "model_dump"):
        dumped = message.model_dump()
        if isinstance(dumped, dict):
            return dumped

    raise TypeError(f"Unsupported message type: {type(message)!r}")


def iter_text_blocks(messages: Iterable[Any]) -> list[str]:
    """Extract text blocks from assistant messages in order."""
    parts: list[str] = []
    for message in messages:
        msg = serialize_message(message)
        if msg.get("type") != "assistant":
            continue

        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
        elif isinstance(content, str):
            parts.append(content)
    return parts


def build_final_summary(
    result_message: dict[str, Any] | None,
    final_text: str | None,
) -> dict[str, Any]:
    """Build a consistent summary for the completed agent run."""
    summary: dict[str, Any] = {
        "text": final_text,
        "is_complete": result_message is not None,
    }
    if result_message:
        summary.update(
            {
                "subtype": result_message.get("subtype"),
                "duration_ms": result_message.get("duration_ms"),
                "duration_api_ms": result_message.get("duration_api_ms"),
                "is_error": result_message.get("is_error"),
                "num_turns": result_message.get("num_turns"),
                "session_id": result_message.get("session_id"),
                "openai_trace_id": result_message.get("openai_trace_id"),
                "total_cost_usd": result_message.get("total_cost_usd"),
                "usage": result_message.get("usage"),
                "result": result_message.get("result"),
                "structured_output": result_message.get("structured_output"),
            }
        )

    return {k: v for k, v in summary.items() if v is not None or k in {"text", "is_complete"}}
