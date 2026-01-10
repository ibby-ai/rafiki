# CLAUDE.md - Ralph Module Development Guide

This file provides guidance to Claude Code when working with the Ralph autonomous coding loop module.

## Module Purpose

Ralph Wiggum is an autonomous coding loop that:
1. Takes a PRD (Product Requirements Document) with tasks
2. Iterates through tasks using the Claude CLI
3. Validates work with feedback commands
4. Creates git commits to track progress
5. Returns results via HTTP API for polling

## Architecture

```
agent_sandbox/ralph/
├── __init__.py      # Public API exports
├── loop.py          # Main orchestrator (run_ralph_loop)
├── schemas.py       # Pydantic models (Prd, IterationResult, etc.)
├── prd.py           # PRD file management
├── progress.py      # Progress tracking
├── status.py        # Polling status file
├── git.py           # Git operations
├── feedback.py      # Feedback command execution
└── workspace.py     # Workspace initialization
```

## Key Functions

### `run_ralph_loop()` in `loop.py`
The main entry point. Orchestrates the entire loop:
- Initializes workspace and git
- Iterates until completion or failure
- Writes status before EVERY return statement
- Returns `RalphLoopResult`

### `build_prompt()` in `loop.py`
Builds the CLI prompt with:
- `{task_id}` - Current task ID
- `{task_description}` - Task description
- `{task_steps}` - Verification steps
- `{workspace_path}` - Full path to workspace (CRITICAL for file creation)

### `run_cli()` in `loop.py`
Executes Claude CLI as subprocess:
- Uses `--dangerously-skip-permissions` for non-interactive execution
- Captures stdout + stderr
- Returns (output, exit_code)

## HTTP Endpoints

Defined in `agent_sandbox/app.py`:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/ralph/start` | POST | Start async Ralph loop |
| `/ralph/{job_id}` | GET | Poll status (requires `call_id` query param) |

## Testing Commands

```bash
# Run all Ralph tests
uv run pytest tests/test_ralph*.py -v

# Run specific test
uv run pytest tests/test_ralph_loop.py::TestRunRalphLoop::test_loop_completes_all_tasks -v

# Test end-to-end with Modal
modal serve -m agent_sandbox.app
# Then use curl to POST to /ralph/start
```

## Common Development Tasks

### Adding a New Field to IterationResult

1. Add field to `IterationResult` in `schemas.py`
2. Update both `IterationResult` append calls in `loop.py` (lines ~216 and ~292)
3. Add tests in `tests/test_ralph_schemas.py`

### Modifying the Prompt Template

1. Edit `RALPH_PROMPT_TEMPLATE` in `agent_sandbox/prompts/prompts.py`
2. If adding new placeholders, update `build_prompt()` in `loop.py`
3. Update tests in `tests/test_ralph_loop.py::TestBuildPrompt`

### Adding New Return Points in run_ralph_loop()

**CRITICAL**: Every return statement in `run_ralph_loop()` must be preceded by a `write_status()` call. This ensures the status file reflects the final state for polling.

Pattern:
```python
tasks_completed = len([t for t in current_prd.userStories if t.passes])
tasks_total = len(current_prd.userStories)
write_status(
    workspace,
    status=RalphLoopStatus.COMPLETE.value,  # Use .value for string
    current_iteration=i,
    max_iterations=max_iterations,
    tasks_completed=tasks_completed,
    tasks_total=tasks_total,
    current_task=None,  # Clear on completion
)
return RalphLoopResult(...)
```

### Adding New Feedback Commands

1. Add to `ALLOWED_FEEDBACK_COMMANDS` set in `feedback.py`
2. Add test in `tests/test_ralph_feedback.py::TestAllowedFeedbackCommands`

## Important Patterns

### Status File Updates
- `write_status()` is called BEFORE every CLI execution (with `current_task`)
- `write_status()` is called BEFORE every return (with `current_task=None`)
- Status values use `RalphLoopStatus.VALUE.value` (string form)

### CLI Output Truncation
CLI output is truncated to 2000 characters to prevent response bloat:
```python
cli_output=output[:2000] if output else None
```

### First Iteration Timeout
Cold starts may take longer, so `first_iteration_timeout` provides a separate timeout for iteration 1:
```python
iteration_timeout = (
    first_iteration_timeout if i == 1 and first_iteration_timeout else timeout_per_iteration
)
```

### Git Identity
Set in `git.py`:
- Name: `Ralph Wiggum`
- Email: `ralph@modal.local`

## File Locations in Workspace

| File | Purpose |
|------|---------|
| `prd.json` | PRD with `passes` status |
| `progress.txt` | Human-readable log |
| `status.json` | Machine-readable polling status |
| `.git/` | Git repository |

## Common Issues

### Issue: Status shows "running" after completion
**Cause**: Missing `write_status()` before a return statement
**Fix**: Ensure every return in `run_ralph_loop()` is preceded by `write_status()`

### Issue: CLI doesn't create files in workspace
**Cause**: Prompt doesn't specify workspace path
**Fix**: Ensure `{workspace_path}` is in the prompt template and `build_prompt()` formats it

### Issue: Task marked complete but deliverable missing
**Cause**: Loop trusts CLI exit code, no deliverable verification
**Status**: Known limitation - deliverable verification is a future enhancement

### Issue: First iteration timeout (exit code 124)
**Cause**: Cold start takes longer than `timeout_per_iteration`
**Fix**: Set `first_iteration_timeout` to a higher value (e.g., 600s)

## Test Files

| File | Tests |
|------|-------|
| `tests/test_ralph_loop.py` | Main loop, build_prompt, run_cli |
| `tests/test_ralph_schemas.py` | All Pydantic models |
| `tests/test_ralph_status.py` | Status file read/write |
| `tests/test_ralph_prd.py` | PRD management |
| `tests/test_ralph_progress.py` | Progress tracking |
| `tests/test_ralph_git.py` | Git operations |
| `tests/test_ralph_feedback.py` | Feedback commands |
| `tests/test_ralph_workspace.py` | Workspace initialization |

## Linting

Always run after changes:
```bash
uv run ruff check --fix .
uv run ruff format .
```

## Git Clone Workspace Source

The `git_clone` workspace source type has been verified working. Key implementation details:

### Ownership Consistency
Both `workspace.py` and `git.py` use `_git_subprocess_kwargs()` to run git commands as the `claude` user in Modal sandboxes. This ensures consistent file ownership and prevents "dubious ownership" errors.

### Verified With
- Repository: `https://github.com/snarktank/ralph`
- See `docs/ralph-git-clone-verification.md` for full test results

### Example Usage
```python
workspace_source = WorkspaceSource(
    type="git_clone",
    git_url="https://github.com/snarktank/ralph.git",
    git_branch="main"
)
```

The cloned repository's `.git` directory is preserved, and `init_git()` skips re-initialization when it detects an existing git repo.
