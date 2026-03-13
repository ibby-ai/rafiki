"""Schemas for async job queue endpoints.

These schemas define the request/response formats for the job queue API,
which allows submitting agent queries for background processing with support
for scheduling, webhooks, and artifact collection.

Key Features:
    - Async job queuing with status tracking
    - Scheduled execution with Unix timestamps
    - Webhook callbacks on completion/failure
    - Artifact manifest for job outputs
    - Multi-tenancy with tenant_id/user_id

Webhook Configuration Examples:
    Basic webhook without signature:
    ```python
    webhook = WebhookConfig(
        url="https://example.com/webhook"
    )
    ```

    Webhook with custom headers:
    ```python
    webhook = WebhookConfig(
        url="https://example.com/webhook",
        headers={"X-Custom-Header": "value", "Authorization": "Bearer token"}
    )
    ```

    Webhook with HMAC signing (recommended for security):
    ```python
    webhook = WebhookConfig(
        url="https://example.com/webhook",
        signing_secret="your-shared-secret-key",  # Or use secret_ref
        timeout_seconds=15,
        max_attempts=5
    )
    ```

    Verifying webhook signatures (recipient side):
    ```python
    import hmac, hashlib

    # Extract signature components from X-Agent-Signature header
    # Format: "t=1234567890,v1=abcdef123456..."
    signature_header = request.headers["X-Agent-Signature"]
    parts = dict(part.split("=") for part in signature_header.split(","))
    timestamp = parts["t"]
    received_sig = parts["v1"]

    # Reconstruct signed message
    payload = request.body.decode("utf-8")
    message = f"{timestamp}.{payload}"

    # Compute expected signature
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    # Compare signatures (use constant-time comparison in production)
    if not hmac.compare_digest(expected_sig, received_sig):
        raise ValueError("Invalid webhook signature")

    # Optional: Check timestamp to prevent replay attacks
    if abs(int(timestamp) - int(time.time())) > 300:  # 5 minute tolerance
        raise ValueError("Webhook timestamp too old")
    ```

Artifact Path Format:
    All artifact paths are relative to the job workspace root (/data/jobs/{job_id}/).

    Valid artifact paths (relative, within workspace):
    - "output.txt" → /data/jobs/{job_id}/output.txt
    - "results/data.csv" → /data/jobs/{job_id}/results/data.csv
    - "logs/debug.log" → /data/jobs/{job_id}/logs/debug.log
    - "./report.pdf" → /data/jobs/{job_id}/report.pdf (normalized)

    Invalid artifact paths (rejected for security):
    - "../other_job/file.txt" → Path traversal blocked
    - "/etc/passwd" → Absolute path blocked
    - "../../data/secrets" → Escapes workspace boundary

    Path traversal prevention ensures jobs can only access their own files.

Timezone Handling:
    All timestamps (schedule_at, created_at, etc.) are Unix timestamps in UTC:
    - Seconds since 1970-01-01 00:00:00 UTC
    - No timezone conversion is performed
    - Clients must convert local times to UTC before submitting

    Example (Python):
    ```python
    from datetime import datetime, timezone

    # Schedule for specific UTC time
    schedule_time = datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc)
    schedule_at = int(schedule_time.timestamp())  # UTC unix timestamp

    # Schedule for 1 hour from now
    import time
    schedule_at = int(time.time()) + 3600
    ```

See: modal_backend.jobs for the job lifecycle implementation.
See: modal_backend.platform_services.webhooks for webhook delivery logic.
"""

from typing import Any, Literal

from pydantic import AnyHttpUrl, Field

from modal_backend.models.base import BaseSchema


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
    agent_type: str | None = Field(default=None, description="Agent type from Cloudflare Worker")
    session_id: str | None = Field(default=None, description="Session ID from Cloudflare Worker")
    session_key: str | None = Field(default=None, description="Session key from Cloudflare Worker")
    job_id: str | None = Field(
        default=None, description="Pre-generated job ID from Cloudflare Worker"
    )
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

    Contains comprehensive job metadata including status, timing, results, artifacts,
    and webhook delivery information. Returned by GET /jobs/{job_id} endpoint.

    Status Lifecycle Diagram:
        ```
                    ┌─────────┐
                    │ queued  │  ← Job submitted via enqueue_job()
                    └────┬────┘
                         │
                         │ Worker picks up job
                         │
                    ┌────▼────┐
            ┌──────►│ running │◄─────┐
            │       └────┬────┘      │ Retry on failure
            │            │            │ (if configured)
            │            │            │
            │            ▼            │
            │       ┌─────────┐      │
            │       │Decision │      │
            │       └────┬────┘      │
            │            │            │
            │    ┌───────┼───────┐   │
            │    │       │       │   │
            │    ▼       ▼       ▼   │
            │  ┌──────┐ ┌──────┐ ┌──────┐
            └──┤failed│ │complete│canceled│ (Terminal states)
               └──────┘ └──────┘ └──────┘
                  │         │        │
                  │         │        │
                  ▼         ▼        ▼
            Webhook    Webhook   No webhook
            triggered  triggered (if enabled)
        ```

    Status Values:
        - **queued**: Job submitted and waiting for worker pickup
          - created_at is set
          - schedule_at may delay processing
          - Can transition to: running, canceled

        - **running**: Worker has picked up the job and is executing
          - started_at is set
          - Agent SDK is processing the query
          - Can transition to: complete, failed, canceled

        - **complete**: Job finished successfully (terminal state)
          - completed_at is set
          - result contains agent response (messages, summary)
          - Webhook triggered if configured
          - Cannot transition to other states

        - **failed**: Job encountered an error (terminal state)
          - completed_at is set
          - error contains error message
          - Webhook triggered if configured
          - May retry if worker implements retry logic
          - Cannot transition to other states (unless retried as new job)

        - **canceled**: Job was canceled before completion (terminal state)
          - canceled_at is set
          - Job was canceled via DELETE /jobs/{job_id}
          - Workers skip canceled jobs
          - No webhook triggered
          - Cannot transition to other states

    Terminal States:
        complete, failed, and canceled are terminal states - once a job reaches
        one of these states, it will not transition to any other state. Terminal
        states have completed_at or canceled_at timestamps set.

    Timing Fields:
        - created_at: When job was submitted (always present)
        - started_at: When worker began processing (running/complete/failed)
        - completed_at: When job finished (complete/failed only)
        - canceled_at: When job was canceled (canceled only)
        - queue_latency_ms: started_at - created_at
        - duration_ms: completed_at - started_at

    Result Structure (when status=complete):
        ```python
        {
            "messages": [
                {"type": "text", "content": "..."},
                {"type": "tool_use", "tool_name": "...", ...},
                # ... more messages
            ],
            "summary": {
                "session_id": "sess_abc123",
                "duration_ms": 1234,
                "num_turns": 3,
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "total_cost_usd": 0.001
            }
        }
        ```

    Artifact Structure (when artifacts present):
        Artifacts are files created by the agent during job execution.
        Available via GET /jobs/{job_id}/artifacts and
        GET /jobs/{job_id}/artifacts/{path}

        ```python
        {
            "root": "/data/jobs/550e8400-e29b-41d4-a716-446655440000",
            "files": [
                {
                    "path": "output.txt",
                    "size_bytes": 1024,
                    "content_type": "text/plain",
                    "created_at": 1672531200,
                    "modified_at": 1672531250
                },
                {
                    "path": "results/data.csv",
                    "size_bytes": 2048,
                    "content_type": "text/csv",
                    "created_at": 1672531300
                }
            ]
        }
        ```

    Webhook Status (when webhook configured):
        Tracks delivery attempts for job completion webhooks.

        ```python
        {
            "url": "https://example.com/webhook",
            "attempts": 3,
            "last_status": 200,
            "delivered_at": 1672531400
        }
        ```

    Multi-Tenancy Fields:
        - tenant_id: Isolates jobs by organization/workspace
        - user_id: Identifies end-user within tenant
        - metadata: Custom client data for tracking

    Examples:
        Queued job:
        ```python
        {
            "ok": True,
            "job_id": "550e8400-e29b-41d4-a716-446655440000",
            "status": "queued",
            "created_at": 1672531200,
            "updated_at": 1672531200,
            "attempts": 0
        }
        ```

        Completed job with result:
        ```python
        {
            "ok": True,
            "job_id": "550e8400-e29b-41d4-a716-446655440000",
            "status": "complete",
            "result": {
                "messages": [...],
                "summary": {"session_id": "sess_123", ...}
            },
            "created_at": 1672531200,
            "started_at": 1672531205,
            "completed_at": 1672531210,
            "queue_latency_ms": 5000,
            "duration_ms": 5000,
            "attempts": 1
        }
        ```

        Failed job with error:
        ```python
        {
            "ok": True,
            "job_id": "550e8400-e29b-41d4-a716-446655440000",
            "status": "failed",
            "error": "Agent SDK error: connection timeout",
            "created_at": 1672531200,
            "started_at": 1672531205,
            "completed_at": 1672531210,
            "attempts": 1
        }
        ```

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


class WorkspaceMetadata(BaseSchema):
    """Metadata for a job workspace tracked for retention purposes.

    Stores information about a job's workspace directory including creation time,
    size, and file count. Used by the retention system to determine which workspaces
    are eligible for cleanup based on age and job status.

    Attributes:
        job_id: UUID of the job that owns this workspace
        workspace_root: Absolute path to the workspace directory
        created_at: Unix timestamp when workspace was created
        size_bytes: Total size of workspace in bytes (may be None if not computed)
        file_count: Number of files in the workspace
        status: Workspace status ("active" or "deleted")
        deleted_at: Unix timestamp when workspace was deleted (None if active)
        job_status: Status of the associated job (queued, running, complete, failed, canceled)

    """

    job_id: str = Field(description="UUID of the job")
    workspace_root: str = Field(description="Absolute path to workspace directory")
    created_at: int = Field(description="Unix timestamp when workspace was created")
    size_bytes: int | None = Field(default=None, ge=0, description="Total workspace size in bytes")
    file_count: int = Field(default=0, ge=0, description="Number of files in workspace")
    status: Literal["active", "deleted"] = Field(default="active", description="Workspace status")
    deleted_at: int | None = Field(
        default=None, description="Unix timestamp when workspace was deleted"
    )
    job_status: Literal["queued", "running", "complete", "failed", "canceled"] | None = Field(
        default=None, description="Status of the associated job"
    )


class WorkspaceCleanupRequest(BaseSchema):
    """Request body for triggering workspace cleanup.

    Allows filtering which workspaces to clean up based on age and job status.
    Supports dry-run mode to preview cleanup without actually deleting files.

    Attributes:
        older_than_days: Only clean up workspaces older than this many days.
                        If None, uses default retention settings.
        status_filter: Only clean up workspaces for jobs with these statuses.
                      If None, uses default (complete, failed, canceled).
        dry_run: If True, returns what would be deleted without actually deleting.

    """

    older_than_days: int | None = Field(
        default=None,
        ge=0,
        description="Only cleanup workspaces older than this many days (None = use defaults)",
    )
    status_filter: list[Literal["queued", "running", "complete", "failed", "canceled"]] | None = (
        Field(
            default=None,
            description="Only cleanup workspaces for jobs with these statuses",
        )
    )
    dry_run: bool = Field(
        default=False, description="If True, report what would be deleted without deleting"
    )


class WorkspaceCleanupResponse(BaseSchema):
    """Response body for workspace cleanup operations.

    Reports statistics about the cleanup operation including number of workspaces
    checked, deleted, and bytes freed.

    Attributes:
        ok: True if cleanup completed successfully
        dry_run: True if this was a dry-run (no actual deletions)
        workspaces_checked: Total number of workspaces evaluated
        workspaces_deleted: Number of workspaces deleted (or would be deleted in dry-run)
        bytes_freed: Total bytes freed (or would be freed in dry-run)
        deleted_job_ids: List of job IDs whose workspaces were deleted
        errors: List of error messages encountered during cleanup

    """

    ok: bool = Field(default=True, description="True if cleanup completed successfully")
    dry_run: bool = Field(default=False, description="True if this was a preview only")
    workspaces_checked: int = Field(default=0, ge=0, description="Number of workspaces evaluated")
    workspaces_deleted: int = Field(default=0, ge=0, description="Number of workspaces deleted")
    bytes_freed: int = Field(default=0, ge=0, description="Total bytes freed by cleanup")
    deleted_job_ids: list[str] = Field(
        default_factory=list, description="Job IDs whose workspaces were deleted"
    )
    errors: list[str] = Field(default_factory=list, description="Errors encountered during cleanup")


class WorkspaceRetentionStatusResponse(BaseSchema):
    """Response body for workspace retention status queries.

    Provides an overview of workspace retention settings and current state,
    including counts, total size, and age statistics.

    Attributes:
        enabled: Whether workspace retention is enabled
        retention_days: Days to keep completed job workspaces
        failed_retention_days: Days to keep failed job workspaces
        total_workspaces: Total number of tracked workspaces
        active_workspaces: Number of active (not deleted) workspaces
        total_size_bytes: Total size of all active workspaces
        oldest_workspace_age_days: Age in days of the oldest active workspace
        workspaces_pending_cleanup: Number of workspaces eligible for cleanup

    """

    enabled: bool = Field(description="Whether workspace retention is enabled")
    retention_days: int = Field(description="Days to keep completed job workspaces")
    failed_retention_days: int = Field(description="Days to keep failed job workspaces")
    total_workspaces: int = Field(default=0, ge=0, description="Total tracked workspaces")
    active_workspaces: int = Field(default=0, ge=0, description="Active (not deleted) workspaces")
    total_size_bytes: int = Field(default=0, ge=0, description="Total size of active workspaces")
    oldest_workspace_age_days: float | None = Field(
        default=None, description="Age of oldest workspace in days"
    )
    workspaces_pending_cleanup: int = Field(
        default=0, ge=0, description="Workspaces eligible for cleanup"
    )


class WorkspaceDeleteResponse(BaseSchema):
    """Response body for deleting a specific job workspace.

    Attributes:
        ok: True if deletion was successful
        job_id: UUID of the job whose workspace was deleted
        deleted: True if workspace was actually deleted (False if already deleted/not found)
        bytes_freed: Number of bytes freed by deletion

    """

    ok: bool = Field(default=True, description="True if operation completed successfully")
    job_id: str = Field(description="UUID of the job")
    deleted: bool = Field(description="True if workspace was deleted")
    bytes_freed: int = Field(default=0, ge=0, description="Bytes freed by deletion")
