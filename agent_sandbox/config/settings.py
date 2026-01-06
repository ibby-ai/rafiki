"""
Configuration and settings management using Pydantic Settings.

This module handles environment variables, Modal secrets, and application settings.
All settings can be configured via environment variables (case-insensitive).

See CLAUDE.md and docs/configuration.md for usage guidance.
"""

from functools import lru_cache
from typing import Self

import modal
from pydantic import Field, model_validator
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
    service_ports: list[int] = Field(
        default=[8001],
        description="List of encrypted ports to expose via tunnels",
    )
    persist_vol_name: str = "svc-runner-8001-vol"

    # Custom domains for production deployments
    custom_domains: list[str] | None = Field(
        default=None,
        description="Custom domain names for production (e.g., ['api.example.com'])",
    )

    # Admin secret for privileged operations
    admin_secret_name: str = Field(
        default="admin-secret",
        description="Modal secret name for admin operations (terminate, snapshot)",
    )

    # Security settings
    # enforce_connect_token: Require Modal connect token in X-Verified-User-Data header
    enforce_connect_token: bool = False
    # require_proxy_auth: Require Modal workspace auth for public HTTP endpoints
    require_proxy_auth: bool = False

    # Timeouts
    service_timeout: int = Field(default=60, description="Health check timeout (seconds)")
    sandbox_timeout: int = Field(
        default=60 * 60 * 24, description="Max sandbox lifetime (seconds, default 24h)"
    )
    sandbox_idle_timeout: int = Field(
        default=60 * 10, description="Shutdown after idle (seconds, default 10min)"
    )

    # Resource requests (guaranteed minimums)
    sandbox_cpu: float = Field(default=1.0, description="CPU cores requested")
    sandbox_memory: int = Field(default=2048, description="Memory requested (MB)")
    # Resource limits (hard caps, optional)
    sandbox_cpu_limit: float | None = Field(
        default=None, description="Max CPU cores (None = no limit)"
    )
    sandbox_memory_limit: int | None = Field(
        default=None, description="Max memory (MB, None = no limit)"
    )
    sandbox_ephemeral_disk: int | None = Field(
        default=None,
        description="Ephemeral disk size (MiB). Modal maximum is 3.0 TiB.",
    )

    # Autoscaling controls (optional)
    # See: https://modal.com/docs/guide/cold-start#scaling-settings
    min_containers: int | None = Field(
        default=1, description="Minimum warm containers (reduces cold starts)"
    )
    max_containers: int | None = Field(default=None, description="Maximum concurrent containers")
    buffer_containers: int | None = Field(
        default=None, description="Extra warm containers beyond demand"
    )
    scaledown_window: int | None = Field(
        default=None, description="Seconds before scaling down idle containers"
    )

    # Input concurrency - multiple requests per container
    # See: https://modal.com/docs/guide/concurrent-inputs
    concurrent_max_inputs: int | None = Field(
        default=None, description="Max concurrent requests per container"
    )
    concurrent_target_inputs: int | None = Field(
        default=None, description="Target concurrent requests (for load balancing)"
    )

    # Retry policy (optional) - exponential backoff for transient failures
    # See: https://modal.com/docs/guide/retries
    retry_max_attempts: int | None = Field(default=None, description="Max retry attempts")
    retry_initial_delay: float | None = Field(
        default=None, description="First retry delay (seconds)"
    )
    retry_backoff_coefficient: float | None = Field(
        default=None, description="Delay multiplier per retry (e.g., 2.0)"
    )
    retry_max_delay: float | None = Field(
        default=None, description="Max delay between retries (seconds)"
    )

    # Persistence and queue settings
    persist_vol_version: int | None = Field(
        default=None, description="Volume version (None=default, 2=v2 volumes)"
    )
    volume_commit_interval: int | None = Field(
        default=None,
        description="Seconds between volume commits (None=commit on termination only)",
    )
    job_queue_name: str = "agent-job-queue"
    job_results_dict: str = "agent-job-results"
    session_store_name: str = "agent-session-store"
    job_queue_cron: str | None = Field(
        default=None, description="Cron expression for queue processing (e.g., '*/5 * * * *')"
    )
    max_jobs_per_run: int | None = Field(
        default=None, description="Max jobs per scheduled queue processing run"
    )

    # Snapshot and lifecycle
    enable_memory_snapshot: bool = Field(
        default=True,
        description="Enable Modal memory snapshots for faster cold starts",
    )

    # Webhook delivery defaults
    webhook_default_timeout: int = Field(
        default=10, description="Default webhook timeout in seconds"
    )
    webhook_default_max_attempts: int = Field(
        default=3, description="Default max webhook delivery attempts"
    )
    webhook_retry_initial_delay: float = Field(
        default=1.0, description="Initial retry delay for webhooks (seconds)"
    )
    webhook_retry_backoff_coefficient: float = Field(
        default=2.0, description="Backoff multiplier for webhook retries"
    )
    webhook_retry_max_delay: float = Field(
        default=30.0, description="Max delay between webhook retries (seconds)"
    )
    webhook_signing_secret: str | None = Field(
        default=None,
        description="Optional global signing secret for webhook payloads",
    )

    # Agent execution limits
    agent_max_turns: int | None = Field(
        default=50, description="Maximum conversation turns (None = unlimited)"
    )

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


def get_modal_secrets(include_admin: bool = False) -> list[modal.Secret]:
    """Get Modal secrets required for the application.

    Args:
        include_admin: If True, include the admin secret for privileged operations.
            The admin secret is optional and won't fail if not configured.

    Returns:
        List of Modal Secret objects.
    """
    secrets = [modal.Secret.from_name("anthropic-secret", required_keys=["ANTHROPIC_API_KEY"])]

    if include_admin:
        settings = get_settings()
        # Admin secret is optional - use required_keys=[] to avoid failure if not set
        secrets.append(modal.Secret.from_name(settings.admin_secret_name))

    return secrets


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings.

    Returns:
        Cached Settings instance.
    """
    return Settings()
