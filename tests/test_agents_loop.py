"""Tests for agent loop execution and multi-agent architecture."""

from pathlib import Path
from uuid import uuid4

import pytest
from agents import Agent, SQLiteSession

from modal_backend.agent_runtime import build_agent_options, ensure_session
from modal_backend.agent_runtime.base import AgentConfig, SubAgentConfig
from modal_backend.agent_runtime.registry import get_agent_config, list_agent_types
from modal_backend.instructions.prompts import SYSTEM_PROMPT
from modal_backend.mcp_tools import get_allowed_tools, get_mcp_servers


def test_build_agent_options(mock_settings):
    """Test that agent options are built correctly."""
    mcp_servers = get_mcp_servers()
    allowed_tools = get_allowed_tools()

    agent = build_agent_options(mcp_servers, allowed_tools, SYSTEM_PROMPT)

    assert agent is not None
    assert isinstance(agent, Agent)
    assert agent.instructions == SYSTEM_PROMPT
    assert len(agent.tools) > 0


def test_list_agent_types():
    types = list_agent_types()
    assert isinstance(types, list)
    assert "default" in types
    assert "marketing" in types
    assert "research" in types
    assert len(types) >= 3


def test_get_agent_config_default():
    config = get_agent_config("default")
    assert isinstance(config, AgentConfig)
    assert config.name == "default"
    assert config.display_name == "Default Agent"
    assert config.system_prompt is not None
    assert len(config.system_prompt) > 0


def test_get_agent_config_marketing():
    config = get_agent_config("marketing")
    assert isinstance(config, AgentConfig)
    assert config.name == "marketing"
    assert config.display_name == "Marketing Agent"
    assert "marketing" in config.description.lower()
    assert config.max_turns == 30
    assert config.can_spawn_subagents is False


def test_get_agent_config_research():
    config = get_agent_config("research")
    assert isinstance(config, AgentConfig)
    assert config.name == "research"
    assert config.display_name == "Research Agent"
    assert "research" in config.description.lower()
    assert config.max_turns == 50
    assert config.can_spawn_subagents is True
    assert "mcp__sessions__spawn_session" in config.allowed_tools


def test_get_agent_config_research_effective_tools():
    config = get_agent_config("research")
    effective_tools = config.get_effective_allowed_tools()

    assert "Bash" in effective_tools


def test_get_agent_config_research_subagents():
    config = get_agent_config("research")
    subagents = config.get_subagents()

    assert subagents is not None
    assert isinstance(subagents, dict)
    assert len(subagents) == 3

    assert "researcher" in subagents
    assert "data-analyst" in subagents
    assert "report-writer" in subagents

    for name, agent in subagents.items():
        assert isinstance(agent, SubAgentConfig)
        assert agent.description is not None
        assert len(agent.description) > 0
        assert agent.tools is not None
        assert len(agent.tools) > 0
        assert agent.prompt is not None
        assert len(agent.prompt) > 0
        assert agent.model == "gpt-4.1-mini"


def test_get_agent_config_default_no_subagents():
    config = get_agent_config("default")
    subagents = config.get_subagents()
    assert subagents is None


def test_get_agent_config_marketing_no_subagents():
    config = get_agent_config("marketing")
    subagents = config.get_subagents()
    assert subagents is None


def test_get_agent_config_invalid():
    with pytest.raises(ValueError) as exc_info:
        get_agent_config("nonexistent")
    assert "Unknown agent type" in str(exc_info.value)
    assert "nonexistent" in str(exc_info.value)


def test_agent_config_get_allowed_tools_defaults():
    config = AgentConfig(
        name="test",
        display_name="Test",
        description="Test agent",
        system_prompt="Test prompt",
        allowed_tools=[],
    )
    tools = config.get_allowed_tools()
    assert isinstance(tools, list)
    assert len(tools) > 0


def test_agent_config_get_allowed_tools_custom():
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
    config = AgentConfig(
        name="test",
        display_name="Test",
        description="Test agent",
        system_prompt="Test prompt",
        mcp_servers=None,
    )
    servers = config.get_mcp_servers()
    assert isinstance(servers, dict)


def test_agent_config_get_mcp_servers_custom():
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
    config = AgentConfig(
        name="test",
        display_name="Test",
        description="Test agent",
        system_prompt="Test prompt",
    )
    subagents = config.get_subagents()
    assert subagents is None


def test_agent_config_get_subagents_custom():
    custom_subagents = {
        "helper": SubAgentConfig(
            description="Test helper",
            tools=["Read"],
            prompt="Test prompt",
            model="gpt-4.1-mini",
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


def test_build_agent_options_with_subagents():
    mcp_servers = get_mcp_servers()
    allowed_tools = get_allowed_tools()

    test_subagents = {
        "helper": SubAgentConfig(
            description="A helper subagent for testing",
            tools=["Read", "Write"],
            prompt="You are a helpful assistant.",
            model="gpt-4.1-mini",
        ),
    }

    agent = build_agent_options(
        mcp_servers,
        allowed_tools,
        SYSTEM_PROMPT,
        subagents=test_subagents,
    )

    assert isinstance(agent, Agent)
    assert agent.handoffs is not None
    assert len(agent.handoffs) == 1
    assert agent.handoffs[0].tool_name == "transfer_to_helper"


def test_build_agent_options_without_subagents():
    mcp_servers = get_mcp_servers()
    allowed_tools = get_allowed_tools()

    agent = build_agent_options(
        mcp_servers,
        allowed_tools,
        SYSTEM_PROMPT,
    )

    assert isinstance(agent, Agent)
    assert agent.handoffs == []


@pytest.mark.asyncio
async def test_ensure_session_new(tmp_path: Path):
    db_path = str(tmp_path / "sessions.sqlite")
    session, session_id = await ensure_session(None, fork_session=False, db_path=db_path)

    assert isinstance(session, SQLiteSession)
    assert isinstance(session_id, str)
    assert len(session_id) > 0


@pytest.mark.asyncio
async def test_ensure_session_fork_copies_history(tmp_path: Path):
    db_path = str(tmp_path / "sessions.sqlite")
    source_id = str(uuid4())
    source = SQLiteSession(source_id, db_path=db_path)
    await source.add_items([{"role": "user", "content": "hello"}])

    forked, forked_id = await ensure_session(source_id, fork_session=True, db_path=db_path)
    copied = await forked.get_items()

    assert forked_id != source_id
    assert len(copied) == 1


@pytest.mark.asyncio
async def test_ensure_session_resume_uses_existing_history(tmp_path: Path):
    db_path = str(tmp_path / "sessions.sqlite")
    source_id = str(uuid4())
    source = SQLiteSession(source_id, db_path=db_path)
    await source.add_items([{"role": "user", "content": "remember this"}])

    resumed, resumed_id = await ensure_session(source_id, fork_session=False, db_path=db_path)
    items = await resumed.get_items()

    assert resumed_id == source_id
    assert len(items) == 1


@pytest.mark.slow
def test_run_agent_integration():
    pytest.skip("Requires OPENAI_API_KEY and makes real API calls")


if __name__ == "__main__":
    pytest.main([__file__])
