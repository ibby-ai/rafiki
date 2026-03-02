"""Tests for Modal gateway /query upstream error normalization."""

from __future__ import annotations

import json

import modal_backend.main as main


def test_normalize_query_upstream_error_handles_nested_fastapi_detail() -> None:
    nested = {
        "ok": False,
        "error": "Token missing.",
        "error_type": "AuthError",
        "request_id": "req-123",
    }
    raw = json.dumps({"detail": json.dumps(nested)})

    normalized = main._normalize_query_upstream_error(raw)

    assert normalized == {
        "ok": False,
        "error": "Token missing.",
        "error_type": "AuthError",
        "request_id": "req-123",
    }


def test_normalize_query_upstream_error_handles_validation_detail_list() -> None:
    raw = json.dumps(
        {
            "detail": [
                {
                    "type": "missing",
                    "loc": ["body", "question"],
                    "msg": "Field required",
                }
            ]
        }
    )

    normalized = main._normalize_query_upstream_error(raw)

    assert normalized["ok"] is False
    assert normalized["error"] == "Background sandbox validation error"
    assert isinstance(normalized["detail"], list)


def test_normalize_query_upstream_error_handles_plain_text() -> None:
    normalized = main._normalize_query_upstream_error("upstream plain error")
    assert normalized == {"ok": False, "error": "upstream plain error"}


def test_normalize_query_upstream_error_prefers_top_level_error() -> None:
    raw = json.dumps({"ok": False, "error": "Controller failed", "request_id": "req-9"})
    normalized = main._normalize_query_upstream_error(raw)

    assert normalized == {
        "ok": False,
        "error": "Controller failed",
        "request_id": "req-9",
    }
