"""Pydantic models for Ralph autonomous coding loop.

Defines all request/response schemas and internal data structures for the
Ralph loop system.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class PrdItem(BaseModel):
    """A single item in the PRD (Product Requirements Document)."""

    id: str
    category: str  # "functional", "technical", "quality"
    description: str
    steps: list[str] = Field(default_factory=list)  # Verification steps
    passes: bool = False
    priority: int = 0  # Higher = more important


class Prd(BaseModel):
    """Product Requirements Document containing user stories."""

    name: str
    userStories: list[PrdItem]  # noqa: N815 - camelCase matches original PRD JSON format


class WorkspaceSource(BaseModel):
    """How to initialize the workspace."""

    type: Literal["empty", "git_clone", "template"] = "empty"
    git_url: str | None = None  # For git_clone
    git_branch: str | None = None
    template_path: str | None = None  # For template (relative to /data/templates)


class IterationStatus(str, Enum):
    """Status of a single Ralph iteration."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class IterationResult(BaseModel):
    """Result of a single Ralph loop iteration."""

    iteration: int
    task_id: str | None = None
    task_description: str | None = None
    status: IterationStatus
    cli_exit_code: int
    feedback_passed: bool
    commit_sha: str | None = None
    error: str | None = None
    cli_output: str | None = None  # Truncated stdout+stderr from CLI


class RalphLoopStatus(str, Enum):
    """Overall status of the Ralph loop."""

    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    STOPPED = "stopped"
    PAUSED = "paused"
    MAX_ITERATIONS = "max_iterations"


class RalphLoopResult(BaseModel):
    """Final result of a completed Ralph loop."""

    job_id: str
    status: RalphLoopStatus
    iterations_completed: int
    iterations_max: int
    tasks_completed: int
    tasks_total: int
    iteration_results: list[IterationResult] = Field(default_factory=list)
    final_prd: Prd | None = None
    error: str | None = None


class RalphStartRequest(BaseModel):
    """Request body to start a Ralph loop."""

    prd: Prd
    workspace_source: WorkspaceSource = Field(default_factory=WorkspaceSource)
    prompt_template: str | None = None  # Custom prompt override
    max_iterations: int = Field(default=10, ge=1, le=100)
    timeout_per_iteration: int = Field(default=300, ge=60, le=3600)
    first_iteration_timeout: int | None = Field(
        default=None, ge=60, le=3600
    )  # Longer timeout for first iteration (cold start)
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["Read", "Write", "Bash", "Glob", "Grep"]
    )
    feedback_commands: list[str] = Field(default_factory=list)
    feedback_timeout: int = Field(default=120, ge=10, le=600)
    auto_commit: bool = True
    max_consecutive_failures: int = Field(default=3, ge=1, le=10)


class RalphExecuteRequest(BaseModel):
    """Request body to execute a Ralph loop in a CLI sandbox."""

    job_id: str
    prd: Prd
    workspace_source: WorkspaceSource = Field(default_factory=WorkspaceSource)
    prompt_template: str | None = None
    max_iterations: int = Field(default=10, ge=1, le=100)
    timeout_per_iteration: int = Field(default=300, ge=60, le=3600)
    first_iteration_timeout: int | None = Field(default=None, ge=60, le=3600)
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["Read", "Write", "Bash", "Glob", "Grep"]
    )
    feedback_commands: list[str] = Field(default_factory=list)
    feedback_timeout: int = Field(default=120, ge=10, le=600)
    auto_commit: bool = True
    max_consecutive_failures: int = Field(default=3, ge=1, le=10)
    # Optional checkpoint for resuming a paused loop
    resume_checkpoint: dict | None = Field(
        default=None,
        description="Checkpoint data from a paused loop for resuming execution",
    )


class RalphStartResponse(BaseModel):
    """Response from starting a Ralph loop."""

    job_id: str
    call_id: str
    status: str = "started"


class RalphStatusResponse(BaseModel):
    """Response for Ralph loop status polling."""

    job_id: str
    status: str
    current_iteration: int
    max_iterations: int
    tasks_completed: int
    tasks_total: int
    current_task: str | None = None
    result: RalphLoopResult | None = None


# =============================================================================
# RALPH CONTROL SCHEMAS (PAUSE/RESUME)
# =============================================================================


class RalphPauseRequest(BaseModel):
    """Request to pause a running Ralph loop."""

    requested_by: str | None = None
    reason: str | None = None


class RalphPauseResponse(BaseModel):
    """Response from pausing a Ralph loop."""

    ok: bool
    job_id: str
    status: str  # "paused" or "already_paused" or "not_running"
    paused_at: int | None = None
    reason: str | None = None
    message: str | None = None


class RalphResumeRequest(BaseModel):
    """Request to resume a paused Ralph loop."""

    requested_by: str | None = None


class RalphResumeResponse(BaseModel):
    """Response from resuming a Ralph loop."""

    ok: bool
    job_id: str
    status: str  # "resumed" or "not_paused"
    call_id: str | None = None  # New Modal call ID for resumed loop
    message: str | None = None


class RalphCheckpoint(BaseModel):
    """Checkpoint state for pausing/resuming Ralph loops."""

    job_id: str
    iteration: int
    max_iterations: int
    tasks_completed: int
    tasks_total: int
    current_task_id: str | None = None
    iteration_results: list[IterationResult] = Field(default_factory=list)
    prd_json: str  # Serialized PRD state
    created_at: int
    reason: str | None = None
    requested_by: str | None = None


# =============================================================================
# RALPH ITERATION SNAPSHOT SCHEMAS (ROLLBACK)
# =============================================================================


class RalphIterationSnapshotEntry(BaseModel):
    """Entry for an iteration snapshot."""

    job_id: str
    iteration: int
    task_id: str | None = None
    task_description: str | None = None
    image_id: str  # Modal Image object_id
    commit_sha: str | None = None
    created_at: int
    feedback_passed: bool = False


class RalphSnapshotListResponse(BaseModel):
    """Response listing iteration snapshots for a job."""

    ok: bool
    job_id: str
    snapshots: list[RalphIterationSnapshotEntry] = Field(default_factory=list)
    total: int = 0


class RalphRollbackRequest(BaseModel):
    """Request to rollback to a specific iteration."""

    iteration: int
    requested_by: str | None = None


class RalphRollbackResponse(BaseModel):
    """Response from rolling back to an iteration."""

    ok: bool
    job_id: str
    iteration: int
    status: str  # "rolled_back" or "snapshot_not_found" or "error"
    message: str | None = None


# =============================================================================
# RALPH STREAMING SCHEMAS (SSE)
# =============================================================================


class RalphStreamEvent(BaseModel):
    """Event sent during Ralph SSE streaming."""

    event_type: str  # "started", "iteration_start", "iteration_complete", "iteration_failed", "done", "error"
    job_id: str
    iteration: int | None = None
    task_id: str | None = None
    task_description: str | None = None
    status: str | None = None
    cli_exit_code: int | None = None
    feedback_passed: bool | None = None
    commit_sha: str | None = None
    error: str | None = None
    result: RalphLoopResult | None = None  # Final result on "done" event
