"""Image factory registry for agent sandbox runtimes."""

from __future__ import annotations

from collections.abc import Callable

import modal

from agent_sandbox.config.settings import Settings
from agent_sandbox.images.base import AgentImageBuilder


class ImageFactory:
    """Registry for image builders keyed by name."""

    _builders: dict[str, AgentImageBuilder] = {}

    @classmethod
    def register(cls, name: str) -> Callable[[type[AgentImageBuilder]], type[AgentImageBuilder]]:
        def decorator(builder_cls: type[AgentImageBuilder]) -> type[AgentImageBuilder]:
            instance = builder_cls()
            instance.name = name
            cls._builders[name] = instance
            return builder_cls

        return decorator

    @classmethod
    def build_image(cls, name: str, settings: Settings) -> modal.Image:
        builder = cls._builders.get(name)
        if not builder:
            available = ", ".join(sorted(cls._builders)) or "<none>"
            raise ValueError(f"Unknown image builder '{name}'. Available: {available}")
        return builder.build(settings)

    @classmethod
    def list_available(cls) -> list[str]:
        return sorted(cls._builders.keys())


def resolve_image_builder_name(settings: Settings) -> str:
    """Resolve which image builder to use given settings."""
    if settings.agent_image_override:
        return "custom"
    if settings.agent_image_builder:
        return settings.agent_image_builder
    return settings.agent_provider


def get_agent_image(settings: Settings) -> modal.Image:
    """Return the Modal image configured for the current agent settings."""
    return ImageFactory.build_image(resolve_image_builder_name(settings), settings)


# Register built-in builders
from agent_sandbox.images import claude_image as _claude_image  # noqa: E402,F401
from agent_sandbox.images import custom_image as _custom_image  # noqa: E402,F401
