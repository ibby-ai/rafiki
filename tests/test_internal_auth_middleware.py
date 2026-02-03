"""Tests for Cloudflare internal auth middleware."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent_sandbox.config.settings import get_settings
from agent_sandbox.middleware.cloudflare_auth import INTERNAL_AUTH_HEADER, internal_auth_middleware

DEFAULT_SECRET = "super-secret"


def _build_token(
    secret: str,
    *,
    issued_at_ms: int | None = None,
    expires_at_ms: int | None = None,
    service: str = "cloudflare-worker",
) -> str:
    now_ms = int(time.time() * 1000)
    issued_at_ms = issued_at_ms if issued_at_ms is not None else now_ms
    expires_at_ms = expires_at_ms if expires_at_ms is not None else now_ms + 300_000
    payload = {
        "service": service,
        "issued_at": issued_at_ms,
        "expires_at": expires_at_ms,
    }
    payload_str = json.dumps(payload, separators=(",", ":"))
    payload_bytes = payload_str.encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    signature_b64 = base64.b64encode(signature).decode("utf-8")
    payload_b64 = base64.b64encode(payload_bytes).decode("utf-8")
    return f"{payload_b64}.{signature_b64}"


def _build_raw_token(secret: str, payload_bytes: bytes) -> str:
    signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    signature_b64 = base64.b64encode(signature).decode("utf-8")
    payload_b64 = base64.b64encode(payload_bytes).decode("utf-8")
    return f"{payload_b64}.{signature_b64}"


def _build_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(internal_auth_middleware)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/health_check")
    async def health_check():
        return {"ok": True}

    @app.get("/protected")
    async def protected(_: Request):
        return {"ok": True}

    return app


def _reset_settings(monkeypatch, **env: str) -> None:
    for key in ["INTERNAL_AUTH_SECRET"]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_requires_token_when_missing(monkeypatch):
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=DEFAULT_SECRET)
    client = TestClient(_build_app())
    response = client.get("/protected")
    assert response.status_code == 401
    assert response.json()["error"] == "Missing internal auth token"


def test_accepts_valid_token(monkeypatch):
    secret = DEFAULT_SECRET
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=secret)
    token = _build_token(secret)
    client = TestClient(_build_app())
    response = client.get("/protected", headers={INTERNAL_AUTH_HEADER: token})
    assert response.status_code == 200


def test_rejects_invalid_signature(monkeypatch):
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=DEFAULT_SECRET)
    token = _build_token("wrong-secret")
    client = TestClient(_build_app())
    response = client.get("/protected", headers={INTERNAL_AUTH_HEADER: token})
    assert response.status_code == 401
    assert response.json()["error"] == "Invalid token signature"


def test_rejects_expired_token(monkeypatch):
    secret = "super-secret"
    now_ms = int(time.time() * 1000)
    token = _build_token(secret, issued_at_ms=now_ms - 600_000, expires_at_ms=now_ms - 300_000)
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=secret)
    client = TestClient(_build_app())
    response = client.get("/protected", headers={INTERNAL_AUTH_HEADER: token})
    assert response.status_code == 401
    assert response.json()["error"] == "Token expired"


def test_health_bypasses_enforcement(monkeypatch):
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=DEFAULT_SECRET)
    client = TestClient(_build_app())
    response = client.get("/health")
    assert response.status_code == 200


def test_health_check_bypasses_enforcement(monkeypatch):
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=DEFAULT_SECRET)
    client = TestClient(_build_app())
    response = client.get("/health_check")
    assert response.status_code == 200


def test_options_bypasses_enforcement(monkeypatch):
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=DEFAULT_SECRET)
    client = TestClient(_build_app())
    response = client.options("/protected")
    assert response.status_code == 405


def test_rejects_invalid_token_format(monkeypatch):
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=DEFAULT_SECRET)
    client = TestClient(_build_app())
    response = client.get("/protected", headers={INTERNAL_AUTH_HEADER: "not-a-token"})
    assert response.status_code == 401
    assert response.json()["error"] == "Invalid token format"


def test_rejects_bearer_prefix(monkeypatch):
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=DEFAULT_SECRET)
    token = _build_token(DEFAULT_SECRET)
    client = TestClient(_build_app())
    response = client.get(
        "/protected",
        headers={INTERNAL_AUTH_HEADER: f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "Invalid token format"


def test_rejects_invalid_payload_base64(monkeypatch):
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=DEFAULT_SECRET)
    client = TestClient(_build_app())
    response = client.get("/protected", headers={INTERNAL_AUTH_HEADER: "!!!.AAAA"})
    assert response.status_code == 401
    assert response.json()["error"] == "Invalid token payload"


def test_rejects_invalid_payload_json(monkeypatch):
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=DEFAULT_SECRET)
    token = _build_raw_token(DEFAULT_SECRET, b"not-json")
    client = TestClient(_build_app())
    response = client.get("/protected", headers={INTERNAL_AUTH_HEADER: token})
    assert response.status_code == 401
    assert response.json()["error"] == "Invalid token payload"


def test_rejects_invalid_signature_base64(monkeypatch):
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=DEFAULT_SECRET)
    token = _build_token(DEFAULT_SECRET)
    payload_b64, _ = token.split(".", maxsplit=1)
    client = TestClient(_build_app())
    response = client.get("/protected", headers={INTERNAL_AUTH_HEADER: f"{payload_b64}.!!!"})
    assert response.status_code == 401
    assert response.json()["error"] == "Invalid token signature"


def test_rejects_service_mismatch(monkeypatch):
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=DEFAULT_SECRET)
    token = _build_token(DEFAULT_SECRET, service="other-service")
    client = TestClient(_build_app())
    response = client.get("/protected", headers={INTERNAL_AUTH_HEADER: token})
    assert response.status_code == 401
    assert response.json()["error"] == "Invalid token service"


def test_rejects_future_issued_at(monkeypatch):
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=DEFAULT_SECRET)
    now_ms = int(time.time() * 1000)
    token = _build_token(
        DEFAULT_SECRET,
        issued_at_ms=now_ms + 120_000,
        expires_at_ms=now_ms + 420_000,
    )
    client = TestClient(_build_app())
    response = client.get("/protected", headers={INTERNAL_AUTH_HEADER: token})
    assert response.status_code == 401
    assert response.json()["error"] == "Token issued in the future"


def test_rejects_invalid_timestamp_order(monkeypatch):
    _reset_settings(monkeypatch, INTERNAL_AUTH_SECRET=DEFAULT_SECRET)
    now_ms = int(time.time() * 1000)
    token = _build_token(
        DEFAULT_SECRET,
        issued_at_ms=now_ms,
        expires_at_ms=now_ms - 1,
    )
    client = TestClient(_build_app())
    response = client.get("/protected", headers={INTERNAL_AUTH_HEADER: token})
    assert response.status_code == 401
    assert response.json()["error"] == "Invalid token timestamps"


def test_missing_secret_returns_500(monkeypatch):
    _reset_settings(monkeypatch)
    token = _build_token(DEFAULT_SECRET)
    client = TestClient(_build_app(), raise_server_exceptions=False)
    response = client.get("/protected", headers={INTERNAL_AUTH_HEADER: token})
    assert response.status_code == 500
