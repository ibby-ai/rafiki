"""Schemas for async job queue endpoints.

These schemas define the request/response formats for the job queue API,
which allows submitting agent queries for background processing.

See: agent_sandbox.jobs for the job lifecycle implementation.
"""

from typing import Any, Literal

from pydantic import AnyHttpUrl, Field

from agent_sandbox.schemas.base import BaseSchema


class WebhookConfig(BaseSchema):
    """Webhook configuration for job completion callbacks."""

    url: AnyHttpUrl = Field(description="Webhook URL for job completion")
    headers: dict[str, str] | None = Field(
        default=None, description="Optional headers to include with the webhook request"
    )
    signing_secret: str | None = Field(
        default=None,
        description="Optional shared secret for signing webhook payloads",
    )
    secret_ref: str | None = Field(
        default=None,
        description="Optional reference to a stored secret in your system",
    )
    timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        description="Optional webhook timeout override in seconds",
    )
    max_attempts: int | None = Field(
        default=None,
        ge=1,
        description="Optional max delivery attempts override",
    )


class WebhookStatus(BaseSchema):
    """Webhook delivery status returned in job status responses."""

    url: AnyHttpUrl | None = Field(default=None, description="Webhook URL")
    secret_ref: str | None = Field(
        default=None,
        description="Reference to a stored secret (if used during delivery)",
    )
    attempts: int | None = Field(default=None, description="Delivery attempts so far")
    last_status: int | None = Field(
        default=None, description="Last HTTP status received from webhook"
    )
    last_error: str | None = Field(default=None, description="Last delivery error")
    delivered_at: int | None = Field(
        default=None, description="Unix timestamp when webhook succeeded"
    )


class ArtifactEntry(BaseSchema):
    """Metadata for a single artifact file."""

    path: str = Field(description="Relative path to the artifact file")
    size_bytes: int | None = Field(default=None, ge=0, description="File size in bytes")
    content_type: str | None = Field(default=None, description="MIME type, if known")
    checksum_sha256: str | None = Field(default=None, description="SHA-256 checksum, if computed")
    created_at: int | None = Field(default=None, description="Unix timestamp when file was created")
    modified_at: int | None = Field(
        default=None, description="Unix timestamp when file was last modified"
    )


class ArtifactManifest(BaseSchema):
    """Manifest of files created by a job."""

    root: str | None = Field(default=None, description="Root directory for job artifacts")
    files: list[ArtifactEntry] = Field(default_factory=list, description="List of artifact entries")


class JobSubmitRequest(BaseSchema):
    """Request body for enqueueing a job."""

    question: str = Field(description="The user question/prompt to process")
    tenant_id: str | None = Field(default=None, description="Tenant or workspace identifier")
    user_id: str | None = Field(default=None, description="End-user identifier")
    schedule_at: int | None = Field(
        default=None,
        description="Unix timestamp to schedule execution (omit for immediate)",
    )
    webhook: WebhookConfig | None = Field(
        default=None, description="Webhook callback configuration"
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Optional metadata for client tracking"
    )


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
    tenant_id: str | None = Field(default=None, description="Tenant or workspace identifier")
    user_id: str | None = Field(default=None, description="End-user identifier")
    schedule_at: int | None = Field(
        default=None, description="Unix timestamp when job is scheduled to run"
    )
    webhook: WebhookStatus | None = Field(default=None, description="Webhook delivery metadata")
    artifacts: ArtifactManifest | None = Field(
        default=None, description="Manifest of artifacts generated by the job"
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Optional metadata returned for client tracking"
    )
    started_at: int | None = Field(
        default=None, description="Unix timestamp when job execution started"
    )
    completed_at: int | None = Field(default=None, description="Unix timestamp when job finished")
    queue_latency_ms: int | None = Field(
        default=None, description="Milliseconds between enqueue and start"
    )
    duration_ms: int | None = Field(
        default=None, description="Wall-clock execution duration in milliseconds"
    )
    agent_duration_ms: int | None = Field(
        default=None, description="Agent-reported duration in milliseconds"
    )
    agent_duration_api_ms: int | None = Field(
        default=None, description="Agent API duration in milliseconds"
    )
    usage: dict[str, Any] | None = Field(
        default=None, description="Model usage metadata when available"
    )
    total_cost_usd: float | None = Field(
        default=None, description="Total cost in USD when available"
    )
    num_turns: int | None = Field(
        default=None, description="Number of turns in the agent run when available"
    )
    session_id: str | None = Field(default=None, description="Agent session id when available")
    tool_call_count: int | None = Field(
        default=None, description="Count of tool calls used by the agent"
    )
    models: list[str] | None = Field(
        default=None, description="Unique model identifiers used in the run"
    )
    sandbox_id: str | None = Field(default=None, description="Modal sandbox id used for the run")


class ArtifactListResponse(BaseSchema):
    """Response body for job artifact listings."""

    ok: bool = Field(default=True, description="Always true for valid job lookups")
    job_id: str = Field(description="UUID of the job")
    artifacts: ArtifactManifest = Field(description="Manifest of artifacts for the job")
