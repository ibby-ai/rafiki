"""Schemas for async job queue endpoints.

These schemas define the request/response formats for the job queue API,
which allows submitting agent queries for background processing.

See: agent_sandbox.jobs for the job lifecycle implementation.
"""

from typing import Literal

from pydantic import Field

from agent_sandbox.schemas.base import BaseSchema


class JobSubmitRequest(BaseSchema):
    """Request body for enqueueing a job."""

    question: str = Field(description="The user question/prompt to process")


class JobSubmitResponse(BaseSchema):
    """Response body returned after enqueueing a job."""

    ok: bool = Field(default=True, description="Always true on successful enqueue")
    job_id: str = Field(description="UUID for tracking job status")


class JobStatusResponse(BaseSchema):
    """Response body for job status lookups.

    Status values:
        - queued: Waiting for worker pickup
        - running: Worker is processing the job
        - complete: Finished successfully (result populated)
        - failed: Encountered an error (error populated)
        - canceled: Canceled by user before completion
    """

    ok: bool = Field(default=True, description="Always true for valid job lookups")
    job_id: str = Field(description="UUID of the job")
    status: Literal["queued", "running", "complete", "failed", "canceled"] = Field(
        description="Current job state in lifecycle"
    )
    result: dict | None = Field(
        default=None,
        description="Agent response on completion (keys: messages, summary)",
    )
    error: str | None = Field(default=None, description="Error message if status is 'failed'")
    created_at: int | None = Field(default=None, description="Unix timestamp when job was enqueued")
    updated_at: int | None = Field(default=None, description="Unix timestamp of last status change")
    canceled_at: int | None = Field(
        default=None, description="Unix timestamp when job was canceled"
    )
    question: str | None = Field(
        default=None, description="Original question submitted with the job"
    )
    attempts: int | None = Field(
        default=None, ge=0, description="Number of processing attempts by workers"
    )
