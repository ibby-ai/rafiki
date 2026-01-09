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
