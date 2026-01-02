"""
Configuration and settings management using Pydantic Settings.

This module handles environment variables, Modal secrets, and application settings.
"""

from functools import lru_cache
from typing import Self

import modal
from pydantic import model_validator
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
    require_proxy_auth: bool = False

    # Timeouts (in seconds)
    service_timeout: int = 60
    sandbox_timeout: int = 60 * 60 * 12  # 12 hours
    sandbox_idle_timeout: int = 60 * 10  # 10 minutes

    # Resource limits (requests)
    sandbox_cpu: float = 1.0
    sandbox_memory: int = 2048  # MB
    # Resource limits (hard limits; optional)
    sandbox_cpu_limit: float | None = None
    sandbox_memory_limit: int | None = None  # MB
    sandbox_ephemeral_disk: int | None = None  # MB

    # Autoscaling controls (optional)
    min_containers: int | None = None
    max_containers: int | None = None
    buffer_containers: int | None = None
    scaledown_window: int | None = None  # seconds

    # Input concurrency (optional)
    concurrent_max_inputs: int | None = None
    concurrent_target_inputs: int | None = None

    # Retry policy (optional)
    retry_max_attempts: int | None = None
    retry_initial_delay: float | None = None
    retry_backoff_coefficient: float | None = None
    retry_max_delay: float | None = None

    # Persistence and queue settings (optional)
    persist_vol_version: int | None = None
    volume_commit_interval: int | None = None  # seconds
    job_queue_name: str = "agent-job-queue"
    job_results_dict: str = "agent-job-results"
    job_queue_cron: str | None = None
    max_jobs_per_run: int | None = None  # Max jobs to process per scheduled run

    # Snapshot and lifecycle settings (optional)
    enable_memory_snapshot: bool = False

    # Agent filesystem root
    agent_fs_root: str = "/data"

    @model_validator(mode="after")
    def validate_concurrency_settings(self) -> Self:
        """Validate that concurrency settings are consistent."""
        if (
            self.concurrent_max_inputs is not None
            and self.concurrent_target_inputs is not None
            and self.concurrent_target_inputs > self.concurrent_max_inputs
        ):
            raise ValueError(
                f"concurrent_target_inputs ({self.concurrent_target_inputs}) "
                f"cannot exceed concurrent_max_inputs ({self.concurrent_max_inputs})"
            )
        return self


def get_modal_secrets() -> list[modal.Secret]:
    """Get Modal secrets required for the application.

    Returns:
        List of Modal Secret objects.
    """
    return [modal.Secret.from_name("anthropic-secret", required_keys=["ANTHROPIC_API_KEY"])]


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings.

    Returns:
        Cached Settings instance.
    """
    return Settings()
