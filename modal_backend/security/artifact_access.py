"""Scoped artifact access token helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

ARTIFACT_ACCESS_SERVICE = "cloudflare-worker-artifact"


class ArtifactTokenError(ValueError):
    """Raised when a scoped artifact token is invalid."""

    status_code: int

    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("utf-8")


def build_artifact_access_token(
    secret: str,
    *,
    session_id: str,
    job_id: str,
    artifact_path: str,
    ttl_ms: int = 120_000,
    token_id: str | None = None,
    now_ms: int | None = None,
) -> str:
    issued_at = int(now_ms if now_ms is not None else time.time() * 1000)
    expires_at = issued_at + max(1, ttl_ms)
    artifact_hash = hashlib.sha256(f"{job_id}:{artifact_path}".encode()).hexdigest()[:32]
    payload = {
        "service": ARTIFACT_ACCESS_SERVICE,
        "session_id": session_id,
        "job_id": job_id,
        "artifact_path": artifact_path,
        "artifact_id": artifact_hash,
        "token_id": token_id or str(uuid.uuid4()),
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    return f"{_b64encode(payload_bytes)}.{_b64encode(signature)}"


def verify_artifact_access_token(
    raw_token: str,
    *,
    secret: str,
    expected_job_id: str,
    expected_artifact_path: str,
    expected_session_id: str | None = None,
    max_ttl_seconds: int = 300,
    now_ms: int | None = None,
    skew_ms: int = 60_000,
    is_revoked: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    token = raw_token.strip()
    parts = token.split(".")
    if len(parts) != 2:
        raise ArtifactTokenError("Invalid artifact access token format", 401)

    payload_b64, signature_b64 = parts
    try:
        payload_bytes = base64.b64decode(payload_b64, validate=True)
        signature_bytes = base64.b64decode(signature_b64, validate=True)
    except binascii.Error as exc:
        raise ArtifactTokenError("Invalid artifact access token encoding", 401) from exc

    expected_signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_signature, signature_bytes):
        raise ArtifactTokenError("Invalid artifact access token signature", 401)

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactTokenError("Invalid artifact access token payload", 401) from exc
    if not isinstance(payload, dict):
        raise ArtifactTokenError("Invalid artifact access token payload", 401)

    if payload.get("service") != ARTIFACT_ACCESS_SERVICE:
        raise ArtifactTokenError("Invalid artifact token service", 401)

    try:
        issued_at = int(payload.get("issued_at"))
        expires_at = int(payload.get("expires_at"))
    except (TypeError, ValueError) as exc:
        raise ArtifactTokenError("Invalid artifact token timestamps", 401) from exc

    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    if issued_at > current_ms + skew_ms:
        raise ArtifactTokenError("Artifact token issued in the future", 401)
    if expires_at < current_ms - skew_ms:
        raise ArtifactTokenError("Artifact token expired", 401)
    if expires_at < issued_at:
        raise ArtifactTokenError("Invalid artifact token timestamps", 401)

    max_ttl_ms = max(1, max_ttl_seconds) * 1000
    if (expires_at - issued_at) > max_ttl_ms:
        raise ArtifactTokenError("Artifact token ttl exceeds limit", 401)

    if str(payload.get("job_id") or "") != expected_job_id:
        raise ArtifactTokenError("Artifact token job mismatch", 403)
    if str(payload.get("artifact_path") or "") != expected_artifact_path:
        raise ArtifactTokenError("Artifact token path mismatch", 403)

    if expected_session_id:
        payload_session = str(payload.get("session_id") or "")
        if payload_session != expected_session_id:
            raise ArtifactTokenError("Artifact token session mismatch", 403)

    token_id = str(payload.get("token_id") or "")
    if token_id and is_revoked and is_revoked(token_id):
        raise ArtifactTokenError("Artifact token revoked", 403)

    return payload
