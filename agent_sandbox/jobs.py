"""Queue-based async job processing for agent runs."""

from __future__ import annotations

import time
import uuid
from typing import Any

import modal

from agent_sandbox.config.settings import get_settings
from agent_sandbox.schemas.jobs import JobStatusResponse

_settings = get_settings()

JOB_QUEUE = modal.Queue.from_name(_settings.job_queue_name, create_if_missing=True)
JOB_RESULTS = modal.Dict.from_name(_settings.job_results_dict, create_if_missing=True)


def enqueue_job(question: str) -> str:
    """Enqueue a job and return its job id."""
    job_id = str(uuid.uuid4())
    now = int(time.time())
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
    """Return the job status record if available."""
    record = JOB_RESULTS.get(job_id)
    if not record:
        return None
    return JobStatusResponse(**record)


def cancel_job(job_id: str) -> JobStatusResponse | None:
    """Mark a queued job as canceled. Processing will skip canceled jobs."""
    record = JOB_RESULTS.get(job_id)
    if not record:
        return None
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
    record = JOB_RESULTS.get(job_id, {"job_id": job_id})
    now = int(time.time())
    updated = {
        **record,
        **updates,
        "updated_at": now,
    }
    JOB_RESULTS[job_id] = updated


def should_skip_job(job_id: str) -> bool:
    record = JOB_RESULTS.get(job_id)
    if not record:
        return False
    return record.get("status") == "canceled"


def bump_attempts(job_id: str) -> int:
    record = JOB_RESULTS.get(job_id, {"job_id": job_id})
    attempts = int(record.get("attempts", 0)) + 1
    update_job(job_id, {"attempts": attempts})
    return attempts
