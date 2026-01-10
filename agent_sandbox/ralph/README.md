# Ralph Wiggum - Autonomous Coding Loop

Ralph Wiggum is an autonomous coding loop pattern for the Claude Code CLI. It works through a PRD (Product Requirements Document) containing tasks, iterating until all tasks are complete or max iterations are reached. After each successful iteration, it optionally creates git commits to track progress.

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Ralph Loop Flow                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   1. Initialize Workspace (empty, git clone, or template)       │
│   2. Write PRD and create initial git commit                    │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │  FOR each iteration (up to max_iterations):             │   │
│   │                                                         │   │
│   │  ┌─────────────────────────────────────────────────┐    │   │
│   │  │ 1. Get highest priority incomplete task        │    │   │
│   │  │ 2. Write status.json for polling               │    │   │
│   │  │ 3. Build prompt with task + workspace path     │    │   │
│   │  │ 4. Run Claude CLI                              │    │   │
│   │  │ 5. Run feedback commands (if configured)       │    │   │
│   │  │ 6. Mark task complete in prd.json              │    │   │
│   │  │ 7. Create git commit (if auto_commit=True)     │    │   │
│   │  │ 8. Update progress.txt                         │    │   │
│   │  └─────────────────────────────────────────────────┘    │   │
│   │                                                         │   │
│   │  EXIT WHEN: All tasks pass OR stop signal OR failure    │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│   3. Write final status to status.json                          │
│   4. Return RalphLoopResult                                     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Module Structure

| File | Purpose |
|------|---------|
| `loop.py` | Main orchestrator: `run_ralph_loop()`, `build_prompt()`, `run_cli()` |
| `schemas.py` | Pydantic models: `Prd`, `PrdItem`, `IterationResult`, `RalphLoopResult`, etc. |
| `prd.py` | PRD file management: `read_prd()`, `write_prd()`, `mark_task_complete()` |
| `progress.py` | Progress tracking: `init_progress()`, `append_progress()` |
| `status.py` | Polling status file: `write_status()`, `read_status()` |
| `git.py` | Git operations: `init_git()`, `commit_changes()`, `get_git_log()` |
| `feedback.py` | Feedback loop execution: `run_feedback_loops()`, allowlist validation |
| `workspace.py` | Workspace initialization: `initialize_workspace()` |

## Quick Start

### Programmatic Usage

```python
from pathlib import Path
from agent_sandbox.ralph import (
    run_ralph_loop,
    Prd,
    PrdItem,
    WorkspaceSource,
)

# Define your PRD
prd = Prd(
    name="hello-world-project",
    userStories=[
        PrdItem(
            id="task-1",
            category="functional",
            description="Create hello.py that prints Hello World",
            steps=["Run python hello.py", "Verify output contains 'Hello World'"],
            priority=1,
        ),
        PrdItem(
            id="task-2",
            category="quality",
            description="Add unit tests for hello.py",
            steps=["Run pytest"],
            priority=2,
        ),
    ],
)

# Run the loop
result = run_ralph_loop(
    job_id="job-12345",
    prd=prd,
    workspace=Path("/data-cli/jobs/job-12345"),
    workspace_source=WorkspaceSource(type="empty"),
    max_iterations=10,
    timeout_per_iteration=300,
    first_iteration_timeout=600,  # Longer timeout for cold start
    auto_commit=True,
    feedback_commands=["pytest"],  # Optional validation
)

# Check result
print(f"Status: {result.status}")
print(f"Tasks completed: {result.tasks_completed}/{result.tasks_total}")
for ir in result.iteration_results:
    print(f"  Iteration {ir.iteration}: {ir.status} - {ir.task_id}")
```

### HTTP API Usage

```bash
# Start the server
modal serve -m agent_sandbox.app

# Submit a job
curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/ralph/start' \
  -H "Content-Type: application/json" \
  -d '{
    "prd": {
      "name": "test-project",
      "userStories": [{
        "id": "task-1",
        "category": "functional",
        "description": "Create hello.py that prints Hello World",
        "steps": ["Run python hello.py"],
        "priority": 1,
        "passes": false
      }]
    },
    "max_iterations": 5,
    "auto_commit": true
  }'

# Response: {"job_id": "...", "call_id": "...", "status": "started"}

# Poll for status
curl 'https://<org>--test-sandbox-http-app-dev.modal.run/ralph/{job_id}?call_id={call_id}'
```

## Workspace Files

During execution, Ralph creates and manages these files in the workspace:

| File | Description |
|------|-------------|
| `prd.json` | PRD with task completion status (`passes: true/false`) |
| `progress.txt` | Human-readable progress log with timestamps |
| `status.json` | Machine-readable status for polling (iteration, task, etc.) |
| `.git/` | Git repository with commit history |

## Configuration Options

### RalphStartRequest Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `prd` | `Prd` | required | Product requirements document |
| `workspace_source` | `WorkspaceSource` | `empty` | How to initialize workspace |
| `prompt_template` | `str \| None` | default template | Custom prompt template |
| `max_iterations` | `int` | 10 | Maximum loop iterations |
| `timeout_per_iteration` | `int` | 300 | CLI timeout per iteration (seconds) |
| `first_iteration_timeout` | `int \| None` | None | Longer timeout for first iteration (cold start) |
| `allowed_tools` | `list[str]` | `["Read", "Write", "Bash", "Glob", "Grep"]` | CLI tool permissions |
| `feedback_commands` | `list[str]` | `[]` | Validation commands to run |
| `feedback_timeout` | `int` | 120 | Timeout for feedback commands |
| `auto_commit` | `bool` | True | Create git commits on success |
| `max_consecutive_failures` | `int` | 3 | Stop after N consecutive CLI failures |

### WorkspaceSource Types

- **empty**: Start with an empty workspace directory
- **git_clone**: Clone from a git repository (`git_url`, `git_branch`)
- **template**: Copy from a template directory (`template_path`)

#### Git Clone Example

Clone an existing repository and work on it:

```bash
curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/ralph/start' \
  -H "Content-Type: application/json" \
  -d '{
    "prd": {
      "name": "existing-repo-tasks",
      "userStories": [{
        "id": "task-1",
        "category": "technical",
        "description": "List files and create summary",
        "steps": ["Run ls -la", "Verify output file exists"],
        "priority": 1,
        "passes": false
      }]
    },
    "workspace_source": {
      "type": "git_clone",
      "git_url": "https://github.com/snarktank/ralph.git",
      "git_branch": "main"
    },
    "max_iterations": 3,
    "first_iteration_timeout": 600
  }'
```

The repository is cloned into the workspace, preserving git history. Ralph can then work on the existing codebase.

## Result Statuses

### RalphLoopStatus

| Status | Description |
|--------|-------------|
| `complete` | All tasks marked as `passes: true` |
| `failed` | Max consecutive CLI failures reached |
| `max_iterations` | Loop exhausted without completing all tasks |
| `stopped` | Stop signal (`<promise>COMPLETE</promise>`) received |

### IterationStatus

| Status | Description |
|--------|-------------|
| `running` | Currently executing |
| `completed` | Task completed successfully |
| `failed` | CLI or feedback command failed |
| `stopped` | Stopped by signal |

## Feedback Commands

Feedback commands are run after each successful CLI iteration to validate the work. Only allowlisted commands are permitted for security:

```python
ALLOWED_FEEDBACK_COMMANDS = {
    "npm run test", "npm run lint", "npm run build",
    "pytest", "pytest -v", "python -m pytest",
    "make test", "make lint", "make check",
    "ruff check .", "ruff format --check .",
    "cargo test", "cargo check",
    "go test ./...", "go vet ./...",
}
```

Custom `npm run <script>` commands are also allowed if the script name is alphanumeric.

## Git Integration

Ralph uses git to track progress:

- **Author**: `Ralph Wiggum <ralph@modal.local>`
- **Initial commit**: Created after workspace initialization
- **Iteration commits**: Created after each successful iteration (if `auto_commit=True`)
- **Commit message format**: `Ralph iteration {n}: {task_description}`

## Debugging

### CLI Output Capture

Each `IterationResult` includes `cli_output` (truncated to 2000 chars) for debugging:

```python
for ir in result.iteration_results:
    if ir.cli_output:
        print(f"CLI output: {ir.cli_output[:500]}...")
```

### Status Polling

The `status.json` file is updated throughout execution for real-time monitoring:

```json
{
  "status": "running",
  "current_iteration": 3,
  "max_iterations": 10,
  "tasks_completed": 2,
  "tasks_total": 5,
  "current_task": "task-3"
}
```

Final status is written before the loop returns, ensuring the status file reflects the actual outcome.

## Known Limitations

1. **No deliverable verification**: Tasks are marked complete based on CLI exit code and feedback commands. The loop does not verify that specific files were actually created.

2. **Feedback command allowlist**: Only pre-approved commands can be used for validation. Custom scripts require `npm run <script>` format.

3. **Single-task focus**: Each iteration works on one task. Complex interdependent tasks may require careful PRD ordering.

## Testing

```bash
# Run all Ralph tests
uv run pytest tests/test_ralph*.py -v

# Run specific test file
uv run pytest tests/test_ralph_loop.py -v

# Run with coverage
uv run pytest tests/test_ralph*.py --cov=agent_sandbox.ralph
```
