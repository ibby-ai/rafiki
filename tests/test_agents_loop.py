"""Tests for agent loop execution."""

import pytest

from agent_sandbox.agents.loop import build_agent_options
from agent_sandbox.prompts.prompts import SYSTEM_PROMPT
from agent_sandbox.tools import get_allowed_tools, get_mcp_servers


def test_build_agent_options(mock_settings):
    """Test that agent options are built correctly."""
    mcp_servers = get_mcp_servers()
    allowed_tools = get_allowed_tools()

    options = build_agent_options(mcp_servers, allowed_tools, SYSTEM_PROMPT)

    assert options is not None
    assert options.system_prompt == SYSTEM_PROMPT
    assert options.mcp_servers == mcp_servers
    assert options.allowed_tools == allowed_tools


@pytest.mark.slow
def test_run_agent_integration():
    """Integration test for agent execution (requires API key)."""
    # This test requires a valid Anthropic API key and will make actual API calls
    # Marked as slow to avoid running in fast CI
    pytest.skip("Requires API key and makes real API calls")


if __name__ == "__main__":
    pytest.main([__file__])
