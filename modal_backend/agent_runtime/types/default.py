"""Default agent configuration.

This maintains backward compatibility with the original agent behavior.
Uses the existing SYSTEM_PROMPT and default tool configuration.
"""

from modal_backend.agent_runtime.base import AgentConfig
from modal_backend.instructions.prompts import SYSTEM_PROMPT


def default_agent_config() -> AgentConfig:
    """Create the default agent configuration.

    This agent type maintains the original behavior:
    - General-purpose coding assistant
    - Access to all default tools
    - Uses the original SYSTEM_PROMPT

    Returns:
        AgentConfig for the default agent type.
    """
    return AgentConfig(
        name="default",
        display_name="Default Agent",
        description="General-purpose coding agent with access to all standard tools. "
        "Maintains backward compatibility with the original agent behavior.",
        system_prompt=SYSTEM_PROMPT,
        # Empty list = use default tools from registry
        allowed_tools=[],
        max_turns=None,  # Use global setting
        mcp_servers=None,  # Use default servers
        can_spawn_subagents=True,  # Default can spawn children
        subagent_types=["default", "marketing", "research"],
    )
