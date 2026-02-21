"""Research agent configuration.

Multi-agent research coordinator using OpenAI handoffs.
"""

from modal_backend.agent_runtime.base import AgentConfig, SubAgentConfig
from modal_backend.instructions.research import RESEARCH_SYSTEM_PROMPT
from modal_backend.instructions.subagents import (
    DATA_ANALYST_PROMPT,
    REPORT_WRITER_PROMPT,
    RESEARCHER_PROMPT,
)


def research_agent_config() -> AgentConfig:
    """Create the research agent configuration with OpenAI handoffs."""
    subagents = {
        "researcher": SubAgentConfig(
            description=(
                "Use this agent to gather research information on any topic. "
                "The researcher uses web search to find relevant information and sources. "
                "Writes findings to /data/research_notes/ for later use."
            ),
            tools=["WebSearch(*)", "WebFetch(*)", "Write"],
            prompt=RESEARCHER_PROMPT,
            model="gpt-4.1-mini",
        ),
        "data-analyst": SubAgentConfig(
            description=(
                "Use this agent AFTER researchers complete their work. "
                "Reads research notes, extracts numerical data, and creates charts. "
                "Saves charts to /data/charts/ and summaries to /data/data/."
            ),
            tools=["Glob", "Read", "Bash", "Write"],
            prompt=DATA_ANALYST_PROMPT,
            model="gpt-4.1-mini",
        ),
        "report-writer": SubAgentConfig(
            description=(
                "Use this agent to create a formal research report. "
                "Reads research notes and data, then synthesizes into a report. "
                "Saves reports to /data/reports/."
            ),
            tools=["Glob", "Read", "Write", "Bash"],
            prompt=REPORT_WRITER_PROMPT,
            model="gpt-4.1-mini",
        ),
    }

    return AgentConfig(
        name="research",
        display_name="Research Agent",
        description=(
            "Multi-agent research system with OpenAI handoff-based delegation. "
            "Uses specialized subagents for information gathering, analysis, and reporting."
        ),
        system_prompt=RESEARCH_SYSTEM_PROMPT,
        allowed_tools=[
            "Read",
            "Write",
            "Glob",
            "Bash",
            "WebSearch(*)",
            "WebFetch(*)",
            "mcp__sessions__spawn_session",
            "mcp__sessions__check_session_status",
            "mcp__sessions__get_session_result",
            "mcp__sessions__list_child_sessions",
        ],
        max_turns=50,
        mcp_servers=None,
        can_spawn_subagents=True,
        subagent_types=["default", "marketing", "research"],
        subagents=subagents,
    )
