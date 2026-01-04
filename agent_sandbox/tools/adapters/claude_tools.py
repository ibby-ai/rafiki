"""Claude SDK adapter for provider-agnostic tools."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server
from claude_agent_sdk import tool as claude_tool

from agent_sandbox.tools.protocol import ToolDefinition
from agent_sandbox.tools.registry import get_tool_definitions


def _wrap_tool(tool_def: ToolDefinition):
    @claude_tool(tool_def.name, tool_def.description, tool_def.parameters)
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        return await tool_def.handler(args)

    _handler.__name__ = f"tool_{tool_def.server}_{tool_def.name}"
    return _handler


def build_claude_mcp_servers() -> dict[str, Any]:
    servers: dict[str, Any] = {}
    definitions = get_tool_definitions()
    for server_name, tools in definitions.items():
        wrapped_tools = [_wrap_tool(tool_def) for tool_def in tools]
        servers[server_name] = create_sdk_mcp_server(
            name=server_name,
            version="1.0.0",
            tools=wrapped_tools,
        )
    return servers
