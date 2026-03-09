"""Tests for strict gateway->sandbox scoped auth header behavior."""

from __future__ import annotations

from types import SimpleNamespace

import anyio
import pytest
from fastapi import HTTPException

import modal_backend.main as main
from modal_backend.models import QueryBody, SessionStopRequest
from modal_backend.security.cloudflare_auth import INTERNAL_AUTH_HEADER, SANDBOX_SESSION_AUTH_HEADER
from modal_backend.settings.settings import Settings


class _AsyncOnlyMethod:
    def __init__(self, impl) -> None:
        self.aio = impl

    def __call__(self, *args, **kwargs):
        raise AssertionError("sync Modal interface should not be called in async flow")


def test_add_sandbox_auth_header_requires_scoped_secret(monkeypatch) -> None:
    monkeypatch.setattr(main, "_lookup_sandbox_session_secret", lambda **_kwargs: None)
    monkeypatch.setattr(
        main,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
        ),
    )

    with pytest.raises(HTTPException, match="Missing scoped sandbox auth secret"):
        main._add_sandbox_auth_header(
            headers={},
            request_path="/query",
            sandbox_id="sb-missing",
            session_id="sess-1",
        )


def test_add_sandbox_auth_header_builds_scoped_token(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "_lookup_sandbox_session_secret",
        lambda **_kwargs: "sandbox-secret",
    )
    monkeypatch.setattr(
        main,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
            sandbox_session_token_ttl_seconds=120,
        ),
    )

    headers: dict[str, str] = {}
    main._add_sandbox_auth_header(
        headers=headers,
        request_path="/query",
        sandbox_id="sb-123",
        session_id="sess-1",
    )

    assert SANDBOX_SESSION_AUTH_HEADER in headers
    assert headers["X-Sandbox-Id"] == "sb-123"
    assert INTERNAL_AUTH_HEADER not in headers


def test_sandbox_runtime_env_propagates_scoped_token_ttl(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
            sandbox_session_token_ttl_seconds=45,
        ),
    )

    env = main._sandbox_runtime_env("sandbox-secret")
    assert env["SANDBOX_SESSION_TOKEN_TTL_SECONDS"] == "45"


def test_lookup_sandbox_secret_ignores_mismatched_prewarm_secret(monkeypatch) -> None:
    main._SANDBOX_SESSION_SECRET_CACHE.clear()
    monkeypatch.setattr(
        main,
        "SESSIONS",
        {
            main.SANDBOX_NAME: {
                "id": "sb-real",
                "sandbox_session_secret": "real-secret",
            }
        },
    )
    monkeypatch.setattr(main, "get_warm_pool_entries", lambda: [])

    resolved = main._lookup_sandbox_session_secret(
        sandbox_id="sb-real",
        prewarm_claimed={
            "sandbox_id": "sb-stale",
            "sandbox_session_secret": "stale-secret",
        },
    )
    assert resolved == "real-secret"


def test_lookup_sandbox_secret_uses_in_memory_cache(monkeypatch) -> None:
    main._SANDBOX_SESSION_SECRET_CACHE.clear()
    main._remember_sandbox_session_secret(
        sandbox_id="sb-cache",
        secret="cache-secret",
    )
    monkeypatch.setattr(main, "SESSIONS", {})
    monkeypatch.setattr(main, "get_warm_pool_entries", lambda: [])

    resolved = main._lookup_sandbox_session_secret(sandbox_id="sb-cache")
    assert resolved == "cache-secret"


def test_pool_status_reports_scoped_secret_transition_signals(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "get_warm_pool_status",
        lambda: {
            "total": 2,
            "warm": 1,
            "claimed": 1,
            "entries": [
                {"sandbox_id": "sb-1", "sandbox_session_secret": "secret-1"},
                {"sandbox_id": "sb-2", "sandbox_session_secret": ""},
            ],
        },
    )
    monkeypatch.setattr(
        main,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
            enable_warm_pool=True,
        ),
    )

    payload = anyio.run(main.pool_status_endpoint)

    assert payload["ok"] is True
    assert payload["missing_scoped_secret_count"] == 1
    assert payload["scoped_secret_transition_stable"] is False
    assert "legacy_fallback_enabled" not in payload
    assert "legacy_fallback_cutoff_epoch_ms" not in payload


def test_stop_session_immediate_uses_scoped_sandbox_auth(monkeypatch) -> None:
    captured_headers: dict[str, str] = {}
    captured_url: str | None = None

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, bool]:
            return {"interrupted": True}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, json: dict, headers: dict | None = None):
            nonlocal captured_url
            captured_url = url
            captured_headers.update(headers or {})
            return _FakeResponse()

    class _FakeSandbox:
        object_id = "sb-stop-1"

    async def _fake_get_or_start_background_sandbox_aio(*, session_id: str | None = None):
        assert session_id == "sess-stop-1"
        return _FakeSandbox(), "https://sandbox.internal"

    monkeypatch.setattr(
        main,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
            enable_session_cancellation=True,
        ),
    )
    monkeypatch.setattr(main, "_lookup_sandbox_session_secret", lambda **_kwargs: "sandbox-secret")
    monkeypatch.setattr(main, "get_session_cancellation", lambda _session_id: None)
    monkeypatch.setattr(
        main,
        "cancel_session",
        lambda **kwargs: {
            "status": "requested",
            "requested_at": 1,
            "expires_at": 2,
            "reason": kwargs.get("reason"),
            "requested_by": kwargs.get("requested_by"),
        },
    )
    monkeypatch.setattr(
        main, "get_or_start_background_sandbox_aio", _fake_get_or_start_background_sandbox_aio
    )
    monkeypatch.setattr(main.httpx, "AsyncClient", _FakeAsyncClient)

    response = anyio.run(
        main.stop_session,
        "sess-stop-1",
        SessionStopRequest(mode="immediate", reason="test", requested_by="user"),
    )

    assert response.ok is True
    assert response.message == "Session interrupted immediately."
    assert captured_url == "https://sandbox.internal/session/sess-stop-1/stop"
    assert SANDBOX_SESSION_AUTH_HEADER in captured_headers
    assert captured_headers["X-Sandbox-Id"] == "sb-stop-1"
    assert INTERNAL_AUTH_HEADER not in captured_headers


def test_stop_session_immediate_surfaces_controller_failure(monkeypatch) -> None:
    class _FakeResponse:
        status_code = 401
        text = "Missing sandbox session auth token"

        @staticmethod
        def json() -> dict[str, bool]:
            return {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, json: dict, headers: dict | None = None):
            return _FakeResponse()

    class _FakeSandbox:
        object_id = "sb-stop-err"

    async def _fake_get_or_start_background_sandbox_aio(*, session_id: str | None = None):
        return _FakeSandbox(), "https://sandbox.internal"

    monkeypatch.setattr(
        main,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
            enable_session_cancellation=True,
        ),
    )
    monkeypatch.setattr(main, "_lookup_sandbox_session_secret", lambda **_kwargs: "sandbox-secret")
    monkeypatch.setattr(main, "get_session_cancellation", lambda _session_id: None)
    monkeypatch.setattr(
        main,
        "cancel_session",
        lambda **kwargs: {
            "status": "requested",
            "requested_at": 1,
            "expires_at": 2,
            "reason": kwargs.get("reason"),
            "requested_by": kwargs.get("requested_by"),
        },
    )
    monkeypatch.setattr(
        main, "get_or_start_background_sandbox_aio", _fake_get_or_start_background_sandbox_aio
    )
    monkeypatch.setattr(main.httpx, "AsyncClient", _FakeAsyncClient)

    response = anyio.run(
        main.stop_session,
        "sess-stop-2",
        SessionStopRequest(mode="immediate"),
    )

    assert response.ok is False
    assert response.message is not None
    assert "controller stop returned 401" in response.message


class _FakeSyncSandbox:
    def __init__(self, sandbox_id: str, service_url: str) -> None:
        self.object_id = sandbox_id
        self._service_url = service_url
        self.terminated = False

    def tunnels(self) -> dict[int, SimpleNamespace]:
        return {main.SERVICE_PORT: SimpleNamespace(url=self._service_url)}

    def poll(self):
        return None

    def terminate(self, *, wait: bool = False) -> None:
        self.terminated = True

    def set_tags(self, _tags: dict[str, str]) -> None:
        return None


class _FakeAsyncSandbox:
    def __init__(self, sandbox_id: str, service_url: str) -> None:
        self.object_id = sandbox_id
        self._service_url = service_url
        self.terminated = False
        self.tunnels = SimpleNamespace(aio=self._tunnels_aio)
        self.poll = SimpleNamespace(aio=self._poll_aio)
        self.terminate = SimpleNamespace(aio=self._terminate_aio)

    async def _tunnels_aio(self) -> dict[int, SimpleNamespace]:
        return {main.SERVICE_PORT: SimpleNamespace(url=self._service_url)}

    async def _poll_aio(self):
        return None

    async def _terminate_aio(self, *, wait: bool = False) -> None:
        self.terminated = True


def test_clear_background_sandbox_state_requires_matching_expected_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "SESSIONS", {})
    monkeypatch.setattr(main, "get_warm_pool_entries", lambda: [])
    main._SANDBOX_SESSION_SECRET_CACHE.clear()
    sandbox = _FakeSyncSandbox("sb-keep", "https://sandbox.keep")
    main._remember_sandbox_session_secret(sandbox_id="sb-keep", secret="secret-keep")
    main._set_background_sandbox_state(sandbox, "https://sandbox.keep")

    assert main._clear_background_sandbox_state(expected_sandbox_id="sb-other") is False
    cached_sb, cached_url = main._get_background_sandbox_state()
    assert cached_sb is sandbox
    assert cached_url == "https://sandbox.keep"
    assert main._lookup_sandbox_session_secret(sandbox_id="sb-keep") == "secret-keep"

    assert main._clear_background_sandbox_state(expected_sandbox_id="sb-keep") is True
    cached_sb, cached_url = main._get_background_sandbox_state()
    assert cached_sb is None
    assert cached_url is None
    assert main._lookup_sandbox_session_secret(sandbox_id="sb-keep") is None


def test_set_background_sandbox_state_evicts_replaced_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "SESSIONS", {})
    monkeypatch.setattr(main, "get_warm_pool_entries", lambda: [])
    main._set_background_sandbox_state(None, None)
    main._SANDBOX_SESSION_SECRET_CACHE.clear()

    sandbox_old = _FakeSyncSandbox("sb-old", "https://sandbox.old")
    sandbox_new = _FakeSyncSandbox("sb-new", "https://sandbox.new")
    main._remember_sandbox_session_secret(sandbox_id="sb-old", secret="old-secret")
    main._remember_sandbox_session_secret(sandbox_id="sb-new", secret="new-secret")
    main._set_background_sandbox_state(sandbox_old, "https://sandbox.old")

    assert main._lookup_sandbox_session_secret(sandbox_id="sb-old") == "old-secret"
    main._set_background_sandbox_state(sandbox_new, "https://sandbox.new")
    assert main._lookup_sandbox_session_secret(sandbox_id="sb-old") is None
    assert main._lookup_sandbox_session_secret(sandbox_id="sb-new") == "new-secret"

    main._clear_background_sandbox_state(expected_sandbox_id="sb-new")


def test_remember_sandbox_secret_enforces_cache_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "SESSIONS", {})
    monkeypatch.setattr(main, "get_warm_pool_entries", lambda: [])
    main._SANDBOX_SESSION_SECRET_CACHE.clear()
    monkeypatch.setattr(main, "_SANDBOX_SESSION_SECRET_CACHE_MAX_ENTRIES", 1)

    main._remember_sandbox_session_secret(sandbox_id="sb-old", secret="old-secret")
    main._remember_sandbox_session_secret(sandbox_id="sb-new", secret="new-secret")

    assert main._lookup_sandbox_session_secret(sandbox_id="sb-old") is None
    assert main._lookup_sandbox_session_secret(sandbox_id="sb-new") == "new-secret"


def test_get_or_start_background_sandbox_retries_once_after_readiness_timeout(monkeypatch) -> None:
    main._set_background_sandbox_state(None, None)
    sandbox = _FakeSyncSandbox("sb-reuse", "https://sandbox.reuse")

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
    monkeypatch.setattr(main.modal.App, "lookup", lambda *args, **kwargs: object())
    monkeypatch.setattr(main.modal.Sandbox, "from_name", lambda *args, **kwargs: sandbox)
    monkeypatch.setattr(main, "_lookup_sandbox_session_secret", lambda **_kwargs: "reuse-secret")

    attempts = {"count": 0}

    def _fake_wait(
        *,
        sandbox,
        service_url: str,
        timeout_seconds: int,
        phase: str,
        startup_attempt: int,
        recycle_allowed: bool,
        from_warm_pool: bool = False,
    ) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise main._SandboxReadinessTimeoutError(
                sandbox=sandbox,
                service_url=service_url,
                phase=phase,
                startup_attempt=startup_attempt,
                recycle_allowed=recycle_allowed,
                from_warm_pool=from_warm_pool,
            )

    monkeypatch.setattr(main, "_wait_for_service_or_raise_readiness_timeout", _fake_wait)

    returned_sb, returned_url = main.get_or_start_background_sandbox()

    assert returned_sb is sandbox
    assert returned_url == "https://sandbox.reuse"
    assert attempts["count"] == 2


def test_get_or_start_background_sandbox_fails_after_second_timeout(monkeypatch) -> None:
    main._set_background_sandbox_state(None, None)
    sandbox = _FakeSyncSandbox("sb-timeout", "https://sandbox.timeout")

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
    monkeypatch.setattr(main.modal.App, "lookup", lambda *args, **kwargs: object())
    monkeypatch.setattr(main.modal.Sandbox, "from_name", lambda *args, **kwargs: sandbox)
    monkeypatch.setattr(main, "_lookup_sandbox_session_secret", lambda **_kwargs: "reuse-secret")

    attempts = {"count": 0}

    def _always_timeout(
        *,
        sandbox,
        service_url: str,
        timeout_seconds: int,
        phase: str,
        startup_attempt: int,
        recycle_allowed: bool,
        from_warm_pool: bool = False,
    ) -> None:
        attempts["count"] += 1
        raise main._SandboxReadinessTimeoutError(
            sandbox=sandbox,
            service_url=service_url,
            phase=phase,
            startup_attempt=startup_attempt,
            recycle_allowed=recycle_allowed,
            from_warm_pool=from_warm_pool,
        )

    monkeypatch.setattr(main, "_wait_for_service_or_raise_readiness_timeout", _always_timeout)

    with pytest.raises(TimeoutError, match="failed after 2 attempts"):
        main.get_or_start_background_sandbox()

    assert attempts["count"] == 2


def test_get_or_start_background_sandbox_aio_retries_once_after_timeout(monkeypatch) -> None:
    main._set_background_sandbox_state(None, None)
    sandbox = _FakeAsyncSandbox("sb-async", "https://sandbox.async")

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

    async def _from_name_aio(*args, **kwargs):
        return sandbox

    monkeypatch.setattr(main.modal.App, "lookup", _AsyncOnlyMethod(_lookup_aio))
    monkeypatch.setattr(main.modal.Sandbox, "from_name", _AsyncOnlyMethod(_from_name_aio))
    monkeypatch.setattr(main, "_lookup_sandbox_session_secret", lambda **_kwargs: "reuse-secret")

    attempts = {"count": 0}

    async def _fake_wait_async(
        *,
        sandbox,
        service_url: str,
        timeout_seconds: int,
        phase: str,
        startup_attempt: int,
        recycle_allowed: bool,
        from_warm_pool: bool = False,
    ) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise main._SandboxReadinessTimeoutError(
                sandbox=sandbox,
                service_url=service_url,
                phase=phase,
                startup_attempt=startup_attempt,
                recycle_allowed=recycle_allowed,
                from_warm_pool=from_warm_pool,
            )

    monkeypatch.setattr(main, "_wait_for_service_or_raise_readiness_timeout_aio", _fake_wait_async)

    returned_sb, returned_url = anyio.run(main.get_or_start_background_sandbox_aio)

    assert returned_sb is sandbox
    assert returned_url == "https://sandbox.async"
    assert attempts["count"] == 2


def test_reuse_by_name_missing_scoped_secret_falls_back_to_create(monkeypatch) -> None:
    main._set_background_sandbox_state(None, None)
    sandbox = _FakeSyncSandbox("sb-reuse-missing-secret", "https://sandbox.reuse")

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
    monkeypatch.setattr(main.modal.App, "lookup", lambda *args, **kwargs: object())
    monkeypatch.setattr(main.modal.Sandbox, "from_name", lambda *args, **kwargs: sandbox)
    monkeypatch.setattr(main, "_lookup_sandbox_session_secret", lambda **_kwargs: None)
    monkeypatch.setattr(
        main, "_wait_for_service_or_raise_readiness_timeout", lambda **_kwargs: None
    )

    create_called = {"value": False}

    def _fake_create(*args, **kwargs):
        create_called["value"] = True
        raise RuntimeError("create-fallback-triggered")

    monkeypatch.setattr(main.modal.Sandbox, "create", _fake_create)

    with pytest.raises(RuntimeError, match="create-fallback-triggered"):
        main.get_or_start_background_sandbox()

    assert create_called["value"] is True


def test_attach_missing_scoped_secret_retries_then_fails(monkeypatch) -> None:
    main._set_background_sandbox_state(None, None)
    sandbox = _FakeSyncSandbox("sb-attach-missing-secret", "https://sandbox.attach")

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
    monkeypatch.setattr(main.modal.App, "lookup", lambda *args, **kwargs: object())

    create_phase = {"active": False}

    def _fake_from_name(*args, **kwargs):
        if create_phase["active"]:
            create_phase["active"] = False
            return sandbox
        raise RuntimeError("skip-reuse-by-name")

    def _fake_create(*args, **kwargs):
        create_phase["active"] = True
        raise main.modal_exc.AlreadyExistsError("already-exists")

    monkeypatch.setattr(main.modal.Sandbox, "from_name", _fake_from_name)
    monkeypatch.setattr(main.modal.Sandbox, "create", _fake_create)
    monkeypatch.setattr(main, "_lookup_sandbox_session_secret", lambda **_kwargs: None)

    with pytest.raises(TimeoutError, match="startup failed after 2 attempts"):
        main.get_or_start_background_sandbox()

    assert sandbox.terminated is True


def test_async_warm_pool_claim_uses_poll_aio(monkeypatch) -> None:
    main._set_background_sandbox_state(None, None)

    class _FakeWarmPoolSandbox:
        object_id = "sb-warm-async"

        def __init__(self) -> None:
            self.poll = SimpleNamespace(aio=self._poll_aio)
            self.tunnels = SimpleNamespace(aio=self._tunnels_aio)
            self.set_tags = SimpleNamespace(aio=self._set_tags_aio)
            self.terminate = SimpleNamespace(aio=self._terminate_aio)
            self.poll_aio_calls = 0

        async def _poll_aio(self):
            self.poll_aio_calls += 1
            return None

        async def _tunnels_aio(self):
            return {main.SERVICE_PORT: SimpleNamespace(url="https://sandbox.warm.async")}

        async def _set_tags_aio(self, _tags):
            return None

        async def _terminate_aio(self):
            return None

    sandbox = _FakeWarmPoolSandbox()

    monkeypatch.setattr(
        main,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
            enable_warm_pool=True,
            enable_session_snapshots=False,
            service_timeout=1,
        ),
    )

    async def _lookup_aio(*args, **kwargs):
        return object()

    async def _from_name_aio(*args, **kwargs):
        raise RuntimeError("skip-reuse-by-name")

    async def _from_id_aio(_sandbox_id):
        return sandbox

    async def _spawn_aio(*args):
        return None

    monkeypatch.setattr(main.modal.App, "lookup", _AsyncOnlyMethod(_lookup_aio))
    monkeypatch.setattr(main.modal.Sandbox, "from_name", _AsyncOnlyMethod(_from_name_aio))
    monkeypatch.setattr(
        main,
        "claim_warm_sandbox",
        lambda **_kwargs: {
            "sandbox_id": "sb-warm-async",
            "sandbox_name": "warm-async-name",
            "sandbox_session_secret": "warm-secret",
        },
    )
    monkeypatch.setattr(main.modal.Sandbox, "from_id", _AsyncOnlyMethod(_from_id_aio))
    monkeypatch.setattr(
        main,
        "replenish_warm_pool",
        SimpleNamespace(spawn=SimpleNamespace(aio=_spawn_aio)),
    )

    async def _fake_wait_async(**_kwargs):
        return None

    monkeypatch.setattr(main, "_wait_for_service_or_raise_readiness_timeout_aio", _fake_wait_async)

    returned_sb, returned_url = anyio.run(main.get_or_start_background_sandbox_aio)

    assert returned_sb is sandbox
    assert returned_url == "https://sandbox.warm.async"
    assert sandbox.poll_aio_calls == 1


def test_terminate_sandbox_uses_wait_when_supported(monkeypatch) -> None:
    calls: list[bool] = []

    class _WaitSandbox:
        def terminate(self, *, wait: bool = False) -> None:
            calls.append(wait)

    monkeypatch.setattr(main, "_sandbox_terminate_supports_wait", lambda: True)

    main._terminate_sandbox(_WaitSandbox(), wait_for_exit=True)

    assert calls == [True]


def test_terminate_sandbox_async_uses_wait_when_supported(monkeypatch) -> None:
    calls: list[bool] = []

    class _WaitSandbox:
        def __init__(self) -> None:
            self.terminate = SimpleNamespace(aio=self._terminate_aio)

        async def _terminate_aio(self, *, wait: bool = False) -> None:
            calls.append(wait)

    monkeypatch.setattr(main, "_sandbox_terminate_supports_wait", lambda: True)

    async def _run() -> None:
        await main._terminate_sandbox_async(_WaitSandbox(), wait_for_exit=True)

    anyio.run(_run)

    assert calls == [True]


def test_terminate_service_sandbox_waits_for_exit(monkeypatch) -> None:
    sandbox = _FakeSyncSandbox("sb-term", "https://sandbox.term")
    captured: list[bool] = []

    monkeypatch.setattr(
        main, "get_or_start_background_sandbox", lambda: (sandbox, "https://sandbox")
    )
    monkeypatch.setattr(main, "_clear_background_sandbox_state", lambda **_kwargs: True)

    def _fake_terminate(_sandbox, *, wait_for_exit: bool = False) -> None:
        captured.append(wait_for_exit)

    monkeypatch.setattr(main, "_terminate_sandbox", _fake_terminate)

    result = main.terminate_service_sandbox.local()

    assert result["ok"] is True
    assert captured == [True]


def test_query_proxy_prewarm_uses_async_from_id_without_fallback(monkeypatch) -> None:
    calls = {"from_id": 0, "fallback": 0}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        @staticmethod
        def json() -> dict[str, object]:
            return {"ok": True, "session_id": "sess-prewarm", "messages": ["ok"]}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, json: dict, headers: dict | None = None, timeout=None):
            assert url == "https://prewarm.internal/query"
            assert headers is not None
            return _FakeResponse()

    async def _from_id_aio(_sandbox_id):
        calls["from_id"] += 1
        return SimpleNamespace(object_id="sb-prewarm")

    async def _fake_wait_async(**_kwargs):
        return None

    async def _fallback(**_kwargs):
        calls["fallback"] += 1
        return SimpleNamespace(object_id="sb-fallback"), "https://fallback.internal"

    monkeypatch.setattr(
        main,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
            enable_prewarm=True,
            enable_session_snapshots=False,
            service_timeout=1,
        ),
    )
    monkeypatch.setattr(main, "Settings", lambda: main._settings)
    monkeypatch.setattr(
        main,
        "claim_prewarm",
        lambda warm_id, claimed_by: {
            "claimed": True,
            "sandbox_id": "sb-prewarm",
            "sandbox_url": "https://prewarm.internal",
            "sandbox_session_secret": "prewarm-secret",
            "status": "claimed",
            "claimed_by": claimed_by,
            "warm_id": warm_id,
        },
    )
    monkeypatch.setattr(main.modal.Sandbox, "from_id", _AsyncOnlyMethod(_from_id_aio))
    monkeypatch.setattr(main, "_wait_for_service_or_raise_readiness_timeout_aio", _fake_wait_async)
    monkeypatch.setattr(main, "get_or_start_background_sandbox_aio", _fallback)
    monkeypatch.setattr(main.httpx, "AsyncClient", _FakeAsyncClient)

    request = SimpleNamespace(
        headers={"X-Session-History-Authority": "durable-object"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    body = QueryBody(question="ping", session_id="sess-prewarm", warm_id="warm-123")

    result = anyio.run(main.query_proxy, request, body)

    assert result["ok"] is True
    assert calls["from_id"] == 1
    assert calls["fallback"] == 0


def test_query_proxy_prewarm_timeout_marks_failed_and_falls_back(monkeypatch) -> None:
    captured_reasons: list[str] = []
    fallback_calls = {"count": 0}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        @staticmethod
        def json() -> dict[str, object]:
            return {"ok": True, "session_id": "sess-fallback", "messages": ["ok"]}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, json: dict, headers: dict | None = None, timeout=None):
            assert url == "https://fallback.internal/query"
            return _FakeResponse()

    async def _from_id_aio(_sandbox_id):
        return SimpleNamespace(object_id="sb-prewarm-timeout")

    async def _fake_wait_async(**kwargs):
        raise main._SandboxReadinessTimeoutError(
            sandbox=kwargs["sandbox"],
            service_url=kwargs["service_url"],
            phase=kwargs["phase"],
            startup_attempt=kwargs["startup_attempt"],
            recycle_allowed=kwargs["recycle_allowed"],
        )

    async def _handle_timeout(_timeout):
        return None

    async def _fallback(**_kwargs):
        fallback_calls["count"] += 1
        return SimpleNamespace(object_id="sb-fallback"), "https://fallback.internal"

    monkeypatch.setattr(
        main,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
            enable_prewarm=True,
            enable_session_snapshots=False,
            service_timeout=1,
        ),
    )
    monkeypatch.setattr(main, "Settings", lambda: main._settings)
    monkeypatch.setattr(
        main,
        "claim_prewarm",
        lambda warm_id, claimed_by: {
            "claimed": True,
            "sandbox_id": "sb-prewarm-timeout",
            "sandbox_url": "https://prewarm.internal",
            "sandbox_session_secret": "prewarm-secret",
            "status": "claimed",
            "claimed_by": claimed_by,
            "warm_id": warm_id,
        },
    )
    monkeypatch.setattr(main.modal.Sandbox, "from_id", _AsyncOnlyMethod(_from_id_aio))
    monkeypatch.setattr(main, "_wait_for_service_or_raise_readiness_timeout_aio", _fake_wait_async)
    monkeypatch.setattr(main, "_handle_readiness_timeout_async", _handle_timeout)
    monkeypatch.setattr(
        main, "mark_prewarm_failed", lambda warm_id, reason: captured_reasons.append(reason)
    )
    monkeypatch.setattr(main, "get_or_start_background_sandbox_aio", _fallback)
    monkeypatch.setattr(main, "_add_sandbox_auth_header", lambda **_kwargs: None)
    monkeypatch.setattr(main.httpx, "AsyncClient", _FakeAsyncClient)

    request = SimpleNamespace(
        headers={"X-Session-History-Authority": "durable-object"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    body = QueryBody(question="ping", session_id="sess-prewarm", warm_id="warm-timeout")

    result = anyio.run(main.query_proxy, request, body)

    assert result["ok"] is True
    assert fallback_calls["count"] == 1
    assert captured_reasons and "Readiness timeout" in captured_reasons[0]


def test_query_stream_prewarm_uses_async_from_id_without_fallback(monkeypatch) -> None:
    calls = {"from_id": 0, "fallback": 0}

    class _FakeStreamResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            yield b'event: done\ndata: {"session_id":"sess-stream"}\n\n'

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def stream(self, method: str, url: str, json: dict, headers: dict | None = None):
            assert method == "POST"
            assert url == "https://prewarm.internal/query_stream"
            assert headers is not None
            return _FakeStreamResponse()

    async def _from_id_aio(_sandbox_id):
        calls["from_id"] += 1
        return SimpleNamespace(object_id="sb-prewarm-stream")

    async def _fake_wait_async(**_kwargs):
        return None

    async def _fallback(**_kwargs):
        calls["fallback"] += 1
        return SimpleNamespace(object_id="sb-fallback-stream"), "https://fallback.internal"

    monkeypatch.setattr(
        main,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
            enable_prewarm=True,
            enable_session_snapshots=False,
            service_timeout=1,
        ),
    )
    monkeypatch.setattr(main, "Settings", lambda: main._settings)
    monkeypatch.setattr(
        main,
        "claim_prewarm",
        lambda warm_id, claimed_by: {
            "claimed": True,
            "sandbox_id": "sb-prewarm-stream",
            "sandbox_url": "https://prewarm.internal",
            "sandbox_session_secret": "prewarm-secret",
            "status": "claimed",
            "claimed_by": claimed_by,
            "warm_id": warm_id,
        },
    )
    monkeypatch.setattr(main.modal.Sandbox, "from_id", _AsyncOnlyMethod(_from_id_aio))
    monkeypatch.setattr(main, "_wait_for_service_or_raise_readiness_timeout_aio", _fake_wait_async)
    monkeypatch.setattr(main, "get_or_start_background_sandbox_aio", _fallback)
    monkeypatch.setattr(main, "_add_sandbox_auth_header", lambda **_kwargs: None)
    monkeypatch.setattr(main.httpx, "AsyncClient", _FakeAsyncClient)

    async def _run() -> list[bytes]:
        request = SimpleNamespace(
            headers={"X-Session-History-Authority": "durable-object"},
            client=SimpleNamespace(host="127.0.0.1"),
        )
        body = QueryBody(question="ping", session_id="sess-prewarm", warm_id="warm-stream")
        response = await main.query_stream(request, body)
        return [chunk async for chunk in response.body_iterator]

    chunks = anyio.run(_run)

    assert chunks == [b'event: done\ndata: {"session_id":"sess-stream"}\n\n']
    assert calls["from_id"] == 1
    assert calls["fallback"] == 0


def test_query_stream_prewarm_timeout_marks_failed_and_falls_back(monkeypatch) -> None:
    captured_reasons: list[str] = []
    fallback_calls = {"count": 0}

    class _FakeStreamResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            yield b'event: done\ndata: {"session_id":"sess-stream"}\n\n'

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def stream(self, method: str, url: str, json: dict, headers: dict | None = None):
            assert method == "POST"
            assert url == "https://fallback.internal/query_stream"
            return _FakeStreamResponse()

    async def _from_id_aio(_sandbox_id):
        return SimpleNamespace(object_id="sb-prewarm-stream")

    async def _fake_wait_async(**kwargs):
        raise main._SandboxReadinessTimeoutError(
            sandbox=kwargs["sandbox"],
            service_url=kwargs["service_url"],
            phase=kwargs["phase"],
            startup_attempt=kwargs["startup_attempt"],
            recycle_allowed=kwargs["recycle_allowed"],
        )

    async def _handle_timeout(_timeout):
        return None

    async def _fallback(**_kwargs):
        fallback_calls["count"] += 1
        return SimpleNamespace(object_id="sb-fallback-stream"), "https://fallback.internal"

    monkeypatch.setattr(
        main,
        "_settings",
        Settings(
            internal_auth_secret="internal-secret",
            enable_prewarm=True,
            enable_session_snapshots=False,
            service_timeout=1,
        ),
    )
    monkeypatch.setattr(main, "Settings", lambda: main._settings)
    monkeypatch.setattr(
        main,
        "claim_prewarm",
        lambda warm_id, claimed_by: {
            "claimed": True,
            "sandbox_id": "sb-prewarm-stream",
            "sandbox_url": "https://prewarm.internal",
            "sandbox_session_secret": "prewarm-secret",
            "status": "claimed",
            "claimed_by": claimed_by,
            "warm_id": warm_id,
        },
    )
    monkeypatch.setattr(main.modal.Sandbox, "from_id", _AsyncOnlyMethod(_from_id_aio))
    monkeypatch.setattr(main, "_wait_for_service_or_raise_readiness_timeout_aio", _fake_wait_async)
    monkeypatch.setattr(main, "_handle_readiness_timeout_async", _handle_timeout)
    monkeypatch.setattr(
        main, "mark_prewarm_failed", lambda warm_id, reason: captured_reasons.append(reason)
    )
    monkeypatch.setattr(main, "get_or_start_background_sandbox_aio", _fallback)
    monkeypatch.setattr(main, "_add_sandbox_auth_header", lambda **_kwargs: None)
    monkeypatch.setattr(main.httpx, "AsyncClient", _FakeAsyncClient)

    async def _run() -> list[bytes]:
        request = SimpleNamespace(
            headers={"X-Session-History-Authority": "durable-object"},
            client=SimpleNamespace(host="127.0.0.1"),
        )
        body = QueryBody(question="ping", session_id="sess-prewarm", warm_id="warm-stream")
        response = await main.query_stream(request, body)
        return [chunk async for chunk in response.body_iterator]

    chunks = anyio.run(_run)

    assert chunks
    assert fallback_calls["count"] == 1
    assert captured_reasons and "Readiness timeout" in captured_reasons[0]


def test_tunnel_discovery_failure_retries_then_fails(monkeypatch) -> None:
    main._set_background_sandbox_state(None, None)

    class _NoTunnelSandbox(_FakeSyncSandbox):
        def tunnels(self) -> dict[int, SimpleNamespace]:
            return {}

    sandbox = _NoTunnelSandbox("sb-no-tunnel", "https://unused")

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
    monkeypatch.setattr(main.modal.App, "lookup", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        main.modal.Sandbox,
        "from_name",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("skip-reuse-by-name")),
    )
    monkeypatch.setattr(main.modal.Sandbox, "create", lambda *args, **kwargs: sandbox)
    monkeypatch.setattr(
        main, "_wait_for_service_or_raise_readiness_timeout", lambda **_kwargs: None
    )
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)

    now = {"value": 0}

    def _fake_time() -> int:
        now["value"] += 31
        return now["value"]

    monkeypatch.setattr(main.time, "time", _fake_time)

    with pytest.raises(TimeoutError, match="startup failed after 2 attempts"):
        main.get_or_start_background_sandbox()
