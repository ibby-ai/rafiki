"""Tool registry for OpenAI Agents SDK tools."""

from __future__ import annotations

import glob
import ipaddress
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from agents import Tool, WebSearchTool, function_tool

from modal_backend.mcp_tools.calculate_tool import calculate
from modal_backend.mcp_tools.session_tools import (
    check_session_status,
    get_session_result,
    list_child_sessions,
    spawn_session,
)

_BASH_MAX_COMMAND_CHARS = 1000
_BASH_MAX_TIMEOUT_SECONDS = 300
_BASH_DENYLIST_PATTERNS = (
    "rm -rf /",
    "mkfs",
    "shutdown",
    "reboot",
    "poweroff",
    ":(){:|:&};:",
    "dd if=",
    ">/dev/sd",
    ">/dev/nvme",
)
_SAFE_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9.-]+$")


def _validate_bash_command(command: str) -> None:
    if not command or not command.strip():
        raise ValueError("Bash command cannot be empty")
    if len(command) > _BASH_MAX_COMMAND_CHARS:
        raise ValueError("Bash command exceeds maximum length")

    lowered = command.lower()
    for pattern in _BASH_DENYLIST_PATTERNS:
        if pattern in lowered:
            raise ValueError(f"Bash command contains blocked pattern: {pattern}")


def _is_private_host(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"}:
        return True
    if not _SAFE_HOSTNAME_RE.match(host):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)


def _validate_web_fetch_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("WebFetch only supports http and https URLs")
    if not parsed.hostname:
        raise ValueError("WebFetch URL must include a hostname")
    if _is_private_host(parsed.hostname):
        raise ValueError("WebFetch URL points to a private or blocked host")


@function_tool(name_override="Read")
def read_file(path: str) -> str:
    """Read a file from disk."""
    file_path = Path(path).expanduser()
    return file_path.read_text(encoding="utf-8")


@function_tool(name_override="Write")
def write_file(path: str, content: str) -> str:
    """Write content to a file on disk."""
    file_path = Path(path).expanduser()
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {file_path}"


@function_tool(name_override="Glob")
def glob_files(pattern: str) -> list[str]:
    """Find files matching a glob pattern."""
    return sorted(glob.glob(pattern, recursive=True))


@function_tool(name_override="Bash")
def run_bash(command: str, timeout_seconds: int = 60) -> str:
    """Run a bash command in the sandbox."""
    _validate_bash_command(command)
    timeout = max(1, min(timeout_seconds, _BASH_MAX_TIMEOUT_SECONDS))
    completed = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = completed.stdout or ""
    error = completed.stderr or ""
    if completed.returncode != 0:
        return f"Exit code: {completed.returncode}\nSTDOUT:\n{output}\nSTDERR:\n{error}"
    return output or error or "(command completed with no output)"


@function_tool(name_override="WebFetch")
def web_fetch(url: str, timeout_seconds: int = 15, max_chars: int = 20000) -> str:
    """Fetch a URL and return text content."""
    _validate_web_fetch_url(url)
    timeout = max(1, min(timeout_seconds, 60))
    max_len = max(256, min(max_chars, 100000))
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    text = resp.text
    if len(text) > max_len:
        return text[:max_len] + "\n\n[truncated]"
    return text


class ToolRegistry:
    """Registry for available tools and allowlist mapping."""

    def __init__(self):
        self._servers: dict[str, Any] = {}
        self._allowed_tools: list[str] = []
        self._tool_map: dict[str, Tool] = {}
        self._initialize_defaults()

    def _initialize_defaults(self) -> None:
        self._servers = {}
        self._allowed_tools = [
            "Read",
            "Write",
            "Glob",
            "Bash",
            "WebSearch(*)",
            "WebFetch(*)",
            "mcp__utilities__calculate",
            "mcp__sessions__spawn_session",
            "mcp__sessions__check_session_status",
            "mcp__sessions__get_session_result",
            "mcp__sessions__list_child_sessions",
        ]
        self._tool_map = {
            "Read": read_file,
            "Write": write_file,
            "Glob": glob_files,
            "Bash": run_bash,
            "WebFetch": web_fetch,
            "mcp__utilities__calculate": calculate,
            "mcp__sessions__spawn_session": spawn_session,
            "mcp__sessions__check_session_status": check_session_status,
            "mcp__sessions__get_session_result": get_session_result,
            "mcp__sessions__list_child_sessions": list_child_sessions,
        }

    def register_server(self, name: str, server: Any):
        self._servers[name] = server

    def add_allowed_tool(self, tool_name: str):
        if tool_name not in self._allowed_tools:
            self._allowed_tools.append(tool_name)

    def get_servers(self) -> dict[str, Any]:
        return self._servers.copy()

    def get_allowed_tools(self) -> list[str]:
        return self._allowed_tools.copy()

    def build_tools_for_allowed(self, allowed_tools: list[str]) -> list[Tool]:
        """Build concrete OpenAI tool objects from an allowlist."""
        built: list[Tool] = []
        added_ids: set[str] = set()

        def _add(tool: Tool, key: str) -> None:
            if key in added_ids:
                return
            added_ids.add(key)
            built.append(tool)

        for allowed in allowed_tools:
            if allowed == "WebSearch(*)":
                _add(WebSearchTool(), "WebSearch")
                continue
            if allowed == "WebFetch(*)":
                tool = self._tool_map.get("WebFetch")
                if tool:
                    _add(tool, "WebFetch")
                continue

            # Exact matches first
            if allowed in self._tool_map:
                _add(self._tool_map[allowed], allowed)
                continue

            # Support wildcard prefixes from legacy allowlists
            if allowed.endswith("(*)"):
                prefix = allowed[:-3]
                for name, tool in self._tool_map.items():
                    if name == prefix or name.startswith(prefix):
                        _add(tool, name)

        return built


_registry = ToolRegistry()


def get_mcp_servers() -> dict[str, Any]:
    return _registry.get_servers()


def get_allowed_tools() -> list[str]:
    return _registry.get_allowed_tools()


def build_tools_for_allowed(allowed_tools: list[str]) -> list[Tool]:
    return _registry.build_tools_for_allowed(allowed_tools)
