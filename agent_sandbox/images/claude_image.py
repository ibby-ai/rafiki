"""Default Claude Agent SDK image builder."""

from __future__ import annotations

import modal

from agent_sandbox.config.settings import Settings
from agent_sandbox.images import ImageFactory
from agent_sandbox.images.base import AgentImageBuilder


@ImageFactory.register("claude")
class ClaudeImageBuilder(AgentImageBuilder):
    """Builds the default image with Claude Agent SDK dependencies."""

    def build(self, settings: Settings) -> modal.Image:
        return (
            modal.Image.debian_slim(python_version="3.11")
            .pip_install("claude-agent-sdk", "fastapi", "uvicorn", "httpx")
            .pip_install("uv")
            .apt_install("curl")
            .run_commands(
                "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
                "apt-get install -y nodejs",
                "npm install -g @anthropic-ai/claude-agent-sdk",
            )
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
