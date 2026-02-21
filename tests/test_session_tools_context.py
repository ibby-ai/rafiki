"""Tests for request-local parent context used by session tools."""

from __future__ import annotations

import asyncio

import pytest

from modal_backend.mcp_tools import session_tools


def test_parent_context_reset_restores_previous_value() -> None:
    first_token = session_tools.set_parent_context("parent-a")
    second_token = session_tools.set_parent_context("parent-b")
    assert session_tools.get_parent_context() == "parent-b"

    session_tools.reset_parent_context(second_token)
    assert session_tools.get_parent_context() == "parent-a"

    session_tools.reset_parent_context(first_token)
    assert session_tools.get_parent_context() is None


@pytest.mark.asyncio
async def test_parent_context_isolated_per_task() -> None:
    root_token = session_tools.set_parent_context("root-parent")

    async def worker(name: str) -> str:
        token = session_tools.set_parent_context(name)
        try:
            await asyncio.sleep(0)
            return str(session_tools.get_parent_context())
        finally:
            session_tools.reset_parent_context(token)

    try:
        values = await asyncio.gather(worker("child-a"), worker("child-b"))
        assert sorted(values) == ["child-a", "child-b"]
        assert session_tools.get_parent_context() == "root-parent"
    finally:
        session_tools.reset_parent_context(root_token)
