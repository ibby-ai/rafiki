"""Example custom tools for string manipulation.

This file demonstrates the pattern for creating MCP tools that can be
registered with the agent. Copy this file to modal_backend/mcp_tools/ and
follow INTEGRATION.md to enable these tools.
"""

from agents import function_tool


@function_tool(name_override="mcp__utilities__reverse_string")
def reverse_string(text: str) -> str:
    """Reverse the input string.

    Args:
        text: The string to reverse.

    Returns:
        Reversed string payload.
    """
    reversed_text = text[::-1]
    return f"Reversed: {reversed_text}"


@function_tool(name_override="mcp__utilities__count_words")
def count_words(text: str) -> str:
    """Count words in the input text.

    Args:
        text: The text to analyze.

    Returns:
        Word count payload.
    """
    word_count = len(text.split())
    return f"Word count: {word_count}"


@function_tool(name_override="mcp__utilities__to_uppercase")
def to_uppercase(text: str) -> str:
    """Convert text to uppercase.

    Args:
        text: The text to convert.

    Returns:
        Uppercase string.
    """
    return text.upper()
