"""Schemas for schedule CRUD and dispatch state."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from modal_backend.models.base import BaseSchema
from modal_backend.models.jobs import WebhookConfig

ScheduleType = Literal["one_off", "cron"]


class ScheduleCreateRequest(BaseSchema):
    """Request body for creating a schedule."""

    name: str = Field(description="Human-readable schedule name")
    question: str = Field(description="Agent prompt to execute when schedule triggers")
    agent_type: str | None = Field(default=None, description="Optional agent type")
    schedule_type: ScheduleType = Field(description="Schedule type: one_off or cron")
    run_at: int | None = Field(default=None, description="Unix timestamp for one-off runs (UTC)")
    cron: str | None = Field(default=None, description="Cron expression for recurring schedules")
    timezone: str = Field(default="UTC", description="IANA timezone for cron schedules")
    enabled: bool = Field(default=True, description="Whether this schedule is active")
    webhook: WebhookConfig | None = Field(default=None, description="Optional webhook callback")
    metadata: dict[str, Any] | None = Field(
        default=None, description="Optional metadata for client tracking"
    )


class ScheduleUpdateRequest(BaseSchema):
    """Request body for partial schedule updates."""

    name: str | None = Field(default=None)
    question: str | None = Field(default=None)
    agent_type: str | None = Field(default=None)
    run_at: int | None = Field(default=None)
    cron: str | None = Field(default=None)
    timezone: str | None = Field(default=None)
    enabled: bool | None = Field(default=None)
    webhook: WebhookConfig | None = Field(default=None)
    metadata: dict[str, Any] | None = Field(default=None)


class ScheduleResponse(BaseSchema):
    """Schedule resource representation."""

    schedule_id: str
    name: str
    question: str
    agent_type: str | None = None
    schedule_type: ScheduleType
    run_at: int | None = None
    cron: str | None = None
    timezone: str = "UTC"
    enabled: bool = True
    webhook: WebhookConfig | None = None
    metadata: dict[str, Any] | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    created_at: int
    updated_at: int
    last_run_at: int | None = None
    next_run_at: int | None = None
    last_job_id: str | None = None
    last_error: str | None = None


class ScheduleListResponse(BaseSchema):
    """Collection response for schedule listing."""

    ok: bool = True
    schedules: list[ScheduleResponse]


class ScheduleDeleteResponse(BaseSchema):
    """Response for deleting a schedule."""

    ok: bool = True
    schedule_id: str
    deleted: bool = True
