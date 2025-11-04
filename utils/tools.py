"""
Definition of MCP tool servers and the set of tools available to the agent.

We use the Claude Agent SDK's `@tool` decorator to define individual tools and
`create_sdk_mcp_server` to bundle them into an MCP server the agent can reach.

Naming conventions:
- Tool invocation names exposed to the agent are prefixed by the MCP server and
  tool name, e.g. `mcp__utilities__calculate`.

Note: `calculate` uses Python `eval` for brevity; replace with a safe parser in
production to avoid executing untrusted input.
"""
from claude_agent_sdk import tool, create_sdk_mcp_server
from typing import Any, Dict, List

# Define multiple tools using the @tool decorator
@tool("calculate", "Perform calculations", {"expression": str})
async def calculate(args: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a simple arithmetic expression and return the result.

    Args:
        args: Dict with key `expression` containing a Python arithmetic string.

    Returns:
        A Claude Agent SDK content dict containing a textual result.
    """
    result = eval(args["expression"])  # Use safe eval in production
    return {"content": [{"type": "text", "text": f"Result: {result}"}]}

@tool("translate", "Translate text", {"text": str, "target_lang": str})
async def translate(args: dict[str, Any]) -> dict[str, Any]:
    """Pretend to translate text to a target language (stub).

    Replace with a real translation call or library as needed.
    """
    return {"content": [{"type": "text", "text": f"Translated: {args['text']}"}]}

@tool("search_web", "Search the web", {"query": str})
async def search_web(args: dict[str, Any]) -> dict[str, Any]:
    """Stub web search tool that echoes the query.

    Replace with an implementation that queries your preferred search API
    (respecting TOS and rate limits) and returns summarized results.
    """
    return {"content": [{"type": "text", "text": f"Search results for: {args['query']}"}]}

multi_tool_server = create_sdk_mcp_server(
    name="utilities",
    version="1.0.0",
    tools=[calculate, translate, search_web]  # Pass decorated functions
)

MCP_SERVERS: Dict[str, Any] = {"utilities": multi_tool_server}

ALLOWED_TOOLS: List[str] = [
    # MCP tools exposed by this package's server
    # "mcp__utilities__calculate",
    # "mcp__utilities__translate",
    # Built-in tools (examples) that may be available in runtime
    "Read",
    "Write",
    "WebSearch(*)",
    "WebFetch(*)",
]
