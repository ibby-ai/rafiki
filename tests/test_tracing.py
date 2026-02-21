"""Tests for LangSmith tracing helpers."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from modal_backend import tracing


def test_langsmith_run_context_attaches_metadata(monkeypatch):
    calls: list[dict] = []

    class _Ctx:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    def _fake_tracing_context(**kwargs):
        calls.append(kwargs)
        return _Ctx()

    monkeypatch.setattr(
        tracing,
        "get_settings",
        lambda: SimpleNamespace(enable_langsmith_tracing=True),
    )
    monkeypatch.setitem(
        sys.modules, "langsmith", SimpleNamespace(tracing_context=_fake_tracing_context)
    )

    with tracing.langsmith_run_context({"trace_id": "trace-xyz", "session_id": "sess-1"}):
        pass

    assert len(calls) == 1
    assert calls[0]["metadata"]["trace_id"] == "trace-xyz"
    assert "openai-agents-sdk" in calls[0]["tags"]
