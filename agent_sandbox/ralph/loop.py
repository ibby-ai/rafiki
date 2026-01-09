"""Main Ralph loop orchestrator.

Implements the autonomous coding loop that works through a PRD until all
tasks are complete or max iterations is reached.
"""

import subprocess
from pathlib import Path

from agent_sandbox.prompts.prompts import RALPH_PROMPT_TEMPLATE
from agent_sandbox.utils.cli import claude_cli_env, demote_to_claude

from .feedback import run_feedback_loops
from .git import commit_changes, init_git
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
    RalphLoopResult,
    RalphLoopStatus,
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

    Returns:
        RalphLoopResult with final status and iteration history.
    """
    prompt_template = prompt_template or RALPH_PROMPT_TEMPLATE
    allowed_tools = allowed_tools or ["Read", "Write", "Bash", "Glob", "Grep"]
    feedback_commands = feedback_commands or []

    # Initialize workspace with source code
    initialize_workspace(workspace, workspace_source)

    # Write PRD and initialize tracking files
    write_prd(workspace, prd)
    init_progress(workspace, prd.name)
    init_git(workspace)
    commit_changes(workspace, "Initial commit: PRD and progress setup")

    iteration_results: list[IterationResult] = []
    consecutive_failures = 0

    for i in range(1, max_iterations + 1):
        current_prd = read_prd(workspace)

        # Check if already complete
        if all_tasks_complete(current_prd):
            tasks_completed = len([t for t in current_prd.userStories if t.passes])
            tasks_total = len(current_prd.userStories)
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
    )
