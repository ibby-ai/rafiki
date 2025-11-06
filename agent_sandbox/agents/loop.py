"""
Utilities to run a single-shot agent interaction (no web server).

This module is invoked in two ways:
- From a Modal sandbox via `sb.exec("python", "-m", "agent_sandbox.agents.loop", ...)`
- Directly as a script (`python -m agent_sandbox.agents.loop --question ...`) for local testing.

It constructs `ClaudeAgentOptions` using our local MCP tool server(s) and
system prompt, then issues a query and prints streamed responses.
"""

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
import anyio
from typing import Any, Dict, List
from agent_sandbox.prompts.prompts import SYSTEM_PROMPT, DEFAULT_QUESTION
from agent_sandbox.tools import get_mcp_servers, get_allowed_tools
import argparse


def build_agent_options(
    mcp_servers: Dict[str, Any],
    allowed_tools: List[str],
    system_prompt: str = SYSTEM_PROMPT,
) -> ClaudeAgentOptions:
    """Create `ClaudeAgentOptions` for a CLI or sandbox run.

    Args:
        mcp_servers: Mapping of MCP server name to server instance created by
            `create_sdk_mcp_server`.
        allowed_tools: Whitelist of tool names the agent is allowed to invoke.
        system_prompt: Behavior-shaping prompt for the agent.

    Returns:
        A configured `ClaudeAgentOptions`.

    See also:
        Modal docs for sandbox execution and file mounting; tools are
        defined in `agent_sandbox.tools` and the environment is configured in
        `agent_sandbox.config.settings`.
    """
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
    )


async def run_agent(question: str = DEFAULT_QUESTION):
    """Execute a single agent query and print the streamed response.

    Args:
        question: Natural-language input to pass to the agent.
    """
    options = build_agent_options(
        get_mcp_servers(), 
        get_allowed_tools(), 
        SYSTEM_PROMPT
    )
    
    async with ClaudeSDKClient(options=options) as client:
        await client.query(question)

        # Extract and print response
        async for msg in client.receive_response():
            print(msg)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", type=str, default=DEFAULT_QUESTION)
    args = parser.parse_args()
    anyio.run(run_agent, args.question)

