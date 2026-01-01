"""
Configuration and settings management using Pydantic Settings.

This module handles environment variables, Modal secrets, and application settings.
"""

import modal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and Modal secrets."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Anthropic API configuration
    anthropic_api_key: str = ""

    # Sandbox configuration
    sandbox_name: str = "svc-runner-8001"
    service_port: int = 8001
    persist_vol_name: str = "svc-runner-8001-vol"

    # Security settings
    enforce_connect_token: bool = False

    # Timeouts (in seconds)
    service_timeout: int = 60
    sandbox_timeout: int = 60 * 60 * 12  # 12 hours
    sandbox_idle_timeout: int = 60 * 10  # 10 minutes

    # Resource limits
    sandbox_cpu: float = 1.0
    sandbox_memory: int = 2048  # MB

    # Agent filesystem root
    agent_fs_root: str = "/data"


def get_modal_secrets() -> list[modal.Secret]:
    """Get Modal secrets required for the application.

    Returns:
        List of Modal Secret objects.
    """
    return [modal.Secret.from_name("anthropic-secret", required_keys=["ANTHROPIC_API_KEY"])]
