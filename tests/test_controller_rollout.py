from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import anyio
import pytest
from modal import exception as modal_exc

import modal_backend.controller_rollout as rollout
import modal_backend.jobs as jobs
import modal_backend.main as main
from modal_backend.settings.settings import Settings


class _FakeModalDict(dict):
    def put(self, key, value, *, skip_if_exists: bool = False):
        if skip_if_exists and key in self:
            return False
        self[key] = value
        return True


class _AsyncOnlyMethod:
    def __init__(self, impl) -> None:
        self.aio = impl

    def __call__(self, *args, **kwargs):
        raise AssertionError("sync Modal interface should not be called in async flow")


class _FakeSyncSandbox:
    def __init__(self, sandbox_id: str, service_url: str) -> None:
        self.object_id = sandbox_id
        self._service_url = service_url
        self.terminate_calls: list[bool] = []

    def tunnels(self) -> dict[int, SimpleNamespace]:
        return {main.SERVICE_PORT: SimpleNamespace(url=self._service_url)}

    def poll(self):
        return None

    def terminate(self, *, wait: bool = False) -> None:
        self.terminate_calls.append(wait)

    def set_tags(self, _tags: dict[str, str]) -> None:
        return None


class _FakeAsyncSandbox:
    def __init__(self, sandbox_id: str, service_url: str) -> None:
        self.object_id = sandbox_id
        self._service_url = service_url
        self.tunnels = SimpleNamespace(aio=self._tunnels_aio)
        self.poll = SimpleNamespace(aio=self._poll_aio)
        self.set_tags = SimpleNamespace(aio=self._set_tags_aio)

    async def _tunnels_aio(self) -> dict[int, SimpleNamespace]:
        return {main.SERVICE_PORT: SimpleNamespace(url=self._service_url)}

    async def _poll_aio(self):
        return None

    async def _set_tags_aio(self, _tags: dict[str, str]) -> None:
        return None


@pytest.fixture(autouse=True)
def reset_cached_controller_state() -> None:
    main._set_background_sandbox_state(None, None)
    main._SANDBOX_SESSION_SECRET_CACHE.clear()


def _bind_rollout_store(monkeypatch: pytest.MonkeyPatch, store: _FakeModalDict) -> None:
    monkeypatch.setattr(rollout, "CONTROLLER_ROLLOUT", store)
    for name in (
        "acquire_promotion_commit",
        "acquire_rollout_lock",
        "build_public_rollout_status",
        "clear_active_controller_pointer",
        "finish_controller_request",
        "get_active_controller_pointer",
        "get_controller_inflight",
        "get_controller_request_admission",
        "get_controller_service",
        "get_draining_controller_services",
        "list_controller_services",
        "list_session_controller_routes",
        "promotion_commit_owned_by",
        "release_promotion_commit",
        "release_rollout_lock",
        "rollout_lock_owned_by",
        "set_active_controller_pointer",
        "start_controller_request",
        "update_controller_service",
        "upsert_controller_service",
    ):
        monkeypatch.setattr(main, name, getattr(rollout, name))


def test_rollout_store_tracks_inflight_and_redacts_scoped_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeModalDict()
    monkeypatch.setattr(rollout, "CONTROLLER_ROLLOUT", store)

    rollout.set_active_controller_pointer(
        {
            "active_generation": 2,
            "status": "active",
            "sandbox_id": "sb-active",
            "service_url": "https://active.internal",
        }
    )
    rollout.upsert_controller_service(
        {
            "generation": 2,
            "sandbox_id": "sb-active",
            "sandbox_name": "svc-active",
            "service_url": "https://active.internal",
            "status": "active",
            "sandbox_session_secret": "top-secret",
            "created_at": 1,
        }
    )

    rollout.start_controller_request(
        sandbox_id="sb-active",
        request_id="req-1",
        session_id="sess-1",
        request_kind="query_stream",
        generation=2,
    )
    status = rollout.build_public_rollout_status()

    assert status["active"]["sandbox_id"] == "sb-active"
    assert "sandbox_session_secret" not in status["active"]
    assert status["services"][0]["inflight"]["total"] == 1
    assert status["services"][0]["inflight"]["query_stream"] == 1
    assert "sandbox_session_secret" not in status["services"][0]
    assert "synthetic_session_id" not in status["services"][0]

    rollout.finish_controller_request(
        sandbox_id="sb-active",
        request_id="req-1",
        session_id="sess-1",
        request_kind="query_stream",
    )
    assert rollout.get_session_controller_route("sess-1") is None


def test_get_or_start_background_sandbox_aio_refreshes_from_active_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = _FakeAsyncSandbox("sb-old", "https://old.internal")
    fresh = _FakeAsyncSandbox("sb-new", "https://new.internal")
    main._set_background_sandbox_state(stale, "https://old.internal", generation=1)

    pointer = {
        "active_generation": 2,
        "sandbox_id": "sb-new",
        "service_url": "https://new.internal",
    }

    async def _from_id_aio(_sandbox_id: str):
        assert _sandbox_id == "sb-new"
        return fresh

    async def _ensure_pointer_aio():
        return pointer

    async def _wait_ready(**_kwargs):
        return None

    monkeypatch.setattr(main, "_ensure_active_pointer_from_registry_aio", _ensure_pointer_aio)
    monkeypatch.setattr(main, "_wait_for_service_or_raise_readiness_timeout_aio", _wait_ready)
    monkeypatch.setattr(main.modal.Sandbox, "from_id", _AsyncOnlyMethod(_from_id_aio))
    monkeypatch.setattr(
        main,
        "get_controller_service",
        lambda sandbox_id: {"sandbox_id": sandbox_id, "sandbox_session_secret": "secret"},
    )

    sandbox, url = anyio.run(main.get_or_start_background_sandbox_aio)

    assert sandbox is fresh
    assert url == "https://new.internal"
    assert main._get_background_sandbox_generation() == 2


def test_controller_request_leases_drive_inflight_and_session_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeModalDict()
    monkeypatch.setattr(rollout, "CONTROLLER_ROLLOUT", store)

    rollout.start_controller_request(
        sandbox_id="sb-a",
        request_id="req-query",
        session_id="sess-1",
        request_kind="query",
        generation=2,
    )
    rollout.start_controller_request(
        sandbox_id="sb-a",
        request_id="req-stream",
        session_id="sess-1",
        request_kind="query_stream",
        generation=2,
    )

    inflight = rollout.get_controller_inflight("sb-a")
    route = rollout.get_session_controller_route("sess-1")

    assert inflight["total"] == 2
    assert inflight["query"] == 1
    assert inflight["query_stream"] == 1
    assert route == {
        "session_id": "sess-1",
        "sandbox_id": "sb-a",
        "active_requests": 2,
        "updated_at": route["updated_at"],
        "generation": 2,
    }

    rollout.finish_controller_request(
        sandbox_id="sb-a",
        request_id="req-query",
        session_id="sess-1",
        request_kind="query",
    )
    assert rollout.get_controller_inflight("sb-a")["total"] == 1


def test_finish_controller_request_is_best_effort_when_lease_scan_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeModalDict()
    monkeypatch.setattr(rollout, "CONTROLLER_ROLLOUT", store)

    rollout.start_controller_request(
        sandbox_id="sb-a",
        request_id="req-query",
        session_id="sess-1",
        request_kind="query",
        generation=2,
    )

    def _broken_inflight(_sandbox_id: str):
        raise RuntimeError("rollout store unavailable")

    monkeypatch.setattr(rollout, "get_controller_inflight", _broken_inflight)

    assert rollout.finish_controller_request(
        sandbox_id="sb-a",
        request_id="req-query",
        session_id="sess-1",
        request_kind="query",
    ) == {
        "sandbox_id": "sb-a",
        "total": 0,
        "query": 0,
        "query_stream": 0,
        "updated_at": pytest.approx(time.time(), abs=5),
    }


def test_promotion_commit_slot_reclaims_stale_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeModalDict(
        {
            "promotion-commit:1": {
                "operation_id": "rollout-old",
                "expected_generation": 1,
                "target_generation": 2,
                "candidate_sandbox_id": "sb-old",
                "acquired_at": 10,
            }
        }
    )
    monkeypatch.setattr(rollout, "CONTROLLER_ROLLOUT", store)
    monkeypatch.setattr(
        rollout,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
            controller_rollout_lock_max_age_seconds=5,
        ),
    )
    monkeypatch.setattr(rollout.time, "time", lambda: 20)

    acquired = rollout.acquire_promotion_commit(
        expected_generation=1,
        target_generation=2,
        operation_id="rollout-new",
        candidate_sandbox_id="sb-new",
    )

    assert acquired["acquired"] is True
    assert store["promotion-commit:1"]["operation_id"] == "rollout-new"
    assert store["promotion-commit:1"]["candidate_sandbox_id"] == "sb-new"


def test_ensure_active_pointer_from_registry_recovers_single_active_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeModalDict()
    _bind_rollout_store(monkeypatch, store)
    rollout.upsert_controller_service(
        {
            "generation": 3,
            "sandbox_id": "sb-active",
            "sandbox_name": "svc-active",
            "service_url": "https://active.internal",
            "image_version": "img-active",
            "status": "active",
            "created_at": 100,
            "promoted_at": 101,
            "last_verified_readiness_at": 102,
        }
    )

    pointer = main._ensure_active_pointer_from_registry()

    assert pointer is not None
    assert pointer["active_generation"] == 3
    assert pointer["sandbox_id"] == "sb-active"
    assert pointer["service_url"] == "https://active.internal"
    assert pointer["image_version"] == "img-active"
    assert pointer["promoted_at"] == 101
    assert pointer["last_verified_readiness_at"] == 102
    assert rollout.get_active_controller_pointer()["sandbox_id"] == "sb-active"


def test_start_controller_request_rejects_draining_service_for_new_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeModalDict()
    monkeypatch.setattr(rollout, "CONTROLLER_ROLLOUT", store)
    rollout.set_active_controller_pointer(
        {
            "active_generation": 2,
            "status": "active",
            "sandbox_id": "sb-drain",
            "service_url": "https://drain.internal",
        }
    )
    rollout.upsert_controller_service(
        {
            "generation": 2,
            "sandbox_id": "sb-drain",
            "service_url": "https://drain.internal",
            "status": "draining",
            "created_at": 1,
        }
    )

    with pytest.raises(RuntimeError, match="no longer admissible"):
        rollout.start_controller_request(
            sandbox_id="sb-drain",
            request_id="req-drain",
            session_id="sess-1",
            request_kind="query",
            generation=2,
            require_active=True,
        )


def test_rollout_controller_sandbox_keeps_pointer_on_failed_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = {
        "sandbox": _FakeSyncSandbox("sb-b", "https://b.internal"),
        "sandbox_id": "sb-b",
        "sandbox_name": "svc-b",
        "service_url": "https://b.internal",
        "sandbox_session_secret": "secret-b",
        "image_version": "img-b",
        "claimed_from_pool": False,
    }
    abort_calls: list[tuple[dict[str, object], int, str]] = []
    persist_called = {"value": False}

    monkeypatch.setattr(
        main,
        "acquire_rollout_lock",
        lambda _operation_id: {"acquired": True, "entry": {"operation_id": "lock-1"}},
    )
    monkeypatch.setattr(main, "release_rollout_lock", lambda _operation_id: True)
    monkeypatch.setattr(
        main,
        "_ensure_active_pointer_from_registry",
        lambda: {
            "active_generation": 1,
            "sandbox_id": "sb-a",
            "sandbox_name": "svc-a",
            "service_url": "https://a.internal",
            "image_version": "img-a",
        },
    )
    monkeypatch.setattr(
        main,
        "get_controller_service",
        lambda sandbox_id: {
            "generation": 1,
            "sandbox_id": sandbox_id,
            "sandbox_name": "svc-a",
            "service_url": "https://a.internal",
            "image_version": "img-a",
            "status": "active",
        },
    )
    monkeypatch.setattr(main, "_prepare_rollout_candidate_sync", lambda generation: candidate)
    monkeypatch.setattr(
        main,
        "_verify_rollout_candidate_sync",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("verification failed")),
    )
    monkeypatch.setattr(
        main,
        "_abort_rollout_candidate",
        lambda *, candidate, generation, error: abort_calls.append(
            (candidate, generation, str(error))
        ),
    )
    monkeypatch.setattr(
        main,
        "_persist_active_controller_pointer",
        lambda **_kwargs: persist_called.__setitem__("value", True),
    )

    with pytest.raises(RuntimeError, match="verification failed"):
        main._rollout_controller_sandbox_sync(reason="test")

    assert persist_called["value"] is False
    assert abort_calls == [(candidate, 2, "verification failed")]


def test_rollout_controller_sandbox_promotes_candidate_and_marks_previous_draining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = {
        "sandbox": _FakeSyncSandbox("sb-b", "https://b.internal"),
        "sandbox_id": "sb-b",
        "sandbox_name": "svc-b",
        "service_url": "https://b.internal",
        "sandbox_session_secret": "secret-b",
        "image_version": "img-b",
        "claimed_from_pool": True,
    }
    updates: list[tuple[str, dict[str, object]]] = []
    drain_spawns: list[tuple[str, int, int]] = []
    persisted: list[dict[str, object]] = []

    monkeypatch.setattr(
        main,
        "acquire_rollout_lock",
        lambda _operation_id: {"acquired": True, "entry": {"operation_id": "lock-1"}},
    )
    monkeypatch.setattr(main, "release_rollout_lock", lambda _operation_id: True)
    monkeypatch.setattr(
        main,
        "_ensure_active_pointer_from_registry",
        lambda: {
            "active_generation": 1,
            "sandbox_id": "sb-a",
            "sandbox_name": "svc-a",
            "service_url": "https://a.internal",
            "image_version": "img-a",
        },
    )
    monkeypatch.setattr(
        main,
        "get_controller_service",
        lambda sandbox_id: {
            "generation": 1,
            "sandbox_id": sandbox_id,
            "sandbox_name": "svc-a",
            "service_url": "https://a.internal",
            "image_version": "img-a",
            "status": "active",
        }
        if sandbox_id == "sb-a"
        else {
            "generation": 2,
            "sandbox_id": sandbox_id,
            "sandbox_name": "svc-b",
            "service_url": "https://b.internal",
            "image_version": "img-b",
            "status": "promoting",
        },
    )
    monkeypatch.setattr(main, "_prepare_rollout_candidate_sync", lambda generation: candidate)
    monkeypatch.setattr(
        main,
        "_verify_rollout_candidate_sync",
        lambda **_kwargs: {
            "verified_at": 123,
            "synthetic_session_id": "__controller_rollout__gen_2",
        },
    )
    monkeypatch.setattr(
        main,
        "_persist_active_controller_pointer",
        lambda **kwargs: persisted.append(kwargs)
        or {
            "active_generation": kwargs["generation"],
            "sandbox_id": kwargs["sandbox_id"],
            "service_url": kwargs["service_url"],
            "rollback_target_sandbox_id": kwargs["rollback_target"]["sandbox_id"],
        },
    )
    monkeypatch.setattr(
        main,
        "update_controller_service",
        lambda sandbox_id, payload: updates.append((sandbox_id, dict(payload))),
    )
    monkeypatch.setattr(main, "remove_from_pool", lambda _sandbox_id: True)
    monkeypatch.setattr(
        main,
        "replenish_warm_pool",
        SimpleNamespace(spawn=lambda: None),
    )
    monkeypatch.setattr(
        main,
        "_schedule_controller_drain",
        lambda sandbox_id, generation, deadline: drain_spawns.append(
            (sandbox_id, generation, deadline)
        )
        or {
            "ok": True,
            "status": "scheduled",
            "mode": "spawned",
            "sandbox_id": sandbox_id,
            "drain_call_id": "fc-test",
            "scheduled_at": 123,
        },
    )
    monkeypatch.setattr(main, "_set_background_sandbox_state", lambda *args, **kwargs: None)

    result = main._rollout_controller_sandbox_sync(reason="test")

    assert result["ok"] is True
    assert result["status"] == "promoted"
    assert persisted[0]["generation"] == 2
    assert persisted[0]["rollback_target"]["sandbox_id"] == "sb-a"
    assert (
        "sb-b",
        {
            "status": "active",
            "promoted_at": result["promoted_at"],
            "last_verified_readiness_at": 123,
            "promotion_reason": "test",
        },
    ) in updates
    draining_updates = [payload for sandbox_id, payload in updates if sandbox_id == "sb-a"]
    assert draining_updates and draining_updates[0]["status"] == "draining"
    assert drain_spawns and drain_spawns[0][0] == "sb-a"
    assert result["drain_status"] == {
        "ok": True,
        "status": "scheduled",
        "mode": "spawned",
        "sandbox_id": "sb-a",
        "drain_call_id": "fc-test",
        "scheduled_at": 123,
    }


def test_rollout_controller_sandbox_reports_drain_failure_at_top_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = {
        "sandbox": _FakeSyncSandbox("sb-b", "https://b.internal"),
        "sandbox_id": "sb-b",
        "sandbox_name": "svc-b",
        "service_url": "https://b.internal",
        "sandbox_session_secret": "secret-b",
        "image_version": "img-b",
        "claimed_from_pool": False,
    }
    updates: list[tuple[str, dict[str, object]]] = []
    persisted: list[dict[str, object]] = []

    monkeypatch.setattr(
        main,
        "acquire_rollout_lock",
        lambda _operation_id: {"acquired": True, "entry": {"operation_id": "lock-1"}},
    )
    monkeypatch.setattr(main, "release_rollout_lock", lambda _operation_id: True)
    monkeypatch.setattr(
        main,
        "_ensure_active_pointer_from_registry",
        lambda: {
            "active_generation": 1,
            "sandbox_id": "sb-a",
            "sandbox_name": "svc-a",
            "service_url": "https://a.internal",
            "image_version": "img-a",
        },
    )
    monkeypatch.setattr(
        main,
        "get_controller_service",
        lambda sandbox_id: {
            "generation": 1,
            "sandbox_id": sandbox_id,
            "sandbox_name": "svc-a",
            "service_url": "https://a.internal",
            "image_version": "img-a",
            "status": "active",
        }
        if sandbox_id == "sb-a"
        else {
            "generation": 2,
            "sandbox_id": sandbox_id,
            "sandbox_name": "svc-b",
            "service_url": "https://b.internal",
            "image_version": "img-b",
            "status": "promoting",
        },
    )
    monkeypatch.setattr(main, "_prepare_rollout_candidate_sync", lambda generation: candidate)
    monkeypatch.setattr(
        main,
        "_verify_rollout_candidate_sync",
        lambda **_kwargs: {
            "verified_at": 123,
            "synthetic_session_id": "__controller_rollout__gen_2",
        },
    )
    monkeypatch.setattr(
        main,
        "_persist_active_controller_pointer",
        lambda **kwargs: persisted.append(kwargs)
        or {
            "active_generation": kwargs["generation"],
            "sandbox_id": kwargs["sandbox_id"],
            "service_url": kwargs["service_url"],
            "rollback_target_sandbox_id": kwargs["rollback_target"]["sandbox_id"],
        },
    )
    monkeypatch.setattr(
        main,
        "update_controller_service",
        lambda sandbox_id, payload: updates.append((sandbox_id, dict(payload))),
    )
    monkeypatch.setattr(
        main,
        "_schedule_controller_drain",
        lambda sandbox_id, generation, deadline: {
            "ok": False,
            "status": "drain_failed",
            "mode": "inline",
            "sandbox_id": sandbox_id,
            "error": "rollout store unavailable",
        },
    )
    monkeypatch.setattr(main, "_set_background_sandbox_state", lambda *args, **kwargs: None)

    result = main._rollout_controller_sandbox_sync(reason="test")

    assert result["ok"] is False
    assert result["status"] == "promoted_with_drain_failure"
    assert result["error"] == "controller drain failed after promotion"
    assert result["drain_status"] == {
        "ok": False,
        "status": "drain_failed",
        "mode": "inline",
        "sandbox_id": "sb-a",
        "error": "rollout store unavailable",
    }
    assert persisted[0]["generation"] == 2
    assert (
        "sb-b",
        {
            "status": "active",
            "promoted_at": result["promoted_at"],
            "last_verified_readiness_at": 123,
            "promotion_reason": "test",
        },
    ) in updates


def test_schedule_controller_drain_falls_back_to_inline_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inline_calls: list[tuple[str, int, int]] = []

    monkeypatch.setattr(
        main,
        "drain_controller_sandbox",
        SimpleNamespace(
            spawn=lambda *_args: (_ for _ in ()).throw(modal_exc.ExecutionError("not hydrated")),
            local=main.drain_controller_sandbox.local,
        ),
    )
    monkeypatch.setattr(
        main,
        "_drain_controller_sandbox_sync",
        lambda *, sandbox_id, expected_generation, drain_deadline_at: inline_calls.append(
            (sandbox_id, expected_generation, drain_deadline_at)
        )
        or {
            "ok": True,
            "status": "terminated",
            "sandbox_id": sandbox_id,
        },
    )

    result = main._schedule_controller_drain("sb-a", 1, 123)

    assert result == {
        "ok": True,
        "status": "terminated",
        "sandbox_id": "sb-a",
        "mode": "inline",
    }
    assert inline_calls == [("sb-a", 1, 123)]


def test_schedule_controller_drain_records_spawn_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        main,
        "drain_controller_sandbox",
        SimpleNamespace(
            spawn=lambda *_args: SimpleNamespace(object_id="fc-scheduled"),
            local=main.drain_controller_sandbox.local,
        ),
    )
    monkeypatch.setattr(
        main,
        "update_controller_service",
        lambda sandbox_id, payload: updates.append((sandbox_id, dict(payload))),
    )
    monkeypatch.setattr(main.time, "time", lambda: 1234)

    result = main._schedule_controller_drain("sb-a", 1, 9999)

    assert result == {
        "ok": True,
        "status": "scheduled",
        "mode": "spawned",
        "sandbox_id": "sb-a",
        "drain_call_id": "fc-scheduled",
        "scheduled_at": 1234,
    }
    assert updates == [
        (
            "sb-a",
            {
                "drain_mode": "spawned",
                "drain_call_id": "fc-scheduled",
                "drain_scheduled_at": 1234,
                "drain_expected_generation": 1,
                "drain_deadline_at": 9999,
            },
        )
    ]


def test_schedule_controller_drain_returns_failure_metadata_when_inline_fallback_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        main,
        "drain_controller_sandbox",
        SimpleNamespace(
            spawn=lambda *_args: (_ for _ in ()).throw(modal_exc.ExecutionError("not hydrated")),
            local=main.drain_controller_sandbox.local,
        ),
    )
    monkeypatch.setattr(
        main,
        "_drain_controller_sandbox_sync",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("rollout store unavailable")),
    )
    monkeypatch.setattr(
        main,
        "update_controller_service",
        lambda sandbox_id, payload: updates.append((sandbox_id, dict(payload))),
    )

    result = main._schedule_controller_drain("sb-a", 1, 123)

    assert result == {
        "ok": False,
        "status": "drain_failed",
        "sandbox_id": "sb-a",
        "error": "rollout store unavailable",
        "mode": "inline",
    }
    assert updates == [
        (
            "sb-a",
            {
                "drain_mode": "inline",
                "drain_failure_reason": "rollout store unavailable",
                "drain_failed_at": updates[0][1]["drain_failed_at"],
            },
        )
    ]


def test_drain_controller_sandbox_clears_rollback_target_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _FakeSyncSandbox("sb-drain", "https://drain.internal")
    pointer_updates: list[dict[str, object]] = []

    monkeypatch.setattr(main, "get_controller_service", lambda sandbox_id: {"status": "draining"})
    monkeypatch.setattr(
        main, "get_controller_inflight", lambda sandbox_id: {"sandbox_id": sandbox_id, "total": 0}
    )
    monkeypatch.setattr(main.modal.Sandbox, "from_id", lambda sandbox_id: sandbox)
    monkeypatch.setattr(main, "update_controller_service", lambda sandbox_id, payload: payload)
    monkeypatch.setattr(
        main,
        "get_active_controller_pointer",
        lambda: {
            "active_generation": 2,
            "sandbox_id": "sb-active",
            "service_url": "https://active.internal",
            "rollback_target_generation": 1,
            "rollback_target_sandbox_id": "sb-drain",
        },
    )
    monkeypatch.setattr(
        main,
        "set_active_controller_pointer",
        lambda pointer: pointer_updates.append(dict(pointer)),
    )
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)

    result = main.drain_controller_sandbox.local(
        "sb-drain",
        1,
        int(time.time()) + 5,
    )

    assert result["ok"] is True
    assert pointer_updates == [
        {
            "active_generation": 2,
            "sandbox_id": "sb-active",
            "service_url": "https://active.internal",
        }
    ]


def test_drain_controller_sandbox_records_execution_call_id_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _FakeSyncSandbox("sb-drain", "https://drain.internal")
    updates: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(main, "_current_modal_function_call_id", lambda: "fc-executed")
    monkeypatch.setattr(main, "get_controller_service", lambda sandbox_id: {"status": "draining"})
    monkeypatch.setattr(
        main,
        "get_controller_inflight",
        lambda sandbox_id: {"sandbox_id": sandbox_id, "total": 0},
    )
    monkeypatch.setattr(main.modal.Sandbox, "from_id", lambda sandbox_id: sandbox)
    monkeypatch.setattr(
        main,
        "update_controller_service",
        lambda sandbox_id, payload: updates.append((sandbox_id, dict(payload))),
    )
    monkeypatch.setattr(main, "get_active_controller_pointer", lambda: None)
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)

    result = main.drain_controller_sandbox.local("sb-drain", 1, int(time.time()) + 5)

    assert result["drain_call_id"] == "fc-executed"
    assert updates == [
        (
            "sb-drain",
            {
                "status": "terminated",
                "terminated_at": updates[0][1]["terminated_at"],
                "drain_timeout_reached": False,
                "inflight_at_termination": {"sandbox_id": "sb-drain", "total": 0},
                "expected_generation": 1,
                "drain_execution_call_id": "fc-executed",
            },
        )
    ]


def test_resolve_controller_route_for_session_prefers_draining_session_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _FakeAsyncSandbox("sb-drain", "https://drain.internal")

    async def _from_id_aio(sandbox_id: str):
        assert sandbox_id == "sb-drain"
        return sandbox

    monkeypatch.setattr(main.modal.Sandbox, "from_id", _AsyncOnlyMethod(_from_id_aio))
    monkeypatch.setattr(
        main,
        "list_session_controller_routes",
        lambda session_id: [
            {"session_id": session_id, "sandbox_id": "sb-drain", "active_requests": 1}
        ],
    )
    monkeypatch.setattr(
        main,
        "get_controller_service",
        lambda sandbox_id: {
            "sandbox_id": sandbox_id,
            "service_url": "https://drain.internal",
            "sandbox_session_secret": "drain-secret",
            "status": "draining",
        },
    )
    monkeypatch.setattr(
        main, "get_controller_request_admission", lambda **_kwargs: {"admissible": True}
    )

    resolved_sandbox, url = anyio.run(main._resolve_controller_route_for_session_aio, "sess-1")

    assert resolved_sandbox is sandbox
    assert url == "https://drain.internal"


def test_resolve_controller_route_for_session_skips_terminated_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback = _FakeAsyncSandbox("sb-active", "https://active.internal")

    async def _from_id_aio(_sandbox_id: str):
        raise AssertionError("terminated route should not be attached")

    async def _fallback_route(*, session_id: str | None = None):
        assert session_id == "sess-1"
        return fallback, "https://active.internal"

    monkeypatch.setattr(main.modal.Sandbox, "from_id", _AsyncOnlyMethod(_from_id_aio))
    monkeypatch.setattr(
        main,
        "list_session_controller_routes",
        lambda session_id: [
            {"session_id": session_id, "sandbox_id": "sb-dead", "active_requests": 1}
        ],
    )
    monkeypatch.setattr(
        main,
        "get_controller_service",
        lambda sandbox_id: {
            "sandbox_id": sandbox_id,
            "service_url": "https://dead.internal",
            "sandbox_session_secret": "dead-secret",
            "status": "terminated",
        },
    )
    monkeypatch.setattr(main, "get_or_start_background_sandbox_aio", _fallback_route)

    resolved_sandbox, url = anyio.run(main._resolve_controller_route_for_session_aio, "sess-1")

    assert resolved_sandbox is fallback
    assert url == "https://active.internal"


def test_prewarm_target_requires_active_pointer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "get_active_controller_pointer", lambda: None)

    assert main._prewarm_target_is_admissible("sb-old") is False


def test_get_or_start_background_sandbox_attaches_pointer_when_bootstrap_loses_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _FakeSyncSandbox("sb-created", "https://created.internal")
    attached = _FakeSyncSandbox("sb-active", "https://active.internal")
    cleanup_calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        main,
        "acquire_rollout_lock",
        lambda _operation_id: {"acquired": True, "entry": {"operation_id": "bootstrap-lock"}},
    )
    monkeypatch.setattr(main, "release_rollout_lock", lambda _operation_id: True)
    monkeypatch.setattr(main, "_ensure_active_pointer_from_registry", lambda: None)
    monkeypatch.setattr(main, "_get_persist_volume", lambda: object())
    monkeypatch.setattr(main, "_current_image_version_id", lambda: "img-current")
    monkeypatch.setattr(main.modal.App, "lookup", lambda *args, **kwargs: object())
    monkeypatch.setattr(main.modal.Sandbox, "create", lambda *args, **kwargs: created)
    monkeypatch.setattr(main, "_wait_for_service_or_raise_readiness_timeout", lambda **kwargs: None)
    monkeypatch.setattr(main, "_resolve_sandbox_session_secret", lambda **kwargs: "scoped-secret")
    monkeypatch.setattr(main, "_remember_sandbox_session_secret", lambda **kwargs: None)
    monkeypatch.setattr(main, "_set_background_sandbox_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        main,
        "_register_bootstrap_controller_as_active",
        lambda **kwargs: {
            "active_generation": 1,
            "sandbox_id": "sb-active",
            "service_url": "https://active.internal",
        },
    )
    monkeypatch.setattr(
        main,
        "_cleanup_bootstrap_loser",
        lambda *, sandbox, claimed_from_pool: cleanup_calls.append(
            (sandbox.object_id, claimed_from_pool)
        ),
    )
    monkeypatch.setattr(
        main,
        "_get_or_attach_active_controller_from_pointer",
        lambda pointer: (attached, str(pointer["service_url"])),
    )

    sandbox, url = main.get_or_start_background_sandbox()

    assert sandbox is attached
    assert url == "https://active.internal"
    assert cleanup_calls == [("sb-created", False)]


def test_get_or_start_background_sandbox_bootstraps_after_stale_registry_recovery_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeModalDict()
    _bind_rollout_store(monkeypatch, store)
    stale = _FakeSyncSandbox("sb-stale", "https://stale.internal")
    created = _FakeSyncSandbox("sb-new", "https://new.internal")

    monkeypatch.setattr(
        main,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
            enable_warm_pool=False,
            enable_session_snapshots=False,
            service_timeout=1,
        ),
    )
    monkeypatch.setattr(main, "_get_persist_volume", lambda: object())
    monkeypatch.setattr(main.modal.App, "lookup", lambda *args, **kwargs: object())
    monkeypatch.setattr(main.modal.Sandbox, "create", lambda *args, **kwargs: created)
    monkeypatch.setattr(
        main.modal.Sandbox,
        "from_id",
        lambda sandbox_id: stale if sandbox_id == "sb-stale" else created,
    )
    monkeypatch.setattr(
        main,
        "_resolve_sandbox_session_secret",
        lambda *, sandbox_id, secret=None: secret or f"secret-{sandbox_id}",
    )
    monkeypatch.setattr(main, "_remember_sandbox_session_secret", lambda **kwargs: None)
    monkeypatch.setattr(main, "_record_controller_service_legacy_metadata", lambda **kwargs: None)

    def _wait_ready(
        *,
        sandbox,
        service_url: str,
        timeout_seconds: int,
        phase: str,
        startup_attempt: int,
        recycle_allowed: bool,
        from_warm_pool: bool = False,
    ) -> None:
        if getattr(sandbox, "object_id", None) == "sb-stale":
            raise main._SandboxStartupRetryableError(
                sandbox=sandbox,
                service_url=service_url,
                phase=phase,
                startup_attempt=startup_attempt,
                recycle_allowed=recycle_allowed,
                from_warm_pool=from_warm_pool,
                detail="stale attach readiness",
            )

    monkeypatch.setattr(main, "_wait_for_service_or_raise_readiness_timeout", _wait_ready)

    rollout.upsert_controller_service(
        {
            "generation": 1,
            "sandbox_id": "sb-stale",
            "sandbox_name": "svc-stale",
            "service_url": "https://stale.internal",
            "image_version": "img-stale",
            "status": "active",
            "created_at": 1,
            "promoted_at": 2,
            "last_verified_readiness_at": 2,
            "sandbox_session_secret": "stale-secret",
        }
    )

    sandbox, url = main.get_or_start_background_sandbox()

    assert sandbox.object_id == "sb-new"
    assert url == "https://new.internal"
    assert rollout.get_active_controller_pointer()["sandbox_id"] == "sb-new"
    assert rollout.get_controller_service("sb-stale")["status"] == "failed"
    assert "stale attach readiness" in str(
        rollout.get_controller_service("sb-stale")["failure_reason"]
    )
    assert rollout.get_controller_service("sb-new")["status"] == "active"


def test_get_or_attach_active_controller_from_pointer_invalidates_stale_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pointer = {
        "active_generation": 2,
        "sandbox_id": "sb-stale",
        "service_url": "https://stale.internal",
    }
    invalidations: list[tuple[dict[str, object], str]] = []
    sandbox = _FakeSyncSandbox("sb-stale", "https://stale.internal")

    monkeypatch.setattr(main.modal.Sandbox, "from_id", lambda sandbox_id: sandbox)
    monkeypatch.setattr(
        main,
        "_wait_for_service_or_raise_readiness_timeout",
        lambda **kwargs: (_ for _ in ()).throw(
            main._SandboxStartupRetryableError(
                sandbox=sandbox,
                service_url="https://stale.internal",
                phase="attach_active_pointer",
                startup_attempt=1,
                recycle_allowed=False,
                detail="stale pointer",
            )
        ),
    )
    monkeypatch.setattr(
        main,
        "_invalidate_stale_active_controller_pointer",
        lambda *, pointer, reason: invalidations.append((dict(pointer), reason)),
    )

    with pytest.raises(main._StaleActiveControllerPointerError, match="stale"):
        main._get_or_attach_active_controller_from_pointer(pointer)

    assert invalidations and invalidations[0][0]["sandbox_id"] == "sb-stale"


def test_get_or_attach_active_controller_from_pointer_aio_invalidates_stale_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pointer = {
        "active_generation": 2,
        "sandbox_id": "sb-stale",
        "service_url": "https://stale.internal",
    }
    invalidations: list[tuple[dict[str, object], str]] = []
    sandbox = _FakeAsyncSandbox("sb-stale", "https://stale.internal")

    async def _from_id_aio(_sandbox_id: str):
        return sandbox

    async def _wait_ready(**_kwargs):
        raise main._SandboxStartupRetryableError(
            sandbox=sandbox,
            service_url="https://stale.internal",
            phase="attach_active_pointer",
            startup_attempt=1,
            recycle_allowed=False,
            detail="stale pointer",
        )

    async def _run_blocking(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(main.modal.Sandbox, "from_id", _AsyncOnlyMethod(_from_id_aio))
    monkeypatch.setattr(main, "_wait_for_service_or_raise_readiness_timeout_aio", _wait_ready)
    monkeypatch.setattr(main, "_run_blocking_modal_call", _run_blocking)
    monkeypatch.setattr(
        main,
        "_invalidate_stale_active_controller_pointer",
        lambda *, pointer, reason: invalidations.append((dict(pointer), reason)),
    )

    async def _run() -> None:
        with pytest.raises(main._StaleActiveControllerPointerError, match="stale"):
            await main._get_or_attach_active_controller_from_pointer_aio(pointer)

    anyio.run(_run)

    assert invalidations and invalidations[0][0]["sandbox_id"] == "sb-stale"


def test_ensure_active_pointer_from_registry_fails_closed_when_registry_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "get_active_controller_pointer", lambda: None)
    monkeypatch.setattr(
        main,
        "list_controller_services",
        lambda *, statuses=None: [
            {
                "generation": 3,
                "sandbox_id": "sb-3",
                "service_url": "https://three.internal",
                "status": "active",
            },
            {
                "generation": 2,
                "sandbox_id": "sb-2",
                "service_url": "https://two.internal",
                "status": "active",
            },
        ],
    )

    with pytest.raises(RuntimeError, match="multiple active services"):
        main._ensure_active_pointer_from_registry()


def test_register_bootstrap_controller_as_active_returns_existing_pointer_after_commit_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _FakeSyncSandbox("sb-created", "https://created.internal")
    updates: list[tuple[str, dict[str, object]]] = []
    winner_pointer = {
        "active_generation": 1,
        "sandbox_id": "sb-winner",
        "service_url": "https://winner.internal",
    }
    pointer_reads = {"count": 0}

    def _pointer() -> dict[str, object] | None:
        pointer_reads["count"] += 1
        if pointer_reads["count"] == 1:
            return None
        return winner_pointer

    monkeypatch.setattr(main, "get_active_controller_pointer", _pointer)
    monkeypatch.setattr(main, "upsert_controller_service", lambda entry: dict(entry))
    monkeypatch.setattr(
        main,
        "_persist_active_controller_pointer",
        lambda **kwargs: (_ for _ in ()).throw(
            main._RolloutCommitRejectedError("active generation advanced")
        ),
    )
    monkeypatch.setattr(
        main,
        "update_controller_service",
        lambda sandbox_id, payload: updates.append((sandbox_id, dict(payload))),
    )

    pointer = main._register_bootstrap_controller_as_active(
        sandbox=sandbox,
        service_url="https://created.internal",
        sandbox_name="svc-created",
        sandbox_session_secret="secret",
        source="created",
        bootstrap_operation_id="bootstrap-1",
    )

    assert pointer == winner_pointer
    assert updates == [
        (
            "sb-created",
            {
                "status": "failed",
                "failed_at": updates[0][1]["failed_at"],
                "failure_reason": "active generation advanced",
            },
        )
    ]


def test_get_or_start_background_sandbox_fails_closed_on_rollout_store_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main,
        "_ensure_active_pointer_from_registry",
        lambda: (_ for _ in ()).throw(RuntimeError("store unavailable")),
    )
    monkeypatch.setattr(
        main.modal.Sandbox,
        "create",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not create sandbox")),
    )

    with pytest.raises(RuntimeError, match="store unavailable"):
        main.get_or_start_background_sandbox()


def test_get_or_start_background_sandbox_aio_bootstraps_after_stale_registry_recovery_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeModalDict()
    _bind_rollout_store(monkeypatch, store)
    stale = _FakeAsyncSandbox("sb-stale", "https://stale.internal")
    created = _FakeAsyncSandbox("sb-new", "https://new.internal")

    monkeypatch.setattr(
        main,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
            enable_warm_pool=False,
            enable_session_snapshots=False,
            service_timeout=1,
        ),
    )

    async def _lookup_aio(*args, **kwargs):
        return object()

    async def _create_aio(*args, **kwargs):
        return created

    async def _from_id_aio(sandbox_id: str):
        return stale if sandbox_id == "sb-stale" else created

    async def _wait_ready(
        *,
        sandbox,
        service_url: str,
        timeout_seconds: int,
        phase: str,
        startup_attempt: int,
        recycle_allowed: bool,
        from_warm_pool: bool = False,
    ) -> None:
        if getattr(sandbox, "object_id", None) == "sb-stale":
            raise main._SandboxStartupRetryableError(
                sandbox=sandbox,
                service_url=service_url,
                phase=phase,
                startup_attempt=startup_attempt,
                recycle_allowed=recycle_allowed,
                from_warm_pool=from_warm_pool,
                detail="stale attach readiness",
            )

    async def _run_blocking(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def _image_version_aio() -> str:
        return "img-current"

    monkeypatch.setattr(main.modal.App, "lookup", _AsyncOnlyMethod(_lookup_aio))
    monkeypatch.setattr(main.modal.Sandbox, "create", _AsyncOnlyMethod(_create_aio))
    monkeypatch.setattr(main.modal.Sandbox, "from_id", _AsyncOnlyMethod(_from_id_aio))
    monkeypatch.setattr(main, "_wait_for_service_or_raise_readiness_timeout_aio", _wait_ready)
    monkeypatch.setattr(main, "_run_blocking_modal_call", _run_blocking)
    monkeypatch.setattr(main, "_get_persist_volume", lambda: object())
    monkeypatch.setattr(main, "_current_image_version_id", lambda: "img-current")
    monkeypatch.setattr(main, "_current_image_version_id_aio", _image_version_aio)
    monkeypatch.setattr(
        main,
        "_resolve_sandbox_session_secret",
        lambda *, sandbox_id, secret=None: secret or f"secret-{sandbox_id}",
    )
    monkeypatch.setattr(main, "_remember_sandbox_session_secret", lambda **kwargs: None)
    monkeypatch.setattr(main, "_record_controller_service_legacy_metadata", lambda **kwargs: None)

    rollout.upsert_controller_service(
        {
            "generation": 1,
            "sandbox_id": "sb-stale",
            "sandbox_name": "svc-stale",
            "service_url": "https://stale.internal",
            "image_version": "img-stale",
            "status": "active",
            "created_at": 1,
            "promoted_at": 2,
            "last_verified_readiness_at": 2,
            "sandbox_session_secret": "stale-secret",
        }
    )

    sandbox, url = anyio.run(main.get_or_start_background_sandbox_aio)

    assert sandbox.object_id == "sb-new"
    assert url == "https://new.internal"
    assert rollout.get_active_controller_pointer()["sandbox_id"] == "sb-new"
    assert rollout.get_controller_service("sb-stale")["status"] == "failed"
    assert "stale attach readiness" in str(
        rollout.get_controller_service("sb-stale")["failure_reason"]
    )
    assert rollout.get_controller_service("sb-new")["status"] == "active"


def test_persist_active_controller_pointer_rejects_lost_rollout_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main,
        "acquire_promotion_commit",
        lambda **kwargs: {"acquired": True, "entry": dict(kwargs)},
    )
    monkeypatch.setattr(main, "release_promotion_commit", lambda **kwargs: True)
    monkeypatch.setattr(main, "rollout_lock_owned_by", lambda _operation_id: False)
    monkeypatch.setattr(
        main,
        "set_active_controller_pointer",
        lambda pointer: (_ for _ in ()).throw(AssertionError("pointer should not be written")),
    )

    with pytest.raises(main._RolloutCommitRejectedError, match="lost rollout lock"):
        main._persist_active_controller_pointer(
            generation=2,
            sandbox_id="sb-b",
            sandbox_name="svc-b",
            service_url="https://b.internal",
            image_version="img-b",
            last_verified_readiness_at=123,
            promoted_at=124,
            rollout_operation_id="rollout-1",
            expected_previous_generation=1,
            expected_previous_sandbox_id="sb-a",
        )


def test_persist_active_controller_pointer_rejects_advanced_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main,
        "acquire_promotion_commit",
        lambda **kwargs: {"acquired": True, "entry": dict(kwargs)},
    )
    monkeypatch.setattr(main, "release_promotion_commit", lambda **kwargs: True)
    monkeypatch.setattr(main, "rollout_lock_owned_by", lambda _operation_id: True)
    monkeypatch.setattr(main, "promotion_commit_owned_by", lambda **kwargs: True)
    monkeypatch.setattr(
        main,
        "get_active_controller_pointer",
        lambda: {
            "active_generation": 2,
            "sandbox_id": "sb-b",
            "service_url": "https://b.internal",
        },
    )
    monkeypatch.setattr(
        main,
        "set_active_controller_pointer",
        lambda pointer: (_ for _ in ()).throw(AssertionError("pointer should not be written")),
    )

    with pytest.raises(main._RolloutCommitRejectedError, match="unexpected active generation"):
        main._persist_active_controller_pointer(
            generation=2,
            sandbox_id="sb-stale",
            sandbox_name="svc-stale",
            service_url="https://stale.internal",
            image_version="img-stale",
            last_verified_readiness_at=123,
            promoted_at=124,
            rollout_operation_id="rollout-old",
            expected_previous_generation=1,
            expected_previous_sandbox_id="sb-a",
        )


def test_persist_active_controller_pointer_allows_only_one_concurrent_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeModalDict()
    _bind_rollout_store(monkeypatch, store)
    rollout.set_active_controller_pointer(
        {
            "active_generation": 1,
            "sandbox_id": "sb-a",
            "sandbox_name": "svc-a",
            "service_url": "https://a.internal",
            "image_version": "img-a",
        }
    )
    monkeypatch.setattr(main, "rollout_lock_owned_by", lambda _operation_id: True)

    base_set = rollout.set_active_controller_pointer
    writes: list[dict[str, object]] = []
    winner_ready = threading.Event()
    loser_finished = threading.Event()
    start_barrier = threading.Barrier(2)

    def _controlled_set(pointer: dict[str, object]) -> dict[str, object]:
        writes.append(dict(pointer))
        winner_ready.set()
        assert loser_finished.wait(timeout=2), "loser did not finish while winner held commit slot"
        return base_set(pointer)

    monkeypatch.setattr(main, "set_active_controller_pointer", _controlled_set)

    outcomes: dict[str, tuple[str, object]] = {}

    def _writer(name: str) -> None:
        try:
            start_barrier.wait(timeout=2)
            pointer = main._persist_active_controller_pointer(
                generation=2,
                sandbox_id=f"sb-{name}",
                sandbox_name=f"svc-{name}",
                service_url=f"https://{name}.internal",
                image_version=f"img-{name}",
                last_verified_readiness_at=123,
                promoted_at=124,
                rollout_operation_id=f"rollout-{name}",
                expected_previous_generation=1,
                expected_previous_sandbox_id="sb-a",
            )
            outcomes[name] = ("ok", pointer)
        except Exception as exc:  # pragma: no cover - exercised by assertions below
            outcomes[name] = ("error", exc)
        finally:
            loser_finished.set()

    thread_a = threading.Thread(target=_writer, args=("one",))
    thread_b = threading.Thread(target=_writer, args=("two",))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert winner_ready.is_set()
    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert len(writes) == 1

    successes = [name for name, (status, _) in outcomes.items() if status == "ok"]
    failures = [value for value in outcomes.values() if value[0] == "error"]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0][1], main._RolloutCommitRejectedError)
    assert "commit slot is already owned" in str(failures[0][1])
    assert rollout.get_active_controller_pointer()["sandbox_id"] == f"sb-{successes[0]}"


def test_rollout_controller_sandbox_aborts_when_commit_slot_is_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = {
        "sandbox": _FakeSyncSandbox("sb-b", "https://b.internal"),
        "sandbox_id": "sb-b",
        "sandbox_name": "svc-b",
        "service_url": "https://b.internal",
        "sandbox_session_secret": "secret-b",
        "image_version": "img-b",
        "claimed_from_pool": False,
    }
    abort_calls: list[tuple[str, int, str]] = []

    monkeypatch.setattr(
        main,
        "acquire_rollout_lock",
        lambda _operation_id: {"acquired": True, "entry": {"operation_id": "lock-1"}},
    )
    monkeypatch.setattr(main, "release_rollout_lock", lambda _operation_id: True)
    monkeypatch.setattr(
        main,
        "_ensure_active_pointer_from_registry",
        lambda: {
            "active_generation": 1,
            "sandbox_id": "sb-a",
            "sandbox_name": "svc-a",
            "service_url": "https://a.internal",
            "image_version": "img-a",
        },
    )
    monkeypatch.setattr(
        main,
        "get_controller_service",
        lambda sandbox_id: {
            "generation": 1,
            "sandbox_id": sandbox_id,
            "sandbox_name": "svc-a",
            "service_url": "https://a.internal",
            "image_version": "img-a",
            "status": "active",
        },
    )
    monkeypatch.setattr(main, "_prepare_rollout_candidate_sync", lambda generation: candidate)
    monkeypatch.setattr(
        main,
        "_verify_rollout_candidate_sync",
        lambda **_kwargs: {
            "verified_at": 123,
            "synthetic_session_id": "__controller_rollout__gen_2",
        },
    )
    monkeypatch.setattr(
        main,
        "_persist_active_controller_pointer",
        lambda **kwargs: (_ for _ in ()).throw(
            main._RolloutCommitRejectedError(
                "Controller promotion commit slot is already owned by another rollout"
            )
        ),
    )
    monkeypatch.setattr(
        main,
        "_abort_rollout_candidate",
        lambda *, candidate, generation, error: abort_calls.append(
            (candidate["sandbox_id"], generation, str(error))
        ),
    )

    with pytest.raises(main._RolloutCommitRejectedError, match="already owned by another rollout"):
        main._rollout_controller_sandbox_sync(reason="test")

    assert abort_calls == [
        (
            "sb-b",
            2,
            "Controller promotion commit slot is already owned by another rollout",
        )
    ]


def test_drain_controller_sandbox_terminates_when_inflight_reaches_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _FakeSyncSandbox("sb-drain", "https://drain.internal")
    inflight_values = [
        {"sandbox_id": "sb-drain", "total": 1},
        {"sandbox_id": "sb-drain", "total": 0},
        {"sandbox_id": "sb-drain", "total": 0},
    ]

    monkeypatch.setattr(main, "get_controller_service", lambda sandbox_id: {"status": "draining"})
    monkeypatch.setattr(main, "get_controller_inflight", lambda sandbox_id: inflight_values.pop(0))
    monkeypatch.setattr(main.modal.Sandbox, "from_id", lambda sandbox_id: sandbox)
    monkeypatch.setattr(main, "update_controller_service", lambda sandbox_id, payload: payload)
    monkeypatch.setattr(main, "get_active_controller_pointer", lambda: None)
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)

    result = main.drain_controller_sandbox.local(
        "sb-drain",
        1,
        int(time.time()) + 5,
    )

    assert result["ok"] is True
    assert result["drain_timeout_reached"] is False
    assert sandbox.terminate_calls == [True]


def test_schedule_controller_drain_spawned_path_matches_inline_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _run(mode: str) -> dict[str, object]:
        store = _FakeModalDict()
        _bind_rollout_store(monkeypatch, store)
        sandbox = _FakeSyncSandbox("sb-drain", "https://drain.internal")
        inflight_values = [
            {"sandbox_id": "sb-drain", "total": 1},
            {"sandbox_id": "sb-drain", "total": 0},
            {"sandbox_id": "sb-drain", "total": 0},
        ]
        spawned_results: list[dict[str, object]] = []

        rollout.set_active_controller_pointer(
            {
                "active_generation": 2,
                "sandbox_id": "sb-active",
                "service_url": "https://active.internal",
                "rollback_target_generation": 1,
                "rollback_target_sandbox_id": "sb-drain",
            }
        )
        rollout.upsert_controller_service(
            {
                "generation": 1,
                "sandbox_id": "sb-drain",
                "service_url": "https://drain.internal",
                "status": "draining",
                "created_at": 1,
            }
        )
        monkeypatch.setattr(main.modal.Sandbox, "from_id", lambda sandbox_id: sandbox)
        monkeypatch.setattr(
            main, "get_controller_inflight", lambda sandbox_id: inflight_values.pop(0)
        )
        monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)

        drain_local = main.drain_controller_sandbox.local
        if mode == "spawned":
            monkeypatch.setattr(
                main,
                "drain_controller_sandbox",
                SimpleNamespace(
                    spawn=lambda *args: (
                        spawned_results.append(drain_local(*args))
                        or SimpleNamespace(object_id="fc-spawned-test")
                    ),
                    local=drain_local,
                ),
            )
        else:
            monkeypatch.setattr(
                main,
                "drain_controller_sandbox",
                SimpleNamespace(
                    spawn=lambda *_args: (_ for _ in ()).throw(
                        modal_exc.ExecutionError("not hydrated")
                    ),
                    local=drain_local,
                ),
            )

        result = main._schedule_controller_drain("sb-drain", 1, int(time.time()) + 5)
        service = dict(rollout.get_controller_service("sb-drain") or {})
        pointer = dict(rollout.get_active_controller_pointer() or {})
        for payload in (service, pointer):
            payload.pop("updated_at", None)
            payload.pop("terminated_at", None)
        return {
            "result": result,
            "service": service,
            "pointer": pointer,
            "spawned_result": spawned_results[0] if spawned_results else None,
            "terminate_calls": list(sandbox.terminate_calls),
        }

    spawned = _run("spawned")
    inline = _run("inline")

    assert spawned["result"]["ok"] is True
    assert spawned["result"]["status"] == "scheduled"
    assert spawned["result"]["mode"] == "spawned"
    assert spawned["result"]["sandbox_id"] == "sb-drain"
    assert spawned["result"]["drain_call_id"] == "fc-spawned-test"
    assert isinstance(spawned["result"]["scheduled_at"], int)
    assert spawned["spawned_result"] == {
        "ok": True,
        "status": "terminated",
        "sandbox_id": "sb-drain",
        "drain_timeout_reached": False,
        "inflight": {"sandbox_id": "sb-drain", "total": 0},
    }
    assert inline["result"] == {
        **spawned["spawned_result"],
        "mode": "inline",
    }
    for payload in (spawned["service"], inline["service"]):
        payload.pop("drain_mode", None)
        payload.pop("drain_call_id", None)
        payload.pop("drain_scheduled_at", None)
        payload.pop("drain_expected_generation", None)
        payload.pop("drain_deadline_at", None)
    assert (
        spawned["service"]
        == inline["service"]
        == {
            "generation": 1,
            "sandbox_id": "sb-drain",
            "service_url": "https://drain.internal",
            "status": "terminated",
            "created_at": 1,
            "drain_timeout_reached": False,
            "inflight_at_termination": {"sandbox_id": "sb-drain", "total": 0},
            "expected_generation": 1,
        }
    )
    assert (
        spawned["pointer"]
        == inline["pointer"]
        == {
            "active_generation": 2,
            "sandbox_id": "sb-active",
            "service_url": "https://active.internal",
        }
    )
    assert spawned["terminate_calls"] == inline["terminate_calls"] == [True]


def test_verify_rollout_candidate_uses_synthetic_query_without_store_leases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeModalDict()
    monkeypatch.setattr(rollout, "CONTROLLER_ROLLOUT", store)
    monkeypatch.setattr(main, "upsert_controller_service", rollout.upsert_controller_service)
    monkeypatch.setattr(main, "update_controller_service", rollout.update_controller_service)
    monkeypatch.setattr(main, "_add_sandbox_auth_header", lambda **kwargs: None)
    monkeypatch.setattr(main, "_current_image_version_id", lambda: "img-1")

    class _FakeResponse:
        def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
            self._payload = payload
            self.status_code = status_code

        def json(self) -> dict[str, object]:
            return dict(self._payload)

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError("bad status")

    synthetic_session_id = "__controller_rollout__gen_2"
    query_calls: list[dict[str, object]] = []

    monkeypatch.setattr(main.httpx, "get", lambda *args, **kwargs: _FakeResponse({"ok": True}))
    monkeypatch.setattr(
        main.httpx,
        "post",
        lambda *args, **kwargs: (
            query_calls.append(dict(kwargs)),
            _FakeResponse(
                {
                    "ok": True,
                    "session_id": synthetic_session_id,
                    "messages": [{"role": "assistant", "content": "controller-rollout-ready"}],
                }
            ),
        )[1],
    )

    result = main._verify_rollout_candidate_sync(
        generation=2,
        candidate={
            "sandbox_id": "sb-b",
            "sandbox_name": "svc-b",
            "service_url": "https://b.internal",
            "sandbox_session_secret": "scoped-secret",
            "image_version": "img-1",
            "source": "created",
        },
    )

    assert result["synthetic_session_id"] == synthetic_session_id
    assert rollout.list_controller_inflight_leases() == []
    assert rollout.get_session_controller_route(synthetic_session_id) is None
    assert query_calls[0]["json"]["session_id"] == synthetic_session_id


def test_claim_warm_sandbox_ignores_claim_lock_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeModalDict(
        {
            jobs._warm_pool_claim_lock_key("sb-locked"): {
                "sandbox_id": "sb-locked",
                "acquired_at": int(time.time()),
            },
            "sb-1": {
                "sandbox_id": "sb-1",
                "sandbox_name": "pool-1",
                "status": "warm",
                "claimed_at": None,
                "claimed_by": None,
            },
        }
    )
    monkeypatch.setattr(jobs, "WARM_POOL", store)

    claimed = jobs.claim_warm_sandbox(session_id="sess-1")

    assert claimed is not None
    assert claimed["sandbox_id"] == "sb-1"
    assert jobs._warm_pool_claim_lock_key("sb-1") not in store


def test_terminate_service_sandbox_defaults_to_safe_rollout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main,
        "rollout_service_sandbox",
        SimpleNamespace(local=lambda **kwargs: {"ok": True, **kwargs}),
    )

    result = main.terminate_service_sandbox.local()

    assert result == {"ok": True, "reason": "terminate_service_sandbox"}


def test_terminate_service_sandbox_immediate_path(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = _FakeSyncSandbox("sb-immediate", "https://immediate.internal")
    terminated: list[tuple[object, bool]] = []
    updates: list[tuple[str, dict[str, object]]] = []
    cleared: list[str] = []

    monkeypatch.setattr(
        main,
        "get_or_start_background_sandbox",
        lambda: (sandbox, "https://immediate.internal"),
    )
    monkeypatch.setattr(
        main,
        "_terminate_sandbox",
        lambda sb, *, wait_for_exit=False: terminated.append((sb, wait_for_exit)),
    )
    monkeypatch.setattr(
        main,
        "get_active_controller_pointer",
        lambda: {"sandbox_id": "sb-immediate"},
    )
    monkeypatch.setattr(main, "clear_active_controller_pointer", lambda: cleared.append("pointer"))
    monkeypatch.setattr(
        main,
        "update_controller_service",
        lambda sandbox_id, payload: updates.append((sandbox_id, dict(payload))),
    )
    monkeypatch.setattr(
        main,
        "_clear_background_sandbox_state",
        lambda *, expected_sandbox_id=None: cleared.append(str(expected_sandbox_id)),
    )

    result = main.terminate_service_sandbox.local(immediate=True)

    assert result == {"ok": True, "message": "Sandbox terminated, writes flushed to volume"}
    assert terminated == [(sandbox, True)]
    assert cleared == ["pointer", "sb-immediate"]
    assert updates == [
        (
            "sb-immediate",
            {
                "status": "terminated",
                "terminated_at": updates[0][1]["terminated_at"],
                "termination_reason": "immediate_terminate_service_sandbox",
            },
        )
    ]


def test_pool_status_endpoint_redacts_scoped_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
            enable_warm_pool=True,
        ),
    )
    monkeypatch.setattr(
        main,
        "get_warm_pool_status",
        lambda: {
            "total": 1,
            "warm": 1,
            "claimed": 0,
            "entries": [
                {
                    "sandbox_id": "sb-1",
                    "sandbox_name": "pool-1",
                    "sandbox_session_secret": "super-secret",
                    "status": "warm",
                }
            ],
        },
    )

    payload = anyio.run(main.pool_status_endpoint)

    assert payload["entries"][0]["has_scoped_secret"] is True
    assert "sandbox_session_secret" not in payload["entries"][0]
