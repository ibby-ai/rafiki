"""Schemas for child session spawning operations.

This module defines request/response schemas for the sub-session spawning system,
which allows parent agents to spawn and manage child sessions for parallel work
delegation.

Key Concepts:
    - Parent Session: The original agent session that spawns children
    - Child Session: A spawned session that executes delegated work
    - Child Registry: Tracks parent-child relationships for lookup

Sandbox Types:
    - agent_sdk: Uses OpenAI Agents SDK for conversational research tasks.
                 Good for: research, analysis, information gathering

Usage Flow:
    1. Parent calls spawn_session tool with task description
    2. System creates child job via enqueue_job() with parent metadata
    3. Parent can poll status via check_session_status tool
    4. When complete, parent retrieves result via get_session_result tool
    5. Parent can list all children via list_child_sessions tool

See: modal_backend.mcp_tools.session_tools for tool implementations.
See: modal_backend.jobs for child job creation and tracking.
"""

from typing import Any, Literal

from pydantic import Field

from modal_backend.models.base import BaseSchema


class SpawnSessionRequest(BaseSchema):
    """Request parameters for spawning a child session.

    Attributes:
        task: Description of what the child session should accomplish.
              Should be clear and specific for best results.
        sandbox_type: Type of sandbox to use for the child.
                     "agent_sdk" for research/conversation tasks.
        context: Optional additional context or instructions for the child.
                 Useful for providing relevant background information.
        timeout_seconds: Maximum time the child session can run.
                        Prevents runaway sessions from consuming resources.
        allowed_tools: Comma-separated list of tools the child can use.
    """

    task: str = Field(description="Description of what the child session should accomplish")
    sandbox_type: Literal["agent_sdk"] = Field(
        default="agent_sdk",
        description="Type of sandbox: 'agent_sdk' for research tasks",
    )
    context: str | None = Field(
        default=None,
        description="Optional additional context or instructions for the child session",
    )
    timeout_seconds: int = Field(
        default=300,
        ge=10,
        le=3600,
        description="Maximum time in seconds for the child session (10-3600)",
    )
    allowed_tools: str | None = Field(
        default=None,
        description="Comma-separated list of allowed tools",
    )


class SpawnSessionResponse(BaseSchema):
    """Response after spawning a child session.

    Attributes:
        ok: True if spawn was successful
        child_id: UUID of the spawned child job for tracking
        status: Initial status (always "queued" on successful spawn)
        error: Error message if spawn failed
    """

    ok: bool = Field(default=True, description="True if spawn was successful")
    child_id: str | None = Field(default=None, description="UUID of the spawned child job")
    status: Literal["queued", "error"] = Field(
        default="queued", description="Initial status of the child session"
    )
    error: str | None = Field(default=None, description="Error message if spawn failed")


class ChildSessionStatus(BaseSchema):
    """Status information for a child session.

    Attributes:
        ok: True if status check was successful
        child_id: UUID of the child session
        status: Current status in the job lifecycle
        task: The original task description
        sandbox_type: Type of sandbox being used
        created_at: Unix timestamp when child was created
        started_at: Unix timestamp when execution started (if running/complete)
        completed_at: Unix timestamp when finished (if complete/failed)
        duration_ms: Execution duration in milliseconds (if complete)
        error: Error message if status is "failed"
    """

    ok: bool = Field(default=True, description="True if status check was successful")
    child_id: str = Field(description="UUID of the child session")
    status: Literal["queued", "running", "complete", "failed", "canceled", "not_found"] = Field(
        description="Current status of the child session"
    )
    task: str | None = Field(default=None, description="Original task description")
    sandbox_type: Literal["agent_sdk"] | None = Field(
        default=None, description="Type of sandbox being used"
    )
    created_at: int | None = Field(
        default=None, description="Unix timestamp when child was created"
    )
    started_at: int | None = Field(
        default=None, description="Unix timestamp when execution started"
    )
    completed_at: int | None = Field(default=None, description="Unix timestamp when finished")
    duration_ms: int | None = Field(default=None, description="Execution duration in milliseconds")
    error: str | None = Field(default=None, description="Error message if status is 'failed'")


class ChildSessionResult(BaseSchema):
    """Result data from a completed child session.

    Attributes:
        ok: True if result retrieval was successful
        child_id: UUID of the child session
        status: Current status (should be "complete" to have result)
        result: The result content from the child session
        summary: Summary information from the agent run
        artifacts: List of artifact paths created by the child
        error: Error message if retrieval failed or child failed
    """

    ok: bool = Field(default=True, description="True if result retrieval was successful")
    child_id: str = Field(description="UUID of the child session")
    status: Literal["queued", "running", "complete", "failed", "canceled", "not_found"] = Field(
        description="Current status of the child session"
    )
    result: str | None = Field(default=None, description="Result text from the child session")
    summary: dict[str, Any] | None = Field(
        default=None, description="Summary information from the agent run"
    )
    artifacts: list[str] | None = Field(
        default=None, description="List of artifact paths created by the child"
    )
    error: str | None = Field(default=None, description="Error message if retrieval failed")


class ChildSessionEntry(BaseSchema):
    """Entry for a single child session in the list response.

    Attributes:
        child_id: UUID of the child session
        task: The original task description
        sandbox_type: Type of sandbox being used
        status: Current status in the job lifecycle
        created_at: Unix timestamp when child was created
        completed_at: Unix timestamp when finished (if applicable)
    """

    child_id: str = Field(description="UUID of the child session")
    task: str = Field(description="Original task description")
    sandbox_type: Literal["agent_sdk"] = Field(description="Type of sandbox being used")
    status: Literal["queued", "running", "complete", "failed", "canceled"] = Field(
        description="Current status of the child session"
    )
    created_at: int | None = Field(
        default=None, description="Unix timestamp when child was created"
    )
    completed_at: int | None = Field(default=None, description="Unix timestamp when finished")


class ChildSessionListResponse(BaseSchema):
    """Response containing list of all child sessions for a parent.

    Attributes:
        ok: True if list retrieval was successful
        children: List of child session entries
        total: Total number of children spawned
        active: Number of children still running
        completed: Number of children that completed successfully
        failed: Number of children that failed
    """

    ok: bool = Field(default=True, description="True if list retrieval was successful")
    children: list[ChildSessionEntry] = Field(
        default_factory=list, description="List of child session entries"
    )
    total: int = Field(default=0, ge=0, description="Total number of children spawned")
    active: int = Field(default=0, ge=0, description="Number of children still running")
    completed: int = Field(
        default=0, ge=0, description="Number of children that completed successfully"
    )
    failed: int = Field(default=0, ge=0, description="Number of children that failed")
