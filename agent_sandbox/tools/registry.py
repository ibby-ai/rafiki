"""Tool registry for managing provider-agnostic tools and allowed tools."""

from agent_sandbox.tools.protocol import ToolDefinition

_DEFAULTS_LOADED = False


class ToolRegistry:
    """Registry for tool definitions and allowed tools."""

    def __init__(self):
        self._servers: dict[str, list[ToolDefinition]] = {}
        self._allowed_tools: list[str] = []
        self._initialize_defaults()

    def _initialize_defaults(self):
        """Initialize default allowed tools."""
        self._allowed_tools = [
            # Built-in tools that may be available in runtime
            "Read",
            "Write",
            "WebSearch(*)",
            "WebFetch(*)",
        ]

    def register_tool(self, tool_def: ToolDefinition):
        """Register a tool definition under its server name.

        Args:
            tool_def: Tool definition to register.
        """
        server_name = tool_def.server
        self._servers.setdefault(server_name, [])
        if tool_def not in self._servers[server_name]:
            self._servers[server_name].append(tool_def)
        self.add_allowed_tool(f"mcp__{server_name}__{tool_def.name}")

    def add_allowed_tool(self, tool_name: str):
        """Add a tool to the allowed list.

        Args:
            tool_name: Tool identifier to allow.
        """
        if tool_name not in self._allowed_tools:
            self._allowed_tools.append(tool_name)

    def get_tool_definitions(self) -> dict[str, list[ToolDefinition]]:
        """Get all registered tool definitions.

        Returns:
            Dictionary mapping server names to tool definitions.
        """
        return {name: tools[:] for name, tools in self._servers.items()}

    def get_allowed_tools(self) -> list[str]:
        """Get list of allowed tool names.

        Returns:
            List of allowed tool identifiers.
        """
        return self._allowed_tools.copy()


# Global registry instance
_registry = ToolRegistry()


def register_tool(tool_def: ToolDefinition) -> None:
    """Register a provider-agnostic tool definition."""
    _registry.register_tool(tool_def)


def get_allowed_tools() -> list[str]:
    """Get list of allowed tool names.

    Returns:
        List of allowed tool identifiers.
    """
    ensure_default_tools_loaded()
    return _registry.get_allowed_tools()


def get_tool_definitions() -> dict[str, list[ToolDefinition]]:
    """Get all registered tool definitions."""
    ensure_default_tools_loaded()
    return _registry.get_tool_definitions()


def ensure_default_tools_loaded() -> None:
    """Load default tools on first access to avoid import cycles."""
    global _DEFAULTS_LOADED
    if _DEFAULTS_LOADED:
        return
    from agent_sandbox.tools import calculate_tool as _calculate_tool  # noqa: F401

    _DEFAULTS_LOADED = True
