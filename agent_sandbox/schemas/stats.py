"""Schemas for statistics and usage tracking endpoints.

These schemas define the request/response formats for the statistics API,
which provides visibility into agent effectiveness and usage patterns.

Key Metrics:
    - Total sessions/jobs started, completed, failed
    - Average duration and error rates
    - Time-series data for trend analysis

See: agent_sandbox.jobs for metrics collection implementation.
"""

from typing import Any

from pydantic import Field

from agent_sandbox.schemas.base import BaseSchema


class SandboxTypeStats(BaseSchema):
    """Statistics for a specific sandbox type."""

    total_sessions: int = Field(default=0, description="Total sessions started")
    completed_sessions: int = Field(default=0, description="Sessions completed successfully")
    failed_sessions: int = Field(default=0, description="Sessions that failed with errors")
    canceled_sessions: int = Field(default=0, description="Sessions canceled before completion")
    running_sessions: int = Field(default=0, description="Sessions currently running")
    queued_sessions: int = Field(default=0, description="Sessions waiting in queue")

    avg_duration_ms: float | None = Field(
        default=None, description="Average session duration in milliseconds"
    )
    avg_queue_latency_ms: float | None = Field(
        default=None, description="Average time from enqueue to start in milliseconds"
    )
    success_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Ratio of completed sessions to total finished (0.0 to 1.0)",
    )

    total_input_tokens: int | None = Field(
        default=None, description="Total input tokens consumed across all sessions"
    )
    total_output_tokens: int | None = Field(
        default=None, description="Total output tokens generated across all sessions"
    )
    total_cost_usd: float | None = Field(
        default=None, description="Total cost in USD across all sessions"
    )


class StatsResponse(BaseSchema):
    """Response body for statistics endpoint.

    Provides comprehensive usage statistics for Agent SDK sandboxes,
    along with aggregate totals and time-series data when available.

    Example Response:
        ```python
        {
            "ok": True,
            "period_start": 1672531200,
            "period_end": 1672617600,
            "agent_sdk": {
                "total_sessions": 150,
                "completed_sessions": 140,
                "failed_sessions": 8,
                "canceled_sessions": 2,
                "avg_duration_ms": 5000,
                "success_rate": 0.946
            },
            "totals": {
                "total_sessions": 150,
                "completed_sessions": 140,
                "failed_sessions": 8,
                "success_rate": 0.946
            }
        }
        ```
    """

    ok: bool = Field(default=True, description="Always true for successful queries")
    period_start: int | None = Field(
        default=None, description="Unix timestamp for start of stats period"
    )
    period_end: int | None = Field(
        default=None, description="Unix timestamp for end of stats period"
    )

    agent_sdk: SandboxTypeStats = Field(
        default_factory=SandboxTypeStats,
        description="Statistics for Agent SDK sandbox",
    )
    totals: SandboxTypeStats = Field(
        default_factory=SandboxTypeStats,
        description="Aggregate statistics across all sessions",
    )

    # Time-series data for trend analysis
    hourly_stats: list[dict[str, Any]] | None = Field(
        default=None,
        description="Per-hour statistics for the last 24 hours",
    )
    daily_stats: list[dict[str, Any]] | None = Field(
        default=None,
        description="Per-day statistics for the last 30 days",
    )

    # Active sessions info
    active_sandboxes: int = Field(default=0, description="Number of currently active sandboxes")
    users_active_last_5min: int = Field(
        default=0, description="Unique users who sent prompts in last 5 minutes"
    )


class StatsQueryParams(BaseSchema):
    """Query parameters for statistics endpoint."""

    period_hours: int | None = Field(
        default=24,
        ge=1,
        le=720,  # Max 30 days
        description="Hours to include in statistics (default 24, max 720)",
    )
    include_time_series: bool = Field(
        default=False,
        description="Include hourly/daily breakdown",
    )
    sandbox_type: str | None = Field(
        default=None,
        description="Filter by sandbox type: 'agent_sdk'",
    )
