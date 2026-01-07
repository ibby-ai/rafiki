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
        description=(
            "Seconds between automatic volume commits. Controls persistence behavior: "
            "None (default) = No automatic commits; writes persist only on sandbox termination. "
            "  - Pro: No I/O overhead during execution. "
            "  - Con: Writes lost if sandbox crashes before graceful shutdown. "
            "  - Best for: Short-lived sandboxes, non-critical artifacts. "
            "0 or negative = Commit after every request (immediate persistence). "
            "  - Pro: Maximum durability, no data loss on crashes. "
            "  - Con: High I/O overhead, slower request latency. "
            "  - Best for: Critical artifacts requiring immediate persistence. "
            "Positive integer (e.g., 60) = Commit at most once per N seconds. "
            "  - Pro: Balances durability and performance. "
            "  - Con: Up to N seconds of writes may be lost on crash. "
            "  - Best for: Long-lived sandboxes with moderate artifact frequency. "
            "Note: Job workspaces force commit regardless of interval to ensure artifacts are available."
        ),
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
        default=10,
        description=(
            "Default webhook HTTP request timeout in seconds. "
            "Example: 10 seconds allows most webhook endpoints to respond. "
            "Increase for slow endpoints, decrease for faster failure detection. "
            "Can be overridden per-webhook via WebhookConfig.timeout_seconds."
        ),
    )
    webhook_default_max_attempts: int = Field(
        default=3, description="Default max webhook delivery attempts"
    )
    webhook_retry_initial_delay: float = Field(
        default=1.0, description="Initial retry delay for webhooks (seconds)"
    )
    webhook_retry_backoff_coefficient: float = Field(
        default=2.0,
        description=(
            "Exponential backoff multiplier for webhook retry delays. "
            "Formula: delay = min(initial_delay * (coefficient ^ attempt), max_delay). "
            "With default 2.0: attempt 1 waits 1s, attempt 2 waits 2s, attempt 3 waits 4s. "
            "Higher values (e.g., 3.0) increase delays faster. Lower values (e.g., 1.5) are gentler. "
            "Can be overridden per-webhook via WebhookConfig."
        ),
    )
    webhook_retry_max_delay: float = Field(
        default=30.0,
        description=(
            "Maximum delay cap between webhook retry attempts in seconds. "
            "Prevents exponential backoff from growing unbounded. "
            "Scenarios: With backoff_coefficient=2.0 and initial_delay=1.0: "
            "attempt 1→1s, attempt 2→2s, attempt 3→4s, attempt 4→8s, attempt 5→16s, "
            "attempt 6→30s (capped), attempt 7→30s (capped). "
            "Lower values (e.g., 10s) speed up retry cycles. "
            "Higher values (e.g., 60s) reduce webhook endpoint load."
        ),
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
    agent_fs_root: str = Field(
        default="/data",
        description=(
            "Root directory for agent filesystem operations and job workspaces. "
            "This is the Modal persistent volume mount point. "
            "Default: /data (matches Modal Volume mount in sandbox creation). "
            "Job workspaces are isolated at {agent_fs_root}/jobs/{job_id}/. "
            "Files written here persist across sandbox restarts when volume is committed. "
            "Must match the path where the Modal Volume is mounted in the sandbox. "
            "Change only if using a different volume mount path."
        ),
    )

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
    settings = get_settings()

    if include_admin:
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
