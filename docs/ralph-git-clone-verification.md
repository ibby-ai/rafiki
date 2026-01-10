# Test Plan: Ralph Loop git_clone Workspace Source

## Executive Summary

This plan outlines how to verify that the Ralph autonomous coding loop correctly handles the `git_clone` workspace source type. The goal is to test cloning the `https://github.com/snarktank/ralph` repository and running a simple Ralph loop against it.

## Implementation Analysis

### Code Flow for git_clone

The workspace source flows through the system as follows:

1. **HTTP Entry** (`app.py:start_ralph`): Receives `RalphStartRequest` with `workspace_source`
2. **Modal Function** (`app.py:run_ralph_remote`): Serializes workspace_source to JSON
3. **CLI Sandbox** (`cli_controller.py:execute_ralph`): Creates workspace at `/data-cli/jobs/{job_id}`
4. **Loop Init** (`loop.py:run_ralph_loop`): Calls `initialize_workspace(workspace, workspace_source)`
5. **Git Clone** (`workspace.py:initialize_workspace`): Runs `git clone [--branch] URL .`
6. **Git Skip** (`git.py:init_git`): Skips `git init` if `.git` exists (preserves clone history)

### Key Implementation Details

- `workspace.py:66-73`: Correctly runs git clone with optional branch
- `git.py:39-42`: Correctly skips init if `.git` exists
- Both use `demote_to_claude()` for subprocess execution in Modal

### No Blocking Issues Found

The implementation appears correct. No obvious errors that would cause git_clone to fail.

---

## Test Plan

### Phase 1: Unit Tests (Local)

Run existing workspace tests to verify the git_clone logic works:

```bash
# Activate venv first
source .venv/bin/activate

# Run all Ralph tests
uv run pytest tests/test_ralph*.py -v

# Run workspace-specific tests
uv run pytest tests/test_ralph_workspace.py -v

# Run git-specific tests
uv run pytest tests/test_ralph_git.py -v
```

**Expected Outcome:** All tests pass, including:
- `TestInitializeWorkspaceGitClone::test_git_clone_creates_repo`
- `TestInitializeWorkspaceGitClone::test_git_clone_with_branch`

---

### Phase 2: Integration Test (Modal Serve)

#### Step 2.1: Start Modal Development Server

```bash
modal serve -m agent_sandbox.app
```

Wait for output showing the HTTP endpoint URL (e.g., `https://<org>--test-sandbox-http-app-dev.modal.run`)

#### Step 2.2: Test Git Clone with Ralph

Create a test PRD file (`test_prd.json`):

```json
{
  "prd": {
    "name": "Ralph git_clone test",
    "userStories": [
      {
        "id": "test-1",
        "category": "technical",
        "description": "List the files in the repository and write them to a file called repo_contents.txt",
        "steps": [
          "Run: ls -la",
          "Verify repo_contents.txt exists"
        ],
        "passes": false,
        "priority": 1
      }
    ]
  },
  "workspace_source": {
    "type": "git_clone",
    "git_url": "https://github.com/snarktank/ralph.git",
    "git_branch": "main"
  },
  "max_iterations": 3,
  "timeout_per_iteration": 300,
  "first_iteration_timeout": 600,
  "allowed_tools": ["Read", "Write", "Bash", "Glob", "Grep"],
  "feedback_commands": [],
  "auto_commit": true,
  "max_consecutive_failures": 2
}
```

#### Step 2.3: Start Ralph Loop via curl

```bash
# Replace <URL> with the actual Modal serve URL
curl -X POST 'https://<URL>/ralph/start' \
  -H 'Content-Type: application/json' \
  -d @test_prd.json
```

**Expected Response:**
```json
{
  "job_id": "<uuid>",
  "call_id": "<call_id>",
  "status": "started"
}
```

#### Step 2.4: Poll for Status

```bash
# Replace values from the start response
job_id="<job_id>"
call_id="<call_id>"
base_url="https://<URL>"

# Poll loop
while true; do
  resp=$(curl -s "${base_url}/ralph/${job_id}?call_id=${call_id}")
  echo "$resp" | python -m json.tool

  status=$(echo "$resp" | python -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))")
  if [ "$status" = "complete" ] || [ "$status" = "failed" ] || [ "$status" = "max_iterations" ]; then
    break
  fi
  echo "Status: $status - waiting..."
  sleep 10
done
```

#### Step 2.5: Verify Results

Check the final response for:
1. `status: "complete"` (or `"max_iterations"` if task wasn't completed)
2. `tasks_completed >= 1`
3. `iteration_results` contains entries with the cloned repo files

---

### Phase 3: Manual Verification via modal run

Alternative approach using `modal run` directly:

```bash
# Run the Ralph loop directly
modal run -m agent_sandbox.app::run_ralph_remote \
  --job-id "test-git-clone-001" \
  --prd-json '{"name":"Git Clone Test","userStories":[{"id":"task-1","category":"technical","description":"List all files in the cloned repo","steps":["Run ls -la"],"passes":false,"priority":1}]}' \
  --workspace-source-json '{"type":"git_clone","git_url":"https://github.com/snarktank/ralph.git","git_branch":"main"}' \
  --max-iterations 2 \
  --timeout-per-iteration 300
```

---

## Success Criteria

1. **Unit tests pass**: All `test_ralph_workspace.py` and `test_ralph_git.py` tests pass
2. **Git clone executes**: The snarktank/ralph repo is successfully cloned
3. **Git history preserved**: `.git` directory exists with full clone history
4. **PRD written alongside clone**: `prd.json` exists in workspace with clone contents
5. **Ralph iteration runs**: At least one iteration completes successfully
6. **Status polling works**: `/ralph/{job_id}` endpoint returns correct status

---

## Potential Issues to Watch For

1. **Network timeout**: GitHub clone may timeout if connection is slow (increase `first_iteration_timeout`)
2. **Auth errors**: Public repos should work without auth; private repos would need credentials
3. **Cold start latency**: First iteration may timeout due to sandbox startup (use `first_iteration_timeout`)
4. **File permissions**: `demote_to_claude()` may fail if claude user doesn't exist in sandbox

---

## Files Modified/Referenced

| File | Purpose |
|------|---------|
| `agent_sandbox/ralph/workspace.py` | Git clone implementation |
| `agent_sandbox/ralph/git.py` | Git init skip logic |
| `agent_sandbox/ralph/loop.py` | Main orchestrator |
| `agent_sandbox/ralph/schemas.py` | WorkspaceSource model |
| `agent_sandbox/app.py` | HTTP endpoints and Modal functions |
| `agent_sandbox/controllers/cli_controller.py` | CLI sandbox execute endpoint |
| `tests/test_ralph_workspace.py` | Workspace unit tests |
| `tests/test_ralph_git.py` | Git operation tests |

---

## Execution Results

### Bug Found and Fixed

**Issue Discovered:** Git "dubious ownership" error when running `git add -A` in the Ralph loop.

**Root Cause:** The `workspace.py:initialize_workspace()` function ran `git clone` without using `demote_to_claude()`, while subsequent git operations in `git.py` used `demote_to_claude()`. This caused ownership mismatch - the repo was cloned as root, but git operations ran as the `claude` user, triggering Git's security checks.

**Fix Applied to `workspace.py`:**
```python
# Added _git_subprocess_kwargs() function
def _git_subprocess_kwargs() -> dict[str, object]:
    """Return subprocess kwargs to run git as the claude user when possible."""
    import os
    if os.getuid() != 0:
        return {}
    try:
        return {
            "env": claude_cli_env(),
            "preexec_fn": demote_to_claude(),
        }
    except RuntimeError:
        return {}

# Updated git clone call to use the function
subprocess.run(
    ["git", "clone", *branch_args, source.git_url, "."],
    cwd=workspace,
    check=True,
    capture_output=True,
    **_git_subprocess_kwargs(),  # NEW
)
```

### Test Results

**Phase 1: Unit Tests**
- All 136 Ralph tests passed
- Key git_clone tests passed:
  - `test_git_clone_creates_repo` ✓
  - `test_git_clone_with_branch` ✓
  - `test_init_git_skips_existing_repo` ✓

**Phase 2: Integration Test (before fix)**
- Status: **FAILED**
- Error: `Command '['git', 'add', '-A']' returned non-zero exit status 128`
- Cause: Dubious ownership (repo cloned as root, operations run as claude)

**Phase 2: Integration Test (after fix)**
- Status: **SUCCESS**
- Result:
  ```json
  {
    "status": "complete",
    "iterations_completed": 1,
    "tasks_completed": 1,
    "tasks_total": 1,
    "final_prd": {
      "name": "Ralph git_clone test",
      "userStories": [{
        "id": "test-1",
        "passes": true
      }]
    }
  }
  ```

### Success Criteria Verification

| Criteria | Status |
|----------|--------|
| Unit tests pass | ✅ 136/136 passed |
| Git clone executes | ✅ snarktank/ralph cloned successfully |
| Git history preserved | ✅ .git exists (init_git skips) |
| PRD written alongside clone | ✅ prd.json written to workspace |
| Ralph iteration runs | ✅ 1 iteration completed task |
| Status polling works | ✅ `/ralph/{job_id}` returned correct status |

### Modal Volume Verification

To verify the git clone actually persisted to the Modal volume, we inspected the CLI sandbox volume:

**Step 1: List jobs in the volume**
```bash
uv run modal volume ls claude-cli-runner-vol /jobs
```

**Step 2: Inspect job workspace contents**
```bash
uv run modal volume ls claude-cli-runner-vol /jobs/d158286a-51a2-4a8f-9477-99a3f1065021
```

**Result - Cloned repo files present:**
```
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/skills
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/flowchart
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/.github
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/.git          # Git history preserved!
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/ralph.sh
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/prompt.md
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/README.md
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/AGENTS.md
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/prd.json.example
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/ralph.webp
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/ralph-flowchart.png
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/.gitignore
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/prd.json           # Ralph artifact
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/progress.txt       # Ralph artifact
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/status.json        # Ralph artifact
jobs/d158286a-51a2-4a8f-9477-99a3f1065021/repo_contents.txt  # Created by Claude!
```

**Step 3: Verify file contents**
```bash
# Get the file Claude created
uv run modal volume get claude-cli-runner-vol \
  /jobs/d158286a-51a2-4a8f-9477-99a3f1065021/repo_contents.txt /tmp/repo_contents.txt
cat /tmp/repo_contents.txt
```

**Result - `repo_contents.txt` (created by Claude):**
```
total 4886
drwxrwxr-x 1 claude claude     147 Jan 10 12:29 .
drwxr-xr-x 1 root   root       432 Jan 10 12:29 ..
drwxr-xr-x 1 claude claude     130 Jan 10 12:29 .git
drwxr-xr-x 1 claude claude       9 Jan 10 12:29 .github
-rw-r--r-- 1 claude claude     147 Jan 10 12:29 .gitignore
-rw-r--r-- 1 claude claude    1200 Jan 10 12:29 AGENTS.md
-rw-r--r-- 1 claude claude    5755 Jan 10 12:29 README.md
drwxr-xr-x 1 claude claude     145 Jan 10 12:29 flowchart
-rw-r--r-- 1 root   root       373 Jan 10 12:29 prd.json
-rw-r--r-- 1 claude claude    2138 Jan 10 12:29 prd.json.example
-rw-r--r-- 1 root   root        43 Jan 10 12:29 progress.txt
-rw-r--r-- 1 claude claude    4199 Jan 10 12:29 prompt.md
-rw-r--r-- 1 claude claude 4704146 Jan 10 12:29 ralph-flowchart.png
-rwxr-xr-x 1 claude claude    2872 Jan 10 12:29 ralph.sh
-rw-r--r-- 1 claude claude  275808 Jan 10 12:29 ralph.webp
drwxr-xr-x 1 claude claude       8 Jan 10 12:29 skills
-rw-r--r-- 1 root   root       146 Jan 10 12:29 status.json
```

**Result - `prd.json` (task marked complete):**
```json
{
  "name": "Ralph git_clone test",
  "userStories": [
    {
      "id": "test-1",
      "passes": true,
      ...
    }
  ]
}
```

**Result - `progress.txt` (Claude's work log):**
```
# Ralph Progress Log: Ralph git_clone test

## Task test-1: List files and create repo_contents.txt
- Ran `ls -la` to list all files in the cloned repository
- Created repo_contents.txt with the directory listing
- Verified repo_contents.txt exists successfully
- Status: COMPLETE
```

### Volume Verification Summary

| Check | Result |
|-------|--------|
| Job workspace exists | ✅ `/jobs/d158286a-51a2-4a8f-9477-99a3f1065021/` |
| Git clone executed | ✅ All repo files present |
| `.git` directory preserved | ✅ Full git history available |
| File ownership correct | ✅ Cloned files owned by `claude:claude` |
| Task completed | ✅ `passes: true` in prd.json |
| Work artifact created | ✅ `repo_contents.txt` exists with `ls -la` output |
| Progress tracked | ✅ `progress.txt` shows work log |

---

### Conclusion

The Ralph loop now correctly supports the `git_clone` workspace source type after fixing the ownership consistency issue in `workspace.py`. The implementation clones repos as the `claude` user, ensuring all subsequent git operations work without permission errors.

**Verification confirms:**
1. The `snarktank/ralph` GitHub repo was successfully cloned
2. Git history (`.git/`) was preserved
3. Claude completed the assigned task within the cloned workspace
4. All artifacts were persisted to the Modal volume
