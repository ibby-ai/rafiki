"""Base abstractions for agent configuration and execution."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from agents import Agent, Runner, SQLiteSession, handoff

_logger = logging.getLogger(__name__)


@dataclass
class SubAgentConfig:
    """Configuration for delegated sub-agents/handoffs."""

    description: str
    prompt: str
    tools: list[str]
    model: str | None = None


@dataclass
class AgentConfig:
    """Configuration for an agent type."""

    name: str
    display_name: str
    description: str
    system_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    max_turns: int | None = None
    mcp_servers: dict[str, Any] | None = None
    can_spawn_subagents: bool = False
    subagent_types: list[str] = field(default_factory=list)
    subagents: dict[str, SubAgentConfig] | None = None

    def get_allowed_tools(self) -> list[str]:
        if self.allowed_tools:
            return self.allowed_tools
        from modal_backend.mcp_tools import get_allowed_tools

        return get_allowed_tools()

    def get_mcp_servers(self) -> dict[str, Any]:
        if self.mcp_servers is not None:
            return self.mcp_servers
        from modal_backend.mcp_tools import get_mcp_servers

        return get_mcp_servers()

    def get_subagents(self) -> dict[str, SubAgentConfig] | None:
        return self.subagents

    def get_subagent_tools(self) -> list[str]:
        if not self.subagents:
            return []

        tools: list[str] = []
        for cfg in self.subagents.values():
            for tool in cfg.tools:
                if tool not in tools:
                    tools.append(tool)
        return tools

    def get_effective_allowed_tools(self) -> list[str]:
        tools = list(self.get_allowed_tools())
        for tool in self.get_subagent_tools():
            if tool not in tools:
                tools.append(tool)
        return tools


async def ensure_session(
    session_id: str | None,
    *,
    fork_session: bool,
    db_path: str,
) -> tuple[SQLiteSession, str]:
    """Return a SQLiteSession, cloning history on fork when requested."""
    from modal_backend.settings.settings import get_settings

    settings = get_settings()
    max_items = settings.openai_session_max_items
    keep_items = settings.openai_session_compaction_keep_items

    db_file = Path(db_path)
    if str(db_file) != ":memory:":
        db_file.parent.mkdir(parents=True, exist_ok=True)

    if session_id and fork_session:
        new_session_id = str(uuid4())
        source = SQLiteSession(session_id, db_path=db_path)
        target = SQLiteSession(new_session_id, db_path=db_path)
        items = await source.get_items()
        if items:
            await target.add_items(items)
            await _compact_session_history(
                target,
                session_id=new_session_id,
                max_items=max_items,
                keep_items=keep_items,
            )
        return target, new_session_id

    resolved = session_id or str(uuid4())
    session = SQLiteSession(resolved, db_path=db_path)
    await _compact_session_history(
        session,
        session_id=resolved,
        max_items=max_items,
        keep_items=keep_items,
    )
    return session, resolved


async def _compact_session_history(
    session: SQLiteSession,
    *,
    session_id: str,
    max_items: int | None,
    keep_items: int | None,
) -> None:
    """Trim session history to deterministic bounds when configured."""
    if max_items is None:
        return

    items = await session.get_items()
    item_count = len(items)
    if item_count <= max_items:
        return

    retained_items = keep_items if keep_items is not None else max_items
    retained_items = min(retained_items, max_items)
    trimmed = items[-retained_items:]

    await session.clear_session()
    await session.add_items(trimmed)
    _logger.info(
        "openai.session.compacted",
        extra={
            "session_id": session_id,
            "items_before": item_count,
            "items_after": retained_items,
            "max_items": max_items,
            "keep_items": retained_items,
        },
    )


def build_agent_options(
    mcp_servers: dict[str, Any],
    allowed_tools: list[str],
    system_prompt: str,
    subagents: dict[str, SubAgentConfig] | None = None,
) -> Agent[Any]:
    """Create an OpenAI Agent configured for this runtime."""
    from modal_backend.mcp_tools import build_tools_for_allowed
    from modal_backend.settings.settings import get_settings
    from modal_backend.tracing import ensure_langsmith_configured

    _ = mcp_servers

    settings = get_settings()
    ensure_langsmith_configured()

    tools = build_tools_for_allowed(allowed_tools)

    handoffs_list = []
    if subagents:
        for name, cfg in subagents.items():
            subagent_tools = build_tools_for_allowed(cfg.tools)
            subagent = Agent(
                name=name,
                handoff_description=cfg.description,
                instructions=cfg.prompt,
                tools=subagent_tools,
                model=cfg.model or settings.openai_model_subagent,
            )
            handoff_name = f"transfer_to_{name.replace('-', '_')}"
            handoffs_list.append(
                handoff(
                    agent=subagent,
                    tool_name_override=handoff_name,
                    tool_description_override=cfg.description,
                )
            )

    return Agent(
        name="assistant",
        instructions=system_prompt,
        tools=tools,
        handoffs=handoffs_list,
        model=settings.openai_model_default,
    )


@dataclass
class ExecutionContext:
    job_id: str | None = None
    job_root: str | None = None
    user_id: str | None = None
    parent_session_id: str | None = None


class AgentExecutor(ABC):
    @abstractmethod
    async def execute(
        self,
        question: str,
        session_id: str | None = None,
        fork_session: bool = False,
        context: ExecutionContext | None = None,
    ) -> AsyncIterable[dict[str, Any]]: ...


class OpenAIAgentExecutor(AgentExecutor):
    """OpenAI Agents SDK implementation of AgentExecutor."""

    def __init__(self, config: AgentConfig):
        self.config = config

    async def execute(
        self,
        question: str,
        session_id: str | None = None,
        fork_session: bool = False,
        context: ExecutionContext | None = None,
    ) -> AsyncIterable[dict[str, Any]]:
        from modal_backend.settings.settings import get_settings
        from modal_backend.tracing import langsmith_run_context

        settings = get_settings()
        max_turns = self.config.max_turns or settings.agent_max_turns or 50

        system_prompt = self.config.system_prompt
        if context and context.job_root:
            system_prompt = (
                f"{system_prompt}\n\n"
                "This is a background job. "
                f"Write all created files under {context.job_root} so they are persisted."
            )

        agent = build_agent_options(
            self.config.get_mcp_servers(),
            self.config.get_effective_allowed_tools(),
            system_prompt,
            subagents=self.config.get_subagents(),
        )

        session, resolved_session_id = await ensure_session(
            session_id,
            fork_session=fork_session,
            db_path=settings.openai_session_db_path,
        )

        metadata = {
            "agent_type": self.config.name,
            "session_id": resolved_session_id,
            "job_id": context.job_id if context else None,
            "user_id": context.user_id if context else None,
        }
        with langsmith_run_context(metadata):
            result = await Runner.run(
                agent,
                question,
                session=session,
                max_turns=max_turns,
            )

        yield {
            "type": "result",
            "session_id": resolved_session_id,
            "result": result.final_output,
            "num_turns": max_turns,
        }
