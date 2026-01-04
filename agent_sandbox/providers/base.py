"""Provider interfaces for agent SDK integrations."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

import modal

from agent_sandbox.config.settings import Settings


class AgentClient(Protocol):
    """Async client protocol for agent SDKs."""

    async def query(self, question: str) -> None:  # pragma: no cover - interface
        ...

    async def receive_response(self) -> AsyncIterator[Any]:  # pragma: no cover - interface
        ...

    async def __aenter__(self):  # pragma: no cover - interface
        ...

    async def __aexit__(self, exc_type, exc, tb):  # pragma: no cover - interface
        ...


class AgentProvider(Protocol):
    """Interface for agent provider implementations."""

    provider_id: str
    display_name: str

    def capabilities(self) -> dict[str, bool]:  # pragma: no cover - interface
        ...

    def required_secrets(self, settings: Settings) -> list[modal.Secret]: ...

    def get_mcp_servers(self) -> dict[str, Any]: ...

    def get_allowed_tools(self) -> list[str]: ...

    def build_options(
        self,
        *,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        allowed_tools: list[str],
        session_id: str | None,
        fork_session: bool,
        max_turns: int | None,
        provider_config: dict[str, Any] | None = None,
        permission_mode: str | None = None,
        can_use_tool: Any | None = None,
    ) -> Any: ...

    def create_client(self, options: Any) -> AgentClient: ...

    def serialize_message(self, message: Any) -> dict[str, Any]: ...

    def build_summary(self, messages: list[Any]) -> dict[str, Any]: ...
