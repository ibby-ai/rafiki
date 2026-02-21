"""Tests for OpenAI migration-critical settings and secret wiring."""

from __future__ import annotations

import pytest

from modal_backend.settings import settings as settings_module


def test_settings_openai_defaults() -> None:
    cfg = settings_module.Settings(internal_auth_secret="test-secret")
    assert cfg.openai_model_default == "gpt-4.1"
    assert cfg.openai_model_subagent == "gpt-4.1-mini"
    assert cfg.openai_session_db_path == "/data/openai_agents_sessions.sqlite3"
    assert cfg.openai_session_max_items == 400
    assert cfg.openai_session_compaction_keep_items == 300


def test_settings_openai_session_compaction_validation() -> None:
    with pytest.raises(ValueError, match="openai_session_max_items must be > 0 when set"):
        settings_module.Settings(
            internal_auth_secret="test-secret",
            openai_session_max_items=0,
        )

    with pytest.raises(
        ValueError,
        match="openai_session_compaction_keep_items cannot exceed openai_session_max_items",
    ):
        settings_module.Settings(
            internal_auth_secret="test-secret",
            openai_session_max_items=200,
            openai_session_compaction_keep_items=201,
        )


def test_get_modal_secrets_includes_required_openai_and_internal(
    monkeypatch,
) -> None:
    cfg = settings_module.Settings(
        internal_auth_secret="test-secret",
        enable_modal_auth_secret=False,
        enable_langsmith_tracing=False,
    )
    monkeypatch.setattr(settings_module, "get_settings", lambda: cfg)

    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_from_name(name: str, required_keys=None):
        calls.append((name, tuple(required_keys or ())))
        return {"name": name, "required_keys": required_keys}

    monkeypatch.setattr(settings_module.modal.Secret, "from_name", fake_from_name)

    secrets = settings_module.get_modal_secrets()

    assert len(secrets) == 2
    assert ("openai-secret", ("OPENAI_API_KEY",)) in calls
    assert ("internal-auth-secret", ("INTERNAL_AUTH_SECRET",)) in calls


def test_get_modal_secrets_includes_modal_auth_secret_by_default(
    monkeypatch,
) -> None:
    cfg = settings_module.Settings(
        internal_auth_secret="test-secret",
        enable_modal_auth_secret=True,
        enable_langsmith_tracing=False,
    )
    monkeypatch.setattr(settings_module, "get_settings", lambda: cfg)

    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_from_name(name: str, required_keys=None):
        calls.append((name, tuple(required_keys or ())))
        return {"name": name, "required_keys": required_keys}

    monkeypatch.setattr(settings_module.modal.Secret, "from_name", fake_from_name)

    secrets = settings_module.get_modal_secrets()

    assert len(secrets) == 3
    assert ("modal-auth-secret", ("SANDBOX_MODAL_TOKEN_ID", "SANDBOX_MODAL_TOKEN_SECRET")) in calls
