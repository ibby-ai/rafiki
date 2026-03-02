"""Tests for readonly SQLite fallback in agent session storage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from modal_backend.agent_runtime import base


@pytest.mark.asyncio
async def test_ensure_session_falls_back_to_tmp_when_db_path_is_readonly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSQLiteSession:
        def __init__(self, session_id: str, *, db_path: str):
            self.session_id = session_id
            self.db_path = db_path

        async def get_items(self):
            if self.db_path != "/tmp/openai_agents_sessions.sqlite3":
                raise RuntimeError("attempt to write a readonly database")
            return []

        async def add_items(self, items):
            return None

        async def clear_session(self):
            return None

    monkeypatch.setattr(base, "SQLiteSession", FakeSQLiteSession)
    monkeypatch.setattr(
        "modal_backend.settings.settings.get_settings",
        lambda: SimpleNamespace(
            openai_session_max_items=400, openai_session_compaction_keep_items=300
        ),
    )

    session, resolved = await base.ensure_session(
        session_id="sess-readonly",
        fork_session=False,
        db_path="/tmp/readonly-openai-agents.sqlite3",
    )

    assert resolved == "sess-readonly"
    assert session.db_path == "/tmp/openai_agents_sessions.sqlite3"


@pytest.mark.asyncio
async def test_ensure_session_does_not_swallow_non_readonly_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSQLiteSession:
        def __init__(self, session_id: str, *, db_path: str):
            self.session_id = session_id
            self.db_path = db_path

        async def get_items(self):
            raise RuntimeError("database is locked")

        async def add_items(self, items):
            return None

        async def clear_session(self):
            return None

    monkeypatch.setattr(base, "SQLiteSession", FakeSQLiteSession)
    monkeypatch.setattr(
        "modal_backend.settings.settings.get_settings",
        lambda: SimpleNamespace(
            openai_session_max_items=400, openai_session_compaction_keep_items=300
        ),
    )

    with pytest.raises(RuntimeError, match="database is locked"):
        await base.ensure_session(
            session_id="sess-non-readonly",
            fork_session=False,
            db_path="/tmp/nonreadonly-openai-agents.sqlite3",
        )


@pytest.mark.asyncio
async def test_ensure_session_falls_back_when_db_path_is_not_writable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSQLiteSession:
        def __init__(self, session_id: str, *, db_path: str):
            self.session_id = session_id
            self.db_path = db_path

        async def get_items(self):
            return []

        async def add_items(self, items):
            return None

        async def clear_session(self):
            return None

    monkeypatch.setattr(base, "SQLiteSession", FakeSQLiteSession)
    monkeypatch.setattr(
        base,
        "_is_db_path_writable",
        lambda path: path == "/tmp/openai_agents_sessions.sqlite3",
    )
    monkeypatch.setattr(
        "modal_backend.settings.settings.get_settings",
        lambda: SimpleNamespace(
            openai_session_max_items=400, openai_session_compaction_keep_items=300
        ),
    )

    session, resolved = await base.ensure_session(
        session_id="sess-unwritable-path",
        fork_session=False,
        db_path="/data/openai_agents_sessions.sqlite3",
    )

    assert resolved == "sess-unwritable-path"
    assert session.db_path == "/tmp/openai_agents_sessions.sqlite3"


@pytest.mark.asyncio
async def test_ensure_session_uses_creatable_nested_db_path_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSQLiteSession:
        def __init__(self, session_id: str, *, db_path: str):
            self.session_id = session_id
            self.db_path = db_path

        async def get_items(self):
            return []

        async def add_items(self, items):
            return None

        async def clear_session(self):
            return None

    monkeypatch.setattr(base, "SQLiteSession", FakeSQLiteSession)
    monkeypatch.setattr(
        "modal_backend.settings.settings.get_settings",
        lambda: SimpleNamespace(
            openai_session_max_items=400, openai_session_compaction_keep_items=300
        ),
    )

    requested_path = tmp_path / "nested" / "openai_agents_sessions.sqlite3"
    session, resolved = await base.ensure_session(
        session_id="sess-creatable-path",
        fork_session=False,
        db_path=str(requested_path),
    )

    assert resolved == "sess-creatable-path"
    assert session.db_path == str(requested_path)
