# Claude CLI Sandbox + Volume Progress Report

Date: 2026-01-09
Branch: claude-code-cli-setup

## Summary
Implemented a dedicated Modal sandbox and persistent volume for the Claude Code CLI flow, wired Ralph to run inside the CLI sandbox/volume, and verified non-root execution with `--dangerously-skip-permissions`. Fixed Ralph git operations to run as the `claude` user to avoid root ownership errors. Normalized `write-result-path` so `jobs/<uuid>/result.json` lands directly under `/data-cli/jobs/<uuid>/`.

## Commits
- `5b0814d` — finalize claude cli sandbox

## Key Changes

### Dedicated CLI Sandbox + Volume
- Added CLI-specific settings:
  - `claude_cli_sandbox_name`
  - `claude_cli_persist_vol_name`
  - `claude_cli_fs_root` (default `/data-cli`)
- Created CLI sandbox creator that mounts the dedicated CLI volume and uses the CLI image.
- CLI runner now executes inside `modal.Sandbox.create()` with the CLI image and dedicated volume.
- HTTP app mounts both the agent SDK volume and the CLI volume.

### CLI Execution & Workspace
- Added `modal_backend/sandbox_runtime/cli_runner.py` to run `claude` inside the CLI sandbox with `demote_to_claude()` and proper env.
- CLI runner writes optional `result.json` and ensures ownership is set to `claude`.

### Ralph Integration
- Added `modal_backend/ralph/runner.py` to run Ralph loop inside the CLI sandbox and volume.
- Ralph loop writes `status.json`, `progress.txt`, `prd.json` inside `/data-cli/jobs/<job_id>/`.

### Git Ownership Fix
- `modal_backend/ralph/git.py` now runs git commands as `claude` when the process is root, avoiding git ownership errors and preserving the non-root requirement.

### Write-Result Path Normalization
- `write-result-path` now recognizes `jobs/<job_id>/...` and anchors to `/data-cli`, preventing nested `jobs/jobs/...` paths.

## Files Added/Updated
- Added: `modal_backend/sandbox_runtime/__init__.py`
- Added: `modal_backend/sandbox_runtime/cli_runner.py`
- Added: `modal_backend/ralph/runner.py`
- Updated: `modal_backend/main.py`
- Updated: `modal_backend/settings/settings.py`
- Updated: `modal_backend/ralph/git.py`
- Updated: `modal_backend/ralph/README.md`
- Updated: `docs/references/configuration.md`

## Test Runs

### CLI (dangerously-skip-permissions)
Command:
```
modal run -m modal_backend.main::run_claude_cli_remote \
  --prompt "Create a file named hello.txt with the text 'hi' in the current directory." \
  --job-id "9a21aaad-f208-45cb-8a1a-899a5c013549" \
  --allowed-tools "Write" \
  --dangerously-skip-permissions \
  --write-result-path "jobs/9a21aaad-f208-45cb-8a1a-899a5c013549/result.json"
```
Artifacts verified:
- `/data-cli/jobs/9a21aaad-f208-45cb-8a1a-899a5c013549/hello.txt`
- `/data-cli/jobs/9a21aaad-f208-45cb-8a1a-899a5c013549/result.json`

### Ralph HTTP (dangerously-skip-permissions via loop)
Commands:
```
modal serve -m modal_backend.main
curl -X POST 'https://saidiibrahim--test-sandbox-http-app-dev.modal.run/ralph/start' \
  -H 'Content-Type: application/json' \
  -d '{"prd":{"name":"test-project","userStories":[{"id":"task-1","category":"functional","description":"Create hello.txt with hi","steps":["Ensure hello.txt exists"],"priority":1,"passes":false}]},"max_iterations":1,"timeout_per_iteration":120,"auto_commit":false}'
```
Response:
- job_id: `a36f0318-2823-40fc-ae37-a029532520dc`
- call_id: `fc-01KEH5JQACATAHE94X4K21A227`

Artifacts verified under `/data-cli/jobs/a36f0318-2823-40fc-ae37-a029532520dc/`:
- `.git/`
- `status.json`
- `progress.txt`
- `prd.json`
- `hello.txt`

Ralph status endpoint returned `status: complete`.

### Lint/Format
```
uv run ruff check --fix .
uv run ruff format .
```

## Notes / Observations
- `--dangerously-skip-permissions` requires non-root execution. Both the CLI runner and Ralph loop demote to the `claude` user, which avoids CLI permission failures.
- `write-result-path` normalization now aligns CLI outputs with expected `/data-cli/jobs/<job_id>/` layout.

## Update: Dev Mode Fixes (Session 2)

### Issues Fixed

1. **Dev mode sandbox lookup fails** (`app.py`)
   - Error: `App test-sandbox not found in environment main. Note that Apps must be deployed to look up sandboxes by name.`
   - Root cause: `Sandbox.from_name("test-sandbox", ...)` in `AlreadyExistsError` handlers fails in dev mode because only deployed apps can be looked up by string name.
   - Fix: Wrapped `from_name()` calls in all 4 `AlreadyExistsError` handlers with try/except for `NotFoundError`:
     - `get_or_start_background_sandbox()` (line 1708)
     - `get_or_start_background_sandbox_aio()` (line 1839)
     - `get_or_start_cli_sandbox()` (line 1923)
     - `get_or_start_cli_sandbox_aio()` (line 2019)

2. **CLI volume commit/reload fails with AuthError** (`cli_controller.py`)
   - Error: `modal.exception.AuthError: Token missing. Could not authenticate client.`
   - Root cause: Inside the sandbox, Modal auth isn't available, so `modal.Volume.reload()` and `commit()` fail.
   - Fix: Added `modal_exc.AuthError` handler to `_maybe_reload_cli_volume()` and `_commit_cli_volume()` to silently return when auth is missing (expected inside sandbox).

### Files Modified
- `modal_backend/main.py` — Added try/except for `NotFoundError` in 4 `AlreadyExistsError` handlers
- `modal_backend/api/cli_controller.py` — Added `modal_exc` import and `AuthError` handlers

### Verification

**Unit Tests:**
```bash
uv run pytest tests/test_ralph*.py -v
# Result: 136/136 tests pass
```

**Dev Mode Smoke Test:**
```bash
uv run modal serve -m modal_backend.main

# Start Ralph
curl -X POST "https://saidiibrahim--test-sandbox-http-app-dev.modal.run/ralph/start" \
  -H "Content-Type: application/json" \
  -d '{"prd":{"name":"test-project","userStories":[{"id":"task-1","category":"functional","description":"Create hello.txt with the text hi","steps":["Ensure hello.txt exists"],"priority":1,"passes":false}]},"max_iterations":2,"timeout_per_iteration":180,"auto_commit":false}'

# Response: {"job_id":"8d0e4206-0f84-40f4-9982-f03504271797","call_id":"fc-01KEHC0JQJTYT13FP90CGNVW9S","status":"started"}

# Poll result
curl "https://saidiibrahim--test-sandbox-http-app-dev.modal.run/ralph/8d0e4206-0f84-40f4-9982-f03504271797?call_id=fc-01KEHC0JQJTYT13FP90CGNVW9S"

# Response: {"status":"complete","tasks_completed":1,"tasks_total":1,...}
```

Ralph now works correctly in dev mode (`modal serve`).

## Remaining Considerations
- If desired, consider documenting the `jobs/<job_id>/` normalization behavior in the CLI API docs to avoid confusion for callers.
- Optional: add a dedicated test for `write-result-path` normalization in unit tests.
