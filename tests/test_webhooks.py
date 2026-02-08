"""Tests for webhook helpers."""

from modal_backend.platform_services.webhooks import build_headers, serialize_payload, sign_payload


def test_build_headers_with_signing():
    payload = serialize_payload({"event": "job.complete", "job": {"job_id": "123"}})
    config = {"signing_secret": "secret", "headers": {"X-Test": "ok"}}
    headers, timestamp = build_headers(config=config, payload=payload, default_secret=None)

    assert headers["Content-Type"] == "application/json"
    assert headers["X-Test"] == "ok"
    assert headers["X-Agent-Timestamp"] == str(timestamp)
    expected = sign_payload("secret", timestamp, payload)
    assert headers["X-Agent-Signature"] == f"t={timestamp},v1={expected}"


def test_build_headers_without_signing():
    payload = serialize_payload({"event": "job.complete", "job": {"job_id": "123"}})
    headers, _ = build_headers(config={}, payload=payload, default_secret=None)

    assert headers["Content-Type"] == "application/json"
    assert "X-Agent-Timestamp" not in headers
    assert "X-Agent-Signature" not in headers
