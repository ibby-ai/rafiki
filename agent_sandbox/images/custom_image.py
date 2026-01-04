"""Custom image builder for user-provided base images."""

from __future__ import annotations

import modal

from agent_sandbox.config.settings import Settings
from agent_sandbox.images import ImageFactory
from agent_sandbox.images.base import AgentImageBuilder


@ImageFactory.register("custom")
class CustomImageBuilder(AgentImageBuilder):
    """Builds an image from a user-provided registry image."""

    def build(self, settings: Settings) -> modal.Image:
        if not settings.agent_image_override:
            raise ValueError("agent_image_override must be set when using custom image builder")
        return (
            modal.Image.from_registry(settings.agent_image_override)
            .pip_install("uv")
            .env({"AGENT_FS_ROOT": settings.agent_fs_root})
            .workdir("/root/app")
            .add_local_dir(
                ".",
                remote_path="/root/app",
                copy=True,
                ignore=[".git", ".venv", "__pycache__", "*.pyc", ".DS_Store", "Makefile"],
            )
            .run_commands("cd /root/app && uv pip install -e . --system --no-cache")
        )
