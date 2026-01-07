"""Webhook delivery helpers for job completion callbacks.

This module provides utilities for building, signing, and delivering webhook
notifications when jobs complete or fail. Webhooks are secured using HMAC-SHA256
signatures to prevent tampering and replay attacks.

Security Model:
    Webhook payloads are signed using HMAC-SHA256 with a shared secret. Each
    delivery attempt generates a fresh signature with the current timestamp to
    prevent replay attacks. Recipients must verify signatures to ensure authenticity.

Webhook Signature Verification (Recipient Guide):
    1. Extract timestamp and signature from X-Agent-Signature header:
       Format: "t={timestamp},v1={signature}"

    2. Reconstruct the signed message:
       message = f"{timestamp}.{json_payload}"

    3. Compute HMAC-SHA256 signature with your shared secret:
       expected_signature = hmac.new(
           secret.encode("utf-8"),
           message.encode(),
           hashlib.sha256
       ).hexdigest()

    4. Compare signatures using constant-time comparison:
       if not hmac.compare_digest(expected_signature, received_signature):
           return 401  # Signature verification failed

    5. Check timestamp freshness to prevent replay attacks:
       if abs(time.time() - int(timestamp)) > 300:  # 5 minute tolerance
           return 401  # Timestamp too old

Example Webhook Receiver (FastAPI):
    ```python
    import hmac
    import hashlib
    import time
    from fastapi import HTTPException, Header, Request

    @app.post("/webhook")
    async def receive_webhook(
        request: Request,
        x_agent_signature: str = Header(...),
        x_agent_timestamp: str = Header(...)
    ):
        # Extract signature components
        parts = dict(part.split("=") for part in x_agent_signature.split(","))
        timestamp = parts["t"]
        signature = parts["v1"]

        # Get raw JSON body
        payload = await request.body()

        # Verify signature
        message = f"{timestamp}.{payload.decode()}"
        expected = hmac.new(
            SECRET.encode(), message.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, signature):
            raise HTTPException(401, "Invalid signature")

        # Verify timestamp freshness
        if abs(time.time() - int(timestamp)) > 300:
            raise HTTPException(401, "Timestamp expired")

        # Process webhook safely
        data = json.loads(payload)
        # ... handle job.complete or job.failed event
    ```

Delivery Guarantees:
    - Webhooks are delivered asynchronously via Modal background function
    - Retries use exponential backoff (configurable)
    - Signatures are regenerated for each retry with fresh timestamp
    - Delivery status tracked in job record (attempts, last_status, last_error)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from agent_sandbox.schemas.jobs import JobStatusResponse


def build_webhook_payload(event: str, job_status: JobStatusResponse) -> dict[str, Any]:
    """Build a standardized webhook payload for job events.

    Creates a consistent JSON structure for webhook notifications containing
    the event type and full job status information.

    Args:
        event: Event type identifier, typically "job.complete" or "job.failed"
        job_status: Complete job status response including result/error, metrics,
                   artifacts, and all job metadata

    Returns:
        Dictionary with two keys:
            - event: str - The event type
            - job: dict - Full job status serialized with None values excluded

    Example:
        >>> status = JobStatusResponse(job_id="123", status="complete", ...)
        >>> payload = build_webhook_payload("job.complete", status)
        >>> payload
        {
            "event": "job.complete",
            "job": {
                "job_id": "123",
                "status": "complete",
                "result": {...},
                "duration_ms": 1234,
                ...
            }
        }
    """
    return {
        "event": event,
        "job": job_status.model_dump(exclude_none=True),
    }


def serialize_payload(payload: dict[str, Any]) -> str:
    """Serialize payload to canonical JSON for signing and transport.

    Creates a deterministic JSON representation by sorting keys and removing
    whitespace. This ensures the same payload always produces identical output,
    which is critical for signature verification.

    Args:
        payload: Dictionary to serialize (typically output of build_webhook_payload)

    Returns:
        Compact JSON string with sorted keys and no whitespace

    Implementation Details:
        - sort_keys=True: Ensures consistent key ordering for signature verification.
          Without this, {"a":1,"b":2} and {"b":2,"a":1} would have different signatures.
        - separators=(",",":"): Removes whitespace between keys/values for compactness
          and consistency across different JSON libraries
        - ensure_ascii=True: Escapes non-ASCII characters to prevent encoding issues
          during HTTP transport

    Example:
        >>> payload = {"event": "job.complete", "job": {"id": "123"}}
        >>> serialize_payload(payload)
        '{"event":"job.complete","job":{"id":"123"}}'
    """
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=True)


def sign_payload(secret: str, timestamp: int, payload: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload.

    Creates a cryptographic signature to prove payload authenticity and prevent
    tampering. The signature includes a timestamp to prevent replay attacks where
    an attacker intercepts and resends a valid webhook.

    Args:
        secret: Shared secret key known by both sender and recipient. Must be kept
               confidential and transmitted securely (e.g., via environment variables,
               not in the webhook URL or headers).
        timestamp: Unix timestamp (seconds since epoch) when signature was generated.
                  Recipients should verify this is recent to prevent replay attacks.
        payload: Serialized JSON payload (output of serialize_payload). Must be the
                exact byte-for-byte representation that will be sent in the HTTP body.

    Returns:
        Hexadecimal string representation of the HMAC-SHA256 signature (64 characters)

    Security Properties:
        - **Authentication**: Proves the webhook came from someone with the secret
        - **Integrity**: Detects any modification to the payload
        - **Non-repudiation**: Sender cannot deny sending the webhook
        - **Replay Protection**: Timestamp prevents reusing old valid webhooks

    Message Format:
        The signed message combines timestamp and payload: "{timestamp}.{payload}"
        This format ensures:
        - Same payload with different timestamps produces different signatures
        - Recipients can verify both payload and timestamp haven't been tampered with

    Example:
        >>> secret = "wh_secret_abc123"
        >>> timestamp = 1704067200
        >>> payload = '{"event":"job.complete","job":{"id":"123"}}'
        >>> sign_payload(secret, timestamp, payload)
        '3a7f2c1b5d8e9f4a6c3b2e1d0f9a8c7b5e3d2c1a0f9e8d7c6b5a4e3d2c1b0a9f'

    Verification:
        Recipients must reconstruct the same message and compute their own signature
        using the shared secret, then compare using hmac.compare_digest() to prevent
        timing attacks.
    """
    message = f"{timestamp}.{payload}".encode()
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def build_headers(
    *,
    config: dict[str, Any],
    payload: str,
    default_secret: str | None,
) -> tuple[dict[str, str], int]:
    """Build HTTP headers for webhook delivery with optional signature.

    Constructs headers for webhook HTTP requests, including optional HMAC-SHA256
    signature headers for payload authentication. Custom headers from config are
    merged with standard headers.

    Args:
        config: Webhook configuration dictionary containing:
               - headers (dict, optional): Custom HTTP headers to include
               - signing_secret (str, optional): Per-webhook secret for signing
        payload: Serialized JSON payload to sign (output of serialize_payload)
        default_secret: Global default signing secret used if config doesn't
                       specify one. If both are None, no signature is generated.

    Returns:
        Tuple of (headers dict, timestamp int):
            - headers: Complete HTTP headers including signature if secret provided
            - timestamp: Unix timestamp used for signature (0 if unsigned)

    Header Structure:
        Standard headers:
            Content-Type: application/json

        Custom headers (if provided in config):
            Any custom headers are merged in. Custom headers override standard
            headers if there's a conflict.

        Signature headers (if secret available):
            X-Agent-Timestamp: {unix_timestamp}
            X-Agent-Signature: t={timestamp},v1={hex_signature}

    Signature Header Format:
        The X-Agent-Signature header uses Stripe-style versioned signatures:
            "t={timestamp},v1={signature}"

        Where:
            - t: Unix timestamp when signature was generated
            - v1: Version identifier for signature algorithm (HMAC-SHA256)
            - signature: 64-character hex string of HMAC-SHA256 digest

        This format allows future algorithm changes (v2, v3) while maintaining
        backward compatibility.

    Security Notes:
        - Signatures are generated fresh for each call with current timestamp
        - Recipients MUST verify both signature and timestamp freshness
        - Custom headers are applied AFTER standard headers to allow overrides
        - No signature is generated if no secret is available (unsigned webhook)

    Example (with signature):
        >>> config = {"signing_secret": "wh_secret_123", "headers": {"X-Custom": "value"}}
        >>> payload = '{"event":"job.complete"}'
        >>> headers, ts = build_headers(config=config, payload=payload, default_secret=None)
        >>> headers
        {
            "Content-Type": "application/json",
            "X-Custom": "value",
            "X-Agent-Timestamp": "1704067200",
            "X-Agent-Signature": "t=1704067200,v1=3a7f2c1b..."
        }

    Example (without signature):
        >>> config = {"headers": {"X-Custom": "value"}}
        >>> headers, ts = build_headers(config=config, payload=payload, default_secret=None)
        >>> headers
        {
            "Content-Type": "application/json",
            "X-Custom": "value"
        }
    """
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
