"""Queue-based async job processing for agent runs.

This module provides functions for managing asynchronous agent jobs using Modal's
distributed primitives. Jobs are submitted to a queue and processed by background
workers, with status tracked in a distributed dictionary.

Job Lifecycle:
    1. queued   - Job submitted via enqueue_job(), waiting for worker pickup
    2. running  - Worker has picked up the job and is executing
    3. complete - Job finished successfully with a result
    4. failed   - Job encountered an error during execution
    5. canceled - Job was canceled before completion (skipped by workers)

See: https://modal.com/docs/guide/dicts-and-queues
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

import modal

from agent_sandbox.config.settings import get_settings
from agent_sandbox.schemas.jobs import JobStatusResponse, WebhookConfig

_settings = get_settings()

# Distributed queue for pending job payloads. Workers call JOB_QUEUE.get() to
# receive {"job_id": str, "question": str} messages.
JOB_QUEUE = modal.Queue.from_name(_settings.job_queue_name, create_if_missing=True)

# Distributed dictionary storing job metadata keyed by job_id. Each entry contains
# status, timestamps, result/error, and attempt count. Persists across workers.
JOB_RESULTS = modal.Dict.from_name(_settings.job_results_dict, create_if_missing=True)


def normalize_job_id(job_id: str | None) -> str | None:
    """Normalize and validate job IDs to prevent injection attacks.

    Validates that job_id is a properly formatted UUID and returns the canonical
    string representation. This prevents path traversal, SQL injection, and other
    attacks that could exploit unsanitized job IDs.

    Args:
        job_id: Potential job ID from user input (HTTP path, query params, etc.)

    Returns:
        Canonical UUID string in format "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
        (lowercase, with hyphens), or None if invalid.

    Security Rationale:
        Job IDs are used in:
        - Filesystem paths: /data/jobs/{job_id}/
        - Dict keys: JOB_RESULTS[job_id]
        - HTTP responses

        Without validation, malicious inputs could cause:
        - Path traversal: "../../../etc/passwd" as job_id
        - Resource exhaustion: Extremely long strings
        - Dict key collisions: Non-canonical UUID representations

        By normalizing to UUID format, we ensure:
        - Predictable length (36 characters)
        - Alphanumeric characters + hyphens only
        - Canonical representation (prevents "same" UUID in different formats)

    Examples:
        Valid inputs (normalized to canonical format):
        >>> normalize_job_id("550e8400-e29b-41d4-a716-446655440000")
        '550e8400-e29b-41d4-a716-446655440000'

        >>> normalize_job_id("550E8400-E29B-41D4-A716-446655440000")  # uppercase
        '550e8400-e29b-41d4-a716-446655440000'

        Invalid inputs (rejected):
        >>> normalize_job_id("../../../etc/passwd")
        None

        >>> normalize_job_id("not-a-uuid")
        None

        >>> normalize_job_id("")
        None

        >>> normalize_job_id(None)
        None

    Usage:
        Always use at HTTP endpoint boundaries before filesystem/dict operations:
        ```python
        job_id = normalize_job_id(request.path_params["job_id"])
        if not job_id:
            return JSONResponse({"error": "Invalid job_id"}, status_code=400)
        ```
    """
    if not job_id:
        return None
    try:
        return str(UUID(str(job_id)))
    except (ValueError, TypeError, AttributeError):
        return None


def job_workspace_root(agent_fs_root: str, job_id: str) -> Path:
    """Return the isolated workspace directory for a specific job.

    Constructs the filesystem path where a job can safely write artifacts without
    affecting other jobs. Each job gets its own directory for complete isolation.

    Args:
        agent_fs_root: Root directory for all agent filesystem operations,
                      typically "/data" which is a Modal persistent volume mount
        job_id: Validated UUID job identifier (must be pre-validated with
               normalize_job_id to prevent path traversal)

    Returns:
        Path object pointing to /data/jobs/{job_id}/
        Note: Directory is NOT created automatically - caller must create if needed

    Security & Isolation:
        - Each job_id gets its own directory preventing cross-job access
        - job_id MUST be validated before calling to prevent path traversal
        - Directory is NOT created by this function (explicit creation required)

    Volume Persistence:
        - If agent_fs_root is a Modal persistent volume, job workspaces persist
          across sandbox restarts and can be accessed by other Modal functions
        - Volume commits (if configured) ensure writes are durably persisted
        - Without volume commit, writes persist only until sandbox termination

    Path Structure:
        {agent_fs_root}/jobs/{job_id}/
        Example: /data/jobs/550e8400-e29b-41d4-a716-446655440000/

    Usage:
        ```python
        # Create workspace before job execution
        workspace = job_workspace_root("/data", job_id)
        workspace.mkdir(parents=True, exist_ok=True)

        # Agent writes artifacts to workspace
        (workspace / "output.txt").write_text("results")

        # Later: retrieve artifacts
        artifact_path = resolve_job_artifact("/data", job_id, "output.txt")
        ```

    See Also:
        - resolve_job_artifact(): Safely resolve paths within workspace
        - _ensure_job_workspace(): Creates workspace if missing
    """
    return Path(agent_fs_root) / "jobs" / job_id


def resolve_job_artifact(agent_fs_root: str, job_id: str, artifact_path: str) -> Path | None:
    """Safely resolve artifact path preventing directory traversal attacks.

    Validates that the requested artifact path stays within the job's workspace,
    preventing attackers from accessing files outside the job directory using
    path traversal techniques (../, absolute paths, symlinks).

    Args:
        agent_fs_root: Root directory for agent filesystem, typically "/data"
        job_id: Validated UUID job identifier (must be pre-validated)
        artifact_path: User-provided relative path to artifact within job workspace
                      (e.g., "output.txt", "results/data.csv")

    Returns:
        Absolute Path to artifact if it's within job workspace, None if traversal
        attempted or path escapes the workspace boundary.

    Security Model:
        This function prevents directory traversal attacks using canonical path
        resolution (.resolve()) and boundary checking (.relative_to()):

        1. Compute job workspace base: /data/jobs/{job_id}/
        2. Resolve base to canonical absolute path (follows symlinks)
        3. Join artifact_path to base: base / artifact_path
        4. Resolve candidate to canonical absolute path
        5. Verify candidate is within base using relative_to()
        6. If relative_to() raises ValueError, path escaped → return None

    Blocked Attack Patterns:
        >>> resolve_job_artifact("/data", "550e8400-...", "../../../etc/passwd")
        None  # Attempts to access /etc/passwd

        >>> resolve_job_artifact("/data", "550e8400-...", "/etc/passwd")
        None  # Absolute path escapes workspace

        >>> resolve_job_artifact("/data", "550e8400-...", "subdir/../../other_job/file")
        None  # Traverses out then into different job directory

    Allowed Patterns:
        >>> resolve_job_artifact("/data", "550e8400-...", "output.txt")
        PosixPath('/data/jobs/550e8400-.../output.txt')

        >>> resolve_job_artifact("/data", "550e8400-...", "results/data.csv")
        PosixPath('/data/jobs/550e8400-.../results/data.csv')

        >>> resolve_job_artifact("/data", "550e8400-...", "./output.txt")
        PosixPath('/data/jobs/550e8400-.../output.txt')  # ./ normalized

    Edge Cases:
        - Symlinks: .resolve() follows symlinks before checking boundaries
        - Relative paths: Normalized before boundary check
        - Non-existent files: Still validated (path doesn't need to exist)
        - Empty path: Resolves to workspace root (allowed)

    Implementation Details:
        Uses pathlib.Path.relative_to() which raises ValueError if the candidate
        path is not a descendant of the base path. This is more robust than
        string prefix checking which can be bypassed with tricks like:
        - /data/jobs/abc/../../../etc/passwd
        - /data/jobs/abc/./../../etc/passwd

    Usage in HTTP Endpoints:
        ```python
        @app.get("/jobs/{job_id}/artifacts/{artifact_path:path}")
        def download_artifact(job_id: str, artifact_path: str):
            job_id = normalize_job_id(job_id)
            if not job_id:
                return JSONResponse({"error": "Invalid job_id"}, 400)

            resolved = resolve_job_artifact("/data", job_id, artifact_path)
            if not resolved or not resolved.exists():
                return JSONResponse({"error": "Artifact not found"}, 404)

            return FileResponse(resolved)
        ```

    See Also:
        - job_workspace_root(): Get base workspace directory
        - normalize_job_id(): Validate job IDs before use
    """
    base = job_workspace_root(agent_fs_root, job_id).resolve()
    candidate = (base / artifact_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def _normalize_schedule_at(value: int | float | None) -> int | None:
    """Normalize schedule_at inputs to integer unix timestamps for consistent storage.

    Converts various numeric types to canonical integer unix timestamp format,
    validates that timestamps are positive, and rejects invalid inputs. This
    ensures consistent representation in job records regardless of input format.

    Args:
        value: Potential schedule_at value from user input:
               - int: Unix timestamp in seconds (e.g., 1672531200)
               - float: Unix timestamp with fractional seconds (e.g., 1672531200.5)
               - None: No scheduling (immediate execution)

    Returns:
        - Integer unix timestamp if value is valid and positive
        - None if value is None, non-numeric, or <= 0

    Normalization Rules:
        1. None inputs → None (immediate execution, no scheduling)
        2. Numeric inputs → int(value) (truncates fractional seconds)
        3. Non-positive values → None (invalid: timestamps must be > 0)
        4. Non-numeric values → None (invalid type)

    Why Normalization is Needed:
        - HTTP APIs may send "1672531200" (string) or 1672531200.0 (float)
        - JSON deserializes integers as int or float depending on value
        - Modal Dict storage requires consistent types for equality checks
        - Fractional seconds are not needed for scheduling granularity

    Validation:
        Only positive integers are accepted because:
        - Unix timestamps are always positive (seconds since 1970-01-01)
        - Negative/zero values indicate invalid or uninitialized timestamps
        - Zero is rejected to distinguish from "no schedule" (None)

    Examples:
        Valid inputs (normalized to int):
        >>> _normalize_schedule_at(1672531200)
        1672531200

        >>> _normalize_schedule_at(1672531200.75)  # Fractional seconds
        1672531200  # Truncated

        >>> _normalize_schedule_at(None)
        None  # Immediate execution

        Invalid inputs (rejected):
        >>> _normalize_schedule_at(0)
        None  # Zero is invalid

        >>> _normalize_schedule_at(-1672531200)
        None  # Negative timestamps invalid

        >>> _normalize_schedule_at("2024-01-01")
        None  # Strings not accepted (use int(datetime.timestamp()))

        >>> _normalize_schedule_at([1672531200])
        None  # Non-numeric types rejected

    Usage:
        Called internally by enqueue_job() to sanitize user-provided schedule_at:
        ```python
        def enqueue_job(question, schedule_at=None):
            normalized = _normalize_schedule_at(schedule_at)
            # normalized is guaranteed to be int, None, or rejected
            JOB_RESULTS[job_id] = {"schedule_at": normalized, ...}
        ```

    See Also:
        - is_job_due(): Checks if normalized timestamp has passed
        - enqueue_job(): Accepts schedule_at parameter
    """
    if value is None:
        return None
    try:
        schedule_at = int(value)
    except (TypeError, ValueError):
        return None
    return schedule_at if schedule_at > 0 else None


def _normalize_webhook(value: WebhookConfig | dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize webhook config into a plain dict for persistence in Modal Dict.

    Converts Pydantic WebhookConfig objects to plain Python dictionaries suitable
    for storage in Modal Dict, which cannot directly serialize Pydantic models.
    Ensures consistent storage format regardless of input type.

    Args:
        value: Webhook configuration in various formats:
               - WebhookConfig: Pydantic model instance with url, secret, headers
               - dict[str, Any]: Pre-serialized webhook configuration
               - None: No webhook configured for this job

    Returns:
        - Plain dict with webhook config fields if value is valid
        - None if value is None or invalid type

    Why Normalization is Needed:
        Modal Dict uses pickle for serialization, but Pydantic models can have
        complex internal state that doesn't serialize reliably:
        - Validators and config are not preserved
        - Custom types may fail to pickle
        - Dict representation is more robust for distributed storage
        - Allows reading records without Pydantic dependency

    Dict Structure Preserved:
        The output dict contains the same fields as WebhookConfig:
        {
            "url": "https://example.com/webhook",
            "secret_ref": "webhook-secret",  # Optional
            "headers": {"X-Custom": "value"},  # Optional
            "timeout": 10  # Optional
        }

        Fields with None values are excluded via exclude_none=True to reduce
        storage size and simplify conditional logic.

    Examples:
        Pydantic model input:
        >>> from agent_sandbox.schemas.jobs import WebhookConfig
        >>> config = WebhookConfig(url="https://example.com/hook")
        >>> _normalize_webhook(config)
        {'url': 'https://example.com/hook'}

        Dict input (pass-through):
        >>> config_dict = {"url": "https://example.com/hook", "timeout": 5}
        >>> _normalize_webhook(config_dict)
        {'url': 'https://example.com/hook', 'timeout': 5}

        None input:
        >>> _normalize_webhook(None)
        None

        Invalid type (rejected):
        >>> _normalize_webhook("https://example.com/hook")
        None  # Strings not accepted

    Usage:
        Called internally by enqueue_job() before storing webhook config:
        ```python
        def enqueue_job(question, webhook=None):
            normalized_webhook = _normalize_webhook(webhook)
            # Safe to store in Modal Dict
            JOB_RESULTS[job_id] = {"webhook_config": normalized_webhook, ...}
        ```

    Implementation Note:
        Uses model_dump(exclude_none=True) for Pydantic objects to:
        - Convert to dict representation
        - Exclude fields with None values (cleaner storage)
        - Preserve nested dict/list structures

    See Also:
        - WebhookConfig: Pydantic schema for webhook configuration
        - enqueue_job(): Accepts webhook parameter
        - deliver_webhook(): Uses stored webhook config
    """
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return value
    return None


def enqueue_job(
    question: str,
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    schedule_at: int | float | None = None,
    webhook: WebhookConfig | dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Create a new job and add it to the processing queue.

    Initializes job metadata in JOB_RESULTS with "queued" status, then pushes
    the job payload to JOB_QUEUE for worker pickup.

    Args:
        question: The user question/prompt to be processed by the agent.

    Returns:
        The unique job_id (UUID4) that can be used to track status.
    """
    job_id = str(uuid.uuid4())
    now = int(time.time())
    normalized_schedule_at = _normalize_schedule_at(schedule_at)
    normalized_webhook = _normalize_webhook(webhook)
    # Initialize job record with queued status before pushing to queue
    JOB_RESULTS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "question": question,
        "created_at": now,
        "updated_at": now,
        "attempts": 0,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "schedule_at": normalized_schedule_at,
        "webhook_config": normalized_webhook,
        "webhook": {
            "url": normalized_webhook.get("url") if normalized_webhook else None,
            "secret_ref": normalized_webhook.get("secret_ref") if normalized_webhook else None,
            "attempts": 0 if normalized_webhook else None,
        },
        "metadata": metadata,
    }
    JOB_QUEUE.put({"job_id": job_id, "question": question})
    return job_id


def _status_payload(record: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = JobStatusResponse.model_fields.keys()
    return {key: record.get(key) for key in allowed_keys if key in record}


def get_job_status(job_id: str) -> JobStatusResponse | None:
    """Retrieve the current status and metadata for a job.

    Args:
        job_id: The unique identifier returned by enqueue_job().

    Returns:
        JobStatusResponse with current status, result/error, and timestamps,
        or None if the job_id is not found.
    """
    record = JOB_RESULTS.get(job_id)
    if not record:
        return None
    return JobStatusResponse(**_status_payload(record))


def get_job_record(job_id: str) -> dict[str, Any] | None:
    """Return the raw job record from the job store."""
    record = JOB_RESULTS.get(job_id)
    return record if record else None


def is_job_due(job_id: str, *, now: int | None = None) -> bool:
    """Check if a scheduled job is ready to execute based on schedule_at timestamp.

    Compares the job's schedule_at timestamp (if present) against current time
    to determine if the job should be processed now. Used by workers to implement
    deferred job execution and job scheduling.

    Args:
        job_id: Unique job identifier to check
        now: Optional unix timestamp to use as current time (for testing).
             Defaults to time.time() if None.

    Returns:
        True in any of these cases:
        - Job has no schedule_at field (immediate execution)
        - Job's schedule_at is None (immediate execution)
        - Current time >= schedule_at (scheduled time has arrived)
        - Job record not found (defensive: assume due to avoid dropping)

        False only when:
        - Job has future schedule_at and current time < schedule_at

    Scheduling Behavior:
        Jobs with schedule_at are deferred until the specified unix timestamp.
        Workers should check is_job_due() before processing and re-queue jobs
        that are not yet due.

        schedule_at must be a unix timestamp (seconds since epoch, UTC).
        No timezone conversion is performed - caller must provide UTC timestamps.

    Examples:
        ```python
        # Immediate execution (no schedule_at)
        job_id = enqueue_job("What is 2+2?")
        assert is_job_due(job_id) is True

        # Scheduled for future
        future_time = int(time.time()) + 3600  # 1 hour from now
        job_id = enqueue_job("Reminder", schedule_at=future_time)
        assert is_job_due(job_id) is False

        # Scheduled time has passed
        past_time = int(time.time()) - 60  # 1 minute ago
        job_id = enqueue_job("Late job", schedule_at=past_time)
        assert is_job_due(job_id) is True

        # Test with custom timestamp
        job_id = enqueue_job("Test", schedule_at=1000)
        assert is_job_due(job_id, now=999) is False
        assert is_job_due(job_id, now=1000) is True
        assert is_job_due(job_id, now=1001) is True
        ```

    See Also:
        - job_schedule_delay(): Get seconds until job is due
        - enqueue_job(): Create scheduled jobs with schedule_at parameter
    """
    record = JOB_RESULTS.get(job_id)
    if not record:
        return True
    schedule_at = record.get("schedule_at")
    if not schedule_at:
        return True
    now_ts = now if now is not None else int(time.time())
    return int(schedule_at) <= now_ts


def job_schedule_delay(job_id: str, *, now: int | None = None) -> int | None:
    """Return seconds until job is due, or 0 if due."""
    record = JOB_RESULTS.get(job_id)
    if not record:
        return None
    schedule_at = record.get("schedule_at")
    if not schedule_at:
        return 0
    now_ts = now if now is not None else int(time.time())
    delay = int(schedule_at) - now_ts
    return delay if delay > 0 else 0


def cancel_job(job_id: str) -> JobStatusResponse | None:
    """Mark a job as canceled so workers will skip it.

    Only queued or running jobs can be canceled. Jobs that have already
    completed or failed are returned unchanged.

    Args:
        job_id: The unique identifier of the job to cancel.

    Returns:
        Updated JobStatusResponse with "canceled" status if cancelable,
        the unchanged record if already complete/failed,
        or None if job_id is not found.
    """
    record = JOB_RESULTS.get(job_id)
    if not record:
        return None
    # Terminal states cannot be canceled - return as-is
    if record.get("status") in {"complete", "failed"}:
        return JobStatusResponse(**_status_payload(record))
    now = int(time.time())
    updated = {
        **record,
        "status": "canceled",
        "canceled_at": now,
        "updated_at": now,
    }
    JOB_RESULTS[job_id] = updated
    return JobStatusResponse(**_status_payload(updated))


def update_job(job_id: str, updates: dict[str, Any]) -> None:
    """Merge updates into a job's metadata record.

    Used by workers to update status, result, error, or other fields.
    Automatically sets updated_at timestamp.

    Args:
        job_id: The unique identifier of the job to update.
        updates: Dictionary of fields to merge (e.g., {"status": "running"}).
    """
    record = JOB_RESULTS.get(job_id, {"job_id": job_id})
    now = int(time.time())
    updated = {
        **record,
        **updates,
        "updated_at": now,
    }
    JOB_RESULTS[job_id] = updated


def should_skip_job(job_id: str) -> bool:
    """Check if a job should be skipped by the worker.

    Workers call this before processing to respect cancellation requests.

    Args:
        job_id: The unique identifier of the job to check.

    Returns:
        True if the job status is "canceled", False otherwise.
    """
    record = JOB_RESULTS.get(job_id)
    if not record:
        return False
    return record.get("status") == "canceled"


def bump_attempts(job_id: str) -> int:
    """Increment and return the attempt counter for a job.

    Called by workers each time they start processing a job. Used for
    tracking retries and debugging failed jobs.

    Args:
        job_id: The unique identifier of the job.

    Returns:
        The new attempt count after incrementing.
    """
    record = JOB_RESULTS.get(job_id, {"job_id": job_id})
    attempts = int(record.get("attempts", 0)) + 1
    update_job(job_id, {"attempts": attempts})
    return attempts
