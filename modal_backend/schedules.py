"""Schedule storage, validation, and dispatch helpers."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import modal
from croniter import croniter

from modal_backend.jobs import enqueue_job
from modal_backend.models.schedules import (
    ScheduleCreateRequest,
    ScheduleType,
    ScheduleUpdateRequest,
)
from modal_backend.settings.settings import get_settings

_settings = get_settings()

SCHEDULES = modal.Dict.from_name(_settings.schedule_store_name, create_if_missing=True)


class ScheduleError(ValueError):
    """Base schedule validation error."""


class InvalidScheduleIdError(ScheduleError):
    """Raised when a schedule_id is malformed."""


class ScheduleNotFoundError(ScheduleError):
    """Raised when schedule_id is not found."""


def normalize_schedule_id(schedule_id: str | None) -> str | None:
    """Normalize schedule_id as canonical UUID string."""
    if not schedule_id:
        return None
    try:
        return str(UUID(str(schedule_id)))
    except (ValueError, TypeError, AttributeError):
        return None


def _coerce_timezone(timezone: str | None) -> str:
    tz_name = (timezone or "UTC").strip() or "UTC"
    try:
        ZoneInfo(tz_name)
    except Exception as exc:
        raise ScheduleError(f"Invalid timezone: {tz_name}") from exc
    return tz_name


def _normalize_cron(cron: str | None) -> str | None:
    if cron is None:
        return None
    cron_value = cron.strip()
    if not cron_value:
        return None
    if not croniter.is_valid(cron_value):
        raise ScheduleError("Invalid cron expression")
    return cron_value


def compute_next_run_at(cron: str, timezone: str, from_ts: int) -> int:
    """Return next UTC unix timestamp for a cron in the given timezone."""
    tz_name = _coerce_timezone(timezone)
    cron_value = _normalize_cron(cron)
    if not cron_value:
        raise ScheduleError("cron is required")
    tz = ZoneInfo(tz_name)
    base_dt = datetime.fromtimestamp(from_ts, tz=UTC).astimezone(tz)
    iterator = croniter(cron_value, base_dt)
    next_local = iterator.get_next(datetime)
    if next_local.tzinfo is None:
        next_local = next_local.replace(tzinfo=tz)
    return int(next_local.astimezone(UTC).timestamp())


def _is_visible_to_actor(
    record: dict[str, Any], *, user_id: str | None, tenant_id: str | None
) -> bool:
    if tenant_id:
        return record.get("tenant_id") == tenant_id
    if user_id:
        return record.get("user_id") == user_id
    return True


def _next_run_for_create(payload: ScheduleCreateRequest, now_ts: int) -> int | None:
    if not payload.enabled:
        return None
    if payload.schedule_type == "one_off":
        return int(payload.run_at) if payload.run_at is not None else None
    return compute_next_run_at(payload.cron or "", payload.timezone, now_ts)


def create_schedule(
    payload: ScheduleCreateRequest, *, user_id: str | None = None, tenant_id: str | None = None
) -> dict[str, Any]:
    """Create and persist a schedule record."""
    now_ts = int(time.time())
    schedule_id = str(uuid.uuid4())
    timezone = _coerce_timezone(payload.timezone)
    cron_value = _normalize_cron(payload.cron)
    if payload.schedule_type == "cron" and not cron_value:
        raise ScheduleError("cron is required for cron schedules")
    if payload.schedule_type == "one_off" and payload.run_at is None:
        raise ScheduleError("run_at is required for one_off schedules")
    if payload.schedule_type == "one_off" and cron_value is not None:
        raise ScheduleError("cron must be omitted for one_off schedules")

    record: dict[str, Any] = {
        "schedule_id": schedule_id,
        "name": payload.name,
        "question": payload.question,
        "agent_type": payload.agent_type,
        "schedule_type": payload.schedule_type,
        "run_at": int(payload.run_at) if payload.run_at is not None else None,
        "cron": cron_value,
        "timezone": timezone,
        "enabled": payload.enabled,
        "webhook": payload.webhook.model_dump(exclude_none=True) if payload.webhook else None,
        "metadata": payload.metadata,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "created_at": now_ts,
        "updated_at": now_ts,
        "last_run_at": None,
        "next_run_at": _next_run_for_create(payload, now_ts),
        "last_job_id": None,
        "last_error": None,
    }
    SCHEDULES[schedule_id] = record
    return record


def get_schedule(
    schedule_id: str, *, user_id: str | None = None, tenant_id: str | None = None
) -> dict[str, Any] | None:
    """Fetch a schedule if visible to the request actor."""
    normalized = normalize_schedule_id(schedule_id)
    if not normalized:
        raise InvalidScheduleIdError("Invalid schedule_id")
    record = SCHEDULES.get(normalized)
    if not record:
        return None
    if not _is_visible_to_actor(record, user_id=user_id, tenant_id=tenant_id):
        return None
    return record


def list_schedules(
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
    enabled: bool | None = None,
    schedule_type: ScheduleType | None = None,
) -> list[dict[str, Any]]:
    """List schedules with optional filters."""
    output: list[dict[str, Any]] = []
    for _, record in SCHEDULES.items():
        if not _is_visible_to_actor(record, user_id=user_id, tenant_id=tenant_id):
            continue
        if enabled is not None and bool(record.get("enabled")) != enabled:
            continue
        if schedule_type and record.get("schedule_type") != schedule_type:
            continue
        output.append(record)
    output.sort(key=lambda item: item.get("created_at", 0), reverse=True)
    return output


def update_schedule(
    schedule_id: str,
    payload: ScheduleUpdateRequest,
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Update a schedule and recompute next_run_at when needed."""
    normalized = normalize_schedule_id(schedule_id)
    if not normalized:
        raise InvalidScheduleIdError("Invalid schedule_id")
    current = SCHEDULES.get(normalized)
    if not current or not _is_visible_to_actor(current, user_id=user_id, tenant_id=tenant_id):
        raise ScheduleNotFoundError("Schedule not found")

    updates = payload.model_dump(exclude_unset=True)
    updated = dict(current)
    now_ts = int(time.time())
    for key, value in updates.items():
        if key == "webhook":
            updated[key] = value.model_dump(exclude_none=True) if value else None
        else:
            updated[key] = value

    timezone = _coerce_timezone(updated.get("timezone", "UTC"))
    updated["timezone"] = timezone
    cron_value = _normalize_cron(updated.get("cron"))
    updated["cron"] = cron_value

    schedule_type = updated.get("schedule_type")
    if schedule_type == "one_off":
        if updated.get("run_at") is None:
            raise ScheduleError("run_at is required for one_off schedules")
        if "cron" in updates and updates["cron"] is not None:
            raise ScheduleError("cron must be omitted for one_off schedules")
    elif schedule_type == "cron":
        if not cron_value:
            raise ScheduleError("cron is required for cron schedules")
    else:
        raise ScheduleError(f"Unsupported schedule_type: {schedule_type}")

    recompute_fields = {"run_at", "cron", "timezone", "enabled"}
    if recompute_fields.intersection(updates.keys()):
        if not bool(updated.get("enabled")):
            updated["next_run_at"] = None
        elif schedule_type == "one_off":
            updated["next_run_at"] = int(updated["run_at"])
        else:
            anchor = max(now_ts, int(updated.get("last_run_at") or now_ts))
            updated["next_run_at"] = compute_next_run_at(updated["cron"], timezone, anchor)

    updated["updated_at"] = now_ts
    SCHEDULES[normalized] = updated
    return updated


def delete_schedule(
    schedule_id: str, *, user_id: str | None = None, tenant_id: str | None = None
) -> bool:
    """Delete a schedule if visible to actor."""
    normalized = normalize_schedule_id(schedule_id)
    if not normalized:
        raise InvalidScheduleIdError("Invalid schedule_id")
    current = SCHEDULES.get(normalized)
    if not current or not _is_visible_to_actor(current, user_id=user_id, tenant_id=tenant_id):
        return False
    del SCHEDULES[normalized]
    return True


def dispatch_due_schedules(*, now: int | None = None) -> dict[str, int]:
    """Dispatch due schedules to JOB_QUEUE and update schedule state."""
    now_ts = now if now is not None else int(time.time())
    scanned = 0
    dispatched = 0
    failed = 0
    due_ids: list[str] = []

    for schedule_id, record in list(SCHEDULES.items()):
        scanned += 1
        if not record.get("enabled"):
            continue
        next_run_at = record.get("next_run_at")
        if next_run_at is None or int(next_run_at) > now_ts:
            continue
        due_ids.append(schedule_id)
        try:
            job_id = enqueue_job(
                record["question"],
                tenant_id=record.get("tenant_id"),
                user_id=record.get("user_id"),
                webhook=record.get("webhook"),
                metadata={
                    "schedule_id": schedule_id,
                    "schedule_name": record.get("name"),
                    "triggered_at": now_ts,
                },
            )
            record["last_job_id"] = job_id
            record["last_run_at"] = now_ts
            record["last_error"] = None
            if record.get("schedule_type") == "cron":
                record["next_run_at"] = compute_next_run_at(
                    record.get("cron") or "",
                    record.get("timezone") or "UTC",
                    now_ts,
                )
                record["enabled"] = True
            else:
                record["next_run_at"] = None
                record["enabled"] = False
            record["updated_at"] = now_ts
            SCHEDULES[schedule_id] = record
            dispatched += 1
        except Exception as exc:
            record["last_error"] = str(exc)
            record["updated_at"] = now_ts
            SCHEDULES[schedule_id] = record
            failed += 1

    # Reconcile with persisted state to avoid returning a stale counter if another
    # dispatcher raced this pass over shared schedule storage.
    persisted_dispatched = 0
    for schedule_id in due_ids:
        record = SCHEDULES.get(schedule_id)
        if not record:
            continue
        if (
            int(record.get("last_run_at") or 0) == now_ts
            and record.get("last_job_id")
            and not record.get("last_error")
        ):
            persisted_dispatched += 1

    if persisted_dispatched > dispatched:
        dispatched = persisted_dispatched
    if failed + dispatched > len(due_ids):
        failed = max(0, len(due_ids) - dispatched)
    if failed + dispatched < len(due_ids):
        failed = len(due_ids) - dispatched

    return {"scanned": scanned, "dispatched": dispatched, "failed": failed}
