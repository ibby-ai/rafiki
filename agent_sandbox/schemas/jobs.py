"""Schemas for async job queue endpoints."""

from typing import Literal

from pydantic import Field

from agent_sandbox.schemas.base import BaseSchema


class JobSubmitRequest(BaseSchema):
    """Request body for enqueueing a job."""

    question: str


class JobSubmitResponse(BaseSchema):
    """Response body returned after enqueueing a job."""

    ok: bool = True
    job_id: str


class JobStatusResponse(BaseSchema):
    """Response body for job status lookups."""

    ok: bool = True
    job_id: str
    status: Literal["queued", "running", "complete", "failed", "canceled"]
    result: dict | None = None
    error: str | None = None
    created_at: int | None = None
    updated_at: int | None = None
    canceled_at: int | None = None
    question: str | None = None
    attempts: int | None = Field(default=None, ge=0)
