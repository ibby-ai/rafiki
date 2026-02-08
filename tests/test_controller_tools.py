"""Tests for controller tool permission helpers."""

from modal_backend.api.controller import _is_tool_allowed


def test_is_tool_allowed_exact_match():
    allowed = ["mcp__sessions__spawn_session", "Task"]
    assert _is_tool_allowed("Task", allowed) is True
    assert _is_tool_allowed("mcp__sessions__spawn_session", allowed) is True


def test_is_tool_allowed_wildcard_match():
    allowed = ["WebSearch(*)", "WebFetch(*)"]
    assert _is_tool_allowed("WebSearch", allowed) is True
    assert _is_tool_allowed("WebSearch(query)", allowed) is True
    assert _is_tool_allowed("WebFetch", allowed) is True


def test_is_tool_allowed_denied():
    allowed = ["WebSearch(*)"]
    assert _is_tool_allowed("Read", allowed) is False
