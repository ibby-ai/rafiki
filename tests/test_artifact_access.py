"""Tests for scoped artifact access token helpers."""

from __future__ import annotations

import pytest

from modal_backend.security.artifact_access import (
    ArtifactTokenError,
    build_artifact_access_token,
    verify_artifact_access_token,
)


def test_verify_artifact_access_token_accepts_valid_token() -> None:
    token = build_artifact_access_token(
        "secret",
        session_id="sess-1",
        job_id="job-1",
        artifact_path="reports/output.json",
        ttl_ms=120_000,
        token_id="token-1",
        now_ms=1_000_000,
    )

    payload = verify_artifact_access_token(
        token,
        secret="secret",
        expected_job_id="job-1",
        expected_artifact_path="reports/output.json",
        expected_session_id="sess-1",
        now_ms=1_010_000,
    )

    assert payload["job_id"] == "job-1"
    assert payload["artifact_path"] == "reports/output.json"
    assert payload["token_id"] == "token-1"


def test_verify_artifact_access_token_rejects_tampered_signature() -> None:
    token = build_artifact_access_token(
        "secret",
        session_id="sess-1",
        job_id="job-1",
        artifact_path="reports/output.json",
        now_ms=1_000_000,
    )
    payload_b64, signature_b64 = token.split(".", maxsplit=1)
    tampered = f"{payload_b64}.{signature_b64[:-1]}A"

    with pytest.raises(ArtifactTokenError, match="Invalid artifact access token signature"):
        verify_artifact_access_token(
            tampered,
            secret="secret",
            expected_job_id="job-1",
            expected_artifact_path="reports/output.json",
            now_ms=1_010_000,
        )


def test_verify_artifact_access_token_rejects_expired_token() -> None:
    token = build_artifact_access_token(
        "secret",
        session_id="sess-1",
        job_id="job-1",
        artifact_path="reports/output.json",
        ttl_ms=5_000,
        now_ms=1_000_000,
    )

    with pytest.raises(ArtifactTokenError, match="Artifact token expired"):
        verify_artifact_access_token(
            token,
            secret="secret",
            expected_job_id="job-1",
            expected_artifact_path="reports/output.json",
            now_ms=1_100_000,
        )


def test_verify_artifact_access_token_rejects_cross_session_and_revoked() -> None:
    token = build_artifact_access_token(
        "secret",
        session_id="sess-1",
        job_id="job-1",
        artifact_path="reports/output.json",
        token_id="token-1",
        now_ms=1_000_000,
    )

    with pytest.raises(ArtifactTokenError, match="Artifact token session mismatch") as mismatch_err:
        verify_artifact_access_token(
            token,
            secret="secret",
            expected_job_id="job-1",
            expected_artifact_path="reports/output.json",
            expected_session_id="sess-2",
            now_ms=1_010_000,
        )
    assert mismatch_err.value.status_code == 403

    with pytest.raises(ArtifactTokenError, match="Artifact token revoked") as revoked_err:
        verify_artifact_access_token(
            token,
            secret="secret",
            expected_job_id="job-1",
            expected_artifact_path="reports/output.json",
            expected_session_id="sess-1",
            now_ms=1_010_000,
            is_revoked=lambda token_id: token_id == "token-1",
        )
    assert revoked_err.value.status_code == 403
