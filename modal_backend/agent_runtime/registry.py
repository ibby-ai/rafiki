"""Agent registry for managing agent types.

This module provides a singleton registry for agent configurations, allowing
the system to look up and instantiate different agent types by name.

The registry is lazily initialized on first access, registering all default
agent types (default, marketing, research).

Usage:
    from modal_backend.agent_runtime.registry import get_agent_config, get_agent_executor

    # Get configuration for an agent type
    config = get_agent_config("marketing")
    print(config.system_prompt)

    # Get an executor for an agent type
    executor = get_agent_executor("research")
    async for msg in executor.execute("Research AI trends"):
        print(msg)

    # List all available agent types
    from modal_backend.agent_runtime.registry import list_agent_types
    print(list_agent_types())  # ["default", "marketing", "research"]
"""

from modal_backend.agent_runtime.base import AgentConfig, AgentExecutor, ClaudeAgentExecutor


class AgentRegistry:
    """Singleton registry for agent types.

    Manages a collection of AgentConfig instances, allowing lookup by name
    and creation of AgentExecutor instances.

    This is a singleton - all calls to AgentRegistry() return the same instance.
    """

    _instance: "AgentRegistry | None" = None

    def __new__(cls) -> "AgentRegistry":
        """Return the singleton instance, creating it if needed."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._agents: dict[str, AgentConfig] = {}
            cls._instance._initialized = False
        return cls._instance

    def _ensure_initialized(self) -> None:
        """Ensure default agents are registered (lazy initialization)."""
        if self._initialized:
            return
        self._initialized = True
        self._initialize_defaults()

    def _initialize_defaults(self) -> None:
        """Initialize default agent types.

        Imports and registers the built-in agent configurations:
        - default: General-purpose coding agent (current behavior)
        - marketing: Marketing content specialist
        - research: Multi-agent research coordinator
        """
        from modal_backend.agent_runtime.types.default import default_agent_config
        from modal_backend.agent_runtime.types.marketing import marketing_agent_config
        from modal_backend.agent_runtime.types.research import research_agent_config

        self.register(default_agent_config())
        self.register(marketing_agent_config())
        self.register(research_agent_config())

    def register(self, config: AgentConfig) -> None:
        """Register an agent configuration.

        Args:
            config: AgentConfig to register. Uses config.name as the key.
        """
        self._agents[config.name] = config

    def get(self, name: str) -> AgentConfig:
        """Get an agent configuration by name.

        Args:
            name: The agent type name (e.g., "marketing", "research").

        Returns:
            The AgentConfig for the specified agent type.

        Raises:
            ValueError: If no agent with that name is registered.
        """
        self._ensure_initialized()
        if name not in self._agents:
            available = ", ".join(sorted(self._agents.keys()))
            raise ValueError(f"Unknown agent type: {name!r}. Available: {available}")
        return self._agents[name]

    def get_executor(self, name: str) -> AgentExecutor:
        """Get an executor for an agent type.

        Creates a ClaudeAgentExecutor configured for the specified agent type.

        Args:
            name: The agent type name.

        Returns:
            An AgentExecutor instance ready to handle queries.

        Raises:
            ValueError: If no agent with that name is registered.
        """
        config = self.get(name)
        return ClaudeAgentExecutor(config)

    def list_agents(self) -> list[str]:
        """List all registered agent type names.

        Returns:
            Sorted list of agent type names.
        """
        self._ensure_initialized()
        return sorted(self._agents.keys())

    def get_all_configs(self) -> dict[str, AgentConfig]:
        """Get all registered agent configurations.

        Returns:
            Dictionary mapping agent names to their configurations.
        """
        self._ensure_initialized()
        return self._agents.copy()


# Global registry instance
_registry = AgentRegistry()


def get_agent_config(name: str) -> AgentConfig:
    """Get an agent configuration by name.

    Args:
        name: The agent type name (e.g., "default", "marketing", "research").

    Returns:
        The AgentConfig for the specified agent type.

    Raises:
        ValueError: If no agent with that name is registered.
    """
    return _registry.get(name)


def get_agent_executor(name: str) -> AgentExecutor:
    """Get an executor for an agent type.

    Creates an AgentExecutor configured for the specified agent type.

    Args:
        name: The agent type name.

    Returns:
        An AgentExecutor instance ready to handle queries.

    Raises:
        ValueError: If no agent with that name is registered.
    """
    return _registry.get_executor(name)


def list_agent_types() -> list[str]:
    """List all registered agent type names.

    Returns:
        Sorted list of available agent type names.
    """
    return _registry.list_agents()


def register_agent(config: AgentConfig) -> None:
    """Register a custom agent configuration.

    Use this to add custom agent types at runtime.

    Args:
        config: AgentConfig to register.
    """
    _registry.register(config)
