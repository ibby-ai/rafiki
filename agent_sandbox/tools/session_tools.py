"""Session spawning tools for parallel work delegation.

This module provides MCP tools that allow a parent agent to spawn and manage
child sessions for parallel work. Children execute independently and can use
either the Agent SDK (for research/conversation) or CLI (for code execution).

Tools:
    - spawn_session: Create a new child session for a delegated task
    - check_session_status: Check the current status of a child session
    - get_session_result: Retrieve the result from a completed child session
    - list_child_sessions: List all children spawned by this parent

Usage Example:
    1. Parent spawns a child for research:
       spawn_session(task="Research Python async patterns", sandbox_type="agent_sdk")
       -> {"child_id": "abc-123", "status": "queued"}

    2. Parent checks progress:
       check_session_status(child_id="abc-123")
       -> {"status": "running", "started_at": 1672531200}

    3. Parent retrieves result when complete:
       get_session_result(child_id="abc-123")
       -> {"status": "complete", "result": "Python async patterns..."}

Architecture Notes:
    - Children are jobs enqueued via the existing job queue system
    - Parent-child relationships tracked in CHILD_SESSION_REGISTRY
    - Each child gets an isolated workspace at /data/jobs/{child_job_id}/
    - Children run with the same sandbox configuration as standalone jobs

See: agent_sandbox.jobs for child job creation and registry functions.
See: agent_sandbox.schemas.session_spawn for request/response schemas.
"""

from typing import Any

from claude_agent_sdk import tool

from agent_sandbox.config.settings import get_settings
from agent_sandbox.jobs import (
    can_spawn_child,
    enqueue_job,
    get_child_count,
    get_child_session_result,
    get_child_sessions,
    get_job_status,
    register_child_session,
)

_settings = get_settings()


# Store parent context in a module-level variable that gets set by the controller
# This allows tools to know which parent session they belong to
_current_parent_id: str | None = None


def set_parent_context(parent_id: str | None) -> None:
    """Set the current parent session ID for tool context.

    Called by the controller before agent execution to provide context
    for child session tools.

    Args:
        parent_id: UUID of the current parent job/session, or None to clear
    """
    global _current_parent_id
    _current_parent_id = parent_id


def get_parent_context() -> str | None:
    """Get the current parent session ID.

    Returns:
        UUID of the current parent job/session, or None if not set.
    """
    return _current_parent_id


@tool(
    "spawn_session",
    "Spawn a child session for parallel work delegation. Use this to delegate "
    "independent subtasks that can run concurrently. The child runs in an isolated "
    "sandbox and returns results when complete.",
    {
        "task": str,
        "sandbox_type": str,
        "context": str,
        "timeout_seconds": int,
        "allowed_tools": str,
    },
)
async def spawn_session(args: dict[str, Any]) -> dict[str, Any]:
    """Spawn a new child session to execute a delegated task.

    Creates a child job that runs independently in its own sandbox. The parent
    can monitor progress and retrieve results when the child completes.

    Args:
        args: Dict with keys:
            - task (required): Description of what the child should do
            - sandbox_type (optional): "agent_sdk" (default) or "cli"
            - context (optional): Additional context for the child
            - timeout_seconds (optional): Max time for child (default 300)
            - allowed_tools (optional): Comma-separated tools (CLI only)

    Returns:
        Tool result with child_id and initial status.
    """
    task = args.get("task")
    if not task:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: 'task' parameter is required. Provide a description of what the child session should accomplish.",
                }
            ]
        }

    # Check if child sessions are enabled
    if not _settings.enable_child_sessions:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: Child session spawning is disabled. Contact administrator to enable.",
                }
            ]
        }

    # Get parent context
    parent_id = get_parent_context()
    if not parent_id:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: No parent session context available. Cannot spawn child sessions in this context.",
                }
            ]
        }

    # Check if parent can spawn more children
    if not can_spawn_child(parent_id):
        current_count = get_child_count(parent_id)
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: Maximum child sessions reached ({current_count}/{_settings.max_children_per_session}). "
                    "Wait for existing children to complete before spawning more.",
                }
            ]
        }

    # Extract parameters
    sandbox_type = args.get("sandbox_type", "agent_sdk")
    if sandbox_type not in ("agent_sdk", "cli"):
        sandbox_type = "agent_sdk"

    context = args.get("context")
    timeout_seconds = args.get("timeout_seconds", _settings.child_session_default_timeout)
    allowed_tools = args.get("allowed_tools")

    # Build the question/prompt for the child
    child_prompt = task
    if context:
        child_prompt = f"Context: {context}\n\nTask: {task}"

    # Create metadata for the child job
    child_metadata = {
        "is_child_session": True,
        "parent_job_id": parent_id,
        "spawn_context": {
            "task": task,
            "context": context,
            "sandbox_type": sandbox_type,
            "timeout_seconds": timeout_seconds,
            "allowed_tools": allowed_tools,
        },
        "child_sequence": get_child_count(parent_id) + 1,
    }

    # Enqueue the child job
    child_job_id = enqueue_job(
        question=child_prompt,
        metadata=child_metadata,
    )

    # Register in child session registry
    registered = register_child_session(
        parent_id=parent_id,
        child_job_id=child_job_id,
        task=task,
        sandbox_type=sandbox_type,
        context=context,
        timeout_seconds=timeout_seconds,
        allowed_tools=allowed_tools,
    )

    if not registered:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: Failed to register child session. Child job {child_job_id} was created but may not be tracked properly.",
                }
            ]
        }

    return {
        "content": [
            {
                "type": "text",
                "text": f"Child session spawned successfully.\n\n"
                f"child_id: {child_job_id}\n"
                f"status: queued\n"
                f"sandbox_type: {sandbox_type}\n"
                f"task: {task}\n\n"
                f"Use check_session_status to monitor progress, then get_session_result when complete.",
            }
        ]
    }


@tool(
    "check_session_status",
    "Check the current status of a spawned child session. Use this to monitor "
    "progress and determine when a child has completed its task.",
    {"child_id": str},
)
async def check_session_status(args: dict[str, Any]) -> dict[str, Any]:
    """Check the status of a child session.

    Args:
        args: Dict with key:
            - child_id (required): UUID of the child session to check

    Returns:
        Tool result with current status and timing information.
    """
    child_id = args.get("child_id")
    if not child_id:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: 'child_id' parameter is required.",
                }
            ]
        }

    # Check if child sessions are enabled
    if not _settings.enable_child_sessions:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: Child session tracking is disabled.",
                }
            ]
        }

    # Get parent context
    parent_id = get_parent_context()
    if not parent_id:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: No parent session context available.",
                }
            ]
        }

    # Look up child in registry
    children = get_child_sessions(parent_id)
    child_entry = None
    for child in children:
        if child.get("child_job_id") == child_id:
            child_entry = child
            break

    if not child_entry:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: Child session {child_id} not found. It may belong to a different parent session.",
                }
            ]
        }

    # Get job status for additional info
    job_status = get_job_status(child_id)

    status = child_entry.get("status", "unknown")
    task = child_entry.get("task", "")
    sandbox_type = child_entry.get("sandbox_type", "")
    created_at = child_entry.get("created_at")
    started_at = child_entry.get("started_at")
    completed_at = child_entry.get("completed_at")

    # Build status message
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
            duration_ms = (completed_at - started_at) * 1000
            lines.append(f"duration_ms: {duration_ms}")

    if job_status and job_status.error:
        lines.append(f"error: {job_status.error}")

    if status in ("complete", "failed"):
        lines.append("")
        lines.append("Use get_session_result to retrieve the full result.")

    return {
        "content": [
            {
                "type": "text",
                "text": "\n".join(lines),
            }
        ]
    }


@tool(
    "get_session_result",
    "Get the result from a completed child session. Use this after "
    "check_session_status indicates the child has finished.",
    {"child_id": str},
)
async def get_session_result(args: dict[str, Any]) -> dict[str, Any]:
    """Get the result of a completed child session.

    Args:
        args: Dict with key:
            - child_id (required): UUID of the child session

    Returns:
        Tool result with the child's output and any artifacts.
    """
    child_id = args.get("child_id")
    if not child_id:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: 'child_id' parameter is required.",
                }
            ]
        }

    # Check if child sessions are enabled
    if not _settings.enable_child_sessions:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: Child session tracking is disabled.",
                }
            ]
        }

    # Get parent context
    parent_id = get_parent_context()
    if not parent_id:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: No parent session context available.",
                }
            ]
        }

    # Get result from registry
    result = get_child_session_result(parent_id, child_id)
    if not result:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: Child session {child_id} not found. It may belong to a different parent session.",
                }
            ]
        }

    status = result.get("status", "unknown")

    if status == "not_found":
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: Child job record not found for {child_id}.",
                }
            ]
        }

    if status in ("queued", "running"):
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Child session {child_id} is still {status}.\n\n"
                    f"Use check_session_status to monitor progress. Results are only "
                    f"available after the child completes.",
                }
            ]
        }

    if status == "failed":
        error = result.get("error", "Unknown error")
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Child session {child_id} failed.\n\nError: {error}",
                }
            ]
        }

    if status == "canceled":
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Child session {child_id} was canceled before completion.",
                }
            ]
        }

    # status is "complete" - format result
    lines = [
        "Child Session Result",
        "---",
        f"child_id: {child_id}",
        "status: complete",
        f"task: {result.get('task', '')}",
        "",
        "Result:",
        result.get("result", "(no result text)"),
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

    return {
        "content": [
            {
                "type": "text",
                "text": "\n".join(lines),
            }
        ]
    }


@tool(
    "list_child_sessions",
    "List all child sessions spawned by this parent. Shows status of each "
    "child to help track parallel work progress.",
    {},
)
async def list_child_sessions(args: dict[str, Any]) -> dict[str, Any]:
    """List all child sessions spawned by this parent.

    Args:
        args: Dict (no required parameters)

    Returns:
        Tool result with list of all children and their statuses.
    """
    # Check if child sessions are enabled
    if not _settings.enable_child_sessions:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: Child session tracking is disabled.",
                }
            ]
        }

    # Get parent context
    parent_id = get_parent_context()
    if not parent_id:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: No parent session context available.",
                }
            ]
        }

    # Get all children
    children = get_child_sessions(parent_id)

    if not children:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "No child sessions have been spawned yet.\n\n"
                    "Use spawn_session to create child sessions for parallel work.",
                }
            ]
        }

    # Count by status
    status_counts: dict[str, int] = {}
    for child in children:
        status = child.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    total = len(children)
    active = status_counts.get("queued", 0) + status_counts.get("running", 0)
    completed = status_counts.get("complete", 0)
    failed = status_counts.get("failed", 0) + status_counts.get("canceled", 0)

    # Build output
    lines = [
        "Child Sessions",
        "---",
        f"Total: {total} | Active: {active} | Completed: {completed} | Failed: {failed}",
        f"Capacity: {total}/{_settings.max_children_per_session}",
        "",
    ]

    for child in children:
        child_id = child.get("child_job_id", "unknown")
        status = child.get("status", "unknown")
        task = child.get("task", "")
        sandbox_type = child.get("sandbox_type", "")

        # Truncate task if too long
        if len(task) > 50:
            task = task[:47] + "..."

        lines.append(f"- [{status}] {child_id[:8]}... ({sandbox_type})")
        lines.append(f"  Task: {task}")

    return {
        "content": [
            {
                "type": "text",
                "text": "\n".join(lines),
            }
        ]
    }
