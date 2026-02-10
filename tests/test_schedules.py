"""Tests for schedule CRUD and dispatch helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import modal_backend.main as main
import modal_backend.schedules as schedules
from modal_backend.models.schedules import ScheduleCreateRequest, ScheduleUpdateRequest
from modal_backend.security.cloudflare_auth import INTERNAL_AUTH_HEADER
from modal_backend.settings.settings import get_settings


def _fake_schedule_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    store: dict[str, dict] = {}
    monkeypatch.setattr(schedules, "SCHEDULES", store)
    return store


def _build_internal_token(secret: str) -> str:
    now_ms = int(time.time() * 1000)
    payload = {
        "service": "cloudflare-worker",
        "issued_at": now_ms,
        "expires_at": now_ms + 300_000,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    return f"{base64.b64encode(payload_bytes).decode('utf-8')}.{base64.b64encode(signature).decode('utf-8')}"


def _api_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    secret = "test-internal-secret"
    monkeypatch.setenv("INTERNAL_AUTH_SECRET", secret)
    get_settings.cache_clear()
    token = _build_internal_token(secret)
    client = TestClient(main.web_app)
    client.headers.update({INTERNAL_AUTH_HEADER: token})
    return client


def test_schedule_create_one_off(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _fake_schedule_store(monkeypatch)
    payload = ScheduleCreateRequest(
        name="nightly-once",
        question="run once",
        schedule_type="one_off",
        run_at=1_800_000_000,
        enabled=True,
    )

    created = schedules.create_schedule(payload, user_id="user-1")

    assert created["schedule_id"] in store
    assert created["run_at"] == 1_800_000_000
    assert created["next_run_at"] == 1_800_000_000
    assert created["user_id"] == "user-1"


def test_schedule_create_cron_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_schedule_store(monkeypatch)
    payload = ScheduleCreateRequest(
        name="daily-report",
        question="send report",
        schedule_type="cron",
        cron="0 6 * * *",
        timezone="America/New_York",
    )
    created = schedules.create_schedule(payload)
    next_run = datetime.fromtimestamp(created["next_run_at"], tz=UTC).astimezone(
        ZoneInfo("America/New_York")
    )

    assert created["cron"] == "0 6 * * *"
    assert created["timezone"] == "America/New_York"
    assert created["next_run_at"] is not None
    assert next_run.hour == 6
    assert next_run.minute == 0


def test_schedule_update_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _fake_schedule_store(monkeypatch)
    payload = ScheduleCreateRequest(
        name="twice-daily",
        question="do work",
        schedule_type="cron",
        cron="0 */12 * * *",
        timezone="UTC",
    )
    created = schedules.create_schedule(payload, user_id="user-1")
    before = created["next_run_at"]

    updated = schedules.update_schedule(
        created["schedule_id"],
        ScheduleUpdateRequest(timezone="America/Los_Angeles"),
        user_id="user-1",
    )

    assert updated["timezone"] == "America/Los_Angeles"
    assert updated["next_run_at"] is not None
    assert before != updated["next_run_at"]
    assert store[created["schedule_id"]]["timezone"] == "America/Los_Angeles"


def test_schedule_dispatch_due(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _fake_schedule_store(monkeypatch)
    jobs: list[dict[str, object]] = []

    def _fake_enqueue(question: str, **kwargs):
        jobs.append({"question": question, **kwargs})
        return "123e4567-e89b-12d3-a456-426614174000"

    monkeypatch.setattr(schedules, "enqueue_job", _fake_enqueue)
    schedule_id = "123e4567-e89b-12d3-a456-426614174111"
    store[schedule_id] = {
        "schedule_id": schedule_id,
        "name": "due-now",
        "question": "run now",
        "schedule_type": "one_off",
        "run_at": 1000,
        "cron": None,
        "timezone": "UTC",
        "enabled": True,
        "user_id": "user-1",
        "tenant_id": None,
        "webhook": None,
        "metadata": None,
        "created_at": 999,
        "updated_at": 999,
        "last_run_at": None,
        "next_run_at": 1000,
        "last_job_id": None,
        "last_error": None,
    }

    result = schedules.dispatch_due_schedules(now=1000)

    assert result["dispatched"] == 1
    assert jobs[0]["question"] == "run now"
    assert jobs[0]["metadata"] == {
        "schedule_id": schedule_id,
        "schedule_name": "due-now",
        "triggered_at": 1000,
    }
    assert store[schedule_id]["enabled"] is False
    assert store[schedule_id]["next_run_at"] is None
    assert store[schedule_id]["last_job_id"] == "123e4567-e89b-12d3-a456-426614174000"


def test_schedule_dispatch_recurring(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _fake_schedule_store(monkeypatch)
    monkeypatch.setattr(
        schedules,
        "enqueue_job",
        lambda question, **kwargs: "123e4567-e89b-12d3-a456-426614174222",
    )
    schedule_id = "123e4567-e89b-12d3-a456-426614174333"
    store[schedule_id] = {
        "schedule_id": schedule_id,
        "name": "recurring",
        "question": "run recurring",
        "schedule_type": "cron",
        "run_at": None,
        "cron": "*/5 * * * *",
        "timezone": "UTC",
        "enabled": True,
        "user_id": None,
        "tenant_id": "tenant-1",
        "webhook": None,
        "metadata": None,
        "created_at": 0,
        "updated_at": 0,
        "last_run_at": None,
        "next_run_at": 300,
        "last_job_id": None,
        "last_error": None,
    }

    result = schedules.dispatch_due_schedules(now=300)

    assert result["dispatched"] == 1
    assert store[schedule_id]["enabled"] is True
    assert store[schedule_id]["next_run_at"] > 300


def test_schedule_dispatch_counts_match_state_and_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _fake_schedule_store(monkeypatch)
    jobs: list[str] = []

    def _fake_enqueue(question: str, **kwargs):
        if question == "should-fail":
            raise RuntimeError("queue write failed")
        job_id = f"job-{len(jobs) + 1}"
        jobs.append(job_id)
        return job_id

    monkeypatch.setattr(schedules, "enqueue_job", _fake_enqueue)

    store["one-off-ok"] = {
        "schedule_id": "one-off-ok",
        "name": "one-off-ok",
        "question": "should-dispatch",
        "schedule_type": "one_off",
        "run_at": 200,
        "cron": None,
        "timezone": "UTC",
        "enabled": True,
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "webhook": None,
        "metadata": None,
        "created_at": 100,
        "updated_at": 100,
        "last_run_at": None,
        "next_run_at": 200,
        "last_job_id": None,
        "last_error": None,
    }
    store["cron-ok"] = {
        "schedule_id": "cron-ok",
        "name": "cron-ok",
        "question": "should-dispatch-too",
        "schedule_type": "cron",
        "run_at": None,
        "cron": "*/5 * * * *",
        "timezone": "UTC",
        "enabled": True,
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "webhook": None,
        "metadata": None,
        "created_at": 100,
        "updated_at": 100,
        "last_run_at": None,
        "next_run_at": 200,
        "last_job_id": None,
        "last_error": None,
    }
    store["one-off-fail"] = {
        "schedule_id": "one-off-fail",
        "name": "one-off-fail",
        "question": "should-fail",
        "schedule_type": "one_off",
        "run_at": 200,
        "cron": None,
        "timezone": "UTC",
        "enabled": True,
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "webhook": None,
        "metadata": None,
        "created_at": 100,
        "updated_at": 100,
        "last_run_at": None,
        "next_run_at": 200,
        "last_job_id": None,
        "last_error": None,
    }

    result = schedules.dispatch_due_schedules(now=200)

    transitioned = [
        rec
        for rec in store.values()
        if rec.get("last_run_at") == 200 and rec.get("last_job_id") and not rec.get("last_error")
    ]
    assert result["dispatched"] == len(jobs)
    assert result["dispatched"] == len(transitioned)
    assert result["failed"] == 1
    assert store["one-off-ok"]["enabled"] is False
    assert store["one-off-ok"]["next_run_at"] is None
    assert store["cron-ok"]["enabled"] is True
    assert store["cron-ok"]["next_run_at"] > 200
    assert store["one-off-fail"]["last_error"] == "queue write failed"


def test_schedule_pause_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_schedule_store(monkeypatch)
    payload = ScheduleCreateRequest(
        name="pauseable",
        question="do it",
        schedule_type="cron",
        cron="*/10 * * * *",
        timezone="UTC",
    )
    created = schedules.create_schedule(payload, user_id="user-1")

    paused = schedules.update_schedule(
        created["schedule_id"], ScheduleUpdateRequest(enabled=False), user_id="user-1"
    )
    resumed = schedules.update_schedule(
        created["schedule_id"], ScheduleUpdateRequest(enabled=True), user_id="user-1"
    )

    assert paused["enabled"] is False
    assert paused["next_run_at"] is None
    assert resumed["enabled"] is True
    assert resumed["next_run_at"] is not None


def test_schedule_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _fake_schedule_store(monkeypatch)
    payload = ScheduleCreateRequest(
        name="delete-me",
        question="cleanup",
        schedule_type="one_off",
        run_at=1_800_000_000,
    )
    created = schedules.create_schedule(payload, tenant_id="tenant-1")

    assert schedules.delete_schedule(created["schedule_id"], tenant_id="tenant-1") is True
    assert created["schedule_id"] not in store


def test_schedule_create_one_off_missing_run_at_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_schedule_store(monkeypatch)
    client = _api_client(monkeypatch)

    response = client.post(
        "/schedules",
        json={
            "name": "missing-run-at",
            "question": "should fail",
            "schedule_type": "one_off",
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "run_at is required for one_off schedules"}


def test_schedule_create_cron_missing_cron_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_schedule_store(monkeypatch)
    client = _api_client(monkeypatch)

    response = client.post(
        "/schedules",
        json={
            "name": "missing-cron",
            "question": "should fail",
            "schedule_type": "cron",
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "cron is required for cron schedules"}


def test_schedule_dispatch_endpoint_returns_dispatch_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    monkeypatch.setattr(
        main,
        "dispatch_due_schedules",
        lambda: {"scanned": 2, "dispatched": 1, "failed": 0},
    )

    response = client.post("/schedules/dispatch")

    assert response.status_code == 200
    assert response.json() == {"scanned": 2, "dispatched": 1, "failed": 0}
