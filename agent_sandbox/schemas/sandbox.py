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


# Maximum CLI timeout (23 hours, leaving 1 hour buffer from 24-hour function timeout)
MAX_CLI_TIMEOUT_SECONDS = 60 * 60 * 23


class ClaudeCliRequest(BaseSchema):
    """Request body for Claude Code CLI execution."""

    prompt: str
    allowed_tools: list[str] | None = None
    dangerously_skip_permissions: bool = True
    output_format: Literal["json", "text"] = "json"
    timeout_seconds: int = 120
    max_turns: int | None = None
    job_id: str | None = None
    debug: bool = False
    probe: Literal["version", "help", "path"] | None = None
    write_result_path: str | None = None

    @field_validator("job_id")
    @classmethod
    def validate_job_id(cls, value: str | None) -> str | None:
        return _validate_job_id(value)

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout_seconds(cls, value: int) -> int:
        """Validate timeout doesn't exceed Modal function timeout."""
        if value <= 0:
            raise ValueError("timeout_seconds must be positive")
        if value > MAX_CLI_TIMEOUT_SECONDS:
            raise ValueError(
                f"timeout_seconds cannot exceed {MAX_CLI_TIMEOUT_SECONDS} "
                f"({MAX_CLI_TIMEOUT_SECONDS // 3600} hours)"
            )
        return value
