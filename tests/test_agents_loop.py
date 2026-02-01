"""Tests for agent loop execution and multi-agent architecture."""

import pytest
from claude_agent_sdk import AgentDefinition

from agent_sandbox.agents import build_agent_options
from agent_sandbox.agents.base import AgentConfig
from agent_sandbox.agents.registry import get_agent_config, list_agent_types
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


def test_list_agent_types():
    """Test that list_agent_types returns available agent types."""
    types = list_agent_types()
    assert isinstance(types, list)
    assert "default" in types
    assert "marketing" in types
    assert "research" in types
    assert len(types) >= 3


def test_get_agent_config_default():
    """Test getting the default agent configuration."""
    config = get_agent_config("default")
    assert isinstance(config, AgentConfig)
    assert config.name == "default"
    assert config.display_name == "Default Agent"
    assert config.system_prompt is not None
    assert len(config.system_prompt) > 0


def test_get_agent_config_marketing():
    """Test getting the marketing agent configuration."""
    config = get_agent_config("marketing")
    assert isinstance(config, AgentConfig)
    assert config.name == "marketing"
    assert config.display_name == "Marketing Agent"
    assert "marketing" in config.description.lower()
    assert config.max_turns == 30
    assert config.can_spawn_subagents is False


def test_get_agent_config_research():
    """Test getting the research agent configuration."""
    config = get_agent_config("research")
    assert isinstance(config, AgentConfig)
    assert config.name == "research"
    assert config.display_name == "Research Agent"
    assert "research" in config.description.lower()
    assert config.max_turns == 50
    assert config.can_spawn_subagents is True
    assert "mcp__sessions__spawn_session" in config.allowed_tools
    # Research agent has SDK native subagents
    assert "Task" in config.allowed_tools


def test_get_agent_config_research_effective_tools():
    """Test that effective allowed tools include subagent dependencies."""
    config = get_agent_config("research")
    effective_tools = config.get_effective_allowed_tools()

    # Subagent tools should be included even if not on the lead allowlist
    assert "Bash" in effective_tools


def test_get_agent_config_research_subagents():
    """Test that research agent has SDK native subagents configured."""
    config = get_agent_config("research")
    subagents = config.get_subagents()

    # Research agent should have subagents
    assert subagents is not None
    assert isinstance(subagents, dict)
    assert len(subagents) == 3

    # Check expected subagent names
    assert "researcher" in subagents
    assert "data-analyst" in subagents
    assert "report-writer" in subagents

    # Verify each subagent is an AgentDefinition
    for name, agent in subagents.items():
        assert isinstance(agent, AgentDefinition)
        assert agent.description is not None
        assert len(agent.description) > 0
        assert agent.tools is not None
        assert len(agent.tools) > 0
        assert agent.prompt is not None
        assert len(agent.prompt) > 0
        assert agent.model == "haiku"


def test_get_agent_config_default_no_subagents():
    """Test that default agent has no subagents."""
    config = get_agent_config("default")
    subagents = config.get_subagents()
    assert subagents is None


def test_get_agent_config_marketing_no_subagents():
    """Test that marketing agent has no subagents."""
    config = get_agent_config("marketing")
    subagents = config.get_subagents()
    assert subagents is None


def test_get_agent_config_invalid():
    """Test that invalid agent type raises ValueError."""
    with pytest.raises(ValueError) as exc_info:
        get_agent_config("nonexistent")
    assert "Unknown agent type" in str(exc_info.value)
    assert "nonexistent" in str(exc_info.value)


def test_agent_config_get_allowed_tools_defaults():
    """Test that AgentConfig returns default tools when allowed_tools is empty."""
    config = AgentConfig(
        name="test",
        display_name="Test",
        description="Test agent",
        system_prompt="Test prompt",
        allowed_tools=[],  # Empty = use defaults
    )
    tools = config.get_allowed_tools()
    # Should return default tools from registry
    assert isinstance(tools, list)
    assert len(tools) > 0


def test_agent_config_get_allowed_tools_custom():
    """Test that AgentConfig returns custom tools when specified."""
    custom_tools = ["Read", "Write", "WebSearch(*)"]
    config = AgentConfig(
        name="test",
        display_name="Test",
        description="Test agent",
        system_prompt="Test prompt",
        allowed_tools=custom_tools,
    )
    tools = config.get_allowed_tools()
    assert tools == custom_tools


def test_agent_config_get_mcp_servers_defaults():
    """Test that AgentConfig returns default MCP servers when None."""
    config = AgentConfig(
        name="test",
        display_name="Test",
        description="Test agent",
        system_prompt="Test prompt",
        mcp_servers=None,  # None = use defaults
    )
    servers = config.get_mcp_servers()
    # Should return default servers from registry
    assert isinstance(servers, dict)
    assert "utilities" in servers


def test_agent_config_get_mcp_servers_custom():
    """Test that AgentConfig returns custom MCP servers when specified."""
    custom_servers = {"custom_server": "custom_value"}
    config = AgentConfig(
        name="test",
        display_name="Test",
        description="Test agent",
        system_prompt="Test prompt",
        mcp_servers=custom_servers,
    )
    servers = config.get_mcp_servers()
    assert servers == custom_servers


def test_agent_config_get_subagents_none():
    """Test that AgentConfig returns None when no subagents configured."""
    config = AgentConfig(
        name="test",
        display_name="Test",
        description="Test agent",
        system_prompt="Test prompt",
    )
    subagents = config.get_subagents()
    assert subagents is None


def test_agent_config_get_subagents_custom():
    """Test that AgentConfig returns subagents when configured."""
    custom_subagents = {
        "helper": AgentDefinition(
            description="Test helper",
            tools=["Read"],
            prompt="Test prompt",
            model="haiku",
        ),
    }
    config = AgentConfig(
        name="test",
        display_name="Test",
        description="Test agent",
        system_prompt="Test prompt",
        subagents=custom_subagents,
    )
    subagents = config.get_subagents()
    assert subagents == custom_subagents
    assert "helper" in subagents


def test_build_agent_options_with_resume():
    """Test building agent options with session resume."""
    mcp_servers = get_mcp_servers()
    allowed_tools = get_allowed_tools()

    options = build_agent_options(
        mcp_servers,
        allowed_tools,
        SYSTEM_PROMPT,
        resume="session-123",
        fork_session=True,
        max_turns=50,
    )

    assert options.resume == "session-123"
    assert options.fork_session is True
    assert options.max_turns == 50


def test_build_agent_options_with_subagents():
    """Test building agent options with SDK native subagents."""
    mcp_servers = get_mcp_servers()
    allowed_tools = get_allowed_tools()

    # Create test subagents
    test_subagents = {
        "helper": AgentDefinition(
            description="A helper subagent for testing",
            tools=["Read", "Write"],
            prompt="You are a helpful assistant.",
            model="haiku",
        ),
    }

    options = build_agent_options(
        mcp_servers,
        allowed_tools,
        SYSTEM_PROMPT,
        subagents=test_subagents,
    )

    # Verify subagents are passed through to the options
    assert options.agents is not None
    assert options.agents == test_subagents
    assert "helper" in options.agents


def test_build_agent_options_without_subagents():
    """Test building agent options without subagents (default case)."""
    mcp_servers = get_mcp_servers()
    allowed_tools = get_allowed_tools()

    options = build_agent_options(
        mcp_servers,
        allowed_tools,
        SYSTEM_PROMPT,
    )

    # Without subagents, agents should be None
    assert options.agents is None


@pytest.mark.slow
def test_run_agent_integration():
    """Integration test for agent execution (requires API key)."""
    # This test requires a valid Anthropic API key and will make actual API calls
    # Marked as slow to avoid running in fast CI
    pytest.skip("Requires API key and makes real API calls")


if __name__ == "__main__":
    pytest.main([__file__])
