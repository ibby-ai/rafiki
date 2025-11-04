"""
Utilities to run a single-shot agent interaction (no web server).

This module is invoked in two ways:
- From a Modal sandbox via `sb.exec("python", "runner.py", ...)` (see
  `main.sandbox_controller` and `main.main`).
- Directly as a script (`python runner.py --question ...`) for local testing.

It constructs `ClaudeAgentOptions` using our local MCP tool server(s) and
system prompt, then issues a query and prints streamed responses.
"""

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
import anyio
from typing import Any, Dict, List
from utils.prompts import SYSTEM_PROMPT
from utils.tools import MCP_SERVERS, ALLOWED_TOOLS
from utils.prompts import DEFAULT_QUESTION
import argparse

# Use the custom tools with Claude
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

    See also: Modal docs for sandbox execution and file mounting; tools are
    defined in `utils/tools.py` and the environment is configured in
    `utils/env_templates.py`.
    """
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
)

options = build_agent_options(MCP_SERVERS, ALLOWED_TOOLS, SYSTEM_PROMPT)

async def run_agent(question: str = DEFAULT_QUESTION):
    """Execute a single agent query and print the streamed response.

    Args:
        question: Natural-language input to pass to the agent.
    """
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