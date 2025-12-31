"""Tests for calculate tool."""

import pytest
from agent_sandbox.tools.calculate_tool import calculate


@pytest.mark.asyncio
async def test_calculate_simple_expression():
    """Test calculating a simple arithmetic expression."""
    result = await calculate.handler({"expression": "2 + 2"})
    assert "content" in result
    assert len(result["content"]) > 0
    assert "Result: 4" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_calculate_complex_expression():
    """Test calculating a more complex expression."""
    result = await calculate.handler({"expression": "(10 * 5) + 3"})
    assert "content" in result
    assert "Result: 53" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_calculate_division():
    """Test division operations."""
    result = await calculate.handler({"expression": "100 / 4"})
    assert "content" in result
    assert "Result: 25.0" in result["content"][0]["text"]


if __name__ == "__main__":
    pytest.main([__file__])

