"""Internal authentication for Cloudflare control plane requests."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from modal_backend.settings.settings import get_settings

INTERNAL_AUTH_HEADER = "X-Internal-Auth"
INTERNAL_AUTH_SERVICE = "cloudflare-worker"
INTERNAL_AUTH_SKEW_MS = 60_000
SANDBOX_SESSION_AUTH_HEADER = "X-Sandbox-Session-Auth"
SANDBOX_SESSION_AUTH_SERVICE = "modal-http-gateway"


def _coerce_timestamp(value: Any, field: str) -> int:
    if value is None:
        raise HTTPException(status_code=401, detail=f"Missing {field}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail=f"Invalid {field}") from exc


def _b64encode_bytes(value: bytes) -> str:
    return base64.b64encode(value).decode("utf-8")


def build_internal_token(
    secret: str,
    *,
    service: str = INTERNAL_AUTH_SERVICE,
    ttl_ms: int = 300_000,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now_ms = int(time.time() * 1000)
    payload: dict[str, Any] = {
        "service": service,
        "issued_at": now_ms,
        "expires_at": now_ms + max(1, ttl_ms),
    }
    if extra_claims:
        payload.update(extra_claims)
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    return f"{_b64encode_bytes(payload_bytes)}.{_b64encode_bytes(signature)}"


def build_scoped_sandbox_token(
    secret: str,
    *,
    session_id: str | None,
    sandbox_id: str | None,
    request_path: str,
    ttl_ms: int,
) -> str:
    return build_internal_token(
        secret,
        service=SANDBOX_SESSION_AUTH_SERVICE,
        ttl_ms=ttl_ms,
        extra_claims={
            "session_id": session_id or "anonymous",
            "sandbox_id": sandbox_id or "",
            "path": request_path,
        },
    )


def verify_internal_token(raw_token: str) -> dict[str, Any]:
    """Verify internal auth token from Cloudflare Worker."""
    token = raw_token.strip()
    if token.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Invalid token format")
    parts = token.split(".")
    if len(parts) != 2:
        raise HTTPException(status_code=401, detail="Invalid token format")

    payload_b64, signature_b64 = parts
    try:
        payload_bytes = base64.b64decode(payload_b64, validate=True)
    except binascii.Error as exc:
        raise HTTPException(status_code=401, detail="Invalid token payload") from exc
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="Invalid token payload")

    if payload.get("service") != INTERNAL_AUTH_SERVICE:
        raise HTTPException(status_code=401, detail="Invalid token service")

    issued_at = _coerce_timestamp(payload.get("issued_at"), "issued_at")
    expires_at = _coerce_timestamp(payload.get("expires_at"), "expires_at")
    now_ms = int(time.time() * 1000)

    if issued_at > now_ms + INTERNAL_AUTH_SKEW_MS:
        raise HTTPException(status_code=401, detail="Token issued in the future")
    if expires_at < now_ms - INTERNAL_AUTH_SKEW_MS:
        raise HTTPException(status_code=401, detail="Token expired")
    if expires_at < issued_at:
        raise HTTPException(status_code=401, detail="Invalid token timestamps")

    settings = get_settings()
    secret = (settings.internal_auth_secret or "").strip()
    if not secret:
        raise HTTPException(status_code=500, detail="Internal auth secret not configured")

    try:
        signature_bytes = base64.b64decode(signature_b64, validate=True)
    except binascii.Error as exc:
        raise HTTPException(status_code=401, detail="Invalid token signature") from exc
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(expected_sig, signature_bytes):
        raise HTTPException(status_code=401, detail="Invalid token signature")

    return payload


def verify_scoped_sandbox_token(
    raw_token: str,
    *,
    secret: str,
    expected_path: str,
    expected_sandbox_id: str | None = None,
    max_ttl_seconds: int = 120,
) -> dict[str, Any]:
    token = raw_token.strip()
    if token.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Invalid token format")
    parts = token.split(".")
    if len(parts) != 2:
        raise HTTPException(status_code=401, detail="Invalid token format")

    payload_b64, signature_b64 = parts
    try:
        payload_bytes = base64.b64decode(payload_b64, validate=True)
    except binascii.Error as exc:
        raise HTTPException(status_code=401, detail="Invalid token payload") from exc
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="Invalid token payload")

    if payload.get("service") != SANDBOX_SESSION_AUTH_SERVICE:
        raise HTTPException(status_code=401, detail="Invalid token service")

    issued_at = _coerce_timestamp(payload.get("issued_at"), "issued_at")
    expires_at = _coerce_timestamp(payload.get("expires_at"), "expires_at")
    now_ms = int(time.time() * 1000)

    if issued_at > now_ms + INTERNAL_AUTH_SKEW_MS:
        raise HTTPException(status_code=401, detail="Token issued in the future")
    if expires_at < now_ms - INTERNAL_AUTH_SKEW_MS:
        raise HTTPException(status_code=401, detail="Token expired")
    if expires_at < issued_at:
        raise HTTPException(status_code=401, detail="Invalid token timestamps")

    ttl_ms = expires_at - issued_at
    max_ttl_ms = max(1, int(max_ttl_seconds)) * 1000
    if ttl_ms > max_ttl_ms:
        raise HTTPException(status_code=401, detail="Token ttl exceeds limit")

    token_path = payload.get("path")
    if token_path != expected_path:
        raise HTTPException(status_code=401, detail="Token path mismatch")

    token_sandbox_id = str(payload.get("sandbox_id") or "")
    if not token_sandbox_id:
        raise HTTPException(status_code=401, detail="Missing token sandbox id")
    if token_sandbox_id != expected_sandbox_id:
        raise HTTPException(status_code=401, detail="Token sandbox mismatch")

    try:
        signature_bytes = base64.b64decode(signature_b64, validate=True)
    except binascii.Error as exc:
        raise HTTPException(status_code=401, detail="Invalid token signature") from exc
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(expected_sig, signature_bytes):
        raise HTTPException(status_code=401, detail="Invalid token signature")

    return payload


async def internal_auth_middleware(request: Request, call_next):  # type: ignore[override]
    """Verify internal auth headers for Cloudflare requests."""
    if request.method == "OPTIONS":
        return await call_next(request)

    if request.url.path in {"/health", "/health_check"}:
        return await call_next(request)

    settings = get_settings()
    sandbox_secret = (settings.sandbox_session_secret or "").strip()
    scoped_token = request.headers.get(SANDBOX_SESSION_AUTH_HEADER)

    if sandbox_secret:
        if not scoped_token:
            return JSONResponse(
                status_code=401,
                content={"ok": False, "error": "Missing sandbox session auth token"},
            )
        try:
            expected_sandbox_id = (request.headers.get("X-Sandbox-Id") or "").strip()
            if not expected_sandbox_id:
                return JSONResponse(
                    status_code=401,
                    content={"ok": False, "error": "Missing sandbox id header"},
                )
            request.state.internal_auth = verify_scoped_sandbox_token(
                scoped_token,
                secret=sandbox_secret,
                expected_path=request.url.path,
                expected_sandbox_id=expected_sandbox_id,
                max_ttl_seconds=settings.sandbox_session_token_ttl_seconds,
            )
            return await call_next(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"ok": False, "error": exc.detail},
            )

    internal_auth = request.headers.get(INTERNAL_AUTH_HEADER)
    if not internal_auth:
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "Missing internal auth token"},
        )

    try:
        request.state.internal_auth = verify_internal_token(internal_auth)
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "error": exc.detail},
        )

    return await call_next(request)
