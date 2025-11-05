"""Sandbox-related request/response schemas."""

from agent_sandbox.schemas.base import BaseSchema


class QueryBody(BaseSchema):
    """Request body for agent queries."""
    
    question: str

