"""
Configuration and settings management using Pydantic Settings.

This module handles environment variables, Modal secrets, and application settings.
All settings can be configured via environment variables (case-insensitive).

See CLAUDE.md and docs/configuration.md for usage guidance.
"""

import os
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

    # Claude CLI sandbox + volume configuration
    claude_cli_sandbox_name: str = "claude-cli-runner"
    claude_cli_persist_vol_name: str = "claude-cli-runner-vol"
    claude_cli_fs_root: str = Field(
        default="/data-cli",
        description=(
            "Root directory for Claude CLI workspace files. "
            "This is the Modal Volume mount point used by Claude CLI sandboxes. "
            "Default: /data-cli."
        ),
    )
    claude_cli_service_port: int = Field(
        default=8002,
        description="Internal port for the Claude CLI controller service",
    )
    claude_cli_service_ports: list[int] = Field(
        default=[8002],
        description="List of encrypted ports to expose for the Claude CLI sandbox",
    )
    claude_cli_sandbox_timeout: int = Field(
        default=60 * 60 * 24,
        description="Max Claude CLI sandbox lifetime (seconds, default 24h)",
    )
    claude_cli_sandbox_idle_timeout: int = Field(
        default=60 * 30,
        description="Shutdown Claude CLI sandbox after idle (seconds, default 30min)",
    )
    claude_cli_sandbox_cpu: float = Field(
        default=1.0,
        description="Claude CLI sandbox CPU cores requested",
    )
    claude_cli_sandbox_memory: int = Field(
        default=2048,
        description="Claude CLI sandbox memory requested (MB)",
    )
    claude_cli_sandbox_cpu_limit: float | None = Field(
        default=None,
        description="Max Claude CLI sandbox CPU cores (None = no limit)",
    )
    claude_cli_sandbox_memory_limit: int | None = Field(
        default=None,
        description="Max Claude CLI sandbox memory (MB, None = no limit)",
    )
    claude_cli_sandbox_ephemeral_disk: int | None = Field(
        default=None,
        description="Claude CLI sandbox ephemeral disk size (MiB)",
    )

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
    modal_auth_secret_name: str = Field(
        default="modal-auth-secret",
        description=(
            "Modal secret name that provides SANDBOX_MODAL_TOKEN_ID and SANDBOX_MODAL_TOKEN_SECRET "
            "for in-sandbox Modal API access."
        ),
    )
    enable_modal_auth_secret: bool = Field(
        default=True,
        description="Include the modal auth secret in sandbox/function secrets.",
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
    stats_store_name: str = Field(
        default="agent-stats-store",
        description="Modal Dict name for storing aggregate statistics",
    )
    session_snapshot_store_name: str = Field(
        default="agent-session-snapshots",
        description="Modal Dict name for storing session filesystem snapshots",
    )
    enable_session_snapshots: bool = Field(
        default=True,
        description=(
            "Enable automatic filesystem snapshots for session persistence. "
            "When enabled, snapshots are taken after agent queries complete, "
            "allowing session state to be restored when resuming after sandbox timeout."
        ),
    )
    snapshot_min_interval_seconds: int = Field(
        default=60,
        description=(
            "Minimum seconds between snapshots for the same session. "
            "Prevents excessive snapshot creation for rapid-fire queries."
        ),
    )

    # CLI sandbox snapshot settings
    cli_job_snapshot_store_name: str = Field(
        default="cli-job-snapshots",
        description="Modal Dict name for storing CLI job filesystem snapshots",
    )
    enable_cli_job_snapshots: bool = Field(
        default=True,
        description=(
            "Enable automatic filesystem snapshots for CLI job persistence. "
            "When enabled, snapshots are taken after CLI jobs complete, "
            "allowing job state to be restored when resuming after sandbox timeout."
        ),
    )
    cli_snapshot_min_interval_seconds: int = Field(
        default=60,
        description=(
            "Minimum seconds between snapshots for the same CLI job. "
            "Prevents excessive snapshot creation for rapid-fire executions."
        ),
    )

    # Warm pool settings for Agent SDK sandbox
    warm_pool_store_name: str = Field(
        default="agent-warm-pool",
        description="Modal Dict name for storing warm pool metadata",
    )
    enable_warm_pool: bool = Field(
        default=True,
        description=(
            "Enable warm sandbox pool for reduced cold-start latency. "
            "When enabled, the system maintains a pool of pre-warmed sandboxes "
            "ready for immediate use, eliminating sandbox creation overhead."
        ),
    )
    warm_pool_size: int = Field(
        default=2,
        description=(
            "Number of warm sandboxes to maintain in the pool. "
            "Higher values reduce cold-start probability but increase cost. "
            "Recommended: 1-3 for low traffic, 3-5 for moderate traffic."
        ),
    )
    warm_pool_refresh_interval: int = Field(
        default=300,
        description=(
            "Seconds between pool maintenance runs. "
            "The pool maintainer checks sandbox health and replenishes as needed. "
            "Lower values ensure pool readiness but increase API calls."
        ),
    )
    warm_pool_sandbox_max_age: int = Field(
        default=3600,
        description=(
            "Maximum age (seconds) for warm sandboxes before recycling. "
            "Sandboxes older than this are terminated and replaced to ensure "
            "freshness and pick up image changes. Default: 1 hour."
        ),
    )
    warm_pool_claim_timeout: int = Field(
        default=5,
        description=(
            "Seconds to wait when attempting to claim a warm sandbox. "
            "If claiming takes longer, falls back to creating a new sandbox."
        ),
    )

    # CLI Warm pool settings for Claude CLI sandbox
    cli_warm_pool_store_name: str = Field(
        default="cli-warm-pool",
        description="Modal Dict name for storing CLI warm pool metadata",
    )
    enable_cli_warm_pool: bool = Field(
        default=True,
        description=(
            "Enable warm sandbox pool for CLI sandboxes to reduce cold-start latency. "
            "When enabled, the system maintains a pool of pre-warmed CLI sandboxes "
            "ready for immediate use, eliminating sandbox creation overhead."
        ),
    )
    cli_warm_pool_size: int = Field(
        default=2,
        description=(
            "Number of warm CLI sandboxes to maintain in the pool. "
            "Higher values reduce cold-start probability but increase cost. "
            "Recommended: 1-2 for low traffic, 2-3 for moderate traffic."
        ),
    )
    cli_warm_pool_refresh_interval: int = Field(
        default=300,
        description=(
            "Seconds between CLI pool maintenance runs. "
            "The pool maintainer checks sandbox health and replenishes as needed. "
            "Lower values ensure pool readiness but increase API calls."
        ),
    )
    cli_warm_pool_sandbox_max_age: int = Field(
        default=3600,
        description=(
            "Maximum age (seconds) for warm CLI sandboxes before recycling. "
            "Sandboxes older than this are terminated and replaced to ensure "
            "freshness and pick up image changes. Default: 1 hour."
        ),
    )
    cli_warm_pool_claim_timeout: int = Field(
        default=5,
        description=(
            "Seconds to wait when attempting to claim a warm CLI sandbox. "
            "If claiming takes longer, falls back to creating a new sandbox."
        ),
    )

    # Pre-warm API settings (speculative warming on user typing)
    prewarm_store_name: str = Field(
        default="agent-prewarm-store",
        description="Modal Dict name for storing pre-warm request tracking",
    )
    enable_prewarm: bool = Field(
        default=True,
        description=(
            "Enable the pre-warm API for speculative sandbox preparation. "
            "When enabled, clients can call POST /warm when users start typing "
            "to begin sandbox preparation before the actual query arrives."
        ),
    )
    prewarm_timeout_seconds: int = Field(
        default=60,
        description=(
            "How long (seconds) a pre-warmed sandbox reservation remains valid. "
            "If no query arrives within this time, the pre-warm is expired "
            "and the sandbox returns to the pool (if from pool) or continues warming. "
            "Default: 60 seconds."
        ),
    )

    # Image version tracking settings
    image_version_store_name: str = Field(
        default="agent-image-version",
        description="Modal Dict name for storing image version metadata",
    )
    enable_image_version_tracking: bool = Field(
        default=True,
        description="Track image versions to invalidate old warm pool sandboxes on deploy",
    )

    # Session cancellation settings (stop/cancel mid-execution)
    session_cancellation_store_name: str = Field(
        default="agent-session-cancellations",
        description="Modal Dict name for storing session cancellation flags",
    )
    enable_session_cancellation: bool = Field(
        default=True,
        description=(
            "Enable the session stop/cancel API. "
            "When enabled, clients can call POST /session/{id}/stop to gracefully "
            "terminate agent execution mid-query by rejecting further tool calls."
        ),
    )
    cancellation_expiry_seconds: int = Field(
        default=3600,
        description=(
            "How long (seconds) a cancellation flag remains active. "
            "After this time, the cancellation flag is considered stale and ignored. "
            "This prevents old cancellation requests from affecting new sessions. "
            "Default: 1 hour."
        ),
    )

    # Prompt queue settings (follow-up prompts while agent is executing)
    prompt_queue_store_name: str = Field(
        default="agent-prompt-queue",
        description="Modal Dict name for storing per-session prompt queues",
    )
    enable_prompt_queue: bool = Field(
        default=True,
        description=(
            "Enable the prompt queue feature. "
            "When enabled, prompts sent while a session is executing are queued "
            "and processed sequentially after the current query completes."
        ),
    )
    max_queued_prompts_per_session: int = Field(
        default=10,
        description=(
            "Maximum number of prompts that can be queued per session. "
            "Additional prompts are rejected with a 429 status when limit is reached. "
            "Default: 10 prompts."
        ),
    )
    prompt_queue_entry_expiry_seconds: int = Field(
        default=3600,
        description=(
            "How long (seconds) a queued prompt remains valid. "
            "Expired prompts are skipped during processing. "
            "Default: 1 hour."
        ),
    )

    # Multiplayer session settings (collaborative sessions)
    session_metadata_store_name: str = Field(
        default="agent-session-metadata",
        description="Modal Dict name for storing session metadata and message history",
    )
    enable_multiplayer_sessions: bool = Field(
        default=True,
        description=(
            "Enable multiplayer session support. "
            "When enabled, sessions track ownership, authorized users, and message history "
            "with user attribution, allowing multiple users to collaborate on the same session."
        ),
    )
    max_message_history_per_session: int = Field(
        default=100,
        description=(
            "Maximum number of messages to retain in session history. "
            "Older messages are removed when limit is reached. "
            "Default: 100 messages."
        ),
    )
    message_content_max_length: int = Field(
        default=1000,
        description=(
            "Maximum length of message content stored in history. "
            "Longer messages are truncated. This limits storage cost for verbose responses. "
            "Default: 1000 characters."
        ),
    )
    max_authorized_users_per_session: int = Field(
        default=20,
        description=(
            "Maximum number of users that can be authorized on a single session. "
            "Does not include the session owner. "
            "Default: 20 users."
        ),
    )

    # Ralph control settings (pause/resume)
    ralph_control_store_name: str = Field(
        default="ralph-control-store",
        description="Modal Dict name for storing Ralph pause/resume control state",
    )
    enable_ralph_control: bool = Field(
        default=True,
        description=(
            "Enable Ralph pause/resume control. "
            "When enabled, clients can pause and resume Ralph loops mid-execution "
            "via POST /ralph/{job_id}/pause and POST /ralph/{job_id}/resume endpoints."
        ),
    )
    ralph_control_expiry_seconds: int = Field(
        default=86400,
        description=(
            "How long (seconds) a Ralph control entry remains valid. "
            "After this time, pause/checkpoint entries are considered stale. "
            "Default: 24 hours (matches sandbox lifetime)."
        ),
    )

    # GitHub push settings
    github_token_secret_name: str = Field(
        default="github-token",
        description=(
            "Modal secret name that provides GITHUB_TOKEN for push operations. "
            "Create with: modal secret create github-token GITHUB_TOKEN=github_pat_xxx. "
            "Recommended: Use a fine-grained PAT (https://github.com/settings/personal-access-tokens/new) "
            "scoped to specific repositories with 'Contents: Read and write' permission only."
        ),
    )
    enable_github_push: bool = Field(
        default=True,
        description="Enable GitHub push operations in Ralph loops",
    )

    # Ralph iteration snapshot settings (rollback support)
    ralph_iteration_snapshot_store_name: str = Field(
        default="ralph-iteration-snapshots",
        description="Modal Dict name for storing Ralph iteration filesystem snapshots",
    )
    enable_ralph_iteration_snapshots: bool = Field(
        default=True,
        description=(
            "Enable filesystem snapshots after each Ralph iteration. "
            "When enabled, the system takes a snapshot after each successful iteration, "
            "allowing rollback to any previous iteration state."
        ),
    )
    ralph_max_snapshots_per_job: int = Field(
        default=20,
        description=(
            "Maximum number of iteration snapshots to retain per job. "
            "Older snapshots are deleted when limit is reached. "
            "Default: 20 snapshots."
        ),
    )

    # Child session spawning settings
    child_session_registry_name: str = Field(
        default="agent-child-session-registry",
        description="Modal Dict name for storing parent-child session relationships",
    )
    max_children_per_session: int = Field(
        default=10,
        description=(
            "Maximum number of child sessions that can be spawned from a single parent session. "
            "Prevents resource exhaustion from runaway parallel spawning. "
            "Default: 10 children."
        ),
    )
    child_session_default_timeout: int = Field(
        default=300,
        description=(
            "Default timeout in seconds for child sessions if not specified. "
            "Child sessions will be terminated after this duration. "
            "Default: 300 seconds (5 minutes)."
        ),
    )
    enable_child_sessions: bool = Field(
        default=True,
        description=(
            "Enable child session spawning. "
            "When enabled, agents can spawn child sessions for parallel work delegation "
            "using the spawn_session tool. Disable to prevent resource-intensive parallel work."
        ),
    )

    # Workspace Retention Settings
    workspace_retention_store_name: str = Field(
        default="cli-workspace-retention",
        description="Modal Dict name for storing workspace retention metadata",
    )
    enable_workspace_retention: bool = Field(
        default=True,
        description=(
            "Enable automatic workspace retention tracking and cleanup. "
            "When enabled, the system tracks job workspaces and automatically "
            "deletes old workspaces based on retention policy."
        ),
    )
    workspace_retention_days: int = Field(
        default=7,
        description=(
            "Number of days to keep completed job workspaces before cleanup. "
            "Applies to jobs with status 'complete'. "
            "Default: 7 days."
        ),
    )
    failed_job_retention_days: int = Field(
        default=14,
        description=(
            "Number of days to keep failed job workspaces before cleanup. "
            "Failed jobs are retained longer to allow debugging. "
            "Default: 14 days."
        ),
    )
    max_workspace_size_mb: int | None = Field(
        default=None,
        description=(
            "Optional maximum size limit per workspace in megabytes. "
            "Workspaces exceeding this limit may be flagged for review. "
            "None = no limit. Default: None."
        ),
    )
    workspace_cleanup_interval_seconds: int = Field(
        default=3600,
        description=(
            "Seconds between automatic workspace cleanup runs. "
            "The cleanup task checks for expired workspaces at this interval. "
            "Default: 3600 (1 hour)."
        ),
    )

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

    if settings.enable_modal_auth_secret:
        secrets.append(
            modal.Secret.from_name(
                settings.modal_auth_secret_name,
                required_keys=["SANDBOX_MODAL_TOKEN_ID", "SANDBOX_MODAL_TOKEN_SECRET"],
            )
        )

    if settings.enable_github_push:
        secrets.append(
            modal.Secret.from_name(
                settings.github_token_secret_name,
                required_keys=["GITHUB_TOKEN"],
            )
        )

    return secrets


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings.

    Returns:
        Cached Settings instance.
    """
    return Settings()


def _hydrate_modal_token_env() -> None:
    """Populate MODAL_TOKEN_ID/SECRET from auth secret env vars if needed."""
    token_id = (os.getenv("MODAL_TOKEN_ID") or "").strip()
    token_secret = (os.getenv("MODAL_TOKEN_SECRET") or "").strip()
    if token_id and token_secret:
        return

    alt_id = (os.getenv("SANDBOX_MODAL_TOKEN_ID") or "").strip()
    alt_secret = (os.getenv("SANDBOX_MODAL_TOKEN_SECRET") or "").strip()
    if alt_id and alt_secret:
        os.environ["MODAL_TOKEN_ID"] = alt_id
        os.environ["MODAL_TOKEN_SECRET"] = alt_secret


_hydrate_modal_token_env()
