"""Shared rollout state for controller sandbox promotion and draining."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Any

import modal

from modal_backend.settings.settings import get_settings

_settings = get_settings()

CONTROLLER_ROLLOUT = modal.Dict.from_name(
    _settings.controller_rollout_store_name,
    create_if_missing=True,
)

_ACTIVE_POINTER_KEY = "active_pointer"
_ROLLOUT_LOCK_KEY = "rollout_lock"
_PROMOTION_COMMIT_PREFIX = "promotion-commit:"
_SERVICE_PREFIX = "service:"
_INFLIGHT_LEASE_PREFIX = "inflight-lease:"
_logger = logging.getLogger(__name__)


def _service_key(sandbox_id: str) -> str:
    return f"{_SERVICE_PREFIX}{sandbox_id}"


def _promotion_commit_key(expected_generation: int) -> str:
    return f"{_PROMOTION_COMMIT_PREFIX}{max(0, expected_generation)}"


def _inflight_lease_key(sandbox_id: str, request_id: str) -> str:
    return f"{_INFLIGHT_LEASE_PREFIX}{sandbox_id}:{request_id}"


def get_active_controller_pointer() -> dict[str, Any] | None:
    pointer = CONTROLLER_ROLLOUT.get(_ACTIVE_POINTER_KEY)
    return dict(pointer) if isinstance(pointer, dict) else None


def set_active_controller_pointer(pointer: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(pointer)
    CONTROLLER_ROLLOUT[_ACTIVE_POINTER_KEY] = normalized
    return normalized


def clear_active_controller_pointer() -> None:
    try:
        CONTROLLER_ROLLOUT.pop(_ACTIVE_POINTER_KEY)
    except KeyError:
        pass


def get_rollout_lock() -> dict[str, Any] | None:
    entry = CONTROLLER_ROLLOUT.get(_ROLLOUT_LOCK_KEY)
    return dict(entry) if isinstance(entry, dict) else None


def rollout_lock_owned_by(operation_id: str) -> bool:
    existing = get_rollout_lock()
    return bool(existing and existing.get("operation_id") == operation_id)


def acquire_rollout_lock(operation_id: str) -> dict[str, Any]:
    now = int(time.time())
    existing = get_rollout_lock()
    if existing:
        acquired_at = int(existing.get("acquired_at") or 0)
        if now - acquired_at > max(1, _settings.controller_rollout_lock_max_age_seconds):
            try:
                CONTROLLER_ROLLOUT.pop(_ROLLOUT_LOCK_KEY)
            except KeyError:
                pass

    lock_entry = {
        "operation_id": operation_id,
        "acquired_at": now,
    }
    acquired = CONTROLLER_ROLLOUT.put(_ROLLOUT_LOCK_KEY, lock_entry, skip_if_exists=True)
    return {
        "acquired": bool(acquired),
        "entry": lock_entry if acquired else (get_rollout_lock() or lock_entry),
    }


def release_rollout_lock(operation_id: str) -> bool:
    existing = get_rollout_lock()
    if not existing or existing.get("operation_id") != operation_id:
        return False
    try:
        CONTROLLER_ROLLOUT.pop(_ROLLOUT_LOCK_KEY)
    except KeyError:
        return False
    return True


def get_promotion_commit(expected_generation: int) -> dict[str, Any] | None:
    entry = CONTROLLER_ROLLOUT.get(_promotion_commit_key(expected_generation))
    return dict(entry) if isinstance(entry, dict) else None


def promotion_commit_owned_by(
    *,
    expected_generation: int,
    operation_id: str,
    candidate_sandbox_id: str | None = None,
) -> bool:
    entry = get_promotion_commit(expected_generation)
    if not entry or entry.get("operation_id") != operation_id:
        return False
    normalized_candidate = (candidate_sandbox_id or "").strip()
    if (
        normalized_candidate
        and str(entry.get("candidate_sandbox_id") or "").strip() != normalized_candidate
    ):
        return False
    return True


def acquire_promotion_commit(
    *,
    expected_generation: int,
    target_generation: int,
    operation_id: str,
    candidate_sandbox_id: str,
) -> dict[str, Any]:
    now = int(time.time())
    key = _promotion_commit_key(expected_generation)
    existing = get_promotion_commit(expected_generation)
    if existing:
        acquired_at = int(existing.get("acquired_at") or 0)
        if now - acquired_at > max(1, _settings.controller_rollout_lock_max_age_seconds):
            try:
                CONTROLLER_ROLLOUT.pop(key)
            except KeyError:
                pass

    entry = {
        "operation_id": operation_id,
        "expected_generation": max(0, expected_generation),
        "target_generation": max(0, target_generation),
        "candidate_sandbox_id": candidate_sandbox_id,
        "acquired_at": now,
    }
    acquired = CONTROLLER_ROLLOUT.put(key, entry, skip_if_exists=True)
    return {
        "acquired": bool(acquired),
        "entry": entry if acquired else (get_promotion_commit(expected_generation) or entry),
    }


def release_promotion_commit(*, expected_generation: int, operation_id: str) -> bool:
    existing = get_promotion_commit(expected_generation)
    if not existing or existing.get("operation_id") != operation_id:
        return False
    try:
        CONTROLLER_ROLLOUT.pop(_promotion_commit_key(expected_generation))
    except KeyError:
        return False
    return True


def get_controller_service(sandbox_id: str | None) -> dict[str, Any] | None:
    normalized = (sandbox_id or "").strip()
    if not normalized:
        return None
    entry = CONTROLLER_ROLLOUT.get(_service_key(normalized))
    return dict(entry) if isinstance(entry, dict) else None


def upsert_controller_service(entry: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(entry)
    sandbox_id = str(normalized.get("sandbox_id") or "").strip()
    if not sandbox_id:
        raise ValueError("sandbox_id is required")
    CONTROLLER_ROLLOUT[_service_key(sandbox_id)] = normalized
    return normalized


def update_controller_service(sandbox_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    current = get_controller_service(sandbox_id)
    if not current:
        return None
    current.update(updates)
    current["updated_at"] = int(time.time())
    return upsert_controller_service(current)


def list_controller_services(
    *,
    statuses: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    allowed_statuses = set(statuses or [])
    entries: list[dict[str, Any]] = []
    try:
        items = list(CONTROLLER_ROLLOUT.items())
    except Exception as exc:
        raise RuntimeError("Failed to read controller service registry") from exc

    for key, value in items:
        if not isinstance(key, str) or not key.startswith(_SERVICE_PREFIX):
            continue
        if not isinstance(value, dict):
            continue
        if allowed_statuses and value.get("status") not in allowed_statuses:
            continue
        entries.append(dict(value))

    return sorted(
        entries,
        key=lambda entry: (
            int(entry.get("generation") or 0),
            int(entry.get("created_at") or 0),
        ),
        reverse=True,
    )


def get_latest_active_service_from_registry() -> dict[str, Any] | None:
    entries = list_controller_services(statuses=("active",))
    return entries[0] if entries else None


def get_draining_controller_services() -> list[dict[str, Any]]:
    return list_controller_services(statuses=("draining",))


def get_controller_request_admission(
    *,
    sandbox_id: str | None,
    expected_generation: int | None = None,
    allow_draining: bool = False,
) -> dict[str, Any]:
    normalized = (sandbox_id or "").strip()
    if not normalized:
        return {"admissible": False, "reason": "missing_sandbox_id"}

    service = get_controller_service(normalized)
    if not service:
        return {"admissible": False, "reason": "missing_service"}

    status = str(service.get("status") or "").strip() or "unknown"
    generation = int(service.get("generation") or 0)
    if expected_generation is not None and generation != expected_generation:
        return {
            "admissible": False,
            "reason": "generation_mismatch",
            "generation": generation,
            "status": status,
        }

    if allow_draining:
        if status not in {"active", "draining"}:
            return {
                "admissible": False,
                "reason": "service_not_routable",
                "generation": generation,
                "status": status,
            }
        return {
            "admissible": True,
            "generation": generation,
            "status": status,
            "service": dict(service),
        }

    pointer = get_active_controller_pointer()
    if not pointer:
        return {"admissible": False, "reason": "missing_active_pointer"}

    pointer_generation = int(pointer.get("active_generation") or 0)
    pointer_sandbox_id = str(pointer.get("sandbox_id") or "").strip()
    if pointer_sandbox_id != normalized:
        return {
            "admissible": False,
            "reason": "pointer_mismatch",
            "generation": generation,
            "status": status,
        }
    if expected_generation is not None and pointer_generation != expected_generation:
        return {
            "admissible": False,
            "reason": "pointer_generation_mismatch",
            "generation": generation,
            "status": status,
            "pointer_generation": pointer_generation,
        }
    if status != "active":
        return {
            "admissible": False,
            "reason": "service_not_active",
            "generation": generation,
            "status": status,
        }
    return {
        "admissible": True,
        "generation": generation,
        "status": status,
        "service": dict(service),
    }


def _initial_inflight_entry(sandbox_id: str) -> dict[str, Any]:
    return {
        "sandbox_id": sandbox_id,
        "total": 0,
        "query": 0,
        "query_stream": 0,
        "updated_at": int(time.time()),
    }


def get_controller_inflight(sandbox_id: str) -> dict[str, Any]:
    normalized = _initial_inflight_entry(sandbox_id)
    latest_updated_at = 0
    for lease in list_controller_inflight_leases(sandbox_id=sandbox_id):
        kind = "query_stream" if lease.get("request_kind") == "query_stream" else "query"
        normalized["total"] += 1
        normalized[kind] += 1
        latest_updated_at = max(latest_updated_at, int(lease.get("updated_at") or 0))
        generation = lease.get("generation")
        if generation is not None and "generation" not in normalized:
            normalized["generation"] = generation
    if latest_updated_at:
        normalized["updated_at"] = latest_updated_at
    return normalized


def list_controller_inflight_leases(
    *,
    sandbox_id: str | None = None,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    normalized_sandbox_id = (sandbox_id or "").strip()
    normalized_session_id = (session_id or "").strip()
    leases: list[dict[str, Any]] = []
    try:
        items = list(CONTROLLER_ROLLOUT.items())
    except Exception as exc:
        raise RuntimeError("Failed to read controller inflight leases") from exc

    for key, value in items:
        if not isinstance(key, str) or not key.startswith(_INFLIGHT_LEASE_PREFIX):
            continue
        if not isinstance(value, dict):
            continue
        lease = dict(value)
        if (
            normalized_sandbox_id
            and str(lease.get("sandbox_id") or "").strip() != normalized_sandbox_id
        ):
            continue
        if (
            normalized_session_id
            and str(lease.get("session_id") or "").strip() != normalized_session_id
        ):
            continue
        leases.append(lease)

    return sorted(
        leases,
        key=lambda entry: (
            int(entry.get("updated_at") or 0),
            int(entry.get("started_at") or 0),
            str(entry.get("request_id") or ""),
        ),
        reverse=True,
    )


def start_controller_request(
    *,
    sandbox_id: str,
    request_id: str,
    session_id: str | None,
    request_kind: str,
    generation: int | None,
    require_active: bool = False,
    allow_draining: bool = False,
) -> dict[str, Any]:
    kind = "query_stream" if request_kind == "query_stream" else "query"
    now = int(time.time())
    normalized_request_id = request_id.strip()
    if not normalized_request_id:
        raise ValueError("request_id is required")
    if require_active:
        admission = get_controller_request_admission(
            sandbox_id=sandbox_id,
            expected_generation=generation,
            allow_draining=allow_draining,
        )
        if not admission.get("admissible"):
            raise RuntimeError(
                "Controller request is no longer admissible "
                f"(reason={admission.get('reason')}, sandbox_id={sandbox_id})"
            )

    lease: dict[str, Any] = {
        "sandbox_id": sandbox_id,
        "request_id": normalized_request_id,
        "request_kind": kind,
        "started_at": now,
        "updated_at": now,
    }
    if generation is not None:
        lease["generation"] = generation

    normalized_session_id = (session_id or "").strip()
    if normalized_session_id:
        lease["session_id"] = normalized_session_id

    CONTROLLER_ROLLOUT[_inflight_lease_key(sandbox_id, normalized_request_id)] = lease
    return get_controller_inflight(sandbox_id)


def finish_controller_request(
    *,
    sandbox_id: str,
    request_id: str,
    session_id: str | None,
    request_kind: str,
) -> dict[str, Any]:
    _ = (session_id, request_kind)
    normalized_request_id = request_id.strip()
    if normalized_request_id:
        try:
            CONTROLLER_ROLLOUT.pop(_inflight_lease_key(sandbox_id, normalized_request_id))
        except KeyError:
            pass

    try:
        return get_controller_inflight(sandbox_id)
    except Exception:
        _logger.warning(
            "Failed to refresh controller inflight after request cleanup",
            exc_info=True,
            extra={
                "sandbox_id": sandbox_id,
                "request_id": normalized_request_id or None,
            },
        )
        return _initial_inflight_entry(sandbox_id)


def get_session_controller_route(session_id: str | None) -> dict[str, Any] | None:
    normalized = (session_id or "").strip()
    if not normalized:
        return None
    routes = list_session_controller_routes(normalized)
    return routes[0] if routes else None


def list_session_controller_routes(session_id: str | None) -> list[dict[str, Any]]:
    normalized = (session_id or "").strip()
    if not normalized:
        return []

    grouped: dict[str, dict[str, Any]] = {}
    for lease in list_controller_inflight_leases(session_id=normalized):
        sandbox_id = str(lease.get("sandbox_id") or "").strip()
        if not sandbox_id:
            continue
        current = grouped.get(sandbox_id)
        if current is None:
            current = {
                "session_id": normalized,
                "sandbox_id": sandbox_id,
                "active_requests": 0,
                "updated_at": int(lease.get("updated_at") or 0),
            }
            generation = lease.get("generation")
            if generation is not None:
                current["generation"] = generation
            grouped[sandbox_id] = current
        current["active_requests"] = max(0, int(current.get("active_requests") or 0)) + 1
        current["updated_at"] = max(
            int(current.get("updated_at") or 0),
            int(lease.get("updated_at") or 0),
        )

    return sorted(
        grouped.values(),
        key=lambda entry: (
            int(entry.get("updated_at") or 0),
            int(entry.get("active_requests") or 0),
            str(entry.get("sandbox_id") or ""),
        ),
        reverse=True,
    )


def sanitize_controller_service(entry: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(entry)
    sanitized.pop("sandbox_session_secret", None)
    sanitized.pop("synthetic_session_id", None)
    return sanitized


def build_public_rollout_status() -> dict[str, Any]:
    pointer = get_active_controller_pointer()
    services = [sanitize_controller_service(entry) for entry in list_controller_services()]
    for entry in services:
        sandbox_id = str(entry.get("sandbox_id") or "").strip()
        if sandbox_id:
            entry["inflight"] = get_controller_inflight(sandbox_id)

    return {
        "active": sanitize_controller_service(pointer) if pointer else None,
        "services": services,
        "rollout_lock": get_rollout_lock(),
    }
