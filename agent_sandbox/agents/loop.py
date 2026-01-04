"""
Utilities to run a single-shot agent interaction (no web server).

This module is invoked in two ways:
- From a Modal sandbox via `sb.exec("python", "-m", "agent_sandbox.agents.loop", ...)`
- Directly as a script (`python -m agent_sandbox.agents.loop --question ...`) for local testing.

It constructs provider-specific options using our local MCP tool server(s) and
system prompt, then issues a query and prints streamed responses.
"""

import argparse
from typing import Any

import anyio

from agent_sandbox.config.settings import get_settings
from agent_sandbox.prompts.prompts import DEFAULT_QUESTION, SYSTEM_PROMPT
from agent_sandbox.providers import get_provider

_settings = get_settings()


def build_agent_options(
    mcp_servers: dict[str, Any] | None = None,
    allowed_tools: list[str] | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    resume: str | None = None,
    fork_session: bool = False,
    max_turns: int | None = None,
    provider_id: str | None = None,
    provider_config: dict[str, Any] | None = None,
) -> Any:
    """Create provider-specific options for a CLI or sandbox run."""
    provider = get_provider(provider_id or _settings.agent_provider)
    mcp_servers = mcp_servers or provider.get_mcp_servers()
    allowed_tools = allowed_tools or provider.get_allowed_tools()
    return provider.build_options(
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
        session_id=resume,
        fork_session=fork_session,
        max_turns=max_turns,
        provider_config=provider_config,
    )


async def run_agent(
    question: str = DEFAULT_QUESTION,
    session_id: str | None = None,
    fork_session: bool = False,
):
    """Execute a single agent query and print the streamed response.

    Args:
        question: Natural-language input to pass to the agent.
    """
    provider = get_provider(_settings.agent_provider)
    options = build_agent_options(
        system_prompt=SYSTEM_PROMPT,
        resume=session_id,
        fork_session=fork_session,
        max_turns=_settings.agent_max_turns,
        provider_id=provider.provider_id,
        provider_config=_settings.agent_provider_options,
    )

    async with provider.create_client(options) as client:
        await client.query(question)
        async for msg in client.receive_response():
            print(provider.serialize_message(msg))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", type=str, default=DEFAULT_QUESTION)
    parser.add_argument("--session-id", type=str, default=None)
    parser.add_argument("--fork-session", action="store_true")
    args = parser.parse_args()
    anyio.run(run_agent, args.question, args.session_id, args.fork_session)
