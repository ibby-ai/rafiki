"""Image builder protocols for agent sandbox runtimes."""

from __future__ import annotations

from abc import ABC, abstractmethod

import modal

from agent_sandbox.config.settings import Settings


class AgentImageBuilder(ABC):
    """Interface for building Modal images for agent runtimes."""

    name: str

    @abstractmethod
    def build(self, settings: Settings) -> modal.Image:
        """Return a Modal Image configured for this agent runtime."""
        raise NotImplementedError
