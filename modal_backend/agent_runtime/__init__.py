"""Agent orchestration and execution logic.

This package provides the multi-agent architecture with:
- AgentConfig: Dataclass defining agent behavior
- AgentExecutor: Abstract interface for agent execution
- AgentRegistry: Singleton for managing agent types
- build_agent_options: Central function for building ClaudeAgentOptions

Core exports for backward compatibility and common usage:
    from modal_backend.agent_runtime import build_agent_options
    from modal_backend.agent_runtime import get_agent_config, list_agent_types
"""

# Re-export from base.py for backward compatibility
from modal_backend.agent_runtime.base import (
    AgentConfig,
    AgentExecutor,
    ClaudeAgentExecutor,
    ExecutionContext,
    build_agent_options,
)

# Re-export from registry.py
from modal_backend.agent_runtime.registry import (
    get_agent_config,
    get_agent_executor,
    list_agent_types,
    register_agent,
)

__all__ = [
    # Base classes and functions
    "AgentConfig",
    "AgentExecutor",
    "ClaudeAgentExecutor",
    "ExecutionContext",
    "build_agent_options",
    # Registry functions
    "get_agent_config",
    "get_agent_executor",
    "list_agent_types",
    "register_agent",
]
