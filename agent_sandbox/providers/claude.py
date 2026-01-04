"""Claude Agent SDK provider implementation."""

from __future__ import annotations

from typing import Any

import modal
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)
from claude_agent_sdk.types import ResultMessage

from agent_sandbox.config.settings import Settings
from agent_sandbox.controllers.serialization import (
    build_final_summary,
    iter_text_blocks,
    serialize_message,
)
from agent_sandbox.providers.base import AgentClient, AgentProvider
from agent_sandbox.providers.registry import ProviderRegistry
from agent_sandbox.tools import get_allowed_tools
from agent_sandbox.tools.adapters import build_claude_mcp_servers


def _allow_web_only(tool_name: str, tool_input: dict[str, Any], ctx: ToolPermissionContext):
    """Permission handler that allows only web-related tools."""
    if tool_name.startswith("WebSearch") or tool_name.startswith("WebFetch"):
        return PermissionResultAllow(updated_input=tool_input)
    return PermissionResultDeny(message=f"Tool {tool_name} is not allowed")


@ProviderRegistry.register("claude")
class ClaudeProvider(AgentProvider):
    """Claude Agent SDK provider wrapper."""

    display_name = "Claude Agent SDK"

    def capabilities(self) -> dict[str, bool]:
        return {
            "sessions": True,
            "fork_session": True,
            "streaming": True,
        }

    def required_secrets(self, settings: Settings) -> list[modal.Secret]:
        return [
            modal.Secret.from_name(
                "anthropic-secret",
                required_keys=["ANTHROPIC_API_KEY"],
            )
        ]

    def get_mcp_servers(self) -> dict[str, Any]:
        return build_claude_mcp_servers()

    def get_allowed_tools(self) -> list[str]:
        return get_allowed_tools()

    def build_options(
        self,
        *,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        allowed_tools: list[str],
        session_id: str | None,
        fork_session: bool,
        max_turns: int | None,
        provider_config: dict[str, Any] | None = None,
        permission_mode: str | None = None,
        can_use_tool: Any | None = None,
    ) -> ClaudeAgentOptions:
        options: dict[str, Any] = {
            "system_prompt": system_prompt,
            "mcp_servers": mcp_servers,
            "allowed_tools": allowed_tools,
            "resume": session_id,
            "fork_session": fork_session,
            "max_turns": max_turns,
        }
        if permission_mode:
            options["permission_mode"] = permission_mode
        if can_use_tool:
            options["can_use_tool"] = can_use_tool
        if provider_config:
            options.update(provider_config)
        return ClaudeAgentOptions(**options)

    def create_client(self, options: Any) -> AgentClient:
        return ClaudeSDKClient(options=options)

    def serialize_message(self, message: Any) -> dict[str, Any]:
        return serialize_message(message)

    def build_summary(self, messages: list[Any]) -> dict[str, Any]:
        result_message = None
        for message in messages:
            if isinstance(message, ResultMessage):
                result_message = message
                break
        text_blocks = iter_text_blocks(messages)
        final_text = None
        if result_message and result_message.result:
            final_text = result_message.result
        elif text_blocks:
            final_text = "\n".join(text_blocks)
        return build_final_summary(result_message, final_text)

    @staticmethod
    def default_tool_permission_handler():
        return _allow_web_only
