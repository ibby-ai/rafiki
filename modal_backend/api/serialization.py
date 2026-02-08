"""Serialization helpers for Claude Agent SDK messages."""

from collections.abc import Iterable
from typing import Any

from claude_agent_sdk.types import (
    AssistantMessage,
    ContentBlock,
    Message,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


def serialize_content_block(block: ContentBlock) -> dict[str, Any]:
    """Convert a content block into a JSON-serializable dict."""
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingBlock):
        return {"type": "thinking", "thinking": block.thinking, "signature": block.signature}
    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    raise TypeError(f"Unsupported content block type: {type(block)!r}")


def _serialize_content(value: str | list[ContentBlock]) -> str | list[dict[str, Any]]:
    if isinstance(value, list):
        return [serialize_content_block(block) for block in value]
    return value


def serialize_message(message: Message) -> dict[str, Any]:
    """Convert a Claude Agent SDK message into a JSON-serializable dict."""
    if isinstance(message, AssistantMessage):
        return {
            "type": "assistant",
            "content": [serialize_content_block(block) for block in message.content],
            "model": message.model,
            "parent_tool_use_id": message.parent_tool_use_id,
            "error": message.error,
        }
    if isinstance(message, UserMessage):
        return {
            "type": "user",
            "content": _serialize_content(message.content),
            "uuid": message.uuid,
            "parent_tool_use_id": message.parent_tool_use_id,
        }
    if isinstance(message, SystemMessage):
        return {"type": "system", "subtype": message.subtype, "data": message.data}
    if isinstance(message, ResultMessage):
        return {
            "type": "result",
            "subtype": message.subtype,
            "duration_ms": message.duration_ms,
            "duration_api_ms": message.duration_api_ms,
            "is_error": message.is_error,
            "num_turns": message.num_turns,
            "session_id": message.session_id,
            "total_cost_usd": message.total_cost_usd,
            "usage": message.usage,
            "result": message.result,
            "structured_output": message.structured_output,
        }
    if isinstance(message, StreamEvent):
        return {
            "type": "stream_event",
            "uuid": message.uuid,
            "session_id": message.session_id,
            "event": message.event,
            "parent_tool_use_id": message.parent_tool_use_id,
        }
    raise TypeError(f"Unsupported message type: {type(message)!r}")


def iter_text_blocks(messages: Iterable[Message]) -> list[str]:
    """Extract text blocks from assistant messages in order."""
    parts: list[str] = []
    for message in messages:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
    return parts


def build_final_summary(
    result_message: ResultMessage | None,
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
                "subtype": result_message.subtype,
                "duration_ms": result_message.duration_ms,
                "duration_api_ms": result_message.duration_api_ms,
                "is_error": result_message.is_error,
                "num_turns": result_message.num_turns,
                "session_id": result_message.session_id,
                "total_cost_usd": result_message.total_cost_usd,
                "usage": result_message.usage,
                "result": result_message.result,
                "structured_output": result_message.structured_output,
            }
        )
    return summary
