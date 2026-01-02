"""Example custom tools for string manipulation.

This file demonstrates the pattern for creating MCP tools that can be
registered with the agent. Copy this file to agent_sandbox/tools/ and
follow INTEGRATION.md to enable these tools.
"""

from typing import Any

from claude_agent_sdk import tool


@tool(
    "reverse_string",
    "Reverse a string. Useful for palindrome checks or text manipulation.",
    {"text": str},
)
async def reverse_string(args: dict[str, Any]) -> dict[str, Any]:
    """Reverse the input string.

    Args:
        args: Dict with 'text' key containing the string to reverse.

    Returns:
        Content dict with the reversed string.
    """
    text = args.get("text", "")
    reversed_text = text[::-1]
    return {"content": [{"type": "text", "text": f"Reversed: {reversed_text}"}]}


@tool(
    "count_words",
    "Count the number of words in a text string.",
    {"text": str},
)
async def count_words(args: dict[str, Any]) -> dict[str, Any]:
    """Count words in the input text.

    Args:
        args: Dict with 'text' key containing the text to analyze.

    Returns:
        Content dict with the word count.
    """
    text = args.get("text", "")
    word_count = len(text.split())
    return {"content": [{"type": "text", "text": f"Word count: {word_count}"}]}


@tool(
    "to_uppercase",
    "Convert a string to uppercase.",
    {"text": str},
)
async def to_uppercase(args: dict[str, Any]) -> dict[str, Any]:
    """Convert text to uppercase.

    Args:
        args: Dict with 'text' key containing the text to convert.

    Returns:
        Content dict with the uppercase text.
    """
    text = args.get("text", "")
    return {"content": [{"type": "text", "text": text.upper()}]}
