"""Sandbox-related request/response schemas."""

from typing import Literal
from uuid import UUID

from pydantic import field_validator

from agent_sandbox.schemas.base import BaseSchema


def _validate_job_id(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("job_id must be a valid UUID") from exc


class QueryBody(BaseSchema):
    """Request body for agent queries."""

    question: str
    session_id: str | None = None
    session_key: str | None = None
    fork_session: bool = False
    job_id: str | None = None

    @field_validator("job_id")
    @classmethod
    def validate_job_id(cls, value: str | None) -> str | None:
        return _validate_job_id(value)


class ClaudeCliRequest(BaseSchema):
    """Request body for Claude Code CLI execution."""

    prompt: str
    allowed_tools: list[str] | None = None
    output_format: Literal["json", "text"] = "json"
    timeout_seconds: int = 120
    max_turns: int | None = None
    job_id: str | None = None

    @field_validator("job_id")
    @classmethod
    def validate_job_id(cls, value: str | None) -> str | None:
        return _validate_job_id(value)
