"""Webhook delivery helpers for job completion callbacks."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from agent_sandbox.schemas.jobs import JobStatusResponse


def build_webhook_payload(event: str, job_status: JobStatusResponse) -> dict[str, Any]:
    """Build a standardized webhook payload."""
    return {
        "event": event,
        "job": job_status.model_dump(exclude_none=True),
    }


def serialize_payload(payload: dict[str, Any]) -> str:
    """Serialize payload for signing and transport."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=True)


def sign_payload(secret: str, timestamp: int, payload: str) -> str:
    """Return an HMAC SHA-256 signature for the payload."""
    message = f"{timestamp}.{payload}".encode()
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def build_headers(
    *,
    config: dict[str, Any],
    payload: str,
    default_secret: str | None,
) -> tuple[dict[str, str], int]:
    """Build webhook headers with optional signing."""
    headers = {"Content-Type": "application/json"}
    custom_headers = config.get("headers") or {}
    headers.update(custom_headers)

    secret = config.get("signing_secret") or default_secret
    timestamp = int(time.time())
    if secret:
        signature = sign_payload(secret, timestamp, payload)
        headers["X-Agent-Timestamp"] = str(timestamp)
        headers["X-Agent-Signature"] = f"t={timestamp},v1={signature}"
    return headers, timestamp
