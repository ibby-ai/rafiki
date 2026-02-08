"""Tool registry for managing MCP servers and allowed tools."""

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server

from modal_backend.mcp_tools.calculate_tool import calculate
from modal_backend.mcp_tools.session_tools import (
    check_session_status,
    get_session_result,
    list_child_sessions,
    spawn_session,
)


class ToolRegistry:
    """Registry for MCP tool servers and allowed tools."""

    def __init__(self):
        self._servers: dict[str, Any] = {}
        self._allowed_tools: list[str] = []
        self._initialize_defaults()

    def _initialize_defaults(self):
        """Initialize default MCP servers and allowed tools."""
        # Create multi-tool server with utilities
        multi_tool_server = create_sdk_mcp_server(
            name="utilities", version="1.0.0", tools=[calculate]
        )

        # Create session management server for child session spawning
        session_server = create_sdk_mcp_server(
            name="sessions",
            version="1.0.0",
            tools=[
                spawn_session,
                check_session_status,
                get_session_result,
                list_child_sessions,
            ],
        )

        self._servers = {
            "utilities": multi_tool_server,
            "sessions": session_server,
        }

        self._allowed_tools = [
            # Built-in tools that may be available in runtime
            "Read",
            "Write",
            "WebSearch(*)",
            "WebFetch(*)",
            # Custom MCP tools - utilities
            "mcp__utilities__calculate",
            # Custom MCP tools - session spawning
            "mcp__sessions__spawn_session",
            "mcp__sessions__check_session_status",
            "mcp__sessions__get_session_result",
            "mcp__sessions__list_child_sessions",
        ]

    def register_server(self, name: str, server: Any):
        """Register an MCP server.

        Args:
            name: Server identifier.
            server: MCP server instance.
        """
        self._servers[name] = server

    def add_allowed_tool(self, tool_name: str):
        """Add a tool to the allowed list.

        Args:
            tool_name: Tool identifier to allow.
        """
        if tool_name not in self._allowed_tools:
            self._allowed_tools.append(tool_name)

    def get_servers(self) -> dict[str, Any]:
        """Get all registered MCP servers.

        Returns:
            Dictionary mapping server names to server instances.
        """
        return self._servers.copy()

    def get_allowed_tools(self) -> list[str]:
        """Get list of allowed tool names.

        Returns:
            List of allowed tool identifiers.
        """
        return self._allowed_tools.copy()


# Global registry instance
_registry = ToolRegistry()


def get_mcp_servers() -> dict[str, Any]:
    """Get all registered MCP servers.

    Returns:
        Dictionary mapping server names to server instances.
    """
    return _registry.get_servers()


def get_allowed_tools() -> list[str]:
    """Get list of allowed tool names.

    Returns:
        List of allowed tool identifiers.
    """
    return _registry.get_allowed_tools()
