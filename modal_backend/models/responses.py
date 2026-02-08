"""Response schemas for API endpoints."""

from typing import Any, Literal

from modal_backend.models.base import BaseSchema


class TextBlockSchema(BaseSchema):
    """Schema for text content blocks."""

    type: Literal["text"] = "text"
    text: str


class ThinkingBlockSchema(BaseSchema):
    """Schema for thinking content blocks."""

    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: str


class ToolUseBlockSchema(BaseSchema):
    """Schema for tool use content blocks."""

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ToolResultBlockSchema(BaseSchema):
    """Schema for tool result content blocks."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | list[dict[str, Any]] | None = None
    is_error: bool = False


class SummarySchema(BaseSchema):
    """Schema for query result summary."""

    text: str | None = None
    is_complete: bool
    subtype: str | None = None
    duration_ms: int | None = None
    duration_api_ms: int | None = None
    is_error: bool | None = None
    num_turns: int | None = None
    session_id: str | None = None
    total_cost_usd: float | None = None
    usage: dict[str, Any] | None = None
    result: str | None = None
    structured_output: Any | None = None


class QueryResponse(BaseSchema):
    """Schema for successful query response."""

    ok: bool
    messages: list[dict[str, Any]]
    summary: SummarySchema
    session_id: str | None = None


class ErrorResponse(BaseSchema):
    """Schema for error responses."""

    ok: Literal[False] = False
    error: str
    error_type: str
    request_id: str | None = None
