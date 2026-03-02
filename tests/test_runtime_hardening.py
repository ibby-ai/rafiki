"""Tests for sandbox runtime hardening helpers."""

from modal_backend.security.runtime_hardening import apply_runtime_hardening


def test_runtime_hardening_scrubs_sensitive_env(monkeypatch):
    monkeypatch.setenv("SANDBOX_DROP_PRIVILEGES", "false")
    monkeypatch.setenv("INTERNAL_AUTH_SECRET", "secret")
    monkeypatch.setenv("MODAL_TOKEN_ID", "id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "secret")

    report = apply_runtime_hardening("/data")

    assert "INTERNAL_AUTH_SECRET" in report.scrubbed_keys
    assert "MODAL_TOKEN_ID" in report.scrubbed_keys
    assert "MODAL_TOKEN_SECRET" in report.scrubbed_keys


def test_runtime_hardening_writable_roots_from_env(monkeypatch):
    monkeypatch.setenv("SANDBOX_DROP_PRIVILEGES", "false")
    monkeypatch.setenv("SANDBOX_WRITABLE_ROOTS", "/data,/tmp,/workspace")

    report = apply_runtime_hardening("/data")

    assert "/data" in report.writable_roots
    assert "/tmp" in report.writable_roots
    assert "/workspace" in report.writable_roots
