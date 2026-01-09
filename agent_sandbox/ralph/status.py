"""Status file management for Ralph loop polling.

Maintains a status.json file that can be polled to check loop progress
without waiting for the Modal function to complete.
"""

import json
from pathlib import Path


def write_status(
    workspace: Path,
    status: str,
    current_iteration: int,
    max_iterations: int,
    tasks_completed: int,
    tasks_total: int,
    current_task: str | None = None,
) -> None:
    """Write current status to status.json for polling.

    Args:
        workspace: Path to the workspace directory.
        status: Current loop status (e.g., "running", "complete").
        current_iteration: Current iteration number.
        max_iterations: Maximum allowed iterations.
        tasks_completed: Number of completed tasks.
        tasks_total: Total number of tasks.
        current_task: ID of current task being worked on.
    """
    status_path = workspace / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": status,
                "current_iteration": current_iteration,
                "max_iterations": max_iterations,
                "tasks_completed": tasks_completed,
                "tasks_total": tasks_total,
                "current_task": current_task,
            },
            indent=2,
        )
    )


def read_status(workspace: Path) -> dict | None:
    """Read status from status.json.

    Args:
        workspace: Path to the workspace directory.

    Returns:
        Status dict if file exists, None otherwise.
    """
    status_path = workspace / "status.json"
    if status_path.exists():
        return json.loads(status_path.read_text())
    return None
