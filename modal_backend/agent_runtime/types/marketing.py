"""Marketing agent configuration.

Specialized agent for marketing content, copywriting, and campaign analysis.
Has access to web search tools for research but limited file system access.
"""

from modal_backend.agent_runtime.base import AgentConfig
from modal_backend.instructions.marketing import MARKETING_SYSTEM_PROMPT


def marketing_agent_config() -> AgentConfig:
    """Create the marketing agent configuration.

    This agent type specializes in marketing tasks:
    - Content creation (blog posts, social media, email campaigns)
    - Brand voice development
    - Market research and competitive analysis
    - Campaign strategy and performance analysis

    Returns:
        AgentConfig for the marketing agent type.
    """
    return AgentConfig(
        name="marketing",
        display_name="Marketing Agent",
        description="Specialized in marketing content, copywriting, and campaign analysis. "
        "Has web search access for market research.",
        system_prompt=MARKETING_SYSTEM_PROMPT,
        allowed_tools=[
            # File operations for content creation
            "Read",
            "Write",
            # Web tools for research
            "WebSearch(*)",
            "WebFetch(*)",
            # Utility tools
            "mcp__utilities__calculate",
        ],
        max_turns=30,
        mcp_servers=None,  # Use default servers
        can_spawn_subagents=False,  # Marketing agent is a leaf worker
        subagent_types=[],
    )
