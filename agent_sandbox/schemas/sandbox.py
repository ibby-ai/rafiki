"""Sandbox-related request/response schemas."""

from agent_sandbox.schemas.base import BaseSchema


class QueryBody(BaseSchema):
    """Request body for agent queries."""

    question: str
    session_id: str | None = None
    session_key: str | None = None
    fork_session: bool = False
    job_id: str | None = None
