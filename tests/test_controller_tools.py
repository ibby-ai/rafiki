"""Tests for controller/tool allowlist compatibility helpers."""

from modal_backend.api.controller import _is_tool_allowed
from modal_backend.mcp_tools import build_tools_for_allowed


def _tool_names(tools: list[object]) -> list[str]:
    return [str(getattr(tool, "name", "")) for tool in tools]


def test_is_tool_allowed_exact_match() -> None:
    allowed = ["mcp__sessions__spawn_session", "Task"]
    assert _is_tool_allowed("Task", allowed) is True
    assert _is_tool_allowed("mcp__sessions__spawn_session", allowed) is True


def test_is_tool_allowed_wildcard_match() -> None:
    allowed = ["WebSearch(*)", "WebFetch(*)"]
    assert _is_tool_allowed("WebSearch", allowed) is True
    assert _is_tool_allowed("WebSearch(query)", allowed) is True
    assert _is_tool_allowed("WebFetch", allowed) is True


def test_is_tool_allowed_denied() -> None:
    allowed = ["WebSearch(*)"]
    assert _is_tool_allowed("Read", allowed) is False


def test_allowlist_maps_to_openai_tools() -> None:
    tools = build_tools_for_allowed(
        [
            "Read",
            "WebSearch(*)",
            "WebFetch(*)",
            "mcp__utilities__calculate",
            "mcp__sessions__spawn_session",
        ]
    )
    names = _tool_names(tools)
    assert "Read" in names
    assert "web_search" in names
    assert "WebFetch" in names
    assert "mcp__utilities__calculate" in names
    assert "mcp__sessions__spawn_session" in names


def test_allowlist_tools_are_deduplicated() -> None:
    tools = build_tools_for_allowed(["Read", "Read", "WebSearch(*)", "WebSearch(*)"])
    names = _tool_names(tools)
    assert names.count("Read") == 1
    assert names.count("web_search") == 1
