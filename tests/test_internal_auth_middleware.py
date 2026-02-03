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


def _build_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(internal_auth_middleware)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/protected")
    async def protected(_: Request):
        return {"ok": True}

    return app


def _reset_settings(monkeypatch, **env: str) -> None:
    for key in ["ENFORCE_INTERNAL_AUTH", "INTERNAL_AUTH_SECRET"]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_allows_without_token_when_not_enforced(monkeypatch):
    _reset_settings(monkeypatch)
    client = TestClient(_build_app())
    response = client.get("/protected")
    assert response.status_code == 200


def test_requires_token_when_enforced(monkeypatch):
    _reset_settings(monkeypatch, ENFORCE_INTERNAL_AUTH="true", INTERNAL_AUTH_SECRET="secret")
    client = TestClient(_build_app())
    response = client.get("/protected")
    assert response.status_code == 401
    assert response.json()["error"] == "Missing internal auth token"


def test_accepts_valid_token(monkeypatch):
    secret = "super-secret"
    _reset_settings(monkeypatch, ENFORCE_INTERNAL_AUTH="true", INTERNAL_AUTH_SECRET=secret)
    token = _build_token(secret)
    client = TestClient(_build_app())
    response = client.get("/protected", headers={INTERNAL_AUTH_HEADER: token})
    assert response.status_code == 200


def test_rejects_invalid_signature(monkeypatch):
    _reset_settings(monkeypatch, ENFORCE_INTERNAL_AUTH="true", INTERNAL_AUTH_SECRET="secret")
    token = _build_token("wrong-secret")
    client = TestClient(_build_app())
    response = client.get("/protected", headers={INTERNAL_AUTH_HEADER: token})
    assert response.status_code == 401
    assert response.json()["error"] == "Invalid token signature"


def test_rejects_expired_token(monkeypatch):
    secret = "super-secret"
    now_ms = int(time.time() * 1000)
    token = _build_token(secret, issued_at_ms=now_ms - 600_000, expires_at_ms=now_ms - 300_000)
    _reset_settings(monkeypatch, ENFORCE_INTERNAL_AUTH="true", INTERNAL_AUTH_SECRET=secret)
    client = TestClient(_build_app())
    response = client.get("/protected", headers={INTERNAL_AUTH_HEADER: token})
    assert response.status_code == 401
    assert response.json()["error"] == "Token expired"


def test_health_bypasses_enforcement(monkeypatch):
    _reset_settings(monkeypatch, ENFORCE_INTERNAL_AUTH="true", INTERNAL_AUTH_SECRET="secret")
    client = TestClient(_build_app())
    response = client.get("/health")
    assert response.status_code == 200
