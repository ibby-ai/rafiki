"""Regression test for Cloudflare SessionAgent state cleanup.

This test is environment-gated because it exercises a live Worker endpoint.
Set the following variables to enable:

- EDGE_E2E_WORKER_URL (for example: http://localhost:8787)
- EDGE_E2E_SESSION_SIGNING_SECRET
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid

import pytest

httpx = pytest.importorskip("httpx")


def _build_session_token(secret: str, *, session_id: str) -> str:
    now_ms = int(time.time() * 1000)
    payload = {
        "user_id": "pytest-e2e-user",
        "tenant_id": "pytest-e2e-tenant",
        "session_id": session_id,
        "issued_at": now_ms,
        "expires_at": now_ms + 3_600_000,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.b64encode(payload_bytes).decode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    signature_b64 = base64.b64encode(signature).decode("utf-8")
    return f"{payload_b64}.{signature_b64}"


@pytest.mark.slow
def test_session_state_clears_current_prompt_after_query_completion() -> None:
    worker_url = os.getenv("EDGE_E2E_WORKER_URL")
    signing_secret = os.getenv("EDGE_E2E_SESSION_SIGNING_SECRET")

    if not worker_url or not signing_secret:
        pytest.skip(
            "Set EDGE_E2E_WORKER_URL and EDGE_E2E_SESSION_SIGNING_SECRET to run this E2E regression test."
        )

    session_id = f"sess-pytest-{uuid.uuid4().hex[:10]}"
    token = _build_session_token(signing_secret, session_id=session_id)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    with httpx.Client(timeout=60.0) as client:
        query_res = client.post(
            f"{worker_url.rstrip('/')}/query",
            headers=headers,
            json={"question": "Return exactly: cleanup-regression-pass", "session_id": session_id},
        )
        assert query_res.status_code == 200, query_res.text
        query_body = query_res.json()
        assert query_body.get("ok") is True, query_body
        assert query_body.get("session_id") == session_id, query_body

        state_body: dict[str, object] | None = None
        deadline = time.time() + 10.0
        while time.time() < deadline:
            state_res = client.get(
                f"{worker_url.rstrip('/')}/session/{session_id}/state",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert state_res.status_code == 200, state_res.text
            state_body = state_res.json()
            assert state_body.get("ok") is True, state_body
            state = state_body["state"]
            if "current_prompt" not in state:
                break
            time.sleep(0.2)

    assert state_body is not None
    state = state_body["state"]
    assert state["session_id"] == session_id
    assert state["status"] == "idle", state
    assert "current_prompt" not in state, state
