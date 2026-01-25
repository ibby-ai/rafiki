"""Main Ralph loop orchestrator.

Implements the autonomous coding loop that works through a PRD until all
tasks are complete or max iterations is reached.

This module provides two main functions:
- run_ralph_loop(): Synchronous execution returning final result
- run_ralph_loop_streaming(): Generator that yields iteration events for SSE streaming

Both functions support pause/resume via the Ralph control API.
"""

import subprocess
from collections.abc import Generator
from pathlib import Path
from typing import Any

from agent_sandbox.prompts.prompts import RALPH_PROMPT_TEMPLATE
from agent_sandbox.utils.cli import claude_cli_env, demote_to_claude

from .feedback import run_feedback_loops
from .git import (
    commit_changes,
    configure_remote,
    get_authenticated_url,
    init_git,
    push_to_remote,
)
from .prd import (
    all_tasks_complete,
    get_next_task,
    is_task_complete,
    mark_task_complete,
    read_prd,
    write_prd,
)
from .progress import append_progress, init_progress
from .schemas import (
    IterationResult,
    IterationStatus,
    Prd,
    RalphCheckpoint,
    RalphLoopResult,
    RalphLoopStatus,
    RalphStreamEvent,
    WorkspaceSource,
)
from .status import write_status
from .workspace import initialize_workspace

STOP_SIGNAL = "<promise>COMPLETE</promise>"


def build_prompt(
    template: str,
    task_id: str,
    task_description: str,
    task_steps: list[str],
    workspace_path: str,
) -> str:
    """Build prompt with task details.

    Args:
        template: Prompt template string with placeholders.
        task_id: ID of the current task.
        task_description: Description of the current task.
        task_steps: Verification steps for the task.
        workspace_path: Path to the workspace directory.

    Returns:
        Formatted prompt string.
    """
    steps_str = (
        "\n".join(f"  - {step}" for step in task_steps) if task_steps else "  (none specified)"
    )
    return template.format(
        task_id=task_id,
        task_description=task_description,
        task_steps=steps_str,
        workspace_path=workspace_path,
    )


def run_cli(
    workspace: Path,
    prompt: str,
    allowed_tools: list[str],
    timeout: int,
) -> tuple[str, int]:
    """Run Claude CLI subprocess. Returns (output, exit_code).

    Args:
        workspace: Working directory for the CLI.
        prompt: Prompt to send to the CLI.
        allowed_tools: List of allowed tools.
        timeout: Timeout in seconds.

    Returns:
        Tuple of (stdout + stderr output, exit code).
    """
    cmd = ["claude", "-p", prompt, "--output-format", "text"]
    cmd.append("--dangerously-skip-permissions")
    if allowed_tools:
        cmd.extend(["--allowedTools", ",".join(allowed_tools)])

    env = claude_cli_env()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(workspace),
            stdin=subprocess.DEVNULL,
            env=env,
            preexec_fn=demote_to_claude(),
        )
        return result.stdout + result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return f"CLI timed out after {timeout}s", 124
    except Exception as e:
        return str(e), 1


def _handle_push_on_complete(
    workspace: Path,
    push_on_complete: bool,
    remote_url: str | None,
    target_branch: str,
    force_push: bool,
) -> tuple[bool, str | None, str | None]:
    """Handle pushing to remote after completion.

    Args:
        workspace: Path to the workspace directory.
        push_on_complete: Whether push was requested.
        remote_url: The remote repository URL.
        target_branch: Branch name to push to.
        force_push: Whether to force push.

    Returns:
        Tuple of (pushed, pushed_to, push_error).
    """
    if not push_on_complete or not remote_url:
        return False, None, None

    import os

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return False, None, "GITHUB_TOKEN not set in environment"

    try:
        auth_url = get_authenticated_url(remote_url, token)
        configure_remote(workspace, auth_url)
        push_to_remote(workspace, branch=target_branch, force=force_push)
        return True, target_branch, None
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr or "")
        # Sanitize stderr to remove any token that might have leaked
        if token and token in stderr:
            stderr = stderr.replace(token, "[REDACTED]")
        # Never log auth_url - it contains the token
        return False, None, f"Git push failed: {stderr}"
    except Exception as e:
        return False, None, str(e)


def run_ralph_loop(
    job_id: str,
    prd: Prd,
    workspace: Path,
    workspace_source: WorkspaceSource,
    prompt_template: str | None = None,
    max_iterations: int = 10,
    timeout_per_iteration: int = 300,
    first_iteration_timeout: int | None = None,
    allowed_tools: list[str] | None = None,
    feedback_commands: list[str] | None = None,
    feedback_timeout: int = 120,
    auto_commit: bool = True,
    max_consecutive_failures: int = 3,
    # Push options
    push_on_complete: bool = False,
    remote_url: str | None = None,
    target_branch: str = "ralph-output",
    force_push: bool = False,
    # Internal params
    _start_iteration: int = 1,
    _prior_results: list[IterationResult] | None = None,
    _skip_workspace_init: bool = False,
) -> RalphLoopResult:
    """Main Ralph loop execution.

    Args:
        job_id: Unique job identifier.
        prd: PRD containing tasks to complete.
        workspace: Path to workspace directory.
        workspace_source: How to initialize the workspace.
        prompt_template: Custom prompt template (uses default if None).
        max_iterations: Maximum iterations before stopping.
        timeout_per_iteration: CLI timeout per iteration in seconds.
        first_iteration_timeout: Longer timeout for first iteration (cold start).
        allowed_tools: List of allowed CLI tools.
        feedback_commands: Commands to run for validation.
        feedback_timeout: Timeout for feedback commands.
        auto_commit: Whether to auto-commit after each successful iteration.
        max_consecutive_failures: Stop after this many consecutive CLI failures.
        push_on_complete: Push commits to remote after successful completion.
        remote_url: GitHub repository URL for push operations.
        target_branch: Branch name to push to (default: ralph-output).
        force_push: Whether to force push (use with caution).
        _start_iteration: Internal - iteration to start from (for resume).
        _prior_results: Internal - results from prior iterations (for resume).
        _skip_workspace_init: Internal - skip workspace initialization (for resume).

    Returns:
        RalphLoopResult with final status and iteration history.
    """
    # Import pause check function here to avoid circular imports
    from agent_sandbox.jobs import is_ralph_paused, mark_ralph_paused

    prompt_template = prompt_template or RALPH_PROMPT_TEMPLATE
    allowed_tools = allowed_tools or ["Read", "Write", "Bash", "Glob", "Grep"]
    feedback_commands = feedback_commands or []

    # Initialize workspace with source code (unless resuming)
    if not _skip_workspace_init:
        initialize_workspace(workspace, workspace_source)
        write_prd(workspace, prd)
        init_progress(workspace, prd.name)
        init_git(workspace)
        commit_changes(workspace, "Initial commit: PRD and progress setup")

    iteration_results: list[IterationResult] = list(_prior_results) if _prior_results else []
    consecutive_failures = 0

    for i in range(_start_iteration, max_iterations + 1):
        # Check for pause request before starting iteration
        if is_ralph_paused(job_id):
            current_prd = read_prd(workspace)
            task = get_next_task(current_prd)
            tasks_completed = len([t for t in current_prd.userStories if t.passes])
            tasks_total = len(current_prd.userStories)

            # Create checkpoint for resume
            checkpoint = create_checkpoint(
                job_id=job_id,
                iteration=i,
                max_iterations=max_iterations,
                iteration_results=iteration_results,
                prd=current_prd,
                current_task_id=task.id if task else None,
            )
            mark_ralph_paused(job_id, checkpoint.model_dump())

            write_status(
                workspace,
                status=RalphLoopStatus.PAUSED.value,
                current_iteration=i - 1,
                max_iterations=max_iterations,
                tasks_completed=tasks_completed,
                tasks_total=tasks_total,
                current_task=None,
            )

            return RalphLoopResult(
                job_id=job_id,
                status=RalphLoopStatus.PAUSED,
                iterations_completed=i - 1,
                iterations_max=max_iterations,
                tasks_completed=tasks_completed,
                tasks_total=tasks_total,
                iteration_results=iteration_results,
                final_prd=current_prd,
                error=None,
            )

        current_prd = read_prd(workspace)

        # Check if already complete
        if all_tasks_complete(current_prd):
            tasks_completed = len([t for t in current_prd.userStories if t.passes])
            tasks_total = len(current_prd.userStories)

            # Handle push
            pushed, pushed_to, push_error = _handle_push_on_complete(
                workspace, push_on_complete, remote_url, target_branch, force_push
            )

            write_status(
                workspace,
                status=RalphLoopStatus.COMPLETE.value,
                current_iteration=i - 1,
                max_iterations=max_iterations,
                tasks_completed=tasks_completed,
                tasks_total=tasks_total,
                current_task=None,
            )
            return RalphLoopResult(
                job_id=job_id,
                status=RalphLoopStatus.COMPLETE,
                iterations_completed=i - 1,
                iterations_max=max_iterations,
                tasks_completed=tasks_completed,
                tasks_total=tasks_total,
                iteration_results=iteration_results,
                final_prd=current_prd,
                error=None,
                pushed=pushed,
                pushed_to=pushed_to,
                push_error=push_error,
            )

        # Get next task
        task = get_next_task(current_prd)
        if not task:
            break  # No more tasks

        # Write status for polling
        write_status(
            workspace,
            status="running",
            current_iteration=i,
            max_iterations=max_iterations,
            tasks_completed=len([t for t in current_prd.userStories if t.passes]),
            tasks_total=len(current_prd.userStories),
            current_task=task.id,
        )

        # Build prompt with task details
        prompt = build_prompt(
            prompt_template, task.id, task.description, task.steps, str(workspace)
        )

        # Run CLI (use longer timeout for first iteration if specified)
        iteration_timeout = (
            first_iteration_timeout if i == 1 and first_iteration_timeout else timeout_per_iteration
        )
        output, exit_code = run_cli(
            workspace=workspace,
            prompt=prompt,
            allowed_tools=allowed_tools,
            timeout=iteration_timeout,
        )

        # Handle CLI failure
        if exit_code != 0:
            consecutive_failures += 1
            append_progress(
                workspace, f"Iteration {i}: CLI FAILED (exit {exit_code}) on task '{task.id}'"
            )
            iteration_results.append(
                IterationResult(
                    iteration=i,
                    task_id=task.id,
                    task_description=task.description,
                    status=IterationStatus.FAILED,
                    cli_exit_code=exit_code,
                    feedback_passed=False,
                    commit_sha=None,
                    error=f"CLI failed with exit code {exit_code}",
                    cli_output=output[:2000] if output else None,
                )
            )

            if consecutive_failures >= max_consecutive_failures:
                current_prd = read_prd(workspace)
                tasks_completed = len([t for t in current_prd.userStories if t.passes])
                tasks_total = len(current_prd.userStories)
                write_status(
                    workspace,
                    status=RalphLoopStatus.FAILED.value,
                    current_iteration=i,
                    max_iterations=max_iterations,
                    tasks_completed=tasks_completed,
                    tasks_total=tasks_total,
                    current_task=None,
                )
                return RalphLoopResult(
                    job_id=job_id,
                    status=RalphLoopStatus.FAILED,
                    iterations_completed=i,
                    iterations_max=max_iterations,
                    tasks_completed=tasks_completed,
                    tasks_total=tasks_total,
                    iteration_results=iteration_results,
                    final_prd=current_prd,
                    error=f"Max consecutive failures ({max_consecutive_failures}) reached",
                )
            continue  # Retry same task

        # Reset consecutive failures on success
        consecutive_failures = 0

        # Check for stop signal
        if STOP_SIGNAL in output:
            current_prd = read_prd(workspace)
            tasks_completed = len([t for t in current_prd.userStories if t.passes])
            tasks_total = len(current_prd.userStories)

            # Handle push
            pushed, pushed_to, push_error = _handle_push_on_complete(
                workspace, push_on_complete, remote_url, target_branch, force_push
            )

            write_status(
                workspace,
                status=RalphLoopStatus.COMPLETE.value,
                current_iteration=i,
                max_iterations=max_iterations,
                tasks_completed=tasks_completed,
                tasks_total=tasks_total,
                current_task=None,
            )
            return RalphLoopResult(
                job_id=job_id,
                status=RalphLoopStatus.COMPLETE,
                iterations_completed=i,
                iterations_max=max_iterations,
                tasks_completed=tasks_completed,
                tasks_total=tasks_total,
                iteration_results=iteration_results,
                final_prd=current_prd,
                error=None,
                pushed=pushed,
                pushed_to=pushed_to,
                push_error=push_error,
            )

        # Verify task completion - check if agent updated prd.json
        updated_prd = read_prd(workspace)
        task_completed_by_agent = is_task_complete(updated_prd, task.id)

        # Run feedback loops
        feedback_result = None
        feedback_passed = True
        if feedback_commands:
            feedback_result = run_feedback_loops(workspace, feedback_commands, feedback_timeout)
            feedback_passed = feedback_result.passed

        # If feedback passed but task not marked by agent, mark it programmatically
        if feedback_passed and not task_completed_by_agent:
            mark_task_complete(workspace, task.id)

        # Commit if feedback passed
        commit_sha = None
        if auto_commit and feedback_passed:
            commit_sha = commit_changes(workspace, f"Ralph iteration {i}: {task.description}")

        # Update progress
        status_msg = (
            "PASS"
            if feedback_passed
            else f"FAIL ({feedback_result.failed_command if feedback_result else 'unknown'})"
        )
        append_progress(workspace, f"Iteration {i}: Task '{task.id}' - {status_msg}")

        # Record iteration result
        iteration_results.append(
            IterationResult(
                iteration=i,
                task_id=task.id,
                task_description=task.description,
                status=IterationStatus.COMPLETED if feedback_passed else IterationStatus.FAILED,
                cli_exit_code=exit_code,
                feedback_passed=feedback_passed,
                commit_sha=commit_sha,
                error=None
                if feedback_passed
                else (feedback_result.failed_command if feedback_result else None),
                cli_output=output[:2000] if output else None,
            )
        )

    # Max iterations reached
    current_prd = read_prd(workspace)
    tasks_completed = len([t for t in current_prd.userStories if t.passes])
    tasks_total = len(current_prd.userStories)

    # Handle push
    pushed, pushed_to, push_error = _handle_push_on_complete(
        workspace, push_on_complete, remote_url, target_branch, force_push
    )

    write_status(
        workspace,
        status=RalphLoopStatus.MAX_ITERATIONS.value,
        current_iteration=max_iterations,
        max_iterations=max_iterations,
        tasks_completed=tasks_completed,
        tasks_total=tasks_total,
        current_task=None,
    )
    return RalphLoopResult(
        job_id=job_id,
        status=RalphLoopStatus.MAX_ITERATIONS,
        iterations_completed=max_iterations,
        iterations_max=max_iterations,
        tasks_completed=tasks_completed,
        tasks_total=tasks_total,
        iteration_results=iteration_results,
        final_prd=current_prd,
        error=None,
        pushed=pushed,
        pushed_to=pushed_to,
        push_error=push_error,
    )


def create_checkpoint(
    job_id: str,
    iteration: int,
    max_iterations: int,
    iteration_results: list[IterationResult],
    prd: Prd,
    current_task_id: str | None = None,
    reason: str | None = None,
    requested_by: str | None = None,
) -> RalphCheckpoint:
    """Create a checkpoint for pausing a Ralph loop.

    Args:
        job_id: The job identifier.
        iteration: Current iteration number.
        max_iterations: Maximum iterations allowed.
        iteration_results: Results from completed iterations.
        prd: Current PRD state.
        current_task_id: ID of task being worked on (if any).
        reason: Reason for pausing.
        requested_by: Who requested the pause.

    Returns:
        RalphCheckpoint with all state needed to resume.
    """
    import time

    tasks_completed = len([t for t in prd.userStories if t.passes])
    tasks_total = len(prd.userStories)

    return RalphCheckpoint(
        job_id=job_id,
        iteration=iteration,
        max_iterations=max_iterations,
        tasks_completed=tasks_completed,
        tasks_total=tasks_total,
        current_task_id=current_task_id,
        iteration_results=iteration_results,
        prd_json=prd.model_dump_json(),
        created_at=int(time.time()),
        reason=reason,
        requested_by=requested_by,
    )


def run_ralph_loop_streaming(
    job_id: str,
    prd: Prd,
    workspace: Path,
    workspace_source: WorkspaceSource,
    prompt_template: str | None = None,
    max_iterations: int = 10,
    timeout_per_iteration: int = 300,
    first_iteration_timeout: int | None = None,
    allowed_tools: list[str] | None = None,
    feedback_commands: list[str] | None = None,
    feedback_timeout: int = 120,
    auto_commit: bool = True,
    max_consecutive_failures: int = 3,
    # Push options
    push_on_complete: bool = False,
    remote_url: str | None = None,
    target_branch: str = "ralph-output",
    force_push: bool = False,
    # Resume options
    start_iteration: int = 1,
    prior_results: list[IterationResult] | None = None,
    skip_workspace_init: bool = False,
) -> Generator[RalphStreamEvent, None, RalphLoopResult]:
    """Streaming version of Ralph loop that yields events for each iteration.

    This generator yields RalphStreamEvent objects as the loop progresses,
    enabling real-time progress streaming via SSE.

    Args:
        job_id: Unique job identifier.
        prd: PRD containing tasks to complete.
        workspace: Path to workspace directory.
        workspace_source: How to initialize the workspace.
        prompt_template: Custom prompt template (uses default if None).
        max_iterations: Maximum iterations before stopping.
        timeout_per_iteration: CLI timeout per iteration in seconds.
        first_iteration_timeout: Longer timeout for first iteration (cold start).
        allowed_tools: List of allowed CLI tools.
        feedback_commands: Commands to run for validation.
        feedback_timeout: Timeout for feedback commands.
        auto_commit: Whether to auto-commit after each successful iteration.
        max_consecutive_failures: Stop after this many consecutive CLI failures.
        push_on_complete: Push commits to remote after successful completion.
        remote_url: GitHub repository URL for push operations.
        target_branch: Branch name to push to (default: ralph-output).
        force_push: Whether to force push (use with caution).
        start_iteration: Iteration to start from (for resume).
        prior_results: Results from prior iterations (for resume).
        skip_workspace_init: Skip workspace initialization (for resume).

    Yields:
        RalphStreamEvent for each significant event:
        - started: Immediately when streaming begins (before initialization)
        - iteration_start: Beginning of an iteration
        - iteration_complete: Successful iteration completion
        - iteration_failed: Failed iteration
        - paused: Loop was paused
        - done: Loop completed

    Returns:
        RalphLoopResult with final status and iteration history.

    Usage:
        ```python
        async def stream_ralph():
            for event in run_ralph_loop_streaming(job_id, prd, workspace, ...):
                yield f"event: {event.event_type}\\ndata: {event.model_dump_json()}\\n\\n"
        ```
    """
    # Import pause check function here to avoid circular imports
    from agent_sandbox.jobs import is_ralph_paused, mark_ralph_paused

    # Immediately yield a "started" event so clients know streaming is working
    yield RalphStreamEvent(
        event_type="started",
        job_id=job_id,
        status="initializing",
    )

    prompt_template = prompt_template or RALPH_PROMPT_TEMPLATE
    allowed_tools = allowed_tools or ["Read", "Write", "Bash", "Glob", "Grep"]
    feedback_commands = feedback_commands or []

    # Initialize workspace with source code (unless resuming)
    if not skip_workspace_init:
        initialize_workspace(workspace, workspace_source)
        write_prd(workspace, prd)
        init_progress(workspace, prd.name)
        init_git(workspace)
        commit_changes(workspace, "Initial commit: PRD and progress setup")

    iteration_results: list[IterationResult] = list(prior_results) if prior_results else []
    consecutive_failures = 0

    for i in range(start_iteration, max_iterations + 1):
        # Check for pause request before starting iteration
        if is_ralph_paused(job_id):
            current_prd = read_prd(workspace)
            task = get_next_task(current_prd)
            tasks_completed = len([t for t in current_prd.userStories if t.passes])
            tasks_total = len(current_prd.userStories)

            # Create checkpoint for resume
            checkpoint = create_checkpoint(
                job_id=job_id,
                iteration=i,
                max_iterations=max_iterations,
                iteration_results=iteration_results,
                prd=current_prd,
                current_task_id=task.id if task else None,
            )
            mark_ralph_paused(job_id, checkpoint.model_dump())

            write_status(
                workspace,
                status=RalphLoopStatus.PAUSED.value,
                current_iteration=i - 1,
                max_iterations=max_iterations,
                tasks_completed=tasks_completed,
                tasks_total=tasks_total,
                current_task=None,
            )

            # Yield paused event
            yield RalphStreamEvent(
                event_type="paused",
                job_id=job_id,
                iteration=i,
                status="paused",
            )

            return RalphLoopResult(
                job_id=job_id,
                status=RalphLoopStatus.PAUSED,
                iterations_completed=i - 1,
                iterations_max=max_iterations,
                tasks_completed=tasks_completed,
                tasks_total=tasks_total,
                iteration_results=iteration_results,
                final_prd=current_prd,
                error=None,
            )

        current_prd = read_prd(workspace)

        # Check if already complete
        if all_tasks_complete(current_prd):
            tasks_completed = len([t for t in current_prd.userStories if t.passes])
            tasks_total = len(current_prd.userStories)

            # Handle push
            pushed, pushed_to, push_error = _handle_push_on_complete(
                workspace, push_on_complete, remote_url, target_branch, force_push
            )

            write_status(
                workspace,
                status=RalphLoopStatus.COMPLETE.value,
                current_iteration=i - 1,
                max_iterations=max_iterations,
                tasks_completed=tasks_completed,
                tasks_total=tasks_total,
                current_task=None,
            )

            result = RalphLoopResult(
                job_id=job_id,
                status=RalphLoopStatus.COMPLETE,
                iterations_completed=i - 1,
                iterations_max=max_iterations,
                tasks_completed=tasks_completed,
                tasks_total=tasks_total,
                iteration_results=iteration_results,
                final_prd=current_prd,
                error=None,
                pushed=pushed,
                pushed_to=pushed_to,
                push_error=push_error,
            )

            yield RalphStreamEvent(
                event_type="done",
                job_id=job_id,
                iteration=i - 1,
                status="complete",
                result=result,
            )

            return result

        # Get next task
        task = get_next_task(current_prd)
        if not task:
            break  # No more tasks

        # Yield iteration_start event
        yield RalphStreamEvent(
            event_type="iteration_start",
            job_id=job_id,
            iteration=i,
            task_id=task.id,
            task_description=task.description,
            status="running",
        )

        # Write status for polling
        write_status(
            workspace,
            status="running",
            current_iteration=i,
            max_iterations=max_iterations,
            tasks_completed=len([t for t in current_prd.userStories if t.passes]),
            tasks_total=len(current_prd.userStories),
            current_task=task.id,
        )

        # Build prompt with task details
        prompt = build_prompt(
            prompt_template, task.id, task.description, task.steps, str(workspace)
        )

        # Run CLI (use longer timeout for first iteration if specified)
        iteration_timeout = (
            first_iteration_timeout if i == 1 and first_iteration_timeout else timeout_per_iteration
        )
        output, exit_code = run_cli(
            workspace=workspace,
            prompt=prompt,
            allowed_tools=allowed_tools,
            timeout=iteration_timeout,
        )

        # Handle CLI failure
        if exit_code != 0:
            consecutive_failures += 1
            append_progress(
                workspace, f"Iteration {i}: CLI FAILED (exit {exit_code}) on task '{task.id}'"
            )

            iter_result = IterationResult(
                iteration=i,
                task_id=task.id,
                task_description=task.description,
                status=IterationStatus.FAILED,
                cli_exit_code=exit_code,
                feedback_passed=False,
                commit_sha=None,
                error=f"CLI failed with exit code {exit_code}",
                cli_output=output[:2000] if output else None,
            )
            iteration_results.append(iter_result)

            # Yield iteration_failed event
            yield RalphStreamEvent(
                event_type="iteration_failed",
                job_id=job_id,
                iteration=i,
                task_id=task.id,
                task_description=task.description,
                status="failed",
                cli_exit_code=exit_code,
                feedback_passed=False,
                error=f"CLI failed with exit code {exit_code}",
            )

            if consecutive_failures >= max_consecutive_failures:
                current_prd = read_prd(workspace)
                tasks_completed = len([t for t in current_prd.userStories if t.passes])
                tasks_total = len(current_prd.userStories)
                write_status(
                    workspace,
                    status=RalphLoopStatus.FAILED.value,
                    current_iteration=i,
                    max_iterations=max_iterations,
                    tasks_completed=tasks_completed,
                    tasks_total=tasks_total,
                    current_task=None,
                )

                result = RalphLoopResult(
                    job_id=job_id,
                    status=RalphLoopStatus.FAILED,
                    iterations_completed=i,
                    iterations_max=max_iterations,
                    tasks_completed=tasks_completed,
                    tasks_total=tasks_total,
                    iteration_results=iteration_results,
                    final_prd=current_prd,
                    error=f"Max consecutive failures ({max_consecutive_failures}) reached",
                )

                yield RalphStreamEvent(
                    event_type="done",
                    job_id=job_id,
                    iteration=i,
                    status="failed",
                    error=f"Max consecutive failures ({max_consecutive_failures}) reached",
                    result=result,
                )

                return result
            continue  # Retry same task

        # Reset consecutive failures on success
        consecutive_failures = 0

        # Check for stop signal
        if STOP_SIGNAL in output:
            current_prd = read_prd(workspace)
            tasks_completed = len([t for t in current_prd.userStories if t.passes])
            tasks_total = len(current_prd.userStories)

            # Handle push
            pushed, pushed_to, push_error = _handle_push_on_complete(
                workspace, push_on_complete, remote_url, target_branch, force_push
            )

            write_status(
                workspace,
                status=RalphLoopStatus.COMPLETE.value,
                current_iteration=i,
                max_iterations=max_iterations,
                tasks_completed=tasks_completed,
                tasks_total=tasks_total,
                current_task=None,
            )

            result = RalphLoopResult(
                job_id=job_id,
                status=RalphLoopStatus.COMPLETE,
                iterations_completed=i,
                iterations_max=max_iterations,
                tasks_completed=tasks_completed,
                tasks_total=tasks_total,
                iteration_results=iteration_results,
                final_prd=current_prd,
                error=None,
                pushed=pushed,
                pushed_to=pushed_to,
                push_error=push_error,
            )

            yield RalphStreamEvent(
                event_type="done",
                job_id=job_id,
                iteration=i,
                status="complete",
                result=result,
            )

            return result

        # Verify task completion - check if agent updated prd.json
        updated_prd = read_prd(workspace)
        task_completed_by_agent = is_task_complete(updated_prd, task.id)

        # Run feedback loops
        feedback_result = None
        feedback_passed = True
        if feedback_commands:
            feedback_result = run_feedback_loops(workspace, feedback_commands, feedback_timeout)
            feedback_passed = feedback_result.passed

        # If feedback passed but task not marked by agent, mark it programmatically
        if feedback_passed and not task_completed_by_agent:
            mark_task_complete(workspace, task.id)

        # Commit if feedback passed
        commit_sha = None
        if auto_commit and feedback_passed:
            commit_sha = commit_changes(workspace, f"Ralph iteration {i}: {task.description}")

        # Update progress
        status_msg = (
            "PASS"
            if feedback_passed
            else f"FAIL ({feedback_result.failed_command if feedback_result else 'unknown'})"
        )
        append_progress(workspace, f"Iteration {i}: Task '{task.id}' - {status_msg}")

        # Record iteration result
        iter_result = IterationResult(
            iteration=i,
            task_id=task.id,
            task_description=task.description,
            status=IterationStatus.COMPLETED if feedback_passed else IterationStatus.FAILED,
            cli_exit_code=exit_code,
            feedback_passed=feedback_passed,
            commit_sha=commit_sha,
            error=None
            if feedback_passed
            else (feedback_result.failed_command if feedback_result else None),
            cli_output=output[:2000] if output else None,
        )
        iteration_results.append(iter_result)

        # Yield iteration_complete event
        yield RalphStreamEvent(
            event_type="iteration_complete",
            job_id=job_id,
            iteration=i,
            task_id=task.id,
            task_description=task.description,
            status="completed" if feedback_passed else "failed",
            cli_exit_code=exit_code,
            feedback_passed=feedback_passed,
            commit_sha=commit_sha,
            error=None if feedback_passed else iter_result.error,
        )

    # Max iterations reached
    current_prd = read_prd(workspace)
    tasks_completed = len([t for t in current_prd.userStories if t.passes])
    tasks_total = len(current_prd.userStories)

    # Handle push
    pushed, pushed_to, push_error = _handle_push_on_complete(
        workspace, push_on_complete, remote_url, target_branch, force_push
    )

    write_status(
        workspace,
        status=RalphLoopStatus.MAX_ITERATIONS.value,
        current_iteration=max_iterations,
        max_iterations=max_iterations,
        tasks_completed=tasks_completed,
        tasks_total=tasks_total,
        current_task=None,
    )

    result = RalphLoopResult(
        job_id=job_id,
        status=RalphLoopStatus.MAX_ITERATIONS,
        iterations_completed=max_iterations,
        iterations_max=max_iterations,
        tasks_completed=tasks_completed,
        tasks_total=tasks_total,
        iteration_results=iteration_results,
        final_prd=current_prd,
        error=None,
        pushed=pushed,
        pushed_to=pushed_to,
        push_error=push_error,
    )

    yield RalphStreamEvent(
        event_type="done",
        job_id=job_id,
        iteration=max_iterations,
        status="max_iterations",
        result=result,
    )

    return result


def resume_ralph_loop(
    job_id: str,
    workspace: Path,
    checkpoint: RalphCheckpoint | dict[str, Any],
    workspace_source: WorkspaceSource | None = None,
    prompt_template: str | None = None,
    timeout_per_iteration: int = 300,
    first_iteration_timeout: int | None = None,
    allowed_tools: list[str] | None = None,
    feedback_commands: list[str] | None = None,
    feedback_timeout: int = 120,
    auto_commit: bool = True,
    max_consecutive_failures: int = 3,
    # Push options
    push_on_complete: bool = False,
    remote_url: str | None = None,
    target_branch: str = "ralph-output",
    force_push: bool = False,
) -> RalphLoopResult:
    """Resume a paused Ralph loop from a checkpoint.

    Args:
        job_id: The job identifier.
        workspace: Path to workspace directory.
        checkpoint: Checkpoint data from paused loop.
        workspace_source: Workspace source (not used for resume, workspace should exist).
        prompt_template: Custom prompt template.
        timeout_per_iteration: CLI timeout per iteration.
        first_iteration_timeout: Longer timeout for first iteration.
        allowed_tools: List of allowed CLI tools.
        feedback_commands: Commands to run for validation.
        feedback_timeout: Timeout for feedback commands.
        auto_commit: Whether to auto-commit.
        max_consecutive_failures: Stop after this many consecutive CLI failures.
        push_on_complete: Push commits to remote after successful completion.
        remote_url: GitHub repository URL for push operations.
        target_branch: Branch name to push to (default: ralph-output).
        force_push: Whether to force push (use with caution).

    Returns:
        RalphLoopResult with final status.
    """
    from agent_sandbox.jobs import clear_ralph_control

    # Convert dict to RalphCheckpoint if needed
    if isinstance(checkpoint, dict):
        checkpoint = RalphCheckpoint(**checkpoint)

    # Parse the PRD from checkpoint
    import json

    prd = Prd(**json.loads(checkpoint.prd_json))

    # Convert iteration results
    prior_results = checkpoint.iteration_results

    # Clear the pause state
    clear_ralph_control(job_id)

    # Resume from next iteration
    return run_ralph_loop(
        job_id=job_id,
        prd=prd,
        workspace=workspace,
        workspace_source=workspace_source or WorkspaceSource(),
        prompt_template=prompt_template,
        max_iterations=checkpoint.max_iterations,
        timeout_per_iteration=timeout_per_iteration,
        first_iteration_timeout=first_iteration_timeout,
        allowed_tools=allowed_tools,
        feedback_commands=feedback_commands,
        feedback_timeout=feedback_timeout,
        auto_commit=auto_commit,
        max_consecutive_failures=max_consecutive_failures,
        push_on_complete=push_on_complete,
        remote_url=remote_url,
        target_branch=target_branch,
        force_push=force_push,
        _start_iteration=checkpoint.iteration,
        _prior_results=prior_results,
        _skip_workspace_init=True,
    )
