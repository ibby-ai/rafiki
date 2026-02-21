"""Tests for calculate tool."""

import json

import pytest

from modal_backend.mcp_tools.calculate_tool import calculate


@pytest.mark.asyncio
async def test_calculate_simple_expression():
    """Test calculating a simple arithmetic expression."""
    result = await calculate.on_invoke_tool(None, json.dumps({"expression": "2 + 2"}))
    assert result == "Result: 4"


@pytest.mark.asyncio
async def test_calculate_complex_expression():
    """Test calculating a more complex expression."""
    result = await calculate.on_invoke_tool(None, json.dumps({"expression": "(10 * 5) + 3"}))
    assert result == "Result: 53"


@pytest.mark.asyncio
async def test_calculate_division():
    """Test division operations."""
    result = await calculate.on_invoke_tool(None, json.dumps({"expression": "100 / 4"}))
    assert result == "Result: 25.0"


if __name__ == "__main__":
    pytest.main([__file__])
