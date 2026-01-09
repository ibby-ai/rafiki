"""PRD (Product Requirements Document) management for Ralph loops.

Handles reading, writing, and updating PRD files in the workspace.
"""

from pathlib import Path

from .schemas import Prd, PrdItem


def write_prd(workspace: Path, prd: Prd) -> None:
    """Write PRD to prd.json in workspace.

    Args:
        workspace: Path to the workspace directory.
        prd: PRD object to serialize.
    """
    prd_path = workspace / "prd.json"
    prd_path.write_text(prd.model_dump_json(indent=2))


def read_prd(workspace: Path) -> Prd:
    """Read PRD from workspace.

    Args:
        workspace: Path to the workspace directory.

    Returns:
        Parsed PRD object.

    Raises:
        FileNotFoundError: If prd.json doesn't exist.
        ValidationError: If prd.json is malformed.
    """
    prd_path = workspace / "prd.json"
    return Prd.model_validate_json(prd_path.read_text())


def mark_task_complete(workspace: Path, task_id: str) -> Prd:
    """Mark a task as passes=True and save.

    Args:
        workspace: Path to the workspace directory.
        task_id: ID of the task to mark as complete.

    Returns:
        Updated PRD object.
    """
    prd = read_prd(workspace)
    for item in prd.userStories:
        if item.id == task_id:
            item.passes = True
            break
    write_prd(workspace, prd)
    return prd


def get_next_task(prd: Prd) -> PrdItem | None:
    """Get highest priority incomplete task.

    Args:
        prd: PRD object to search.

    Returns:
        Highest priority incomplete task, or None if all complete.
    """
    incomplete = [t for t in prd.userStories if not t.passes]
    if not incomplete:
        return None
    return sorted(incomplete, key=lambda t: -t.priority)[0]


def all_tasks_complete(prd: Prd) -> bool:
    """Check if all tasks are done.

    Args:
        prd: PRD object to check.

    Returns:
        True if all tasks have passes=True.
    """
    return all(t.passes for t in prd.userStories)


def is_task_complete(prd: Prd, task_id: str) -> bool:
    """Check if a specific task is marked complete.

    Args:
        prd: PRD object to search.
        task_id: ID of the task to check.

    Returns:
        True if the task has passes=True.
    """
    for item in prd.userStories:
        if item.id == task_id:
            return item.passes
    return False
