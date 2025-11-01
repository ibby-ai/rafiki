"""
Environment/image configuration for Modal sandboxes.

We define a small registry of environment templates that specify the Modal
`Image`, working directory, and required secrets used by functions and
sandboxes throughout the app. This centralizes image composition and avoids
duplicating configuration across files.

See Modal docs for `modal.Image`, `.workdir`, `.add_local_dir`, and secrets.
"""
from dataclasses import dataclass
from typing import Dict, List
import modal


@dataclass(frozen=True)
class AgentEnvTemplate:
    image: modal.Image
    workdir: str
    secrets: List[modal.Secret]
    """Bundle of environment settings for running the agent in Modal.

    Attributes:
        image: The container image used for functions and sandboxes.
        workdir: The working directory inside the container where code is run.
        secrets: Modal secret objects providing required API keys, etc.
    """

def _base_anthropic_sdk_image() -> modal.Image:
    """Build a base image with Python, FastAPI, uvicorn, httpx and Claude SDK.

    - Uses Debian slim with Python 3.11
    - Installs `claude-agent-sdk` plus FastAPI/uvicorn/httpx
    - Installs Node.js and `@anthropic-ai/claude-code` (Agent SDK dependency)
    - Sets `/root/app` as the workdir and copies the local project into place
    """
    return (
        modal.Image.debian_slim(python_version="3.11").pip_install("claude-agent-sdk", "fastapi", "uvicorn", "httpx")
        .apt_install("curl")
        .run_commands(
            "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
            "apt-get install -y nodejs",
            "npm install -g @anthropic-ai/claude-code", # Needed for Agent SDK
        )
        .workdir("/root/app") # Declare the working directory for the sandbox.
        .add_local_dir(".", remote_path="/root/app")
    )


# Registry of supported environment templates for this app.
ENV_TEMPLATES: Dict[str, AgentEnvTemplate] = {
    "base-anthropic-sdk": AgentEnvTemplate(
        image=_base_anthropic_sdk_image(),
        workdir="/root/app",
        secrets=[modal.Secret.from_name("anthropic-secret", required_keys=["ANTHROPIC_API_KEY"])],
    )
}

def get_env_template(name: str = "base-anthropic-sdk") -> AgentEnvTemplate:
    """Retrieve a named environment template.

    Args:
        name: Template key from `ENV_TEMPLATES`.

    Returns:
        The corresponding `AgentEnvTemplate`.

    Raises:
        ValueError: If the template name is not recognized.
    """
    try:
        return ENV_TEMPLATES[name]
    except KeyError:
        raise ValueError(f"Unknown env template {name}. Available: {list(ENV_TEMPLATES)}")