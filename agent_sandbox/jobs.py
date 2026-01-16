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
from datetime import UTC
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

# Distributed dictionary for storing session filesystem snapshots.
# Keys are session_id values, values are dicts with:
#   - image_id: Modal Image object_id for the snapshot
#   - created_at: Unix timestamp when snapshot was taken
#   - sandbox_name: Name of sandbox that was snapshotted
# Used to restore filesystem state when resuming a session after sandbox timeout.
SESSION_SNAPSHOTS = modal.Dict.from_name(
    _settings.session_snapshot_store_name, create_if_missing=True
)


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


# =============================================================================
# STATISTICS AND METRICS
# =============================================================================
# Distributed dictionary for storing aggregate statistics.
# Keys are time-bucketed (e.g., "stats:hourly:2024-01-15T14", "stats:daily:2024-01-15")
# Values are dicts with counts and aggregates for that time bucket.

STATS_STORE = modal.Dict.from_name(_settings.stats_store_name, create_if_missing=True)


def _get_time_bucket_keys() -> tuple[str, str]:
    """Get current hourly and daily bucket keys for statistics.

    Returns:
        Tuple of (hourly_key, daily_key) for current time bucket.
    """
    from datetime import datetime

    now = datetime.now(UTC)
    hourly_key = f"stats:hourly:{now.strftime('%Y-%m-%dT%H')}"
    daily_key = f"stats:daily:{now.strftime('%Y-%m-%d')}"
    return hourly_key, daily_key


def record_session_start(
    sandbox_type: str = "agent_sdk",
    *,
    job_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Record that a new session has started.

    Args:
        sandbox_type: Type of sandbox ("agent_sdk", "cli", "ralph")
        job_id: Optional job ID for correlation
        user_id: Optional user ID for unique user tracking
    """
    now = int(time.time())
    hourly_key, daily_key = _get_time_bucket_keys()

    for key in [hourly_key, daily_key]:
        bucket = STATS_STORE.get(key) or {
            "timestamp": now,
            "agent_sdk": {"started": 0, "completed": 0, "failed": 0, "canceled": 0},
            "cli": {"started": 0, "completed": 0, "failed": 0, "canceled": 0},
            "ralph": {"started": 0, "completed": 0, "failed": 0, "canceled": 0},
            "users": set(),
            "durations": [],
            "queue_latencies": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
        }

        # Increment started count
        if sandbox_type in bucket:
            bucket[sandbox_type]["started"] = bucket[sandbox_type].get("started", 0) + 1

        # Track unique users (convert set to list for JSON serialization)
        if user_id:
            users = set(bucket.get("users", []))
            users.add(user_id)
            bucket["users"] = list(users)

        bucket["updated_at"] = now
        STATS_STORE[key] = bucket


def record_session_end(
    sandbox_type: str = "agent_sdk",
    status: str = "complete",
    *,
    duration_ms: int | None = None,
    queue_latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
) -> None:
    """Record that a session has ended.

    Args:
        sandbox_type: Type of sandbox ("agent_sdk", "cli", "ralph")
        status: Final status ("complete", "failed", "canceled")
        duration_ms: Session duration in milliseconds
        queue_latency_ms: Time from enqueue to start in milliseconds
        input_tokens: Input tokens consumed
        output_tokens: Output tokens generated
        cost_usd: Cost in USD
    """
    now = int(time.time())
    hourly_key, daily_key = _get_time_bucket_keys()

    for key in [hourly_key, daily_key]:
        bucket = STATS_STORE.get(key) or {
            "timestamp": now,
            "agent_sdk": {"started": 0, "completed": 0, "failed": 0, "canceled": 0},
            "cli": {"started": 0, "completed": 0, "failed": 0, "canceled": 0},
            "ralph": {"started": 0, "completed": 0, "failed": 0, "canceled": 0},
            "users": [],
            "durations": [],
            "queue_latencies": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
        }

        # Increment status count
        if sandbox_type in bucket:
            status_key = status if status in ["completed", "failed", "canceled"] else "completed"
            # Normalize "complete" to "completed" for storage
            if status == "complete":
                status_key = "completed"
            bucket[sandbox_type][status_key] = bucket[sandbox_type].get(status_key, 0) + 1

        # Track durations (keep last 1000 for averaging)
        if duration_ms is not None:
            durations = bucket.get("durations", [])
            durations.append(duration_ms)
            bucket["durations"] = durations[-1000:]

        # Track queue latencies
        if queue_latency_ms is not None:
            latencies = bucket.get("queue_latencies", [])
            latencies.append(queue_latency_ms)
            bucket["queue_latencies"] = latencies[-1000:]

        # Accumulate token usage
        if input_tokens is not None:
            bucket["input_tokens"] = bucket.get("input_tokens", 0) + input_tokens
        if output_tokens is not None:
            bucket["output_tokens"] = bucket.get("output_tokens", 0) + output_tokens
        if cost_usd is not None:
            bucket["cost_usd"] = bucket.get("cost_usd", 0.0) + cost_usd

        bucket["updated_at"] = now
        STATS_STORE[key] = bucket


def get_stats(period_hours: int = 24, include_time_series: bool = False) -> dict[str, Any]:
    """Retrieve aggregated statistics for a time period.

    Args:
        period_hours: Number of hours to include (default 24, max 720)
        include_time_series: Include hourly/daily breakdown

    Returns:
        Dictionary with aggregated statistics matching StatsResponse schema.
    """
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    period_start = now - timedelta(hours=period_hours)

    # Initialize result structure
    result: dict[str, Any] = {
        "ok": True,
        "period_start": int(period_start.timestamp()),
        "period_end": int(now.timestamp()),
        "agent_sdk": {
            "total_sessions": 0,
            "completed_sessions": 0,
            "failed_sessions": 0,
            "canceled_sessions": 0,
        },
        "cli": {
            "total_sessions": 0,
            "completed_sessions": 0,
            "failed_sessions": 0,
            "canceled_sessions": 0,
        },
        "ralph": {
            "total_sessions": 0,
            "completed_sessions": 0,
            "failed_sessions": 0,
            "canceled_sessions": 0,
        },
        "totals": {
            "total_sessions": 0,
            "completed_sessions": 0,
            "failed_sessions": 0,
            "canceled_sessions": 0,
        },
        "active_sandboxes": 0,
        "users_active_last_5min": 0,
    }

    all_durations: list[int] = []
    all_latencies: list[int] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0
    recent_users: set[str] = set()
    hourly_stats: list[dict[str, Any]] = []

    # Iterate through hourly buckets in the period
    current = period_start
    while current <= now:
        hourly_key = f"stats:hourly:{current.strftime('%Y-%m-%dT%H')}"
        bucket = STATS_STORE.get(hourly_key)

        if bucket:
            # Aggregate by sandbox type
            for sandbox_type in ["agent_sdk", "cli", "ralph"]:
                if sandbox_type in bucket:
                    type_stats = bucket[sandbox_type]
                    result[sandbox_type]["total_sessions"] += type_stats.get("started", 0)
                    result[sandbox_type]["completed_sessions"] += type_stats.get("completed", 0)
                    result[sandbox_type]["failed_sessions"] += type_stats.get("failed", 0)
                    result[sandbox_type]["canceled_sessions"] += type_stats.get("canceled", 0)

            # Aggregate durations and latencies
            all_durations.extend(bucket.get("durations", []))
            all_latencies.extend(bucket.get("queue_latencies", []))

            # Aggregate token usage
            total_input_tokens += bucket.get("input_tokens", 0)
            total_output_tokens += bucket.get("output_tokens", 0)
            total_cost += bucket.get("cost_usd", 0.0)

            # Track recent users (last 5 minutes)
            bucket_time = bucket.get("updated_at", 0)
            if bucket_time and int(now.timestamp()) - bucket_time < 300:
                recent_users.update(bucket.get("users", []))

            # Add to time series if requested
            if include_time_series:
                hourly_stats.append(
                    {
                        "hour": current.strftime("%Y-%m-%dT%H"),
                        "started": sum(
                            bucket.get(st, {}).get("started", 0)
                            for st in ["agent_sdk", "cli", "ralph"]
                        ),
                        "completed": sum(
                            bucket.get(st, {}).get("completed", 0)
                            for st in ["agent_sdk", "cli", "ralph"]
                        ),
                        "failed": sum(
                            bucket.get(st, {}).get("failed", 0)
                            for st in ["agent_sdk", "cli", "ralph"]
                        ),
                    }
                )

        current += timedelta(hours=1)

    # Calculate totals
    for sandbox_type in ["agent_sdk", "cli", "ralph"]:
        result["totals"]["total_sessions"] += result[sandbox_type]["total_sessions"]
        result["totals"]["completed_sessions"] += result[sandbox_type]["completed_sessions"]
        result["totals"]["failed_sessions"] += result[sandbox_type]["failed_sessions"]
        result["totals"]["canceled_sessions"] += result[sandbox_type]["canceled_sessions"]

    # Calculate averages and rates
    for sandbox_type in ["agent_sdk", "cli", "ralph", "totals"]:
        stats = result[sandbox_type]
        finished = stats["completed_sessions"] + stats["failed_sessions"]
        if finished > 0:
            stats["success_rate"] = round(stats["completed_sessions"] / finished, 3)

    if all_durations:
        result["totals"]["avg_duration_ms"] = round(sum(all_durations) / len(all_durations), 1)
    if all_latencies:
        result["totals"]["avg_queue_latency_ms"] = round(sum(all_latencies) / len(all_latencies), 1)

    # Add token usage to totals
    if total_input_tokens > 0:
        result["totals"]["total_input_tokens"] = total_input_tokens
    if total_output_tokens > 0:
        result["totals"]["total_output_tokens"] = total_output_tokens
    if total_cost > 0:
        result["totals"]["total_cost_usd"] = round(total_cost, 4)

    result["users_active_last_5min"] = len(recent_users)

    if include_time_series:
        result["hourly_stats"] = hourly_stats

    return result


# =============================================================================
# SESSION SNAPSHOT MANAGEMENT
# =============================================================================
# Functions for storing and retrieving session filesystem snapshots.
# Snapshots capture the sandbox filesystem state after agent work completes,
# enabling session restoration when resuming after sandbox timeout.


def store_session_snapshot(
    session_id: str,
    image_id: str,
    sandbox_name: str,
) -> dict[str, Any]:
    """Store a filesystem snapshot reference for a session.

    Records the Modal Image ID from a sandbox.snapshot_filesystem() call,
    allowing the session's filesystem state to be restored when the session
    resumes in a new sandbox.

    Args:
        session_id: The Claude Agent SDK session ID to associate with this snapshot.
        image_id: Modal Image object_id from sandbox.snapshot_filesystem().
        sandbox_name: Name of the sandbox that was snapshotted (e.g., "svc-runner-8001").

    Returns:
        Dict with snapshot metadata including session_id, image_id, sandbox_name,
        and created_at timestamp.

    Storage Structure:
        SESSION_SNAPSHOTS[session_id] = {
            "session_id": "sess_abc123",
            "image_id": "im-xxx",
            "sandbox_name": "svc-runner-8001",
            "created_at": 1704067200,
        }

    Usage:
        Called after agent query completes to capture filesystem state:
        ```python
        # After agent work completes
        image = sandbox.snapshot_filesystem()
        store_session_snapshot(
            session_id=result.session_id,
            image_id=image.object_id,
            sandbox_name="svc-runner-8001",
        )
        ```

    Note:
        - Only stores the latest snapshot per session (overwrites previous)
        - image_id can be used to create a new sandbox from the snapshot:
          `modal.Sandbox.create(image=modal.Image.from_id(image_id), ...)`
        - Snapshots persist in Modal Dict across sandbox restarts

    See Also:
        - get_session_snapshot(): Retrieve snapshot for session restoration
        - should_snapshot_session(): Check if snapshot is needed
    """
    now = int(time.time())
    snapshot_info = {
        "session_id": session_id,
        "image_id": image_id,
        "sandbox_name": sandbox_name,
        "created_at": now,
    }
    SESSION_SNAPSHOTS[session_id] = snapshot_info
    return snapshot_info


def get_session_snapshot(session_id: str) -> dict[str, Any] | None:
    """Retrieve the stored filesystem snapshot for a session.

    Looks up the most recent snapshot for the given session ID, which can be
    used to restore filesystem state when creating a new sandbox.

    Args:
        session_id: The Claude Agent SDK session ID to look up.

    Returns:
        Snapshot metadata dict if found, containing:
        - session_id: The session identifier
        - image_id: Modal Image object_id for sandbox.create(image=...)
        - sandbox_name: Original sandbox name
        - created_at: Unix timestamp of snapshot

        None if no snapshot exists for this session.

    Usage:
        Called when resuming a session to check for restorable state:
        ```python
        snapshot = get_session_snapshot(session_id)
        if snapshot:
            # Create sandbox from snapshot
            image = modal.Image.from_id(snapshot["image_id"])
            sandbox = modal.Sandbox.create(image=image, ...)
        ```

    See Also:
        - store_session_snapshot(): Store new snapshot
        - should_snapshot_session(): Check if new snapshot needed
    """
    return SESSION_SNAPSHOTS.get(session_id)


def should_snapshot_session(
    session_id: str,
    min_interval_seconds: int = 60,
) -> bool:
    """Check if a new snapshot should be taken for a session.

    Prevents excessive snapshot creation by enforcing a minimum interval
    between snapshots for the same session. Snapshots are expensive I/O
    operations, so throttling is important for high-frequency queries.

    Args:
        session_id: The session to check.
        min_interval_seconds: Minimum seconds since last snapshot before
            allowing a new one. Default 60 seconds.

    Returns:
        True if no snapshot exists for this session, or if the last snapshot
        was taken more than min_interval_seconds ago.
        False if a recent snapshot exists (within the interval).

    Usage:
        ```python
        if should_snapshot_session(session_id, min_interval_seconds=60):
            image = sandbox.snapshot_filesystem()
            store_session_snapshot(session_id, image.object_id, sandbox_name)
        ```

    Throttling Behavior:
        - First query for a session: Always snapshots (no existing snapshot)
        - Rapid follow-ups (<60s): Skip snapshot to reduce I/O
        - After interval: Snapshot to capture recent changes

    See Also:
        - store_session_snapshot(): Store new snapshot
        - get_session_snapshot(): Retrieve existing snapshot
    """
    snapshot = SESSION_SNAPSHOTS.get(session_id)
    if not snapshot:
        return True
    created_at = snapshot.get("created_at", 0)
    now = int(time.time())
    return (now - created_at) >= min_interval_seconds


def delete_session_snapshot(session_id: str) -> bool:
    """Delete the stored snapshot for a session.

    Removes the snapshot reference from storage. The underlying Modal Image
    may still exist in Modal's infrastructure but will no longer be associated
    with this session.

    Args:
        session_id: The session whose snapshot should be deleted.

    Returns:
        True if a snapshot was deleted, False if no snapshot existed.

    Usage:
        Called when a session is explicitly terminated or cleaned up:
        ```python
        delete_session_snapshot(session_id)
        ```
    """
    try:
        del SESSION_SNAPSHOTS[session_id]
        return True
    except KeyError:
        return False


# =============================================================================
# CLI JOB SNAPSHOT MANAGEMENT
# =============================================================================
# Functions for storing and retrieving CLI sandbox filesystem snapshots.
# Snapshots capture the CLI sandbox filesystem state after job execution,
# enabling state restoration when resuming a job after sandbox timeout.
# Unlike session snapshots (keyed by session_id), CLI snapshots are keyed
# by job_id since CLI sandboxes use a job-based execution model.

CLI_JOB_SNAPSHOTS = modal.Dict.from_name(
    _settings.cli_job_snapshot_store_name, create_if_missing=True
)


def store_cli_job_snapshot(
    job_id: str,
    image_id: str,
    sandbox_name: str,
) -> dict[str, Any]:
    """Store a filesystem snapshot reference for a CLI job.

    Records the Modal Image ID from a sandbox.snapshot_filesystem() call,
    allowing the CLI job's filesystem state to be restored when the job
    resumes in a new sandbox.

    Args:
        job_id: The CLI job ID (UUID) to associate with this snapshot.
        image_id: Modal Image object_id from sandbox.snapshot_filesystem().
        sandbox_name: Name of the sandbox that was snapshotted (e.g., "claude-cli-runner").

    Returns:
        Dict with snapshot metadata including job_id, image_id, sandbox_name,
        and created_at timestamp.

    Storage Structure:
        CLI_JOB_SNAPSHOTS[job_id] = {
            "job_id": "550e8400-...",
            "image_id": "im-xxx",
            "sandbox_name": "claude-cli-runner",
            "created_at": 1704067200,
        }

    Usage:
        Called after CLI job completes to capture filesystem state:
        ```python
        # After CLI execution completes
        image = sandbox.snapshot_filesystem()
        store_cli_job_snapshot(
            job_id=job_id,
            image_id=image.object_id,
            sandbox_name="claude-cli-runner",
        )
        ```

    Note:
        - Only stores the latest snapshot per job (overwrites previous)
        - image_id can be used to create a new sandbox from the snapshot:
          `modal.Sandbox.create(image=modal.Image.from_id(image_id), ...)`
        - Snapshots persist in Modal Dict across sandbox restarts

    See Also:
        - get_cli_job_snapshot(): Retrieve snapshot for job restoration
        - should_snapshot_cli_job(): Check if snapshot is needed
    """
    now = int(time.time())
    snapshot_info = {
        "job_id": job_id,
        "image_id": image_id,
        "sandbox_name": sandbox_name,
        "created_at": now,
    }
    CLI_JOB_SNAPSHOTS[job_id] = snapshot_info
    return snapshot_info


def get_cli_job_snapshot(job_id: str) -> dict[str, Any] | None:
    """Retrieve the stored filesystem snapshot for a CLI job.

    Looks up the most recent snapshot for the given job ID, which can be
    used to restore filesystem state when creating a new sandbox.

    Args:
        job_id: The CLI job ID (UUID) to look up.

    Returns:
        Snapshot metadata dict if found, containing:
        - job_id: The job identifier
        - image_id: Modal Image object_id for sandbox.create(image=...)
        - sandbox_name: Original sandbox name
        - created_at: Unix timestamp of snapshot

        None if no snapshot exists for this job.

    Usage:
        Called when resuming a job to check for restorable state:
        ```python
        snapshot = get_cli_job_snapshot(job_id)
        if snapshot:
            # Create sandbox from snapshot
            image = modal.Image.from_id(snapshot["image_id"])
            sandbox = modal.Sandbox.create(image=image, ...)
        ```

    See Also:
        - store_cli_job_snapshot(): Store new snapshot
        - should_snapshot_cli_job(): Check if new snapshot needed
    """
    return CLI_JOB_SNAPSHOTS.get(job_id)


def should_snapshot_cli_job(
    job_id: str,
    min_interval_seconds: int = 60,
) -> bool:
    """Check if a new snapshot should be taken for a CLI job.

    Prevents excessive snapshot creation by enforcing a minimum interval
    between snapshots for the same job. Snapshots are expensive I/O
    operations, so throttling is important for high-frequency executions.

    Args:
        job_id: The job to check.
        min_interval_seconds: Minimum seconds since last snapshot before
            allowing a new one. Default 60 seconds.

    Returns:
        True if no snapshot exists for this job, or if the last snapshot
        was taken more than min_interval_seconds ago.
        False if a recent snapshot exists (within the interval).

    Usage:
        ```python
        if should_snapshot_cli_job(job_id, min_interval_seconds=60):
            image = sandbox.snapshot_filesystem()
            store_cli_job_snapshot(job_id, image.object_id, sandbox_name)
        ```

    Throttling Behavior:
        - First execution for a job: Always snapshots (no existing snapshot)
        - Rapid follow-ups (<60s): Skip snapshot to reduce I/O
        - After interval: Snapshot to capture recent changes

    See Also:
        - store_cli_job_snapshot(): Store new snapshot
        - get_cli_job_snapshot(): Retrieve existing snapshot
    """
    snapshot = CLI_JOB_SNAPSHOTS.get(job_id)
    if not snapshot:
        return True
    created_at = snapshot.get("created_at", 0)
    now = int(time.time())
    return (now - created_at) >= min_interval_seconds


def delete_cli_job_snapshot(job_id: str) -> bool:
    """Delete the stored snapshot for a CLI job.

    Removes the snapshot reference from storage. The underlying Modal Image
    may still exist in Modal's infrastructure but will no longer be associated
    with this job.

    Args:
        job_id: The job whose snapshot should be deleted.

    Returns:
        True if a snapshot was deleted, False if no snapshot existed.

    Usage:
        Called when a job is explicitly cleaned up:
        ```python
        delete_cli_job_snapshot(job_id)
        ```
    """
    try:
        del CLI_JOB_SNAPSHOTS[job_id]
        return True
    except KeyError:
        return False


# =============================================================================
# WARM POOL MANAGEMENT
# =============================================================================
# Functions for managing a pool of pre-warmed Agent SDK sandboxes.
# The pool reduces cold-start latency by keeping sandboxes ready for use.
# Pool entries are stored in a Modal Dict keyed by sandbox object_id.
#
# Pool Entry Structure:
#   WARM_POOL[sandbox_id] = {
#       "sandbox_id": "sb-xxx",           # Modal sandbox object_id
#       "status": "warm" | "claimed",      # Current status
#       "created_at": 1704067200,          # Unix timestamp when added to pool
#       "claimed_at": None | 1704067300,   # Unix timestamp when claimed
#       "claimed_by": None | "session_id", # Session that claimed this sandbox
#       "sandbox_name": "pool-xxx",        # Unique name for this sandbox
#   }
#
# The pool uses optimistic locking via status transitions:
#   - Only "warm" sandboxes can be claimed
#   - Claiming atomically updates status to "claimed"
#   - Multiple callers may race; only one succeeds per sandbox

WARM_POOL = modal.Dict.from_name(_settings.warm_pool_store_name, create_if_missing=True)


def generate_pool_sandbox_name() -> str:
    """Generate a unique name for a pool sandbox.

    Returns:
        A name like "pool-abc12345" that can be used to identify
        pool sandboxes via Sandbox.from_name().
    """
    suffix = uuid.uuid4().hex[:8]
    return f"pool-{suffix}"


def register_warm_sandbox(
    sandbox_id: str,
    sandbox_name: str,
) -> dict[str, Any]:
    """Register a new sandbox in the warm pool.

    Adds a sandbox to the pool with "warm" status, making it available
    for claiming by incoming requests.

    Args:
        sandbox_id: Modal sandbox object_id from sandbox.object_id.
        sandbox_name: Unique name assigned to this sandbox.

    Returns:
        Dict with pool entry metadata.

    Usage:
        ```python
        sb = modal.Sandbox.create(name=pool_name, ...)
        register_warm_sandbox(sb.object_id, pool_name)
        ```

    Note:
        Call this after successfully creating a warm sandbox and
        verifying it's healthy (e.g., after health check passes).
    """
    now = int(time.time())
    entry = {
        "sandbox_id": sandbox_id,
        "sandbox_name": sandbox_name,
        "status": "warm",
        "created_at": now,
        "claimed_at": None,
        "claimed_by": None,
    }
    WARM_POOL[sandbox_id] = entry
    return entry


def claim_warm_sandbox(
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """Attempt to claim a warm sandbox from the pool.

    Iterates through warm sandboxes and attempts to atomically claim one.
    Uses optimistic locking by checking status before updating.

    Args:
        session_id: Optional session ID to associate with the claim.
            Used for debugging and tracking which session owns which sandbox.

    Returns:
        Pool entry dict if a sandbox was successfully claimed, containing:
        - sandbox_id: Modal sandbox object_id for retrieval
        - sandbox_name: Name for Sandbox.from_name()
        - status: "claimed"
        - claimed_at: Claim timestamp
        - claimed_by: The session_id

        None if no warm sandboxes are available.

    Race Condition Handling:
        Multiple callers may attempt to claim the same sandbox.
        This function handles races by:
        1. Fetching entry and checking status == "warm"
        2. Updating status to "claimed"
        3. If another caller claimed it first, trying the next sandbox

        The window between fetch and update is small, so most races
        resolve naturally. For high-contention scenarios, consider
        increasing pool size.

    Usage:
        ```python
        claim = claim_warm_sandbox(session_id="sess_abc")
        if claim:
            sb = modal.Sandbox.from_id(claim["sandbox_id"])
            # Use sandbox...
        ```
    """
    now = int(time.time())

    # Get all pool entries
    # Note: Modal Dict doesn't support atomic iteration with updates,
    # so we iterate over keys and handle races
    try:
        all_entries = list(WARM_POOL.items())
    except Exception:
        return None

    for sandbox_id, entry in all_entries:
        if entry.get("status") != "warm":
            continue

        # Attempt to claim this sandbox
        # Re-fetch to minimize race window
        current = WARM_POOL.get(sandbox_id)
        if not current or current.get("status") != "warm":
            continue

        # Update to claimed status
        claimed_entry = {
            **current,
            "status": "claimed",
            "claimed_at": now,
            "claimed_by": session_id,
        }
        WARM_POOL[sandbox_id] = claimed_entry
        return claimed_entry

    return None


def release_warm_sandbox(sandbox_id: str) -> bool:
    """Return a claimed sandbox to the warm pool.

    Resets a sandbox's status from "claimed" back to "warm",
    making it available for future requests.

    Args:
        sandbox_id: The Modal sandbox object_id to release.

    Returns:
        True if the sandbox was found and released, False otherwise.

    Usage:
        Called when a session ends but the sandbox is still healthy
        and can be reused:
        ```python
        release_warm_sandbox(sandbox_id)
        ```

    Note:
        Only call this if the sandbox is still running and healthy.
        If the sandbox terminated or had errors, use remove_from_pool()
        instead to clean up the pool entry.
    """
    entry = WARM_POOL.get(sandbox_id)
    if not entry:
        return False

    updated = {
        **entry,
        "status": "warm",
        "claimed_at": None,
        "claimed_by": None,
    }
    WARM_POOL[sandbox_id] = updated
    return True


def remove_from_pool(sandbox_id: str) -> bool:
    """Remove a sandbox entry from the pool.

    Deletes the pool entry for a sandbox that is no longer usable
    (terminated, errored, or expired).

    Args:
        sandbox_id: The Modal sandbox object_id to remove.

    Returns:
        True if an entry was removed, False if not found.

    Usage:
        ```python
        # After sandbox terminates or times out
        remove_from_pool(sandbox_id)
        ```
    """
    try:
        del WARM_POOL[sandbox_id]
        return True
    except KeyError:
        return False


def get_warm_pool_entries() -> list[dict[str, Any]]:
    """Get all entries in the warm pool.

    Returns:
        List of pool entry dicts with sandbox metadata.

    Usage:
        ```python
        entries = get_warm_pool_entries()
        warm_count = sum(1 for e in entries if e["status"] == "warm")
        ```
    """
    try:
        return [entry for _, entry in WARM_POOL.items()]
    except Exception:
        return []


def get_warm_pool_status() -> dict[str, Any]:
    """Get current status of the warm pool.

    Returns:
        Dict with pool statistics:
        - total: Total sandboxes in pool (warm + claimed)
        - warm: Available warm sandboxes
        - claimed: Currently claimed sandboxes
        - entries: List of pool entries with metadata

    Usage:
        ```python
        status = get_warm_pool_status()
        if status["warm"] < settings.warm_pool_size:
            # Replenish pool
        ```
    """
    entries = get_warm_pool_entries()
    warm = sum(1 for e in entries if e.get("status") == "warm")
    claimed = sum(1 for e in entries if e.get("status") == "claimed")

    return {
        "total": len(entries),
        "warm": warm,
        "claimed": claimed,
        "entries": entries,
    }


def get_expired_pool_entries(max_age_seconds: int = 3600) -> list[dict[str, Any]]:
    """Get pool entries older than the specified max age.

    Returns sandboxes that should be recycled due to age. This ensures
    pool sandboxes pick up image changes and don't accumulate state.

    Args:
        max_age_seconds: Maximum age before a sandbox is considered expired.
            Default 3600 seconds (1 hour).

    Returns:
        List of expired pool entries that should be removed.

    Usage:
        ```python
        expired = get_expired_pool_entries(max_age_seconds=3600)
        for entry in expired:
            # Terminate sandbox and remove from pool
            sb = modal.Sandbox.from_id(entry["sandbox_id"])
            sb.terminate()
            remove_from_pool(entry["sandbox_id"])
        ```
    """
    now = int(time.time())
    entries = get_warm_pool_entries()
    cutoff = now - max_age_seconds

    return [
        entry
        for entry in entries
        if entry.get("created_at", now) < cutoff and entry.get("status") == "warm"
    ]


def cleanup_stale_pool_entries(sandbox_ids_to_keep: set[str]) -> int:
    """Remove pool entries for sandboxes that no longer exist.

    Used by the pool maintainer to clean up entries for sandboxes
    that have terminated unexpectedly.

    Args:
        sandbox_ids_to_keep: Set of sandbox_ids that are known to still exist.
            Any entry not in this set will be removed.

    Returns:
        Number of stale entries removed.

    Usage:
        ```python
        # Get list of live sandboxes from Modal
        live_ids = {sb.object_id for sb in modal.Sandbox.list(tags={"pool": "agent_sdk"})}
        removed = cleanup_stale_pool_entries(live_ids)
        ```
    """
    entries = get_warm_pool_entries()
    removed = 0
    for entry in entries:
        sandbox_id = entry.get("sandbox_id")
        if sandbox_id and sandbox_id not in sandbox_ids_to_keep:
            if remove_from_pool(sandbox_id):
                removed += 1
    return removed


# =============================================================================
# CLI WARM POOL MANAGEMENT
# =============================================================================
# Functions for managing a pool of pre-warmed CLI sandboxes.
# The pool reduces cold-start latency by keeping CLI sandboxes ready for use.
# Pool entries are stored in a Modal Dict keyed by sandbox object_id.
#
# Pool Entry Structure:
#   CLI_WARM_POOL[sandbox_id] = {
#       "sandbox_id": "sb-xxx",           # Modal sandbox object_id
#       "status": "warm" | "claimed",      # Current status
#       "created_at": 1704067200,          # Unix timestamp when added to pool
#       "claimed_at": None | 1704067300,   # Unix timestamp when claimed
#       "claimed_by": None | "job_id",     # Job that claimed this sandbox
#       "sandbox_name": "cli-pool-xxx",    # Unique name for this sandbox
#   }
#
# The pool uses optimistic locking via status transitions:
#   - Only "warm" sandboxes can be claimed
#   - Claiming atomically updates status to "claimed"
#   - Multiple callers may race; only one succeeds per sandbox

CLI_WARM_POOL = modal.Dict.from_name(_settings.cli_warm_pool_store_name, create_if_missing=True)


def generate_cli_pool_sandbox_name() -> str:
    """Generate a unique name for a CLI pool sandbox.

    Returns:
        A name like "cli-pool-abc12345" that can be used to identify
        CLI pool sandboxes via Sandbox.from_name().
    """
    suffix = uuid.uuid4().hex[:8]
    return f"cli-pool-{suffix}"


def register_cli_warm_sandbox(
    sandbox_id: str,
    sandbox_name: str,
) -> dict[str, Any]:
    """Register a new CLI sandbox in the warm pool.

    Adds a CLI sandbox to the pool with "warm" status, making it available
    for claiming by incoming requests.

    Args:
        sandbox_id: Modal sandbox object_id from sandbox.object_id.
        sandbox_name: Unique name assigned to this sandbox.

    Returns:
        Dict with pool entry metadata.

    Usage:
        ```python
        sb = modal.Sandbox.create(name=pool_name, ...)
        register_cli_warm_sandbox(sb.object_id, pool_name)
        ```

    Note:
        Call this after successfully creating a warm CLI sandbox and
        verifying it's healthy (e.g., after health check passes).
    """
    now = int(time.time())
    entry = {
        "sandbox_id": sandbox_id,
        "sandbox_name": sandbox_name,
        "status": "warm",
        "created_at": now,
        "claimed_at": None,
        "claimed_by": None,
    }
    CLI_WARM_POOL[sandbox_id] = entry
    return entry


def claim_cli_warm_sandbox(
    job_id: str | None = None,
) -> dict[str, Any] | None:
    """Attempt to claim a warm CLI sandbox from the pool.

    Iterates through warm CLI sandboxes and attempts to atomically claim one.
    Uses optimistic locking by checking status before updating.

    Args:
        job_id: Optional job ID to associate with the claim.
            Used for debugging and tracking which job owns which sandbox.

    Returns:
        Pool entry dict if a sandbox was successfully claimed, containing:
        - sandbox_id: Modal sandbox object_id for retrieval
        - sandbox_name: Name for Sandbox.from_name()
        - status: "claimed"
        - claimed_at: Claim timestamp
        - claimed_by: The job_id

        None if no warm CLI sandboxes are available.

    Race Condition Handling:
        Multiple callers may attempt to claim the same sandbox.
        This function handles races by:
        1. Fetching entry and checking status == "warm"
        2. Updating status to "claimed"
        3. If another caller claimed it first, trying the next sandbox

        The window between fetch and update is small, so most races
        resolve naturally. For high-contention scenarios, consider
        increasing pool size.

    Usage:
        ```python
        claim = claim_cli_warm_sandbox(job_id="550e8400-...")
        if claim:
            sb = modal.Sandbox.from_id(claim["sandbox_id"])
            # Use sandbox...
        ```
    """
    now = int(time.time())

    # Get all pool entries
    # Note: Modal Dict doesn't support atomic iteration with updates,
    # so we iterate over keys and handle races
    try:
        all_entries = list(CLI_WARM_POOL.items())
    except Exception:
        return None

    for sandbox_id, entry in all_entries:
        if entry.get("status") != "warm":
            continue

        # Attempt to claim this sandbox
        # Re-fetch to minimize race window
        current = CLI_WARM_POOL.get(sandbox_id)
        if not current or current.get("status") != "warm":
            continue

        # Update to claimed status
        claimed_entry = {
            **current,
            "status": "claimed",
            "claimed_at": now,
            "claimed_by": job_id,
        }
        CLI_WARM_POOL[sandbox_id] = claimed_entry
        return claimed_entry

    return None


def release_cli_warm_sandbox(sandbox_id: str) -> bool:
    """Return a claimed CLI sandbox to the warm pool.

    Resets a CLI sandbox's status from "claimed" back to "warm",
    making it available for future requests.

    Args:
        sandbox_id: The Modal sandbox object_id to release.

    Returns:
        True if the sandbox was found and released, False otherwise.

    Usage:
        Called when a job ends but the sandbox is still healthy
        and can be reused:
        ```python
        release_cli_warm_sandbox(sandbox_id)
        ```

    Note:
        Only call this if the sandbox is still running and healthy.
        If the sandbox terminated or had errors, use remove_from_cli_pool()
        instead to clean up the pool entry.
    """
    entry = CLI_WARM_POOL.get(sandbox_id)
    if not entry:
        return False

    updated = {
        **entry,
        "status": "warm",
        "claimed_at": None,
        "claimed_by": None,
    }
    CLI_WARM_POOL[sandbox_id] = updated
    return True


def remove_from_cli_pool(sandbox_id: str) -> bool:
    """Remove a CLI sandbox entry from the pool.

    Deletes the pool entry for a CLI sandbox that is no longer usable
    (terminated, errored, or expired).

    Args:
        sandbox_id: The Modal sandbox object_id to remove.

    Returns:
        True if an entry was removed, False if not found.

    Usage:
        ```python
        # After sandbox terminates or times out
        remove_from_cli_pool(sandbox_id)
        ```
    """
    try:
        del CLI_WARM_POOL[sandbox_id]
        return True
    except KeyError:
        return False


def get_cli_warm_pool_entries() -> list[dict[str, Any]]:
    """Get all entries in the CLI warm pool.

    Returns:
        List of pool entry dicts with sandbox metadata.

    Usage:
        ```python
        entries = get_cli_warm_pool_entries()
        warm_count = sum(1 for e in entries if e["status"] == "warm")
        ```
    """
    try:
        return [entry for _, entry in CLI_WARM_POOL.items()]
    except Exception:
        return []


def get_cli_warm_pool_status() -> dict[str, Any]:
    """Get current status of the CLI warm pool.

    Returns:
        Dict with pool statistics:
        - total: Total sandboxes in pool (warm + claimed)
        - warm: Available warm sandboxes
        - claimed: Currently claimed sandboxes
        - entries: List of pool entries with metadata

    Usage:
        ```python
        status = get_cli_warm_pool_status()
        if status["warm"] < settings.cli_warm_pool_size:
            # Replenish pool
        ```
    """
    entries = get_cli_warm_pool_entries()
    warm = sum(1 for e in entries if e.get("status") == "warm")
    claimed = sum(1 for e in entries if e.get("status") == "claimed")

    return {
        "total": len(entries),
        "warm": warm,
        "claimed": claimed,
        "entries": entries,
    }


def get_expired_cli_pool_entries(max_age_seconds: int = 3600) -> list[dict[str, Any]]:
    """Get CLI pool entries older than the specified max age.

    Returns CLI sandboxes that should be recycled due to age. This ensures
    pool sandboxes pick up image changes and don't accumulate state.

    Args:
        max_age_seconds: Maximum age before a sandbox is considered expired.
            Default 3600 seconds (1 hour).

    Returns:
        List of expired pool entries that should be removed.

    Usage:
        ```python
        expired = get_expired_cli_pool_entries(max_age_seconds=3600)
        for entry in expired:
            # Terminate sandbox and remove from pool
            sb = modal.Sandbox.from_id(entry["sandbox_id"])
            sb.terminate()
            remove_from_cli_pool(entry["sandbox_id"])
        ```
    """
    now = int(time.time())
    entries = get_cli_warm_pool_entries()
    cutoff = now - max_age_seconds

    return [
        entry
        for entry in entries
        if entry.get("created_at", now) < cutoff and entry.get("status") == "warm"
    ]


def cleanup_stale_cli_pool_entries(sandbox_ids_to_keep: set[str]) -> int:
    """Remove CLI pool entries for sandboxes that no longer exist.

    Used by the CLI pool maintainer to clean up entries for sandboxes
    that have terminated unexpectedly.

    Args:
        sandbox_ids_to_keep: Set of sandbox_ids that are known to still exist.
            Any entry not in this set will be removed.

    Returns:
        Number of stale entries removed.

    Usage:
        ```python
        # Get list of live sandboxes from Modal
        live_ids = {sb.object_id for sb in modal.Sandbox.list(tags={"pool": "cli"})}
        removed = cleanup_stale_cli_pool_entries(live_ids)
        ```
    """
    entries = get_cli_warm_pool_entries()
    removed = 0
    for entry in entries:
        sandbox_id = entry.get("sandbox_id")
        if sandbox_id and sandbox_id not in sandbox_ids_to_keep:
            if remove_from_cli_pool(sandbox_id):
                removed += 1
    return removed
