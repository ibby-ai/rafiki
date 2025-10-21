"""
The code responsible for running the agent.
"""

from utils.tools import multi_tool_server
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
import anyio
# Use the custom tools with Claude
options = ClaudeAgentOptions(
    mcp_servers={"utilities": multi_tool_server},
    allowed_tools=[
        "mcp__utilities__calculate",  # Allow the calculator tool
        "mcp__utilities__translate",  # Allow the translator tool
        # Add other tools as needed
        "Read",
        "Write",
    ]
)

DEFAULT_QUESTION = "What is the capital of France?"

async def run_agent():
    async with ClaudeSDKClient(options=options) as client:
        await client.query(DEFAULT_QUESTION)

        # Extract and print response
        async for msg in client.receive_response():
            print(msg)

if __name__ == "__main__":
    anyio.run(run_agent)