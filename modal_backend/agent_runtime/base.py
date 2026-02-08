"""Base abstractions for agent configuration and execution.

This module defines the core interfaces for the multi-agent architecture:
- AgentConfig: Dataclass defining agent behavior (prompts, tools, capabilities)
- AgentExecutor: Abstract base class for agent execution
- ClaudeAgentExecutor: Default implementation using Claude Agent SDK
- build_agent_options: Central function for building ClaudeAgentOptions

The architecture supports:
- Multiple agent types with different prompts and tool access
- Agent-specific MCP server configurations
- Orchestration via subagent spawning (job-based)
- SDK native subagents via AgentDefinition (in-process)
- Future framework flexibility via the AgentExecutor interface
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterable
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import Message


@dataclass
class AgentConfig:
    """Configuration for an agent type.

    Defines the behavior, capabilities, and constraints for a specific type
    of agent (e.g., marketing, research, default).

    Attributes:
        name: Unique identifier for this agent type (e.g., "marketing", "research")
        display_name: Human-readable name (e.g., "Marketing Agent")
        description: What this agent does and when to use it
        system_prompt: Agent-specific system prompt for behavior shaping
        allowed_tools: Tools this agent can use (empty list = use defaults)
        max_turns: Override global max_turns setting (None = use default)
        mcp_servers: Agent-specific MCP servers (None = use defaults)
        can_spawn_subagents: Whether this agent can orchestrate other agents
        subagent_types: Which agent types this agent can spawn as children
        subagents: SDK native subagent definitions for in-process orchestration.
            When provided, the lead agent can use the built-in Task tool to
            delegate work to these subagents. This is complementary to job-based
            spawning via spawn_session tools.
    """

    name: str
    display_name: str
    description: str
    system_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    max_turns: int | None = None
    mcp_servers: dict[str, Any] | None = None
    can_spawn_subagents: bool = False
    subagent_types: list[str] = field(default_factory=list)
    subagents: dict[str, AgentDefinition] | None = None

    def get_allowed_tools(self) -> list[str]:
        """Return tools for this agent, with defaults if empty.

        Returns:
            List of tool names this agent can use. If allowed_tools is empty,
            returns the default tool list from the tool registry.
        """
        if self.allowed_tools:
            return self.allowed_tools
        from modal_backend.mcp_tools import get_allowed_tools

        return get_allowed_tools()

    def get_mcp_servers(self) -> dict[str, Any]:
        """Return MCP servers for this agent.

        Returns:
            Dictionary of MCP server name to server instance. If mcp_servers
            is None, returns the default servers from the tool registry.
        """
        if self.mcp_servers is not None:
            return self.mcp_servers
        from modal_backend.mcp_tools import get_mcp_servers

        return get_mcp_servers()

    def get_subagents(self) -> dict[str, AgentDefinition] | None:
        """Return SDK native subagent definitions for this agent.

        Returns:
            Dictionary mapping subagent names to AgentDefinition instances,
            or None if this agent has no subagents configured. When not None,
            the lead agent can use the built-in Task tool to delegate work
            to these subagents.

        See Also:
            - AgentDefinition: SDK class for defining subagents
            - Task tool: Built-in tool for delegating to subagents
        """
        return self.subagents

    def get_subagent_tools(self) -> list[str]:
        """Return tools used by SDK native subagents.

        Returns:
            List of tool names referenced by subagent definitions. Empty when
            no subagents are configured.
        """
        if not self.subagents:
            return []

        tools: list[str] = []
        for agent in self.subagents.values():
            if not agent.tools:
                continue
            for tool in agent.tools:
                if tool not in tools:
                    tools.append(tool)
        return tools

    def get_effective_allowed_tools(self) -> list[str]:
        """Return allowed tools plus any subagent tool dependencies."""
        tools = list(self.get_allowed_tools())
        for tool in self.get_subagent_tools():
            if tool not in tools:
                tools.append(tool)
        return tools


def build_agent_options(
    mcp_servers: dict[str, Any],
    allowed_tools: list[str],
    system_prompt: str,
    subagents: dict[str, AgentDefinition] | None = None,
    resume: str | None = None,
    fork_session: bool = False,
    max_turns: int | None = None,
) -> ClaudeAgentOptions:
    """Create ClaudeAgentOptions for agent execution.

    This is the central function for building agent options, used by:
    - CLI execution (loop.py)
    - Controller service (controller.py)
    - AgentRunner class (app.py)

    Args:
        mcp_servers: Mapping of MCP server name to server instance created by
            `create_sdk_mcp_server`.
        allowed_tools: Whitelist of tool names the agent is allowed to invoke.
        system_prompt: Behavior-shaping prompt for the agent.
        subagents: Optional dict of AgentDefinition objects for SDK native
            subagent orchestration. When provided, the lead agent can use
            the built-in Task tool to delegate work to these subagents.
        resume: Optional session ID to resume from.
        fork_session: Whether to fork the session (create new session from state).
        max_turns: Maximum conversation turns before stopping.

    Returns:
        A configured `ClaudeAgentOptions` instance.

    See also:
        Modal docs for sandbox execution and file mounting; tools are
        defined in `modal_backend.mcp_tools` and the environment is configured in
        `modal_backend.settings.settings`.
    """
    from modal_backend.tracing import ensure_langsmith_configured

    ensure_langsmith_configured()
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
        agents=subagents,
        resume=resume,
        fork_session=fork_session,
        max_turns=max_turns,
    )


@dataclass
class ExecutionContext:
    """Context for agent execution.

    Provides additional context that may affect agent behavior during execution.

    Attributes:
        job_id: Optional job identifier for background job execution.
        job_root: Optional workspace root path for file operations.
        user_id: Optional user identifier for attribution.
        parent_session_id: Optional parent session for child agents.
    """

    job_id: str | None = None
    job_root: str | None = None
    user_id: str | None = None
    parent_session_id: str | None = None


class AgentExecutor(ABC):
    """Abstract base class for agent execution.

    This interface allows future framework flexibility - different implementations
    could use different AI frameworks while maintaining a consistent interface.

    Subclasses must implement the execute() method which handles the actual
    agent interaction loop.
    """

    @abstractmethod
    async def execute(
        self,
        question: str,
        session_id: str | None = None,
        fork_session: bool = False,
        context: ExecutionContext | None = None,
    ) -> AsyncIterable[Message]:
        """Execute an agent query and yield response messages.

        Args:
            question: The user's question/prompt to the agent.
            session_id: Optional session ID to resume from.
            fork_session: Whether to fork the session.
            context: Optional execution context with job/user info.

        Yields:
            Message objects from the agent response stream.
        """
        ...


class ClaudeAgentExecutor(AgentExecutor):
    """Claude Agent SDK implementation of AgentExecutor.

    Uses the Claude Agent SDK to execute agent queries with the configuration
    defined in the associated AgentConfig.
    """

    def __init__(self, config: AgentConfig):
        """Initialize executor with agent configuration.

        Args:
            config: AgentConfig defining this agent's behavior.
        """
        self.config = config

    async def execute(
        self,
        question: str,
        session_id: str | None = None,
        fork_session: bool = False,
        context: ExecutionContext | None = None,
    ) -> AsyncIterable[Message]:
        """Execute an agent query using the Claude Agent SDK.

        Args:
            question: The user's question/prompt to the agent.
            session_id: Optional session ID to resume from.
            fork_session: Whether to fork the session.
            context: Optional execution context with job/user info.

        Yields:
            Message objects from the Claude SDK response stream.
        """
        from modal_backend.settings.settings import get_settings

        settings = get_settings()

        # Determine max_turns from config or settings
        max_turns = self.config.max_turns or settings.agent_max_turns

        # Build system prompt with context if needed
        system_prompt = self.config.system_prompt
        if context and context.job_root:
            system_prompt = (
                f"{system_prompt}\n\n"
                f"This is a background job. "
                f"Write all created files under {context.job_root} so they are persisted."
            )

        options = build_agent_options(
            self.config.get_mcp_servers(),
            self.config.get_effective_allowed_tools(),
            system_prompt,
            subagents=self.config.get_subagents(),
            resume=session_id,
            fork_session=fork_session,
            max_turns=max_turns,
        )

        async with ClaudeSDKClient(options=options) as client:
            await client.query(question)
            async for msg in client.receive_response():
                yield msg
