"""Calculate tool for performing arithmetic operations."""

from typing import Any

from claude_agent_sdk import tool


@tool("calculate", "Perform calculations", {"expression": str})
async def calculate(args: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a simple arithmetic expression and return the result.

    Args:
        args: Dict with key `expression` containing a Python arithmetic string.

    Returns:
        A Claude Agent SDK content dict containing a textual result.

    Note:
        This uses Python `eval` for brevity. In production, replace with a safe
        parser to avoid executing untrusted input.
    """
    result = eval(args["expression"])  # Use safe eval in production
    return {"content": [{"type": "text", "text": f"Result: {result}"}]}
