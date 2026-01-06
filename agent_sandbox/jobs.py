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
    """Normalize job IDs to canonical UUID strings."""
    if not job_id:
        return None
    try:
        return str(UUID(str(job_id)))
    except (ValueError, TypeError, AttributeError):
        return None


def job_workspace_root(agent_fs_root: str, job_id: str) -> Path:
    """Return the job workspace root directory for a job."""
    return Path(agent_fs_root) / "jobs" / job_id


def resolve_job_artifact(agent_fs_root: str, job_id: str, artifact_path: str) -> Path | None:
    """Resolve an artifact path within the job workspace, preventing traversal."""
    base = job_workspace_root(agent_fs_root, job_id).resolve()
    candidate = (base / artifact_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def _normalize_schedule_at(value: int | float | None) -> int | None:
    """Normalize schedule_at inputs to integer unix timestamps."""
    if value is None:
        return None
    try:
        schedule_at = int(value)
    except (TypeError, ValueError):
        return None
    return schedule_at if schedule_at > 0 else None


def _normalize_webhook(value: WebhookConfig | dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize webhook config into a plain dict for persistence."""
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
    """Return True if a job is scheduled to run now."""
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
