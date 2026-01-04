"""Framework-agnostic tool decorators."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_sandbox.tools.protocol import ToolDefinition
from agent_sandbox.tools.registry import register_tool


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    *,
    server: str = "utilities",
) -> Callable[[Callable[..., Any]], ToolDefinition]:
    """Register a provider-agnostic tool definition.

    Returns the ToolDefinition to keep usage ergonomic.
    """

    def decorator(func: Callable[..., Any]) -> ToolDefinition:
        tool_def = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            handler=func,
            server=server,
        )
        register_tool(tool_def)
        return tool_def

    return decorator
