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
from typing import Any

import modal

from agent_sandbox.config.settings import get_settings
from agent_sandbox.schemas.jobs import JobStatusResponse

_settings = get_settings()

# Distributed queue for pending job payloads. Workers call JOB_QUEUE.get() to
# receive {"job_id": str, "question": str} messages.
JOB_QUEUE = modal.Queue.from_name(_settings.job_queue_name, create_if_missing=True)

# Distributed dictionary storing job metadata keyed by job_id. Each entry contains
# status, timestamps, result/error, and attempt count. Persists across workers.
JOB_RESULTS = modal.Dict.from_name(_settings.job_results_dict, create_if_missing=True)


def enqueue_job(question: str) -> str:
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
    # Initialize job record with queued status before pushing to queue
    JOB_RESULTS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "question": question,
        "created_at": now,
        "updated_at": now,
        "attempts": 0,
    }
    JOB_QUEUE.put({"job_id": job_id, "question": question})
    return job_id


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
    return JobStatusResponse(**record)


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
        return JobStatusResponse(**record)
    now = int(time.time())
    updated = {
        **record,
        "status": "canceled",
        "canceled_at": now,
        "updated_at": now,
    }
    JOB_RESULTS[job_id] = updated
    return JobStatusResponse(**updated)


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
