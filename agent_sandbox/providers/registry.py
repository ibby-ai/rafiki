"""Provider registry for agent SDK integrations."""

from __future__ import annotations

from collections.abc import Callable

from agent_sandbox.providers.base import AgentProvider


class ProviderRegistry:
    """Registry for agent providers keyed by provider_id."""

    _providers: dict[str, AgentProvider] = {}

    @classmethod
    def register(cls, provider_id: str) -> Callable[[type[AgentProvider]], type[AgentProvider]]:
        def decorator(provider_cls: type[AgentProvider]) -> type[AgentProvider]:
            instance = provider_cls()
            instance.provider_id = provider_id
            cls._providers[provider_id] = instance
            return provider_cls

        return decorator

    @classmethod
    def get(cls, provider_id: str) -> AgentProvider:
        provider = cls._providers.get(provider_id)
        if not provider:
            available = ", ".join(sorted(cls._providers)) or "<none>"
            raise ValueError(f"Unknown agent provider '{provider_id}'. Available: {available}")
        return provider

    @classmethod
    def list_available(cls) -> list[str]:
        return sorted(cls._providers.keys())


# Register built-in providers
from agent_sandbox.providers import claude as _claude  # noqa: E402,F401


def get_provider(provider_id: str) -> AgentProvider:
    return ProviderRegistry.get(provider_id)


def list_providers() -> list[str]:
    return ProviderRegistry.list_available()
