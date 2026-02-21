"""Session spawning tools for parallel work delegation."""

import contextvars
import time

from agents import function_tool

from modal_backend.jobs import (
    can_spawn_child,
    enqueue_job,
    get_child_count,
    get_child_session_result,
    get_child_sessions,
    get_job_status,
    register_child_session,
)
from modal_backend.settings.settings import get_settings

_settings = get_settings()


_parent_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "parent_session_context",
    default=None,
)


def set_parent_context(parent_id: str | None) -> contextvars.Token[str | None]:
    """Set parent session context for the current task/request."""
    return _parent_context.set(parent_id)


def reset_parent_context(token: contextvars.Token[str | None]) -> None:
    """Restore parent session context to a previous value."""
    _parent_context.reset(token)


def get_parent_context() -> str | None:
    """Get the parent session ID for the current task/request."""
    return _parent_context.get()


def _error(message: str) -> str:
    return f"Error: {message}"


@function_tool(name_override="mcp__sessions__spawn_session")
def spawn_session(
    task: str,
    sandbox_type: str = "agent_sdk",
    context: str | None = None,
    timeout_seconds: int | None = None,
    allowed_tools: str | None = None,
) -> str:
    """Spawn a child session for parallel work delegation."""
    if not task:
        return _error("'task' parameter is required.")

    if not _settings.enable_child_sessions:
        return _error("Child session spawning is disabled.")

    parent_id = get_parent_context()
    if not parent_id:
        return _error("No parent session context available.")

    if not can_spawn_child(parent_id):
        current_count = get_child_count(parent_id)
        return _error(
            f"Maximum child sessions reached ({current_count}/{_settings.max_children_per_session})."
        )

    # Preserve behavior: only agent_sdk is currently supported.
    _ = sandbox_type
    resolved_timeout = timeout_seconds or _settings.child_session_default_timeout

    child_prompt = task
    if context:
        child_prompt = f"Context: {context}\n\nTask: {task}"

    child_metadata = {
        "is_child_session": True,
        "parent_job_id": parent_id,
        "spawn_context": {
            "task": task,
            "context": context,
            "sandbox_type": "agent_sdk",
            "timeout_seconds": resolved_timeout,
            "allowed_tools": allowed_tools,
        },
        "child_sequence": get_child_count(parent_id) + 1,
    }

    child_job_id = enqueue_job(question=child_prompt, metadata=child_metadata)

    registered = register_child_session(
        parent_id=parent_id,
        child_job_id=child_job_id,
        task=task,
        sandbox_type="agent_sdk",
        context=context,
        timeout_seconds=resolved_timeout,
        allowed_tools=allowed_tools,
    )
    if not registered:
        return _error(
            f"Failed to register child session. Child job {child_job_id} may not be tracked."
        )

    return (
        "Child session spawned successfully.\n\n"
        f"child_id: {child_job_id}\n"
        "status: queued\n"
        "sandbox_type: agent_sdk\n"
        f"task: {task}\n\n"
        "Use mcp__sessions__check_session_status then mcp__sessions__get_session_result."
    )


@function_tool(name_override="mcp__sessions__check_session_status")
def check_session_status(child_id: str) -> str:
    """Check the current status of a spawned child session."""
    if not child_id:
        return _error("'child_id' parameter is required.")

    if not _settings.enable_child_sessions:
        return _error("Child session tracking is disabled.")

    parent_id = get_parent_context()
    if not parent_id:
        return _error("No parent session context available.")

    children = get_child_sessions(parent_id)
    child_entry = None
    for child in children:
        if child.get("child_job_id") == child_id:
            child_entry = child
            break

    if not child_entry:
        return _error(f"Child session {child_id} not found for this parent session.")

    job_status = get_job_status(child_id)
    status = child_entry.get("status", "unknown")
    task = child_entry.get("task", "")
    sandbox_type = child_entry.get("sandbox_type", "")
    created_at = child_entry.get("created_at")
    started_at = child_entry.get("started_at")
    completed_at = child_entry.get("completed_at")

    lines = [
        "Child Session Status",
        "---",
        f"child_id: {child_id}",
        f"status: {status}",
        f"task: {task}",
        f"sandbox_type: {sandbox_type}",
    ]

    if created_at:
        lines.append(f"created_at: {created_at}")
    if started_at:
        lines.append(f"started_at: {started_at}")
    if completed_at:
        lines.append(f"completed_at: {completed_at}")
        if started_at:
            lines.append(f"duration_ms: {(completed_at - started_at) * 1000}")

    if job_status and job_status.error:
        lines.append(f"error: {job_status.error}")

    if status in ("complete", "failed", "canceled"):
        lines.append("")
        lines.append("Use mcp__sessions__get_session_result to retrieve details.")

    return "\n".join(lines)


@function_tool(name_override="mcp__sessions__get_session_result")
def get_session_result(child_id: str) -> str:
    """Get the result from a completed child session."""
    if not child_id:
        return _error("'child_id' parameter is required.")

    if not _settings.enable_child_sessions:
        return _error("Child session tracking is disabled.")

    parent_id = get_parent_context()
    if not parent_id:
        return _error("No parent session context available.")

    result = get_child_session_result(parent_id, child_id)
    if not result:
        return _error(f"Child session {child_id} not found for this parent session.")

    status = result.get("status", "unknown")
    if status in ("queued", "running"):
        return (
            f"Child session {child_id} is still {status}.\n\n"
            "Use mcp__sessions__check_session_status to monitor progress."
        )

    if status == "failed":
        return f"Child session {child_id} failed.\n\nError: {result.get('error', 'Unknown error')}"

    if status == "canceled":
        return f"Child session {child_id} was canceled before completion."

    lines = [
        "Child Session Result",
        "---",
        f"child_id: {child_id}",
        f"status: {status}",
        f"task: {result.get('task', '')}",
        "",
        "Result:",
        str(result.get("result", "(no result text)")),
    ]

    artifacts = result.get("artifacts")
    if artifacts:
        lines.append("")
        lines.append("Artifacts:")
        for artifact in artifacts:
            lines.append(f"  - {artifact}")

    summary = result.get("summary")
    if summary:
        lines.append("")
        lines.append("Summary:")
        if summary.get("session_id"):
            lines.append(f"  session_id: {summary.get('session_id')}")
        if summary.get("duration_ms"):
            lines.append(f"  duration_ms: {summary.get('duration_ms')}")
        if summary.get("num_turns"):
            lines.append(f"  num_turns: {summary.get('num_turns')}")

    return "\n".join(lines)


@function_tool(name_override="mcp__sessions__list_child_sessions")
def list_child_sessions() -> str:
    """List all child sessions spawned by this parent."""
    if not _settings.enable_child_sessions:
        return _error("Child session tracking is disabled.")

    parent_id = get_parent_context()
    if not parent_id:
        return _error("No parent session context available.")

    children = get_child_sessions(parent_id)
    if not children:
        return "No child sessions have been spawned yet."

    total = len(children)
    active = sum(1 for c in children if c.get("status") in ("queued", "running"))
    completed = sum(1 for c in children if c.get("status") == "complete")
    failed = sum(1 for c in children if c.get("status") in ("failed", "canceled"))

    lines = [
        "Child Sessions",
        "---",
        f"Total: {total}",
        f"Active: {active}",
        f"Completed: {completed}",
        f"Failed: {failed}",
        f"Capacity: {total}/{_settings.max_children_per_session}",
        "",
    ]

    now = int(time.time())
    for child in children:
        child_id = child.get("child_job_id", "unknown")
        status = child.get("status", "unknown")
        task = child.get("task", "")
        sandbox_type = child.get("sandbox_type", "")
        created_at = child.get("created_at")
        age_seconds = now - created_at if created_at else None

        lines.append(f"- [{status}] {child_id[:8]}... ({sandbox_type})")
        lines.append(f"  task: {task}")
        if age_seconds is not None:
            lines.append(f"  age_seconds: {age_seconds}")

    return "\n".join(lines)
