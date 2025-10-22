from dataclasses import dataclass
from typing import Dict, List
import modal


@dataclass(frozen=True)
class AgentEnvTemplate:
    image: modal.Image
    workdir: str
    secrets: List[modal.Secret]

def _base_anthropic_sdk_image() -> modal.Image:
    return (
        modal.Image.debian_slim(python_version="3.11").pip_install("claude-agent-sdk")
        .apt_install("curl")
        .run_commands(
            "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
            "apt-get install -y nodejs",
            "npm install -g @anthropic-ai/claude-code", # Needed for Agent SDK
        )
        .workdir("/root/app") # Declare the working directory for the sandbox.
        .add_local_dir(".", remote_path="/root/app")
    )


ENV_TEMPLATES: Dict[str, AgentEnvTemplate] = {
    "base-anthropic-sdk": AgentEnvTemplate(
        image=_base_anthropic_sdk_image(),
        workdir="/root/app",
        secrets=[modal.Secret.from_name("anthropic-secret", required_keys=["ANTHROPIC_API_KEY"])],
    )
}

def get_env_template(name: str = "base-anthropic-sdk") -> AgentEnvTemplate:
    try:
        return ENV_TEMPLATES[name]
    except KeyError:
        raise ValueError(f"Unknown env template {name}. Available: {list(TEMPLATES)}")