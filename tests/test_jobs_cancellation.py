"""Regression tests for session cancellation helpers."""

from __future__ import annotations

from modal_backend import jobs


class _AuthFailingCancellationStore:
    def get(self, _session_id: str):  # pragma: no cover - exercised in tests below
        class AuthError(Exception):
            pass

        raise AuthError("Token missing")


def test_is_session_cancelled_returns_false_when_modal_auth_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(jobs, "SESSION_CANCELLATIONS", _AuthFailingCancellationStore())
    assert jobs.is_session_cancelled("sess-auth-missing") is False


def test_get_session_cancellation_returns_none_when_modal_auth_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(jobs, "SESSION_CANCELLATIONS", _AuthFailingCancellationStore())
    assert jobs.get_session_cancellation("sess-auth-missing") is None


def test_acknowledge_session_cancellation_returns_none_when_modal_auth_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(jobs, "SESSION_CANCELLATIONS", _AuthFailingCancellationStore())
    assert jobs.acknowledge_session_cancellation("sess-auth-missing") is None
