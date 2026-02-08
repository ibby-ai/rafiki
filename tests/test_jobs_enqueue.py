"""Tests for enqueue_job job_id handling."""

from uuid import UUID

import pytest

import modal_backend.jobs as jobs


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
