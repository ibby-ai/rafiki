"""Research agent configuration.

Multi-agent research coordinator with dual orchestration support:
1. SDK Native Subagents (AgentDefinition) - In-process, low latency
2. Job Spawning (spawn_session tools) - Parallel, isolated execution

This agent demonstrates how to combine both mechanisms for flexible
multi-agent workflows.
"""

from claude_agent_sdk import AgentDefinition

from modal_backend.agent_runtime.base import AgentConfig
from modal_backend.instructions.research import RESEARCH_SYSTEM_PROMPT
from modal_backend.instructions.subagents import (
    DATA_ANALYST_PROMPT,
    REPORT_WRITER_PROMPT,
    RESEARCHER_PROMPT,
)


def research_agent_config() -> AgentConfig:
    """Create the research agent configuration with dual orchestration support.

    This agent type coordinates comprehensive research using two complementary
    orchestration mechanisms:

    1. SDK Native Subagents (AgentDefinition + Task tool):
       - researcher: Gathers information from web sources
       - data-analyst: Analyzes data and creates visualizations
       - report-writer: Synthesizes findings into reports
       These run in-process with low latency, ideal for sequential workflows.

    2. Job Spawning (spawn_session tools):
       - spawn_session: Create parallel child jobs
       - check_session_status: Monitor progress
       - get_session_result: Collect results
       - list_child_sessions: See all children
       These run in isolated sandboxes, ideal for parallel investigation.

    Returns:
        AgentConfig for the research agent type.
    """
    # Define SDK native subagents using AgentDefinition
    subagents = {
        "researcher": AgentDefinition(
            description=(
                "Use this agent to gather research information on any topic. "
                "The researcher uses web search to find relevant information and sources. "
                "Writes findings to /data/research_notes/ for later use."
            ),
            tools=["WebSearch", "WebFetch", "Write"],
            prompt=RESEARCHER_PROMPT,
            model="haiku",
        ),
        "data-analyst": AgentDefinition(
            description=(
                "Use this agent AFTER researchers complete their work. "
                "Reads research notes, extracts numerical data, and creates charts. "
                "Saves charts to /data/charts/ and summaries to /data/data/."
            ),
            tools=["Glob", "Read", "Bash", "Write"],
            prompt=DATA_ANALYST_PROMPT,
            model="haiku",
        ),
        "report-writer": AgentDefinition(
            description=(
                "Use this agent to create a formal research report. "
                "Reads research notes and data, then synthesizes into a report. "
                "Saves reports to /data/reports/."
            ),
            tools=["Glob", "Read", "Write", "Bash"],
            prompt=REPORT_WRITER_PROMPT,
            model="haiku",
        ),
    }

    return AgentConfig(
        name="research",
        display_name="Research Agent",
        description=(
            "Multi-agent research system with dual orchestration support. "
            "Uses SDK native subagents for sequential tasks and job spawning "
            "for parallel investigation."
        ),
        system_prompt=RESEARCH_SYSTEM_PROMPT,
        allowed_tools=[
            # SDK Native subagent delegation (in-process, low latency)
            "Task",
            # File operations for notes and reports
            "Read",
            "Write",
            "Glob",
            # Web tools for direct research by lead agent
            "WebSearch(*)",
            "WebFetch(*)",
            # Job-based spawning (parallel, isolated execution)
            "mcp__sessions__spawn_session",
            "mcp__sessions__check_session_status",
            "mcp__sessions__get_session_result",
            "mcp__sessions__list_child_sessions",
        ],
        max_turns=50,  # Research may need more turns
        mcp_servers=None,  # Use default servers
        can_spawn_subagents=True,  # Research agent coordinates subagents
        subagent_types=["default", "marketing", "research"],
        subagents=subagents,  # SDK native subagent definitions
    )
