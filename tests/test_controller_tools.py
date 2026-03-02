"""Tests for controller/tool allowlist compatibility helpers."""

import json
from types import SimpleNamespace

import pytest

from modal_backend.api.controller import _is_tool_allowed
from modal_backend.mcp_tools import build_tools_for_allowed
from modal_backend.mcp_tools.registry import run_bash, web_fetch


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


@pytest.mark.asyncio
async def test_run_bash_blocks_dangerous_commands() -> None:
    result = await run_bash.on_invoke_tool(None, json.dumps({"command": "rm -rf /"}))
    assert "blocked pattern" in result


@pytest.mark.asyncio
async def test_run_bash_blocks_network_commands() -> None:
    result = await run_bash.on_invoke_tool(
        None, json.dumps({"command": "curl https://example.com"})
    )
    assert "blocked pattern" in result


@pytest.mark.asyncio
async def test_web_fetch_blocks_private_hosts() -> None:
    result = await web_fetch.on_invoke_tool(None, json.dumps({"url": "http://127.0.0.1:8080"}))
    assert "private or blocked host" in result


@pytest.mark.asyncio
async def test_web_fetch_blocks_non_standard_ports() -> None:
    result = await web_fetch.on_invoke_tool(None, json.dumps({"url": "https://example.com:8443"}))
    assert "port is not allowed" in result


@pytest.mark.asyncio
async def test_run_bash_allows_safe_command(monkeypatch) -> None:
    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("modal_backend.mcp_tools.registry.subprocess.run", _fake_run)
    result = await run_bash.on_invoke_tool(None, json.dumps({"command": "echo hello"}))
    assert result == "ok"


@pytest.mark.asyncio
async def test_web_fetch_allows_public_https(monkeypatch) -> None:
    class _Resp:
        text = "hello world"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("modal_backend.mcp_tools.registry.requests.get", lambda *_a, **_k: _Resp())
    result = await web_fetch.on_invoke_tool(None, json.dumps({"url": "https://example.com"}))
    assert result == "hello world"
