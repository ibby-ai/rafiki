"""Agent type implementations.

This package contains the built-in agent type configurations:
- default: General-purpose coding agent (maintains backward compatibility)
- marketing: Marketing content and campaign specialist
- research: Multi-agent research coordinator

Each module exports a factory function that returns an AgentConfig instance.
"""

from modal_backend.agent_runtime.types.default import default_agent_config
from modal_backend.agent_runtime.types.marketing import marketing_agent_config
from modal_backend.agent_runtime.types.research import research_agent_config

__all__ = ["default_agent_config", "marketing_agent_config", "research_agent_config"]
