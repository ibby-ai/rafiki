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
from agent_sandbox.schemas.jobs import (
    ArtifactEntry,
    ArtifactManifest,
    JobStatusResponse,
    WebhookConfig,
)

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

# Distributed dictionary for tracking parent-child session relationships.
# Keys are parent_job_id values, values are lists of child session entries:
#   [{"child_job_id": str, "task": str, "sandbox_type": str, "status": str, ...}, ...]
# Used by session tools to look up children spawned by a parent agent.
CHILD_SESSION_REGISTRY = modal.Dict.from_name(
    _settings.child_session_registry_name, create_if_missing=True
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
        sandbox_type: Type of sandbox ("agent_sdk")
        job_id: Optional job ID for correlation
        user_id: Optional user ID for unique user tracking
    """
    now = int(time.time())
    hourly_key, daily_key = _get_time_bucket_keys()

    for key in [hourly_key, daily_key]:
        bucket = STATS_STORE.get(key) or {
            "timestamp": now,
            "agent_sdk": {"started": 0, "completed": 0, "failed": 0, "canceled": 0},
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
        sandbox_type: Type of sandbox ("agent_sdk")
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
            for sandbox_type in ["agent_sdk"]:
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
                            bucket.get(st, {}).get("started", 0) for st in ["agent_sdk"]
                        ),
                        "completed": sum(
                            bucket.get(st, {}).get("completed", 0) for st in ["agent_sdk"]
                        ),
                        "failed": sum(bucket.get(st, {}).get("failed", 0) for st in ["agent_sdk"]),
                    }
                )

        current += timedelta(hours=1)

    # Calculate totals
    for sandbox_type in ["agent_sdk"]:
        result["totals"]["total_sessions"] += result[sandbox_type]["total_sessions"]
        result["totals"]["completed_sessions"] += result[sandbox_type]["completed_sessions"]
        result["totals"]["failed_sessions"] += result[sandbox_type]["failed_sessions"]
        result["totals"]["canceled_sessions"] += result[sandbox_type]["canceled_sessions"]

    # Calculate averages and rates
    for sandbox_type in ["agent_sdk", "totals"]:
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
# Image Version Tracking
# =============================================================================
# These functions track deployed image versions to enable warm pool invalidation
# when a new image is deployed. On each deploy, the image version is recorded
# and existing warm pool sandboxes running old images are terminated.
#
# Version Entry Structure:
# {
#     "version_id": str,           # Short hash identifying this deploy
#     "deployed_at": float,        # Unix timestamp when deployed
# }
# =============================================================================

IMAGE_VERSION = modal.Dict.from_name(_settings.image_version_store_name, create_if_missing=True)


def get_current_image_version() -> dict[str, Any] | None:
    """Get the current deployed image version info.

    Returns:
        Dict with version_id and deployed_at, or None if not set.

    Usage:
        ```python
        version = get_current_image_version()
        if version:
            print(f"Running image {version['version_id']}")
        ```
    """
    try:
        return IMAGE_VERSION.get("current")
    except KeyError:
        return None


def set_image_version(version_id: str, deployed_at: float) -> None:
    """Record new image version on deploy.

    Called by the deploy invalidation function to record the current
    image version. This allows maintenance functions to detect and
    invalidate sandboxes running older images.

    Args:
        version_id: Short hash identifying this deploy.
        deployed_at: Unix timestamp when deployed.

    Usage:
        ```python
        set_image_version("abc123def456", time.time())
        ```
    """
    IMAGE_VERSION["current"] = {
        "version_id": version_id,
        "deployed_at": deployed_at,
    }
    # Keep history for debugging
    IMAGE_VERSION[f"history:{version_id}"] = {
        "version_id": version_id,
        "deployed_at": deployed_at,
    }


def get_image_deployed_at() -> float | None:
    """Get timestamp when current image was deployed.

    Returns:
        Unix timestamp of current deploy, or None if not tracked.

    Usage:
        ```python
        deployed_at = get_image_deployed_at()
        if deployed_at and sandbox_created_at < deployed_at:
            # Sandbox is running old image
        ```
    """
    version = get_current_image_version()
    return version["deployed_at"] if version else None


# =============================================================================
# Pre-warm API Tracking
# =============================================================================
# These functions manage speculative sandbox pre-warming. When a client calls
# POST /warm (e.g., when user starts typing), we begin sandbox preparation
# and track the request with a warm_id. When the actual query arrives with
# the same warm_id, we can return the pre-warmed sandbox immediately.
#
# Pre-warm Entry Structure:
# {
#     "warm_id": str,              # Unique correlation ID
#     "sandbox_type": str,         # "agent_sdk"
#     "sandbox_id": str | None,    # Modal sandbox object_id (once prepared)
#     "sandbox_url": str | None,   # Tunnel URL (once prepared)
#     "status": str,               # "warming" | "ready" | "claimed" | "expired"
#     "created_at": int,           # Unix timestamp
#     "expires_at": int,           # Unix timestamp (created_at + timeout)
#     "claimed_by": str | None,    # session_id/job_id (when claimed)
#     "session_id": str | None,    # Optional session_id for session restoration
#     "job_id": str | None,        # Optional job_id for workspace setup
# }
# =============================================================================

PREWARM_STORE = modal.Dict.from_name(_settings.prewarm_store_name, create_if_missing=True)


def generate_warm_id() -> str:
    """Generate a unique warm_id for pre-warm request correlation.

    Returns:
        A UUID string for tracking pre-warm requests.

    Usage:
        ```python
        warm_id = generate_warm_id()
        # Return to client, they pass it back with their query
        ```
    """
    return str(uuid.uuid4())


def register_prewarm(
    warm_id: str,
    sandbox_type: str,
    session_id: str | None = None,
    job_id: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Register a new pre-warm request.

    Called when POST /warm is received. Creates a tracking entry for the
    pre-warm request that will be updated as the sandbox warms up.

    Args:
        warm_id: Unique ID for correlation with future queries.
        sandbox_type: "agent_sdk".
        session_id: Optional session_id for session restoration.
        job_id: Optional job_id for workspace setup.
        timeout_seconds: Custom timeout (defaults to settings.prewarm_timeout_seconds).

    Returns:
        The created pre-warm entry dict.

    Usage:
        ```python
        warm_id = generate_warm_id()
        entry = register_prewarm(warm_id, "agent_sdk", session_id="sess_123")
        # Start sandbox warming in background
        # Update entry when ready via update_prewarm_ready()
        ```
    """
    now = int(time.time())
    timeout = timeout_seconds or _settings.prewarm_timeout_seconds

    entry = {
        "warm_id": warm_id,
        "sandbox_type": sandbox_type,
        "sandbox_id": None,
        "sandbox_url": None,
        "status": "warming",
        "created_at": now,
        "expires_at": now + timeout,
        "claimed_by": None,
        "session_id": session_id,
        "job_id": job_id,
    }
    PREWARM_STORE[warm_id] = entry
    return entry


def update_prewarm_ready(
    warm_id: str,
    sandbox_id: str,
    sandbox_url: str,
) -> dict[str, Any] | None:
    """Update a pre-warm entry to ready status with sandbox details.

    Called when sandbox preparation completes successfully.

    Args:
        warm_id: The pre-warm request ID.
        sandbox_id: Modal sandbox object_id.
        sandbox_url: Tunnel URL for the sandbox service.

    Returns:
        Updated entry dict, or None if not found or expired.

    Usage:
        ```python
        # After sandbox is ready
        update_prewarm_ready(warm_id, sb.object_id, tunnel_url)
        ```
    """
    entry = PREWARM_STORE.get(warm_id)
    if not entry:
        return None

    now = int(time.time())
    if now > entry.get("expires_at", 0):
        # Expired - clean up
        try:
            del PREWARM_STORE[warm_id]
        except KeyError:
            pass
        return None

    updated = {
        **entry,
        "status": "ready",
        "sandbox_id": sandbox_id,
        "sandbox_url": sandbox_url,
    }
    PREWARM_STORE[warm_id] = updated
    return updated


def mark_prewarm_failed(warm_id: str, reason: str) -> dict[str, Any] | None:
    """Mark a pre-warm entry as failed.

    Called when pre-warming fails due to an error or when the entry expires
    before the sandbox is ready. This provides visibility into failed pre-warms
    rather than leaving them stuck in "warming" state.

    Args:
        warm_id: The pre-warm request ID.
        reason: Description of why the pre-warm failed.

    Returns:
        Updated entry dict if found, None otherwise.

    Usage:
        ```python
        # When pre-warm fails
        mark_prewarm_failed(warm_id, "Entry expired before sandbox ready")

        # When sandbox creation errors
        mark_prewarm_failed(warm_id, str(exc))
        ```
    """
    entry = PREWARM_STORE.get(warm_id)
    if not entry:
        return None

    updated = {
        **entry,
        "status": "failed",
        "failed_at": int(time.time()),
        "failure_reason": reason,
    }
    PREWARM_STORE[warm_id] = updated
    return updated


def get_prewarm(warm_id: str) -> dict[str, Any] | None:
    """Get a pre-warm entry by warm_id.

    Args:
        warm_id: The pre-warm request ID.

    Returns:
        Pre-warm entry dict if found and not expired, None otherwise.

    Usage:
        ```python
        entry = get_prewarm(warm_id)
        if entry and entry["status"] == "ready":
            # Use pre-warmed sandbox
        ```
    """
    entry = PREWARM_STORE.get(warm_id)
    if not entry:
        return None

    now = int(time.time())
    if now > entry.get("expires_at", 0):
        # Expired - clean up
        try:
            del PREWARM_STORE[warm_id]
        except KeyError:
            pass
        return None

    return entry


def claim_prewarm(
    warm_id: str,
    claimed_by: str,
) -> dict[str, Any] | None:
    """Claim a pre-warmed sandbox for use.

    Called when a query arrives with a warm_id. If the pre-warm is ready,
    marks it as claimed and returns the sandbox details.

    Args:
        warm_id: The pre-warm request ID from the query.
        claimed_by: session_id or job_id claiming the pre-warm.

    Returns:
        Dict with claim result. Always includes "claimed" bool key.
        On success (claimed=True):
        - sandbox_id, sandbox_url, status="claimed", claimed_by, claimed_at
        On failure (claimed=False):
        - reason: "not_found", "still_warming", "failed", or "invalid_status:{status}"
        - entry: Original prewarm entry (for debugging)
        - failure_reason: If status was "failed"

    Usage:
        ```python
        # In query handler
        if warm_id:
            result = claim_prewarm(warm_id, session_id)
            if result and result.get("claimed"):
                # Use result["sandbox_id"] and result["sandbox_url"]
            elif result:
                # Log reason: result.get("reason")
        ```
    """
    entry = get_prewarm(warm_id)
    if not entry:
        return {"claimed": False, "reason": "not_found"}

    status = entry.get("status")

    # Can only claim ready pre-warms
    if status == "warming":
        return {"claimed": False, "reason": "still_warming", "entry": entry}

    if status == "failed":
        return {
            "claimed": False,
            "reason": "failed",
            "failure_reason": entry.get("failure_reason"),
            "entry": entry,
        }

    if status != "ready":
        return {"claimed": False, "reason": f"invalid_status:{status}", "entry": entry}

    now = int(time.time())
    updated = {
        **entry,
        "status": "claimed",
        "claimed_by": claimed_by,
        "claimed_at": now,
        "claimed": True,
    }
    PREWARM_STORE[warm_id] = updated
    return updated


def expire_prewarm(warm_id: str) -> bool:
    """Mark a pre-warm entry as expired and remove it.

    Called when a pre-warm times out without being claimed.

    Args:
        warm_id: The pre-warm request ID to expire.

    Returns:
        True if entry was found and removed, False otherwise.

    Usage:
        ```python
        # In cleanup task
        expire_prewarm(warm_id)
        ```
    """
    try:
        del PREWARM_STORE[warm_id]
        return True
    except KeyError:
        return False


def get_prewarm_status() -> dict[str, Any]:
    """Get current status of the pre-warm store.

    Returns:
        Dict with pre-warm statistics:
        - total: Total pre-warm entries
        - warming: Pre-warms still in progress
        - ready: Pre-warms ready for use
        - claimed: Pre-warms that have been claimed
        - expired: Pre-warms past their expiry time

    Usage:
        ```python
        status = get_prewarm_status()
        print(f"Ready pre-warms: {status['ready']}")
        ```
    """
    now = int(time.time())
    entries = []
    try:
        entries = [entry for _, entry in PREWARM_STORE.items()]
    except Exception:
        pass

    warming = 0
    ready = 0
    claimed = 0
    expired = 0

    for entry in entries:
        if now > entry.get("expires_at", 0):
            expired += 1
        elif entry.get("status") == "warming":
            warming += 1
        elif entry.get("status") == "ready":
            ready += 1
        elif entry.get("status") == "claimed":
            claimed += 1

    return {
        "total": len(entries),
        "warming": warming,
        "ready": ready,
        "claimed": claimed,
        "expired": expired,
        "entries": entries,
    }


def cleanup_expired_prewarms() -> int:
    """Remove expired pre-warm entries from the store.

    Called periodically to clean up pre-warms that weren't claimed
    within their timeout window.

    Returns:
        Number of expired entries removed.

    Usage:
        ```python
        # In scheduled maintenance
        removed = cleanup_expired_prewarms()
        ```
    """
    now = int(time.time())
    removed = 0
    try:
        for warm_id, entry in list(PREWARM_STORE.items()):
            if now > entry.get("expires_at", 0):
                try:
                    del PREWARM_STORE[warm_id]
                    removed += 1
                except KeyError:
                    pass
    except Exception:
        pass
    return removed


# =============================================================================
# SESSION CANCELLATION MANAGEMENT
# =============================================================================
# Functions for tracking and checking session cancellation requests.
# When a user calls POST /session/{id}/stop, a cancellation flag is set.
# The agent's can_use_tool handler checks this flag before allowing tool calls,
# enabling graceful termination of runaway agents.
#
# Cancellation Entry Structure:
#   SESSION_CANCELLATIONS[session_id] = {
#       "session_id": str,       # Session being cancelled
#       "status": str,           # "requested" | "acknowledged"
#       "requested_at": int,     # Unix timestamp of cancellation request
#       "expires_at": int,       # Unix timestamp when flag expires
#       "requested_by": str,     # Optional user/client identifier
#       "reason": str | None,    # Optional cancellation reason
#   }
#
# The cancellation flag is checked in can_use_tool handler:
#   - If cancelled and not expired, tool calls are rejected
#   - Agent receives PermissionResultDeny with cancellation message
#   - Agent SDK handles the denial and should terminate gracefully

SESSION_CANCELLATIONS = modal.Dict.from_name(
    _settings.session_cancellation_store_name, create_if_missing=True
)


def cancel_session(
    session_id: str,
    requested_by: str | None = None,
    reason: str | None = None,
    expiry_seconds: int | None = None,
) -> dict[str, Any]:
    """Request cancellation of an active session.

    Sets a cancellation flag that will be checked by the agent's tool
    permission handler. Once set, further tool calls will be rejected,
    causing the agent to terminate gracefully.

    Args:
        session_id: The Claude Agent SDK session ID to cancel.
        requested_by: Optional identifier of who requested cancellation.
        reason: Optional human-readable reason for cancellation.
        expiry_seconds: Custom expiry time (defaults to settings.cancellation_expiry_seconds).

    Returns:
        Dict with cancellation entry metadata.

    Usage:
        ```python
        # In stop endpoint handler
        result = cancel_session(session_id, requested_by="user_123", reason="User stopped")
        # result["status"] == "requested"
        ```

    Notes:
        - Cancellation is "soft" - it doesn't forcibly terminate the sandbox
        - The agent will finish its current tool call, then be denied further tools
        - Cancellation flags expire after expiry_seconds to prevent stale flags
        - Setting a new cancellation overwrites any existing flag for the session
    """
    now = int(time.time())
    expiry = expiry_seconds or _settings.cancellation_expiry_seconds

    entry = {
        "session_id": session_id,
        "status": "requested",
        "requested_at": now,
        "expires_at": now + expiry,
        "requested_by": requested_by,
        "reason": reason,
    }
    SESSION_CANCELLATIONS[session_id] = entry
    return entry


def is_session_cancelled(session_id: str) -> bool:
    """Check if a session has an active cancellation flag.

    Called by the agent's can_use_tool handler before each tool call.
    Returns True if the session should be cancelled (flag exists and not expired).

    Args:
        session_id: The session ID to check.

    Returns:
        True if session is cancelled and flag hasn't expired, False otherwise.

    Usage:
        ```python
        async def can_use_tool(tool_name, tool_input, ctx):
            if is_session_cancelled(ctx.session_id):
                return PermissionResultDeny(message="Session cancelled by user")
            # ... rest of permission logic
        ```

    Performance:
        This function is called frequently (once per tool call). The Modal Dict
        lookup is fast but adds some latency. For very high-throughput scenarios,
        consider local caching with a short TTL.
    """
    entry = SESSION_CANCELLATIONS.get(session_id)
    if not entry:
        return False

    now = int(time.time())
    if now > entry.get("expires_at", 0):
        # Expired - clean up lazily
        try:
            del SESSION_CANCELLATIONS[session_id]
        except KeyError:
            pass
        return False

    return entry.get("status") in ("requested", "acknowledged")


def get_session_cancellation(session_id: str) -> dict[str, Any] | None:
    """Get the cancellation entry for a session if it exists.

    Args:
        session_id: The session ID to look up.

    Returns:
        Cancellation entry dict if found and not expired, None otherwise.

    Usage:
        ```python
        cancellation = get_session_cancellation(session_id)
        if cancellation:
            print(f"Cancelled by {cancellation['requested_by']}: {cancellation['reason']}")
        ```
    """
    entry = SESSION_CANCELLATIONS.get(session_id)
    if not entry:
        return None

    now = int(time.time())
    if now > entry.get("expires_at", 0):
        # Expired - clean up lazily
        try:
            del SESSION_CANCELLATIONS[session_id]
        except KeyError:
            pass
        return None

    return entry


def acknowledge_session_cancellation(session_id: str) -> dict[str, Any] | None:
    """Mark a session cancellation as acknowledged.

    Called when the agent actually receives and processes the cancellation.
    This is useful for tracking and debugging to distinguish between
    requested and acknowledged cancellations.

    Args:
        session_id: The session ID to acknowledge.

    Returns:
        Updated cancellation entry if found, None otherwise.

    Usage:
        ```python
        async def can_use_tool(tool_name, tool_input, ctx):
            if is_session_cancelled(ctx.session_id):
                acknowledge_session_cancellation(ctx.session_id)
                return PermissionResultDeny(message="Session cancelled")
        ```
    """
    entry = SESSION_CANCELLATIONS.get(session_id)
    if not entry:
        return None

    now = int(time.time())
    if now > entry.get("expires_at", 0):
        return None

    updated = {
        **entry,
        "status": "acknowledged",
        "acknowledged_at": now,
    }
    SESSION_CANCELLATIONS[session_id] = updated
    return updated


def clear_session_cancellation(session_id: str) -> bool:
    """Clear the cancellation flag for a session.

    Called when a session completes or when manually clearing a cancellation.
    This is useful to allow a session to continue if the user changes their mind.

    Args:
        session_id: The session ID to clear.

    Returns:
        True if a cancellation was cleared, False if none existed.

    Usage:
        ```python
        # User wants to resume the session
        clear_session_cancellation(session_id)
        ```
    """
    try:
        del SESSION_CANCELLATIONS[session_id]
        return True
    except KeyError:
        return False


def cleanup_expired_cancellations() -> int:
    """Remove expired cancellation entries from the store.

    Called periodically to clean up stale cancellation flags that
    have passed their expiry time.

    Returns:
        Number of expired entries removed.

    Usage:
        ```python
        # In scheduled maintenance
        removed = cleanup_expired_cancellations()
        ```
    """
    now = int(time.time())
    removed = 0
    try:
        for session_id, entry in list(SESSION_CANCELLATIONS.items()):
            if now > entry.get("expires_at", 0):
                try:
                    del SESSION_CANCELLATIONS[session_id]
                    removed += 1
                except KeyError:
                    pass
    except Exception:
        pass
    return removed


def get_cancellation_status() -> dict[str, Any]:
    """Get current status of session cancellations.

    Returns:
        Dict with cancellation statistics:
        - total: Total active cancellation entries
        - requested: Cancellations waiting to be acknowledged
        - acknowledged: Cancellations that have been processed
        - expired: Entries past their expiry (will be cleaned up)

    Usage:
        ```python
        status = get_cancellation_status()
        print(f"Active cancellations: {status['total']}")
        ```
    """
    now = int(time.time())
    entries = []
    try:
        entries = [entry for _, entry in SESSION_CANCELLATIONS.items()]
    except Exception:
        pass

    requested = 0
    acknowledged = 0
    expired = 0

    for entry in entries:
        if now > entry.get("expires_at", 0):
            expired += 1
        elif entry.get("status") == "requested":
            requested += 1
        elif entry.get("status") == "acknowledged":
            acknowledged += 1

    return {
        "total": len(entries),
        "requested": requested,
        "acknowledged": acknowledged,
        "expired": expired,
    }


# =============================================================================
# Prompt Queue API
# =============================================================================
# Functions for managing per-session follow-up prompt queues.
#
# When a session is actively executing a query, follow-up prompts can be
# queued instead of being rejected. These queued prompts are processed
# sequentially after the current query completes.
#
# Queue Entry Structure:
#   PROMPT_QUEUE[session_id] = {
#       "session_id": str,       # Session this queue belongs to
#       "prompts": [             # List of queued prompts (FIFO order)
#           {
#               "prompt_id": str,      # Unique ID for this prompt
#               "question": str,       # The prompt text
#               "user_id": str | None, # Who submitted the prompt
#               "queued_at": int,      # Unix timestamp when queued
#               "expires_at": int,     # Unix timestamp when prompt expires
#               "metadata": dict,      # Optional metadata
#           }
#       ],
#       "updated_at": int,       # Unix timestamp of last update
#   }
#
# Queue Lifecycle:
#   1. Client submits prompt while session is executing
#   2. Server queues the prompt (if under limit)
#   3. Agent completes current query
#   4. Server dequeues next prompt and submits to agent
#   5. Repeat until queue is empty
#
# Usage:
#   - queue_prompt(): Add a prompt to session queue
#   - dequeue_prompt(): Get and remove next prompt from queue
#   - get_session_queue(): View all pending prompts
#   - clear_session_queue(): Clear all prompts for a session
#   - get_prompt_queue_status(): Overall queue statistics

PROMPT_QUEUE = modal.Dict.from_name(_settings.prompt_queue_store_name, create_if_missing=True)

# Session execution state tracking.
# Stores which sessions are currently executing queries.
#   SESSION_EXECUTION_STATE[session_id] = {
#       "session_id": str,       # Session ID
#       "status": str,           # "executing" | "idle"
#       "started_at": int,       # When execution started
#       "updated_at": int,       # Last status update
#   }
SESSION_EXECUTION_STATE = modal.Dict.from_name(
    "agent-session-execution-state", create_if_missing=True
)


def mark_session_executing(session_id: str) -> dict[str, Any]:
    """Mark a session as currently executing a query.

    Called when a query starts to prevent queued prompts from being
    processed until the current query completes.

    Args:
        session_id: The session ID that is starting execution.

    Returns:
        The execution state entry.
    """
    now = int(time.time())
    entry = {
        "session_id": session_id,
        "status": "executing",
        "started_at": now,
        "updated_at": now,
    }
    SESSION_EXECUTION_STATE[session_id] = entry
    return entry


def mark_session_idle(session_id: str) -> dict[str, Any] | None:
    """Mark a session as idle (not currently executing).

    Called when a query completes to allow queued prompts to be processed.

    Args:
        session_id: The session ID that finished execution.

    Returns:
        The updated execution state entry, or None if not found.
    """
    now = int(time.time())
    entry = SESSION_EXECUTION_STATE.get(session_id)
    if not entry:
        entry = {"session_id": session_id}

    entry.update(
        {
            "status": "idle",
            "updated_at": now,
        }
    )
    SESSION_EXECUTION_STATE[session_id] = entry
    return entry


def is_session_executing(session_id: str) -> bool:
    """Check if a session is currently executing a query.

    Args:
        session_id: The session ID to check.

    Returns:
        True if session is executing, False otherwise.
    """
    entry = SESSION_EXECUTION_STATE.get(session_id)
    if not entry:
        return False
    return entry.get("status") == "executing"


def queue_prompt(
    session_id: str,
    question: str,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add a follow-up prompt to the session's queue.

    Called when a prompt arrives while the session is executing another query.
    The prompt is queued and will be processed after the current query completes.

    Args:
        session_id: The session ID to queue the prompt for.
        question: The prompt text to queue.
        user_id: Optional identifier of who submitted the prompt.
        metadata: Optional metadata to associate with the prompt.

    Returns:
        Dict with queue status:
        - queued: True if prompt was queued successfully
        - prompt_id: Unique ID for this queued prompt
        - position: Position in queue (1-indexed)
        - error: Error message if queueing failed

    Raises:
        None - errors are returned in the response dict.

    Usage:
        ```python
        if is_session_executing(session_id):
            result = queue_prompt(session_id, "follow-up question")
            if result["queued"]:
                print(f"Prompt queued at position {result['position']}")
        ```
    """
    now = int(time.time())
    expiry = now + _settings.prompt_queue_entry_expiry_seconds

    # Get or create queue entry
    queue_entry = PROMPT_QUEUE.get(session_id) or {
        "session_id": session_id,
        "prompts": [],
        "updated_at": now,
    }

    # Check queue limit
    if len(queue_entry["prompts"]) >= _settings.max_queued_prompts_per_session:
        return {
            "queued": False,
            "error": f"Queue limit reached ({_settings.max_queued_prompts_per_session})",
            "queue_size": len(queue_entry["prompts"]),
        }

    # Create prompt entry
    prompt_id = str(uuid.uuid4())
    prompt_entry = {
        "prompt_id": prompt_id,
        "question": question,
        "user_id": user_id,
        "queued_at": now,
        "expires_at": expiry,
        "metadata": metadata or {},
    }

    # Add to queue
    queue_entry["prompts"].append(prompt_entry)
    queue_entry["updated_at"] = now
    PROMPT_QUEUE[session_id] = queue_entry

    return {
        "queued": True,
        "prompt_id": prompt_id,
        "position": len(queue_entry["prompts"]),
        "expires_at": expiry,
        "queue_size": len(queue_entry["prompts"]),
    }


def dequeue_prompt(session_id: str) -> dict[str, Any] | None:
    """Get and remove the next prompt from the session's queue.

    Called after a query completes to get the next prompt to process.
    Expired prompts are skipped and removed.

    Args:
        session_id: The session ID to dequeue from.

    Returns:
        The next prompt entry if available, None if queue is empty or all expired.
        Returns dict with:
        - prompt_id: Unique ID of the prompt
        - question: The prompt text
        - user_id: Who submitted (if provided)
        - queued_at: When it was queued
        - metadata: Associated metadata

    Usage:
        ```python
        while True:
            prompt = dequeue_prompt(session_id)
            if not prompt:
                break
            # Process the prompt
            await process_query(prompt["question"])
        ```
    """
    now = int(time.time())
    queue_entry = PROMPT_QUEUE.get(session_id)
    if not queue_entry or not queue_entry.get("prompts"):
        return None

    prompts = queue_entry["prompts"]

    # Find first non-expired prompt
    while prompts:
        prompt = prompts.pop(0)  # FIFO order
        queue_entry["updated_at"] = now
        PROMPT_QUEUE[session_id] = queue_entry

        # Check if expired
        if now > prompt.get("expires_at", 0):
            continue  # Skip expired prompts

        return prompt

    # All prompts were expired
    return None


def peek_next_prompt(session_id: str) -> dict[str, Any] | None:
    """View the next prompt in queue without removing it.

    Args:
        session_id: The session ID to peek at.

    Returns:
        The next prompt entry if available, None if queue is empty.
    """
    now = int(time.time())
    queue_entry = PROMPT_QUEUE.get(session_id)
    if not queue_entry or not queue_entry.get("prompts"):
        return None

    # Find first non-expired prompt
    for prompt in queue_entry["prompts"]:
        if now <= prompt.get("expires_at", 0):
            return prompt

    return None


def get_session_queue(session_id: str) -> list[dict[str, Any]]:
    """Get all pending prompts in a session's queue.

    Returns only non-expired prompts. Expired prompts are filtered out
    but not removed (they'll be cleaned up on dequeue).

    Args:
        session_id: The session ID to get queue for.

    Returns:
        List of prompt entries, each containing:
        - prompt_id: Unique ID
        - question: The prompt text
        - user_id: Who submitted
        - queued_at: Unix timestamp
        - expires_at: Unix timestamp
        - metadata: Associated metadata

    Usage:
        ```python
        queue = get_session_queue(session_id)
        print(f"Pending prompts: {len(queue)}")
        for i, prompt in enumerate(queue, 1):
            print(f"{i}. {prompt['question'][:50]}...")
        ```
    """
    now = int(time.time())
    queue_entry = PROMPT_QUEUE.get(session_id)
    if not queue_entry or not queue_entry.get("prompts"):
        return []

    # Filter out expired prompts
    return [p for p in queue_entry["prompts"] if now <= p.get("expires_at", 0)]


def get_queue_size(session_id: str) -> int:
    """Get the number of non-expired prompts in a session's queue.

    Args:
        session_id: The session ID to check.

    Returns:
        Number of pending (non-expired) prompts.
    """
    return len(get_session_queue(session_id))


def clear_session_queue(session_id: str) -> int:
    """Clear all prompts from a session's queue.

    Args:
        session_id: The session ID to clear.

    Returns:
        Number of prompts that were cleared.

    Usage:
        ```python
        cleared = clear_session_queue(session_id)
        print(f"Cleared {cleared} queued prompts")
        ```
    """
    queue_entry = PROMPT_QUEUE.get(session_id)
    if not queue_entry or not queue_entry.get("prompts"):
        return 0

    count = len(queue_entry["prompts"])
    queue_entry["prompts"] = []
    queue_entry["updated_at"] = int(time.time())
    PROMPT_QUEUE[session_id] = queue_entry
    return count


def remove_queued_prompt(session_id: str, prompt_id: str) -> bool:
    """Remove a specific prompt from the queue by its ID.

    Args:
        session_id: The session ID.
        prompt_id: The prompt ID to remove.

    Returns:
        True if prompt was found and removed, False otherwise.
    """
    queue_entry = PROMPT_QUEUE.get(session_id)
    if not queue_entry or not queue_entry.get("prompts"):
        return False

    original_len = len(queue_entry["prompts"])
    queue_entry["prompts"] = [p for p in queue_entry["prompts"] if p.get("prompt_id") != prompt_id]

    if len(queue_entry["prompts"]) < original_len:
        queue_entry["updated_at"] = int(time.time())
        PROMPT_QUEUE[session_id] = queue_entry
        return True

    return False


def cleanup_expired_queue_entries() -> dict[str, int]:
    """Remove expired prompts from all session queues.

    Called periodically to clean up stale queue entries.

    Returns:
        Dict with cleanup statistics:
        - sessions_checked: Number of sessions processed
        - prompts_removed: Total expired prompts removed
        - empty_queues_cleared: Queues that became empty and were deleted

    Usage:
        ```python
        # In scheduled maintenance
        stats = cleanup_expired_queue_entries()
        print(f"Removed {stats['prompts_removed']} expired prompts")
        ```
    """
    now = int(time.time())
    sessions_checked = 0
    prompts_removed = 0
    empty_queues_cleared = 0

    try:
        for session_id, queue_entry in list(PROMPT_QUEUE.items()):
            sessions_checked += 1
            if not queue_entry.get("prompts"):
                continue

            original_len = len(queue_entry["prompts"])
            queue_entry["prompts"] = [
                p for p in queue_entry["prompts"] if now <= p.get("expires_at", 0)
            ]

            removed = original_len - len(queue_entry["prompts"])
            if removed > 0:
                prompts_removed += removed
                if not queue_entry["prompts"]:
                    # Queue is now empty, delete it
                    try:
                        del PROMPT_QUEUE[session_id]
                        empty_queues_cleared += 1
                    except KeyError:
                        pass
                else:
                    queue_entry["updated_at"] = now
                    PROMPT_QUEUE[session_id] = queue_entry
    except Exception:
        pass

    return {
        "sessions_checked": sessions_checked,
        "prompts_removed": prompts_removed,
        "empty_queues_cleared": empty_queues_cleared,
    }


def get_prompt_queue_status() -> dict[str, Any]:
    """Get current status of prompt queues across all sessions.

    Returns:
        Dict with queue statistics:
        - sessions_with_queues: Number of sessions that have queues
        - total_queued_prompts: Total prompts across all queues
        - expired_prompts: Prompts past their expiry
        - max_queue_size: Configured limit per session
        - entry_expiry_seconds: Configured expiry time

    Usage:
        ```python
        status = get_prompt_queue_status()
        print(f"Total queued prompts: {status['total_queued_prompts']}")
        ```
    """
    now = int(time.time())
    sessions_with_queues = 0
    total_queued = 0
    expired = 0

    try:
        for _, queue_entry in PROMPT_QUEUE.items():
            if queue_entry.get("prompts"):
                sessions_with_queues += 1
                for prompt in queue_entry["prompts"]:
                    total_queued += 1
                    if now > prompt.get("expires_at", 0):
                        expired += 1
    except Exception:
        pass

    return {
        "sessions_with_queues": sessions_with_queues,
        "total_queued_prompts": total_queued,
        "active_prompts": total_queued - expired,
        "expired_prompts": expired,
        "max_queue_size": _settings.max_queued_prompts_per_session,
        "entry_expiry_seconds": _settings.prompt_queue_entry_expiry_seconds,
    }


# =============================================================================
# Multiplayer Session Metadata Storage and Management
# =============================================================================
#
# This module provides session metadata tracking for multiplayer collaboration.
# Sessions can track ownership, authorized users, and message history with
# user attribution.
#
# Data Structure:
#   SESSION_METADATA[session_id] = {
#       "session_id": str,           # The session identifier
#       "owner_id": str | None,      # User who created the session
#       "created_at": int,           # Unix timestamp when created
#       "updated_at": int,           # Unix timestamp of last activity
#       "name": str | None,          # Human-readable session name
#       "description": str | None,   # Session description
#       "authorized_users": list,    # Users with access (excludes owner)
#       "messages": list[dict],      # Message history with attribution
#   }
#
# Message Entry Structure:
#   {
#       "message_id": str,           # Unique message identifier
#       "role": "user" | "assistant", # Who sent the message
#       "content": str,              # Message content (truncated)
#       "user_id": str | None,       # Who sent (for user role)
#       "timestamp": int,            # Unix timestamp
#       "turn_number": int | None,   # Conversation turn
#       "tokens_used": int | None,   # Tokens consumed (assistant only)
#   }
#
# Usage Examples:
#   ```python
#   # Create session metadata when session starts
#   create_session_metadata(session_id, owner_id="user_123")
#
#   # Share session with another user
#   authorize_session_user(session_id, "user_456", authorized_by="user_123")
#
#   # Add message to history
#   add_message_to_history(session_id, "user", "What is 2+2?", user_id="user_123")
#
#   # Get session info
#   metadata = get_session_metadata(session_id)
#   ```
# =============================================================================

# Modal Dict for storing session metadata (lazy initialization)
# We use lazy initialization to ensure Modal auth credentials are hydrated before Dict access
_SESSION_METADATA: modal.Dict | dict[str, Any] | None = None


def _get_session_metadata_dict() -> modal.Dict | dict[str, Any]:
    """Lazy initialization of SESSION_METADATA with auth hydration.

    This ensures Modal auth credentials (MODAL_TOKEN_ID, MODAL_TOKEN_SECRET) are set
    before attempting to access the distributed Dict. Inside sandboxes, credentials
    come from secrets as SANDBOX_MODAL_TOKEN_* and must be converted.

    Returns:
        Modal Dict for session metadata, or fallback dict for testing.
    """
    global _SESSION_METADATA
    if _SESSION_METADATA is None:
        # Ensure Modal auth credentials are hydrated before Dict access
        from agent_sandbox.config.settings import _hydrate_modal_token_env

        _hydrate_modal_token_env()
        try:
            _SESSION_METADATA = modal.Dict.from_name(
                _settings.session_metadata_store_name, create_if_missing=True
            )
        except Exception:
            # Fallback for testing without Modal
            _SESSION_METADATA = {}
    return _SESSION_METADATA


def create_session_metadata(
    session_id: str,
    owner_id: str | None = None,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Create metadata for a new session.

    Initializes session metadata with ownership and empty message history.
    If metadata already exists, returns the existing entry unchanged.

    Args:
        session_id: The session identifier.
        owner_id: User ID of the session creator.
        name: Optional human-readable name for the session.
        description: Optional description of the session.

    Returns:
        The session metadata entry.

    Usage:
        ```python
        metadata = create_session_metadata(
            session_id="sess_abc123",
            owner_id="user_123",
            name="Code Review Session",
        )
        ```
    """
    # Check if already exists
    store = _get_session_metadata_dict()
    existing = store.get(session_id)
    if existing:
        return existing

    now = int(time.time())
    metadata = {
        "session_id": session_id,
        "owner_id": owner_id,
        "created_at": now,
        "updated_at": now,
        "name": name,
        "description": description,
        "authorized_users": [],
        "messages": [],
    }
    store[session_id] = metadata
    return metadata


def get_session_metadata(session_id: str) -> dict[str, Any] | None:
    """Get metadata for a session.

    Args:
        session_id: The session identifier.

    Returns:
        Session metadata if found, None otherwise.

    Usage:
        ```python
        metadata = get_session_metadata(session_id)
        if metadata:
            print(f"Owner: {metadata['owner_id']}")
            print(f"Users: {metadata['authorized_users']}")
        ```
    """
    return _get_session_metadata_dict().get(session_id)


def update_session_metadata(
    session_id: str,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any] | None:
    """Update session metadata fields.

    Args:
        session_id: The session identifier.
        name: New name for the session (None to leave unchanged).
        description: New description (None to leave unchanged).

    Returns:
        Updated metadata if session exists, None otherwise.
    """
    store = _get_session_metadata_dict()
    metadata = store.get(session_id)
    if not metadata:
        return None

    if name is not None:
        metadata["name"] = name
    if description is not None:
        metadata["description"] = description

    metadata["updated_at"] = int(time.time())
    store[session_id] = metadata
    return metadata


def authorize_session_user(
    session_id: str,
    user_id: str,
    authorized_by: str | None = None,
) -> dict[str, Any] | None:
    """Authorize a user to access a session.

    Adds user to the authorized_users list. The owner is implicitly authorized
    and should not be added to this list.

    Args:
        session_id: The session identifier.
        user_id: User ID to authorize.
        authorized_by: Who is granting access (for audit).

    Returns:
        Updated metadata if session exists and user was added, None otherwise.

    Usage:
        ```python
        result = authorize_session_user(
            session_id, "user_456", authorized_by="user_123"
        )
        if result:
            print(f"Authorized users: {result['authorized_users']}")
        ```
    """
    store = _get_session_metadata_dict()
    metadata = store.get(session_id)
    if not metadata:
        return None

    # Check max authorized users limit
    if len(metadata["authorized_users"]) >= _settings.max_authorized_users_per_session:
        return None

    # Don't add duplicates or the owner
    if user_id not in metadata["authorized_users"] and user_id != metadata.get("owner_id"):
        metadata["authorized_users"].append(user_id)
        metadata["updated_at"] = int(time.time())
        store[session_id] = metadata

    return metadata


def revoke_session_user(
    session_id: str,
    user_id: str,
    revoked_by: str | None = None,
) -> dict[str, Any] | None:
    """Revoke a user's access to a session.

    Removes user from the authorized_users list.

    Args:
        session_id: The session identifier.
        user_id: User ID to revoke.
        revoked_by: Who is revoking access (for audit).

    Returns:
        Updated metadata if session exists and user was removed, None otherwise.
    """
    store = _get_session_metadata_dict()
    metadata = store.get(session_id)
    if not metadata:
        return None

    if user_id in metadata["authorized_users"]:
        metadata["authorized_users"].remove(user_id)
        metadata["updated_at"] = int(time.time())
        store[session_id] = metadata

    return metadata


def is_user_authorized(session_id: str, user_id: str | None) -> bool:
    """Check if a user is authorized to access a session.

    A user is authorized if they are the owner or in the authorized_users list.
    If user_id is None, returns True (anonymous access allowed).
    If session has no metadata, returns True (no access control).

    Args:
        session_id: The session identifier.
        user_id: User ID to check (None for anonymous).

    Returns:
        True if user is authorized, False otherwise.

    Usage:
        ```python
        if is_user_authorized(session_id, user_id):
            # Allow access
        else:
            # Deny access
        ```
    """
    # Anonymous access allowed by default
    if user_id is None:
        return True

    metadata = _get_session_metadata_dict().get(session_id)

    # No metadata = no access control enforced
    if not metadata:
        return True

    # Owner is always authorized
    if metadata.get("owner_id") == user_id:
        return True

    # Check authorized users list
    return user_id in metadata.get("authorized_users", [])


def get_session_users(session_id: str) -> dict[str, Any] | None:
    """Get list of users with access to a session.

    Args:
        session_id: The session identifier.

    Returns:
        Dict with owner_id and authorized_users, or None if session not found.
    """
    metadata = _get_session_metadata_dict().get(session_id)
    if not metadata:
        return None

    return {
        "owner_id": metadata.get("owner_id"),
        "authorized_users": metadata.get("authorized_users", []),
        "total_users": 1 + len(metadata.get("authorized_users", []))
        if metadata.get("owner_id")
        else len(metadata.get("authorized_users", [])),
    }


def add_message_to_history(
    session_id: str,
    role: str,
    content: str,
    user_id: str | None = None,
    turn_number: int | None = None,
    tokens_used: int | None = None,
) -> dict[str, Any] | None:
    """Add a message to session history with user attribution.

    Messages are stored with the user who sent them (for user role).
    Content is truncated to configured max length.

    Args:
        session_id: The session identifier.
        role: Message role ("user" or "assistant").
        content: Message content.
        user_id: Who sent the message (for user role).
        turn_number: Conversation turn number.
        tokens_used: Tokens consumed (for assistant messages).

    Returns:
        The added message entry, or None if session not found.

    Usage:
        ```python
        # Record user message
        add_message_to_history(
            session_id, "user", "What is 2+2?", user_id="user_123"
        )

        # Record assistant response
        add_message_to_history(
            session_id, "assistant", "2+2 equals 4.", tokens_used=50
        )
        ```
    """
    store = _get_session_metadata_dict()
    metadata = store.get(session_id)
    if not metadata:
        # Auto-create metadata if it doesn't exist
        metadata = create_session_metadata(session_id, owner_id=user_id)

    # Truncate content if needed
    max_len = _settings.message_content_max_length
    if len(content) > max_len:
        content = content[:max_len] + "..."

    now = int(time.time())
    message = {
        "message_id": str(uuid.uuid4()),
        "role": role,
        "content": content,
        "user_id": user_id if role == "user" else None,
        "timestamp": now,
        "turn_number": turn_number,
        "tokens_used": tokens_used if role == "assistant" else None,
    }

    # Add to messages list
    metadata["messages"].append(message)

    # Trim to max history size
    max_history = _settings.max_message_history_per_session
    if len(metadata["messages"]) > max_history:
        metadata["messages"] = metadata["messages"][-max_history:]

    metadata["updated_at"] = now
    store[session_id] = metadata

    return message


def get_session_history(
    session_id: str,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Get message history for a session.

    Args:
        session_id: The session identifier.
        limit: Maximum number of messages to return (None for all).
        offset: Number of messages to skip from start.

    Returns:
        List of message entries.

    Usage:
        ```python
        # Get last 10 messages
        history = get_session_history(session_id, limit=10)
        for msg in history:
            print(f"{msg['role']}: {msg['content'][:50]}...")
        ```
    """
    metadata = _get_session_metadata_dict().get(session_id)
    if not metadata:
        return []

    messages = metadata.get("messages", [])

    # Apply offset
    if offset > 0:
        messages = messages[offset:]

    # Apply limit
    if limit is not None:
        messages = messages[:limit]

    return messages


def get_session_message_count(session_id: str) -> int:
    """Get the number of messages in session history.

    Args:
        session_id: The session identifier.

    Returns:
        Number of messages in history.
    """
    metadata = _get_session_metadata_dict().get(session_id)
    if not metadata:
        return 0
    return len(metadata.get("messages", []))


def clear_session_history(session_id: str) -> int:
    """Clear all messages from session history.

    Args:
        session_id: The session identifier.

    Returns:
        Number of messages that were cleared.
    """
    store = _get_session_metadata_dict()
    metadata = store.get(session_id)
    if not metadata:
        return 0

    count = len(metadata.get("messages", []))
    metadata["messages"] = []
    metadata["updated_at"] = int(time.time())
    store[session_id] = metadata
    return count


def delete_session_metadata(session_id: str) -> bool:
    """Delete all metadata for a session.

    Args:
        session_id: The session identifier.

    Returns:
        True if session was found and deleted, False otherwise.
    """
    try:
        store = _get_session_metadata_dict()
        existing = store.get(session_id)
        if existing:
            del store[session_id]
            return True
    except KeyError:
        pass
    return False


def get_multiplayer_status() -> dict[str, Any]:
    """Get current status of multiplayer sessions across the system.

    Returns:
        Dict with statistics:
        - total_sessions: Sessions with metadata
        - shared_sessions: Sessions shared with at least one user
        - total_messages: Total messages tracked
        - max_history_per_session: Configured limit
    """
    total_sessions = 0
    shared_sessions = 0
    total_messages = 0

    try:
        store = _get_session_metadata_dict()
        for _, metadata in store.items():
            total_sessions += 1
            if metadata.get("authorized_users"):
                shared_sessions += 1
            total_messages += len(metadata.get("messages", []))
    except Exception:
        pass

    return {
        "total_sessions": total_sessions,
        "shared_sessions": shared_sessions,
        "total_messages": total_messages,
        "max_history_per_session": _settings.max_message_history_per_session,
    }


# =============================================================================
# WORKSPACE RETENTION TRACKING
# =============================================================================
# Distributed dictionary for tracking workspace metadata and retention.
# Keys are job_id values, values are dicts with workspace metadata.
# Used to determine which workspaces are eligible for cleanup.

WORKSPACE_RETENTION = modal.Dict.from_name(
    _settings.workspace_retention_store_name, create_if_missing=True
)


def register_job_workspace(
    job_id: str,
    workspace_root: str,
    *,
    job_status: str | None = None,
) -> dict[str, Any]:
    """Register a job workspace for retention tracking.

    Called after creating a job workspace to track it for retention and cleanup.
    Records workspace path, creation time, and basic metadata.

    Args:
        job_id: Validated UUID job identifier
        workspace_root: Absolute path to the workspace directory
        job_status: Optional job status (queued, running, complete, failed, canceled)

    Returns:
        Dict with workspace metadata entry that was created/updated.

    Usage:
        ```python
        # After creating workspace directory
        workspace = job_workspace_root("/data", job_id)
        workspace.mkdir(parents=True, exist_ok=True)
        register_job_workspace(job_id, str(workspace))
        ```
    """
    if not _settings.enable_workspace_retention:
        return {}

    now = int(time.time())
    workspace_path = Path(workspace_root)

    # Calculate workspace size and file count if directory exists
    size_bytes = 0
    file_count = 0
    if workspace_path.exists():
        try:
            for path in workspace_path.rglob("*"):
                if path.is_file():
                    file_count += 1
                    size_bytes += path.stat().st_size
        except Exception:
            pass

    entry = {
        "job_id": job_id,
        "workspace_root": str(workspace_root),
        "created_at": now,
        "size_bytes": size_bytes,
        "file_count": file_count,
        "status": "active",
        "deleted_at": None,
        "job_status": job_status,
        "updated_at": now,
    }

    WORKSPACE_RETENTION[job_id] = entry
    return entry


def update_workspace_metadata(
    job_id: str,
    *,
    job_status: str | None = None,
    recalculate_size: bool = False,
) -> dict[str, Any] | None:
    """Update workspace metadata for a job.

    Updates existing workspace tracking entry with new job status or
    recalculates size/file count from filesystem.

    Args:
        job_id: Validated UUID job identifier
        job_status: New job status to record
        recalculate_size: If True, re-scan filesystem for size/file count

    Returns:
        Updated workspace metadata dict, or None if workspace not tracked.
    """
    if not _settings.enable_workspace_retention:
        return None

    entry = WORKSPACE_RETENTION.get(job_id)
    if not entry:
        return None

    now = int(time.time())
    entry["updated_at"] = now

    if job_status:
        entry["job_status"] = job_status

    if recalculate_size:
        workspace_path = Path(entry["workspace_root"])
        size_bytes = 0
        file_count = 0
        if workspace_path.exists():
            try:
                for path in workspace_path.rglob("*"):
                    if path.is_file():
                        file_count += 1
                        size_bytes += path.stat().st_size
            except Exception:
                pass
        entry["size_bytes"] = size_bytes
        entry["file_count"] = file_count

    WORKSPACE_RETENTION[job_id] = entry
    return entry


def get_workspace_metadata(job_id: str) -> dict[str, Any] | None:
    """Get workspace metadata including creation time and size.

    Args:
        job_id: Validated UUID job identifier

    Returns:
        Dict with workspace metadata, or None if not tracked.
    """
    if not _settings.enable_workspace_retention:
        return None

    return WORKSPACE_RETENTION.get(job_id)


def list_workspaces_for_cleanup(
    before_timestamp: int | None = None,
    status_filter: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List workspaces eligible for cleanup based on age and job status.

    Finds workspaces that have exceeded their retention period based on
    job status. Failed jobs have longer retention than completed jobs.

    Args:
        before_timestamp: Only include workspaces created before this timestamp.
                         If None, calculates based on retention settings.
        status_filter: Only include workspaces for jobs with these statuses.
                      If None, defaults to ["complete", "failed", "canceled"].

    Returns:
        List of workspace metadata dicts eligible for cleanup.
    """
    if not _settings.enable_workspace_retention:
        return []

    now = int(time.time())
    default_statuses = ["complete", "failed", "canceled"]
    statuses_to_check = status_filter or default_statuses

    # Calculate cutoff times based on retention settings
    completed_cutoff = now - (_settings.workspace_retention_days * 86400)
    failed_cutoff = now - (_settings.failed_job_retention_days * 86400)

    eligible = []
    try:
        for job_id, entry in WORKSPACE_RETENTION.items():
            # Skip already deleted workspaces
            if entry.get("status") == "deleted":
                continue

            job_status = entry.get("job_status")
            created_at = entry.get("created_at", now)

            # Skip if job status doesn't match filter
            if job_status and job_status not in statuses_to_check:
                continue

            # Determine cutoff based on job status
            if job_status == "failed":
                cutoff = failed_cutoff
            else:
                cutoff = completed_cutoff

            # Override with explicit before_timestamp if provided
            if before_timestamp is not None:
                cutoff = before_timestamp

            # Check if workspace is old enough for cleanup
            if created_at <= cutoff:
                eligible.append(entry)
    except Exception:
        pass

    return eligible


def build_artifact_manifest(workspace_root: str) -> ArtifactManifest:
    """Build artifact manifest from a workspace directory.

    Recursively scans the workspace directory and collects metadata for all files,
    including size, MIME type, and timestamps. This is a shared utility function
    used by both the CLI controller and the HTTP gateway.

    Args:
        workspace_root: Absolute path to the job workspace directory

    Returns:
        ArtifactManifest containing:
            - root: Absolute path to workspace
            - files: List of ArtifactEntry objects with metadata for each file

    Scanning Behavior:
        - Uses rglob("*") for recursive traversal of all subdirectories
        - Only includes files (not directories)
        - Relative paths computed from workspace root
        - Returns empty file list if workspace doesn't exist

    Metadata Collected:
        - path: Relative path from workspace root (e.g., "output.txt")
        - size_bytes: File size from os.stat().st_size
        - content_type: MIME type guessed from extension
        - created_at: Unix timestamp from st_birthtime (macOS/BSD) or None (Linux)
        - modified_at: Unix timestamp from st_mtime (all platforms)

    Usage:
        ```python
        workspace = job_workspace_root("/data", job_id)
        manifest = build_artifact_manifest(str(workspace))
        update_job(job_id, {"artifacts": manifest.model_dump()})
        ```
    """
    import mimetypes

    root_path = Path(workspace_root)
    files: list[ArtifactEntry] = []

    if root_path.exists():
        for path in root_path.rglob("*"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
                content_type, _ = mimetypes.guess_type(str(path))
                created_at = getattr(stat, "st_birthtime", None)
                files.append(
                    ArtifactEntry(
                        path=str(path.relative_to(root_path)),
                        size_bytes=stat.st_size,
                        content_type=content_type,
                        created_at=int(created_at) if created_at is not None else None,
                        modified_at=int(stat.st_mtime),
                    )
                )
            except Exception:
                # Skip files we can't stat
                continue

    return ArtifactManifest(root=str(root_path), files=files)


def mark_workspace_deleted(job_id: str) -> bool:
    """Mark a workspace as deleted in retention tracking.

    Called after successfully deleting a workspace directory to update
    the tracking record. Preserves metadata for audit purposes.

    Args:
        job_id: Validated UUID job identifier

    Returns:
        True if workspace was found and marked deleted, False otherwise.
    """
    if not _settings.enable_workspace_retention:
        return False

    entry = WORKSPACE_RETENTION.get(job_id)
    if not entry:
        return False

    now = int(time.time())
    entry["status"] = "deleted"
    entry["deleted_at"] = now
    entry["updated_at"] = now

    WORKSPACE_RETENTION[job_id] = entry
    return True


def get_workspace_retention_status() -> dict[str, Any]:
    """Get overall workspace retention statistics.

    Returns summary of workspace retention system including settings,
    counts, and cleanup eligibility.

    Returns:
        Dict with retention statistics matching WorkspaceRetentionStatusResponse.
    """
    if not _settings.enable_workspace_retention:
        return {
            "enabled": False,
            "retention_days": _settings.workspace_retention_days,
            "failed_retention_days": _settings.failed_job_retention_days,
            "total_workspaces": 0,
            "active_workspaces": 0,
            "total_size_bytes": 0,
            "oldest_workspace_age_days": None,
            "workspaces_pending_cleanup": 0,
        }

    now = int(time.time())
    total_workspaces = 0
    active_workspaces = 0
    total_size_bytes = 0
    oldest_created_at: int | None = None

    try:
        for _, entry in WORKSPACE_RETENTION.items():
            total_workspaces += 1
            if entry.get("status") == "active":
                active_workspaces += 1
                total_size_bytes += entry.get("size_bytes", 0) or 0
                created_at = entry.get("created_at")
                if created_at and (oldest_created_at is None or created_at < oldest_created_at):
                    oldest_created_at = created_at
    except Exception:
        pass

    # Calculate oldest workspace age in days
    oldest_age_days: float | None = None
    if oldest_created_at is not None:
        oldest_age_days = (now - oldest_created_at) / 86400

    # Count workspaces pending cleanup
    pending_cleanup = len(list_workspaces_for_cleanup())

    return {
        "enabled": _settings.enable_workspace_retention,
        "retention_days": _settings.workspace_retention_days,
        "failed_retention_days": _settings.failed_job_retention_days,
        "total_workspaces": total_workspaces,
        "active_workspaces": active_workspaces,
        "total_size_bytes": total_size_bytes,
        "oldest_workspace_age_days": oldest_age_days,
        "workspaces_pending_cleanup": pending_cleanup,
    }


# =============================================================================
# CHILD SESSION REGISTRY
# =============================================================================
# Functions for managing parent-child session relationships.
# Enables agents to spawn and track child sessions for parallel work delegation.


def register_child_session(
    parent_id: str,
    child_job_id: str,
    task: str,
    sandbox_type: str,
    *,
    context: str | None = None,
    timeout_seconds: int | None = None,
    allowed_tools: str | None = None,
) -> bool:
    """Register a child session under a parent for tracking.

    Creates a record in the CHILD_SESSION_REGISTRY linking a child job to its
    parent session. This enables the parent to look up and monitor its spawned
    children.

    Args:
        parent_id: UUID of the parent job/session that spawned this child
        child_job_id: UUID of the spawned child job
        task: Task description given to the child
        sandbox_type: Type of sandbox used ("agent_sdk")
        context: Optional additional context provided to child
        timeout_seconds: Timeout configured for child session
        allowed_tools: Comma-separated tools allowed (optional)

    Returns:
        True if registration was successful, False if max children exceeded.

    Registry Structure:
        CHILD_SESSION_REGISTRY[parent_id] = [
            {
                "child_job_id": "uuid-1",
                "task": "Research quantum computing",
                "sandbox_type": "agent_sdk",
                "status": "queued",
                "created_at": 1672531200,
                "context": "...",
                "timeout_seconds": 300,
                "allowed_tools": "Read,Write",
            },
            ...
        ]

    Limits:
        Returns False if the parent has already spawned max_children_per_session
        children, preventing resource exhaustion.
    """
    if not _settings.enable_child_sessions:
        return False

    now = int(time.time())

    # Get existing children for this parent
    children = CHILD_SESSION_REGISTRY.get(parent_id) or []

    # Check limit
    if len(children) >= _settings.max_children_per_session:
        return False

    # Create child entry
    child_entry = {
        "child_job_id": child_job_id,
        "task": task,
        "sandbox_type": sandbox_type,
        "status": "queued",
        "created_at": now,
        "context": context,
        "timeout_seconds": timeout_seconds,
        "allowed_tools": allowed_tools,
    }

    children.append(child_entry)
    CHILD_SESSION_REGISTRY[parent_id] = children
    return True


def get_child_sessions(parent_id: str) -> list[dict[str, Any]]:
    """Get all child sessions spawned by a parent.

    Retrieves the list of child sessions registered under a parent ID,
    with status information updated from the job records.

    Args:
        parent_id: UUID of the parent job/session

    Returns:
        List of child session entries with current status.
        Returns empty list if no children found.

    Status Updates:
        The status field in each entry is synchronized with the actual
        job status from JOB_RESULTS to reflect current state.
    """
    if not _settings.enable_child_sessions:
        return []

    children = CHILD_SESSION_REGISTRY.get(parent_id) or []

    # Update status from actual job records
    updated_children = []
    for child in children:
        child_id = child.get("child_job_id")
        if child_id:
            job_record = JOB_RESULTS.get(child_id)
            if job_record:
                child["status"] = job_record.get("status", child.get("status", "queued"))
                child["completed_at"] = job_record.get("completed_at")
                child["started_at"] = job_record.get("started_at")
        updated_children.append(child)

    return updated_children


def update_child_session_status(
    parent_id: str,
    child_job_id: str,
    status: str,
    *,
    completed_at: int | None = None,
) -> bool:
    """Update the status of a child session in the registry.

    Called when a child job transitions to a new state to keep the
    registry in sync with actual job status.

    Args:
        parent_id: UUID of the parent job/session
        child_job_id: UUID of the child job to update
        status: New status value ("running", "complete", "failed", etc.)
        completed_at: Optional completion timestamp for terminal states

    Returns:
        True if child was found and updated, False otherwise.
    """
    if not _settings.enable_child_sessions:
        return False

    children = CHILD_SESSION_REGISTRY.get(parent_id) or []

    for child in children:
        if child.get("child_job_id") == child_job_id:
            child["status"] = status
            if completed_at is not None:
                child["completed_at"] = completed_at
            CHILD_SESSION_REGISTRY[parent_id] = children
            return True

    return False


def get_child_count(parent_id: str) -> int:
    """Get the number of children spawned by a parent.

    Args:
        parent_id: UUID of the parent job/session

    Returns:
        Count of children spawned by this parent.
    """
    if not _settings.enable_child_sessions:
        return 0

    children = CHILD_SESSION_REGISTRY.get(parent_id) or []
    return len(children)


def can_spawn_child(parent_id: str) -> bool:
    """Check if a parent can spawn another child session.

    Verifies that child sessions are enabled and that the parent has not
    exceeded the maximum number of allowed children.

    Args:
        parent_id: UUID of the parent job/session

    Returns:
        True if parent can spawn another child, False otherwise.
    """
    if not _settings.enable_child_sessions:
        return False

    current_count = get_child_count(parent_id)
    return current_count < _settings.max_children_per_session


def get_child_session_result(parent_id: str, child_job_id: str) -> dict[str, Any] | None:
    """Get the result of a completed child session.

    Retrieves the full result from a child job, including the agent response,
    artifacts, and summary information.

    Args:
        parent_id: UUID of the parent job/session (for validation)
        child_job_id: UUID of the child job

    Returns:
        Dict with child session result if complete, None if not found.
        Returns partial info with status if child is still running.

    Result Structure:
        {
            "child_id": "uuid",
            "status": "complete",
            "result": "Agent response text...",
            "summary": {"session_id": "...", "duration_ms": 1234, ...},
            "artifacts": ["output.txt", "data.csv", ...],
        }
    """
    if not _settings.enable_child_sessions:
        return None

    # Verify child belongs to parent
    children = CHILD_SESSION_REGISTRY.get(parent_id) or []
    child_entry = None
    for child in children:
        if child.get("child_job_id") == child_job_id:
            child_entry = child
            break

    if not child_entry:
        return None

    # Get job record
    job_record = JOB_RESULTS.get(child_job_id)
    if not job_record:
        return {
            "child_id": child_job_id,
            "status": "not_found",
            "error": "Child job record not found",
        }

    status = job_record.get("status", "queued")

    # Build result based on status
    result: dict[str, Any] = {
        "child_id": child_job_id,
        "status": status,
        "task": child_entry.get("task"),
        "sandbox_type": child_entry.get("sandbox_type"),
        "created_at": child_entry.get("created_at"),
        "started_at": job_record.get("started_at"),
        "completed_at": job_record.get("completed_at"),
    }

    if status in ("queued", "running"):
        result["error"] = f"Child session is still {status}. Use check_session_status to monitor."
        return result

    if status == "failed":
        result["error"] = job_record.get("error", "Child session failed without error message")
        return result

    if status == "canceled":
        result["error"] = "Child session was canceled"
        return result

    # status is "complete" - extract result
    job_result = job_record.get("result")
    if job_result:
        # Extract text from messages if available
        messages = job_result.get("messages", [])
        result_text_parts = []
        for msg in messages:
            if isinstance(msg, dict):
                msg_type = msg.get("type")
                if msg_type == "text":
                    result_text_parts.append(msg.get("content", ""))
                elif msg_type == "tool_result":
                    tool_content = msg.get("content", "")
                    if isinstance(tool_content, str):
                        result_text_parts.append(tool_content)

        result["result"] = "\n".join(result_text_parts) if result_text_parts else str(job_result)
        result["summary"] = job_result.get("summary")

    # Get artifacts if available
    artifacts = job_record.get("artifacts")
    if artifacts and isinstance(artifacts, dict):
        files = artifacts.get("files", [])
        result["artifacts"] = [
            f.get("path") for f in files if isinstance(f, dict) and f.get("path")
        ]

    return result
