"""Tests for enqueue_job job_id handling."""

from uuid import UUID

import pytest

import modal_backend.jobs as jobs
import modal_backend.main as main
from modal_backend.models.jobs import JobSubmitRequest


class _FakeQueue:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def put(self, item: dict[str, str]) -> None:
        self.items.append(item)


def _patch_storage(monkeypatch: pytest.MonkeyPatch):
    fake_results: dict[str, dict[str, object]] = {}
    fake_queue = _FakeQueue()
    monkeypatch.setattr(jobs, "JOB_RESULTS", fake_results)
    monkeypatch.setattr(jobs, "JOB_QUEUE", fake_queue)
    return fake_results, fake_queue


def test_enqueue_job_preserves_job_id(monkeypatch: pytest.MonkeyPatch):
    job_results, job_queue = _patch_storage(monkeypatch)
    job_id = "123e4567-e89b-12d3-a456-426614174000"

    returned = jobs.enqueue_job("Run job", job_id=job_id)

    assert returned == job_id
    assert returned in job_results
    assert job_queue.items[0]["job_id"] == job_id


def test_enqueue_job_duplicate_job_id(monkeypatch: pytest.MonkeyPatch):
    job_results, _ = _patch_storage(monkeypatch)
    job_id = "123e4567-e89b-12d3-a456-426614174000"
    job_results[job_id] = {"job_id": job_id, "status": "queued"}

    with pytest.raises(jobs.DuplicateJobIdError):
        jobs.enqueue_job("Run job", job_id=job_id)


def test_enqueue_job_generates_job_id(monkeypatch: pytest.MonkeyPatch):
    job_results, job_queue = _patch_storage(monkeypatch)

    returned = jobs.enqueue_job("Run job")

    assert isinstance(UUID(returned), UUID)
    assert returned in job_results
    assert job_queue.items[0]["job_id"] == returned


def test_enqueue_job_persists_session_scope(monkeypatch: pytest.MonkeyPatch):
    job_results, _ = _patch_storage(monkeypatch)

    returned = jobs.enqueue_job(
        "Run job",
        job_id="123e4567-e89b-12d3-a456-426614174001",
        session_id="sess-job-1",
        tenant_id="tenant-1",
        user_id="user-1",
    )

    assert job_results[returned]["session_id"] == "sess-job-1"
    assert job_results[returned]["tenant_id"] == "tenant-1"
    assert job_results[returned]["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_submit_job_forwards_session_scope(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def _fake_run_blocking_modal_call(func, *args, **kwargs):
        captured["func"] = func
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "123e4567-e89b-12d3-a456-426614174002"

    monkeypatch.setattr(main, "_run_blocking_modal_call", _fake_run_blocking_modal_call)

    response = await main.submit_job(
        JobSubmitRequest(
            question="Run job",
            session_id="sess-job-2",
            tenant_id="tenant-2",
            user_id="user-2",
        )
    )

    assert response.job_id == "123e4567-e89b-12d3-a456-426614174002"
    assert captured["func"] is main.enqueue_job
    assert captured["kwargs"]["session_id"] == "sess-job-2"
    assert captured["kwargs"]["tenant_id"] == "tenant-2"
    assert captured["kwargs"]["user_id"] == "user-2"
