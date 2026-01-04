"""Tests for response schemas."""

import pytest
from pydantic import ValidationError

from agent_sandbox.schemas.responses import (
    ErrorResponse,
    QueryResponse,
    SummarySchema,
    TextBlockSchema,
    ThinkingBlockSchema,
    ToolResultBlockSchema,
    ToolUseBlockSchema,
)


class TestTextBlockSchema:
    """Tests for TextBlockSchema."""

    def test_valid_text_block(self):
        """Test creating a valid TextBlockSchema."""
        block = TextBlockSchema(text="Hello, world!")
        assert block.type == "text"
        assert block.text == "Hello, world!"

    def test_text_block_default_type(self):
        """Test that type defaults to 'text'."""
        block = TextBlockSchema(text="test")
        assert block.type == "text"

    def test_text_block_missing_text(self):
        """Test that missing text raises ValidationError."""
        with pytest.raises(ValidationError):
            TextBlockSchema()

    def test_text_block_extra_fields_forbidden(self):
        """Test that extra fields are forbidden."""
        with pytest.raises(ValidationError):
            TextBlockSchema(text="test", extra="not allowed")

    def test_text_block_whitespace_stripping(self):
        """Test that whitespace is stripped from text."""
        block = TextBlockSchema(text="  hello  ")
        assert block.text == "hello"


class TestThinkingBlockSchema:
    """Tests for ThinkingBlockSchema."""

    def test_valid_thinking_block(self):
        """Test creating a valid ThinkingBlockSchema."""
        block = ThinkingBlockSchema(thinking="Processing...", signature="abc123")
        assert block.type == "thinking"
        assert block.thinking == "Processing..."
        assert block.signature == "abc123"

    def test_thinking_block_missing_fields(self):
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError):
            ThinkingBlockSchema(thinking="test")
        with pytest.raises(ValidationError):
            ThinkingBlockSchema(signature="test")

    def test_thinking_block_extra_fields_forbidden(self):
        """Test that extra fields are forbidden."""
        with pytest.raises(ValidationError):
            ThinkingBlockSchema(thinking="test", signature="sig", extra="bad")


class TestToolUseBlockSchema:
    """Tests for ToolUseBlockSchema."""

    def test_valid_tool_use_block(self):
        """Test creating a valid ToolUseBlockSchema."""
        block = ToolUseBlockSchema(
            id="tool_123",
            name="calculate",
            input={"expression": "2+2"},
        )
        assert block.type == "tool_use"
        assert block.id == "tool_123"
        assert block.name == "calculate"
        assert block.input == {"expression": "2+2"}

    def test_tool_use_block_empty_input(self):
        """Test tool use with empty input dict."""
        block = ToolUseBlockSchema(id="tool_1", name="test", input={})
        assert block.input == {}

    def test_tool_use_block_missing_fields(self):
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError):
            ToolUseBlockSchema(id="test", name="test")  # missing input
        with pytest.raises(ValidationError):
            ToolUseBlockSchema(id="test", input={})  # missing name


class TestToolResultBlockSchema:
    """Tests for ToolResultBlockSchema."""

    def test_valid_tool_result_block(self):
        """Test creating a valid ToolResultBlockSchema."""
        block = ToolResultBlockSchema(
            tool_use_id="tool_123",
            content="Result: 4",
            is_error=False,
        )
        assert block.type == "tool_result"
        assert block.tool_use_id == "tool_123"
        assert block.content == "Result: 4"
        assert block.is_error is False

    def test_tool_result_block_defaults(self):
        """Test default values for optional fields."""
        block = ToolResultBlockSchema(tool_use_id="tool_123")
        assert block.content is None
        assert block.is_error is False

    def test_tool_result_block_list_content(self):
        """Test tool result with list content."""
        content = [{"type": "text", "text": "Hello"}]
        block = ToolResultBlockSchema(tool_use_id="tool_123", content=content)
        assert block.content == content

    def test_tool_result_block_error_state(self):
        """Test tool result with error state."""
        block = ToolResultBlockSchema(
            tool_use_id="tool_123",
            content="Error: Division by zero",
            is_error=True,
        )
        assert block.is_error is True


class TestSummarySchema:
    """Tests for SummarySchema."""

    def test_valid_summary_minimal(self):
        """Test creating a minimal valid SummarySchema."""
        summary = SummarySchema(is_complete=True)
        assert summary.is_complete is True
        assert summary.text is None
        assert summary.duration_ms is None

    def test_valid_summary_full(self):
        """Test creating a fully populated SummarySchema."""
        summary = SummarySchema(
            text="The answer is 4.",
            is_complete=True,
            subtype="success",
            duration_ms=1234,
            duration_api_ms=5678,
            is_error=False,
            num_turns=2,
            session_id="session-abc123",
            total_cost_usd=0.015,
            usage={"input_tokens": 100, "output_tokens": 50},
            result="The answer is 4.",
            structured_output=None,
        )
        assert summary.text == "The answer is 4."
        assert summary.is_complete is True
        assert summary.subtype == "success"
        assert summary.duration_ms == 1234
        assert summary.total_cost_usd == 0.015
        assert summary.usage == {"input_tokens": 100, "output_tokens": 50}

    def test_summary_missing_is_complete(self):
        """Test that missing is_complete raises ValidationError."""
        with pytest.raises(ValidationError):
            SummarySchema(text="test")

    def test_summary_incomplete_state(self):
        """Test summary with incomplete state."""
        summary = SummarySchema(is_complete=False, is_error=True)
        assert summary.is_complete is False
        assert summary.is_error is True


class TestQueryResponse:
    """Tests for QueryResponse."""

    def test_valid_query_response(self):
        """Test creating a valid QueryResponse."""
        response = QueryResponse(
            ok=True,
            messages=[{"type": "assistant", "content": []}],
            summary=SummarySchema(is_complete=True, text="Done"),
            provider="claude",
        )
        assert response.ok is True
        assert len(response.messages) == 1
        assert response.summary.is_complete is True
        assert response.provider == "claude"

    def test_query_response_empty_messages(self):
        """Test query response with empty messages list."""
        response = QueryResponse(
            ok=True,
            messages=[],
            summary=SummarySchema(is_complete=True),
        )
        assert response.messages == []

    def test_query_response_missing_fields(self):
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError):
            QueryResponse(ok=True, messages=[])  # missing summary
        with pytest.raises(ValidationError):
            QueryResponse(ok=True, summary=SummarySchema(is_complete=True))

    def test_query_response_nested_summary(self):
        """Test query response with nested summary from dict."""
        response = QueryResponse(
            ok=True,
            messages=[],
            summary={"is_complete": True, "text": "Hello"},
        )
        assert response.summary.text == "Hello"

    def test_query_response_multiple_messages(self):
        """Test query response with multiple message types."""
        messages = [
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "content": [{"type": "text", "text": "Hi"}]},
            {"type": "result", "duration_ms": 100},
        ]
        response = QueryResponse(
            ok=True,
            messages=messages,
            summary=SummarySchema(is_complete=True),
        )
        assert len(response.messages) == 3


class TestErrorResponse:
    """Tests for ErrorResponse."""

    def test_valid_error_response(self):
        """Test creating a valid ErrorResponse."""
        response = ErrorResponse(
            error="Something went wrong",
            error_type="ValueError",
        )
        assert response.ok is False
        assert response.error == "Something went wrong"
        assert response.error_type == "ValueError"
        assert response.request_id is None

    def test_error_response_with_request_id(self):
        """Test error response with request ID."""
        response = ErrorResponse(
            error="Not found",
            error_type="HTTPException",
            request_id="req-abc123",
        )
        assert response.request_id == "req-abc123"

    def test_error_response_ok_always_false(self):
        """Test that ok is always False and cannot be changed."""
        response = ErrorResponse(error="test", error_type="Error")
        assert response.ok is False

    def test_error_response_missing_fields(self):
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError):
            ErrorResponse(error="test")  # missing error_type
        with pytest.raises(ValidationError):
            ErrorResponse(error_type="Error")  # missing error

    def test_error_response_extra_fields_forbidden(self):
        """Test that extra fields are forbidden."""
        with pytest.raises(ValidationError):
            ErrorResponse(error="test", error_type="Error", extra="bad")


if __name__ == "__main__":
    pytest.main([__file__])
