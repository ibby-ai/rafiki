"""
The code responsible for running the agent.
"""

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
import anyio
from typing import Any, Dict, List
from utils.prompts import SYSTEM_PROMPT
from utils.tools import MCP_SERVERS, ALLOWED_TOOLS
from utils.prompts import DEFAULT_QUESTION

# Use the custom tools with Claude

def build_agent_options(
    mcp_servers: Dict[str, Any],
    allowed_tools: List[str],
    system_prompt: str = SYSTEM_PROMPT,
) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
)

options = build_agent_options(MCP_SERVERS, ALLOWED_TOOLS, SYSTEM_PROMPT)

async def run_agent(question: str = DEFAULT_QUESTION):
    async with ClaudeSDKClient(options=options) as client:
        await client.query(question)

        # Extract and print response
        async for msg in client.receive_response():
            print(msg)

if __name__ == "__main__":
    anyio.run(run_agent)