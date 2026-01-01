"""Tests for serialization helpers."""

import pytest
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from agent_sandbox.controllers.serialization import (
    build_final_summary,
    iter_text_blocks,
    serialize_content_block,
    serialize_message,
)


class TestSerializeContentBlock:
    """Tests for serialize_content_block."""

    def test_serialize_text_block(self):
        """Test serializing a TextBlock."""
        block = TextBlock(text="Hello, world!")
        result = serialize_content_block(block)
        assert result == {"type": "text", "text": "Hello, world!"}

    def test_serialize_thinking_block(self):
        """Test serializing a ThinkingBlock."""
        block = ThinkingBlock(thinking="Processing request...", signature="sig123")
        result = serialize_content_block(block)
        assert result == {
            "type": "thinking",
            "thinking": "Processing request...",
            "signature": "sig123",
        }

    def test_serialize_tool_use_block(self):
        """Test serializing a ToolUseBlock."""
        block = ToolUseBlock(
            id="tool_abc123",
            name="calculate",
            input={"expression": "2+2"},
        )
        result = serialize_content_block(block)
        assert result == {
            "type": "tool_use",
            "id": "tool_abc123",
            "name": "calculate",
            "input": {"expression": "2+2"},
        }

    def test_serialize_tool_use_block_empty_input(self):
        """Test serializing a ToolUseBlock with empty input."""
        block = ToolUseBlock(id="tool_1", name="no_args_tool", input={})
        result = serialize_content_block(block)
        assert result["input"] == {}

    def test_serialize_tool_result_block(self):
        """Test serializing a ToolResultBlock."""
        block = ToolResultBlock(
            tool_use_id="tool_abc123",
            content="Result: 4",
            is_error=False,
        )
        result = serialize_content_block(block)
        assert result == {
            "type": "tool_result",
            "tool_use_id": "tool_abc123",
            "content": "Result: 4",
            "is_error": False,
        }

    def test_serialize_tool_result_block_with_error(self):
        """Test serializing a ToolResultBlock with error."""
        block = ToolResultBlock(
            tool_use_id="tool_xyz",
            content="Error: Division by zero",
            is_error=True,
        )
        result = serialize_content_block(block)
        assert result["is_error"] is True

    def test_serialize_unsupported_block_type(self):
        """Test that unsupported block types raise TypeError."""

        class UnsupportedBlock:
            pass

        with pytest.raises(TypeError, match="Unsupported content block type"):
            serialize_content_block(UnsupportedBlock())


class TestSerializeMessage:
    """Tests for serialize_message."""

    def test_serialize_assistant_message(self):
        """Test serializing an AssistantMessage."""
        message = AssistantMessage(
            content=[TextBlock(text="Hello!")],
            model="claude-3-opus",
            parent_tool_use_id=None,
            error=None,
        )
        result = serialize_message(message)
        assert result["type"] == "assistant"
        assert result["content"] == [{"type": "text", "text": "Hello!"}]
        assert result["model"] == "claude-3-opus"
        assert result["parent_tool_use_id"] is None
        assert result["error"] is None

    def test_serialize_assistant_message_with_multiple_blocks(self):
        """Test serializing an AssistantMessage with multiple content blocks."""
        message = AssistantMessage(
            content=[
                TextBlock(text="Let me calculate that."),
                ToolUseBlock(id="tool_1", name="calculate", input={"expr": "2+2"}),
            ],
            model="claude-3-sonnet",
            parent_tool_use_id=None,
            error=None,
        )
        result = serialize_message(message)
        assert len(result["content"]) == 2
        assert result["content"][0]["type"] == "text"
        assert result["content"][1]["type"] == "tool_use"

    def test_serialize_assistant_message_with_error(self):
        """Test serializing an AssistantMessage with error."""
        message = AssistantMessage(
            content=[],
            model="claude-3-opus",
            parent_tool_use_id=None,
            error="Rate limit exceeded",
        )
        result = serialize_message(message)
        assert result["error"] == "Rate limit exceeded"

    def test_serialize_user_message_with_string_content(self):
        """Test serializing a UserMessage with string content."""
        message = UserMessage(
            content="What is the capital of France?",
            uuid="user-msg-123",
            parent_tool_use_id=None,
        )
        result = serialize_message(message)
        assert result["type"] == "user"
        assert result["content"] == "What is the capital of France?"
        assert result["uuid"] == "user-msg-123"

    def test_serialize_user_message_with_tool_result(self):
        """Test serializing a UserMessage with tool result content."""
        message = UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tool_1",
                    content="Result: 4",
                    is_error=False,
                )
            ],
            uuid="user-msg-456",
            parent_tool_use_id="tool_1",
        )
        result = serialize_message(message)
        assert result["type"] == "user"
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "tool_result"
        assert result["parent_tool_use_id"] == "tool_1"

    def test_serialize_system_message(self):
        """Test serializing a SystemMessage."""
        message = SystemMessage(
            subtype="init",
            data={"session_id": "sess-123", "tools": ["calculate"]},
        )
        result = serialize_message(message)
        assert result == {
            "type": "system",
            "subtype": "init",
            "data": {"session_id": "sess-123", "tools": ["calculate"]},
        }

    def test_serialize_result_message(self):
        """Test serializing a ResultMessage."""
        message = ResultMessage(
            subtype="success",
            duration_ms=1234,
            duration_api_ms=5678,
            is_error=False,
            num_turns=2,
            session_id="sess-abc123",
            total_cost_usd=0.015,
            usage={"input_tokens": 100, "output_tokens": 50},
            result="The answer is 4.",
            structured_output=None,
        )
        result = serialize_message(message)
        assert result["type"] == "result"
        assert result["subtype"] == "success"
        assert result["duration_ms"] == 1234
        assert result["duration_api_ms"] == 5678
        assert result["is_error"] is False
        assert result["num_turns"] == 2
        assert result["session_id"] == "sess-abc123"
        assert result["total_cost_usd"] == 0.015
        assert result["usage"] == {"input_tokens": 100, "output_tokens": 50}
        assert result["result"] == "The answer is 4."

    def test_serialize_result_message_with_error(self):
        """Test serializing a ResultMessage with error state."""
        message = ResultMessage(
            subtype="error",
            duration_ms=500,
            duration_api_ms=1000,
            is_error=True,
            num_turns=1,
            session_id="sess-xyz",
            total_cost_usd=0.001,
            usage=None,
            result=None,
            structured_output=None,
        )
        result = serialize_message(message)
        assert result["is_error"] is True
        assert result["subtype"] == "error"

    def test_serialize_stream_event(self):
        """Test serializing a StreamEvent."""
        message = StreamEvent(
            uuid="event-123",
            session_id="sess-abc",
            event="content_block_delta",
            parent_tool_use_id=None,
        )
        result = serialize_message(message)
        assert result == {
            "type": "stream_event",
            "uuid": "event-123",
            "session_id": "sess-abc",
            "event": "content_block_delta",
            "parent_tool_use_id": None,
        }

    def test_serialize_unsupported_message_type(self):
        """Test that unsupported message types raise TypeError."""

        class UnsupportedMessage:
            pass

        with pytest.raises(TypeError, match="Unsupported message type"):
            serialize_message(UnsupportedMessage())


class TestIterTextBlocks:
    """Tests for iter_text_blocks."""

    def test_extract_text_from_single_assistant_message(self):
        """Test extracting text from a single assistant message."""
        messages = [
            AssistantMessage(
                content=[TextBlock(text="Hello, world!")],
                model="claude-3-opus",
                parent_tool_use_id=None,
                error=None,
            )
        ]
        result = iter_text_blocks(messages)
        assert result == ["Hello, world!"]

    def test_extract_text_from_multiple_messages(self):
        """Test extracting text from multiple assistant messages."""
        messages = [
            AssistantMessage(
                content=[TextBlock(text="First message.")],
                model="claude-3-opus",
                parent_tool_use_id=None,
                error=None,
            ),
            AssistantMessage(
                content=[TextBlock(text="Second message.")],
                model="claude-3-opus",
                parent_tool_use_id=None,
                error=None,
            ),
        ]
        result = iter_text_blocks(messages)
        assert result == ["First message.", "Second message."]

    def test_extract_text_with_multiple_blocks(self):
        """Test extracting text from message with multiple text blocks."""
        messages = [
            AssistantMessage(
                content=[
                    TextBlock(text="Part 1."),
                    TextBlock(text="Part 2."),
                ],
                model="claude-3-opus",
                parent_tool_use_id=None,
                error=None,
            )
        ]
        result = iter_text_blocks(messages)
        assert result == ["Part 1.", "Part 2."]

    def test_skip_non_text_blocks(self):
        """Test that non-text blocks are skipped."""
        messages = [
            AssistantMessage(
                content=[
                    TextBlock(text="Before tool."),
                    ToolUseBlock(id="tool_1", name="calc", input={}),
                    TextBlock(text="After tool."),
                ],
                model="claude-3-opus",
                parent_tool_use_id=None,
                error=None,
            )
        ]
        result = iter_text_blocks(messages)
        assert result == ["Before tool.", "After tool."]

    def test_skip_non_assistant_messages(self):
        """Test that non-assistant messages are skipped."""
        messages = [
            SystemMessage(subtype="init", data={}),
            AssistantMessage(
                content=[TextBlock(text="Assistant response.")],
                model="claude-3-opus",
                parent_tool_use_id=None,
                error=None,
            ),
            UserMessage(content="User input", uuid="user-1", parent_tool_use_id=None),
        ]
        result = iter_text_blocks(messages)
        assert result == ["Assistant response."]

    def test_empty_messages_list(self):
        """Test with empty messages list."""
        result = iter_text_blocks([])
        assert result == []

    def test_no_text_blocks(self):
        """Test when there are no text blocks."""
        messages = [
            AssistantMessage(
                content=[ToolUseBlock(id="tool_1", name="calc", input={})],
                model="claude-3-opus",
                parent_tool_use_id=None,
                error=None,
            )
        ]
        result = iter_text_blocks(messages)
        assert result == []


class TestBuildFinalSummary:
    """Tests for build_final_summary."""

    def test_summary_with_result_message(self):
        """Test building summary with a complete result message."""
        result_message = ResultMessage(
            subtype="success",
            duration_ms=1234,
            duration_api_ms=5678,
            is_error=False,
            num_turns=2,
            session_id="sess-abc123",
            total_cost_usd=0.015,
            usage={"input_tokens": 100, "output_tokens": 50},
            result="The answer is 4.",
            structured_output={"answer": 4},
        )
        summary = build_final_summary(result_message, "The answer is 4.")
        assert summary["text"] == "The answer is 4."
        assert summary["is_complete"] is True
        assert summary["subtype"] == "success"
        assert summary["duration_ms"] == 1234
        assert summary["duration_api_ms"] == 5678
        assert summary["is_error"] is False
        assert summary["num_turns"] == 2
        assert summary["session_id"] == "sess-abc123"
        assert summary["total_cost_usd"] == 0.015
        assert summary["usage"] == {"input_tokens": 100, "output_tokens": 50}
        assert summary["result"] == "The answer is 4."
        assert summary["structured_output"] == {"answer": 4}

    def test_summary_without_result_message(self):
        """Test building summary when result message is None."""
        summary = build_final_summary(None, "Partial response text")
        assert summary["text"] == "Partial response text"
        assert summary["is_complete"] is False
        assert "subtype" not in summary
        assert "duration_ms" not in summary

    def test_summary_with_none_text(self):
        """Test building summary with None text."""
        result_message = ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=200,
            is_error=False,
            num_turns=1,
            session_id="sess-1",
            total_cost_usd=0.001,
            usage=None,
            result=None,
            structured_output=None,
        )
        summary = build_final_summary(result_message, None)
        assert summary["text"] is None
        assert summary["is_complete"] is True

    def test_summary_with_error_result(self):
        """Test building summary with error result."""
        result_message = ResultMessage(
            subtype="error",
            duration_ms=50,
            duration_api_ms=100,
            is_error=True,
            num_turns=0,
            session_id="sess-err",
            total_cost_usd=0.0,
            usage=None,
            result=None,
            structured_output=None,
        )
        summary = build_final_summary(result_message, None)
        assert summary["is_complete"] is True
        assert summary["is_error"] is True
        assert summary["subtype"] == "error"

    def test_summary_preserves_all_result_fields(self):
        """Test that summary includes all fields from result message."""
        result_message = ResultMessage(
            subtype="success",
            duration_ms=999,
            duration_api_ms=1999,
            is_error=False,
            num_turns=5,
            session_id="full-test",
            total_cost_usd=1.23,
            usage={"input": 500, "output": 200},
            result="Complete result",
            structured_output={"key": "value"},
        )
        summary = build_final_summary(result_message, "Final text")

        expected_fields = [
            "text",
            "is_complete",
            "subtype",
            "duration_ms",
            "duration_api_ms",
            "is_error",
            "num_turns",
            "session_id",
            "total_cost_usd",
            "usage",
            "result",
            "structured_output",
        ]
        for field in expected_fields:
            assert field in summary, f"Missing field: {field}"


if __name__ == "__main__":
    pytest.main([__file__])
