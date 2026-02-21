"""Calculate tool for performing arithmetic operations."""

from agents import function_tool


@function_tool(name_override="mcp__utilities__calculate")
def calculate(expression: str) -> str:
    """Evaluate a simple arithmetic expression and return the result.

    Args:
        expression: Python arithmetic expression.
    """
    result = eval(expression)  # noqa: S307 - sandbox-local helper for prototype parity
    return f"Result: {result}"
