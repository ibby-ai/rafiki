"""Ralph Wiggum autonomous coding loop module.

Implements an autonomous coding loop that works through a PRD (Product
Requirements Document) until all tasks are complete or max iterations
is reached.

Key Components:
    - schemas: Pydantic models for PRD, iteration results, and API payloads
    - workspace: Workspace initialization (empty, git clone, template)
    - prd: PRD file management (read, write, mark complete)
    - progress: Progress file tracking with rotation
    - status: Status file for polling support
    - git: Git initialization and commit operations
    - feedback: Feedback loop execution with command allowlist
    - loop: Main orchestration loop

Usage:
    from agent_sandbox.ralph import run_ralph_loop, Prd, PrdItem, WorkspaceSource

    prd = Prd(
        name="my-project",
        userStories=[
            PrdItem(
                id="1",
                category="functional",
                description="Create hello.py that prints Hello World",
                steps=["Run python hello.py", "Verify output contains Hello World"],
                priority=1,
            )
        ],
    )

    result = run_ralph_loop(
        job_id="unique-id",
        prd=prd,
        workspace=Path("/data/jobs/unique-id"),
        workspace_source=WorkspaceSource(type="empty"),
        max_iterations=10,
    )
"""

from .feedback import (
    ALLOWED_FEEDBACK_COMMANDS,
    FeedbackResult,
    run_feedback_loops,
    validate_feedback_commands,
)
from .git import commit_changes, get_git_log, init_git
from .loop import run_ralph_loop
from .prd import (
    all_tasks_complete,
    get_next_task,
    is_task_complete,
    mark_task_complete,
    read_prd,
    write_prd,
)
from .progress import append_progress, init_progress, read_progress
from .schemas import (
    IterationResult,
    IterationStatus,
    Prd,
    PrdItem,
    RalphLoopResult,
    RalphLoopStatus,
    RalphStartRequest,
    RalphStartResponse,
    RalphStatusResponse,
    WorkspaceSource,
)
from .status import read_status, write_status
from .workspace import initialize_workspace

__all__ = [
    # Main loop
    "run_ralph_loop",
    # Schemas
    "Prd",
    "PrdItem",
    "WorkspaceSource",
    "IterationStatus",
    "IterationResult",
    "RalphLoopStatus",
    "RalphLoopResult",
    "RalphStartRequest",
    "RalphStartResponse",
    "RalphStatusResponse",
    # PRD management
    "read_prd",
    "write_prd",
    "mark_task_complete",
    "get_next_task",
    "all_tasks_complete",
    "is_task_complete",
    # Progress tracking
    "init_progress",
    "append_progress",
    "read_progress",
    # Status
    "read_status",
    "write_status",
    # Git
    "init_git",
    "commit_changes",
    "get_git_log",
    # Feedback
    "ALLOWED_FEEDBACK_COMMANDS",
    "validate_feedback_commands",
    "run_feedback_loops",
    "FeedbackResult",
    # Workspace
    "initialize_workspace",
]
