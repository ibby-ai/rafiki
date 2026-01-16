# Handoff Document: Agent Sandbox Improvements

## Project Overview

This is a Modal-based agent sandbox starter that runs Claude Agent SDK in isolated sandboxed environments. The project uses a dual-sandbox architecture:
- **Agent SDK Sandbox** (`svc-runner-8001`): Long-lived service for conversational queries via Claude Agent SDK
- **CLI Sandbox** (`claude-cli-runner`): Code execution via Claude Code CLI

## Recent Commits (This Session)

The following commits were created for **Priority 2: Agent SDK Warm Pool**:

```
(pending commit) feat: add Agent SDK warm sandbox pool for reduced cold starts
```

**Previous session commits:**
```
11be2ff docs: add commit history to handoff for next agent
b2590ad docs: update handoff with CLI snapshot implementation
f453804 feat: add CLI sandbox snapshot restoration and persistence
f7f5713 feat: add CLI job snapshot storage functions
390ed2b feat: add CLI job snapshot configuration settings
```

The branch is ahead of `origin/main` by 10+ commits.

## Background Context

We analyzed a blog post from Ramp (https://builders.ramp.com/post/why-we-built-our-background-agent) about their "Inspect" background coding agent and identified 13 improvements to implement in this project. The full plan is documented at:

**Plan file**: `/Users/ibrahimsaidi/.claude/plans/steady-giggling-bengio.md`

## What Has Been Completed

### Priority 5: Statistics & Usage Tracking ✅ COMPLETE

**Files created/modified:**
1. `agent_sandbox/schemas/stats.py` - NEW FILE
   - `SandboxTypeStats` schema for per-sandbox-type statistics
   - `StatsResponse` schema for the `/stats` endpoint response
   - `StatsQueryParams` for query parameters

2. `agent_sandbox/config/settings.py` - MODIFIED
   - Added `stats_store_name` setting (default: "agent-stats-store")

3. `agent_sandbox/jobs.py` - MODIFIED (at end of file)
   - Added `STATS_STORE` Modal Dict for storing aggregate statistics
   - Added `_get_time_bucket_keys()` helper function
   - Added `record_session_start()` function to track session starts
   - Added `record_session_end()` function to track session completions with duration/status
   - Added `get_stats()` function to retrieve aggregated statistics for a time period

4. `agent_sandbox/app.py` - MODIFIED
   - Added import for `get_stats`
   - Added `GET /stats` endpoint after `/service_info`

5. `agent_sandbox/controllers/controller.py` - MODIFIED
   - Added imports for `record_session_start`, `record_session_end`
   - Added stats recording to `/query` endpoint (start tracking, duration, status)
   - Added stats recording to `/query_stream` endpoint (same pattern)

6. `agent_sandbox/schemas/sandbox.py` - MODIFIED
   - Added `user_id` field to `QueryBody` for user tracking in statistics

### Priority 1: Agent SDK Sandbox Snapshots ✅ COMPLETE

**Problem**: When sandbox exits (idle timeout), state is lost on follow-up.

**Solution**: Use Modal's `snapshot_filesystem()` to save/restore sandbox state per session.

**Files modified:**

1. `agent_sandbox/config/settings.py` - MODIFIED
   - Added `session_snapshot_store_name` setting (default: "agent-session-snapshots")
   - Added `enable_session_snapshots` setting (default: True)
   - Added `snapshot_min_interval_seconds` setting (default: 60) for throttling

2. `agent_sandbox/jobs.py` - MODIFIED (at end of file)
   - Added `SESSION_SNAPSHOTS` Modal Dict for storing per-session snapshot references
   - Added `store_session_snapshot()` function to save snapshot image reference
   - Added `get_session_snapshot()` function to retrieve snapshot for a session
   - Added `should_snapshot_session()` function for throttling (min interval between snapshots)
   - Added `delete_session_snapshot()` function for cleanup

3. `agent_sandbox/app.py` - MODIFIED
   - Added imports for `get_session_snapshot`, `should_snapshot_session`, `store_session_snapshot`
   - Added `snapshot_session_state()` Modal function to capture session filesystem state
   - Modified `get_or_start_background_sandbox()` to accept optional `session_id` parameter
     - When session_id is provided and a snapshot exists, creates sandbox from snapshot image
     - Tracks `restored_from_snapshot` in session metadata
   - Modified `get_or_start_background_sandbox_aio()` with same changes
   - Modified `query_proxy()` endpoint to:
     - Pass session_id to sandbox getter for snapshot restoration
     - Spawn `snapshot_session_state` after successful queries (fire-and-forget)
   - Modified `query_stream()` endpoint to:
     - Pass session_id to sandbox getter for snapshot restoration
     - Parse SSE stream to capture session_id from "done" event
     - Spawn `snapshot_session_state` after stream completes

**How it works:**
1. After each agent query completes successfully, the HTTP proxy spawns a background task to snapshot the sandbox filesystem
2. The snapshot is stored with the session_id in `SESSION_SNAPSHOTS` Modal Dict
3. Snapshots are throttled (default: 1 per minute per session) to avoid excessive I/O
4. When a user resumes a session (by passing session_id) and the sandbox needs to be created (e.g., after idle timeout):
   - The system checks for an existing snapshot for that session
   - If found, creates the new sandbox from the snapshot image, preserving filesystem state
   - The Claude Agent SDK conversation is resumed via its `resume=` parameter (existing feature)

**Key Modal API used:**
```python
# After agent work completes
image = sandbox.snapshot_filesystem()
store_session_snapshot(session_id, image.object_id, sandbox_name)

# On session resume (when creating new sandbox)
snapshot = get_session_snapshot(session_id)
if snapshot:
    sandbox_image = modal.Image.from_id(snapshot["image_id"])
    sandbox = modal.Sandbox.create(image=sandbox_image, ...)
```

### Priority 2: Agent SDK Warm Pool ✅ COMPLETE

**Problem**: Cold starts add latency to first request when no sandbox exists.

**Solution**: Maintain a pool of pre-warmed sandboxes ready for immediate use.

**Files modified:**

1. `agent_sandbox/config/settings.py` - MODIFIED
   - Added `warm_pool_store_name` setting (default: "agent-warm-pool")
   - Added `enable_warm_pool` setting (default: True)
   - Added `warm_pool_size` setting (default: 2)
   - Added `warm_pool_refresh_interval` setting (default: 300 seconds)
   - Added `warm_pool_sandbox_max_age` setting (default: 3600 seconds)
   - Added `warm_pool_claim_timeout` setting (default: 5 seconds)

2. `agent_sandbox/jobs.py` - MODIFIED (at end of file)
   - Added `WARM_POOL` Modal Dict for storing pool metadata
   - Added `generate_pool_sandbox_name()` function to create unique pool sandbox names
   - Added `register_warm_sandbox()` function to add sandbox to pool
   - Added `claim_warm_sandbox()` function to atomically claim a sandbox
   - Added `release_warm_sandbox()` function to return sandbox to pool
   - Added `remove_from_pool()` function to delete pool entries
   - Added `get_warm_pool_entries()` function to list all entries
   - Added `get_warm_pool_status()` function for monitoring
   - Added `get_expired_pool_entries()` function to find old sandboxes
   - Added `cleanup_stale_pool_entries()` function to remove dead entries

3. `agent_sandbox/app.py` - MODIFIED
   - Added imports for warm pool functions
   - Added `_create_warm_sandbox_sync()` helper to create pool sandboxes
   - Added `replenish_warm_pool` Modal function to add sandboxes to pool
   - Added `maintain_warm_pool` scheduled Modal function for pool maintenance
   - Added `GET /pool/status` HTTP endpoint for monitoring
   - Modified `get_or_start_background_sandbox()` to try claiming from pool
   - Modified `get_or_start_background_sandbox_aio()` with same changes

**How it works:**
1. Pool sandboxes are pre-created with uvicorn running the agent controller
2. Each pool sandbox is registered in `WARM_POOL` Modal Dict with status="warm"
3. When a request needs a new sandbox (no existing one):
   - First tries to claim from the warm pool
   - If claimed, uses that sandbox's tunnel URL directly
   - If pool empty, falls back to creating a new sandbox
4. After claiming, triggers async replenishment via `replenish_warm_pool.spawn()`
5. Scheduled `maintain_warm_pool` runs every N minutes to:
   - Clean up stale entries for terminated sandboxes
   - Expire old sandboxes (max age) to pick up image changes
   - Replenish pool to target size

**Pool Entry Structure:**
```python
WARM_POOL[sandbox_id] = {
    "sandbox_id": "sb-xxx",           # Modal sandbox object_id
    "sandbox_name": "pool-abc123",    # Unique name for this sandbox
    "status": "warm" | "claimed",     # Current status
    "created_at": 1704067200,         # Unix timestamp when added to pool
    "claimed_at": None | 1704067300,  # Unix timestamp when claimed
    "claimed_by": None | "session_id", # Session that claimed this sandbox
}
```

**Key Modal API used:**
```python
# Create pool sandbox
sb = modal.Sandbox.create(name=pool_name, ...)
sb.set_tags({"pool": "agent_sdk", "status": "warm"})
register_warm_sandbox(sb.object_id, pool_name)

# Claim from pool
claim = claim_warm_sandbox(session_id=session_id)
if claim:
    sb = modal.Sandbox.from_id(claim["sandbox_id"])
    # Use sb.tunnels() to get service URL

# List pool sandboxes
for sb in modal.Sandbox.list(tags={"pool": "agent_sdk"}):
    if sb.poll() is None:  # Still running
        ...
```

**HTTP Endpoints:**
- `GET /pool/status` - Returns pool statistics (warm/claimed counts, entries)

### Priority 10: CLI Sandbox Snapshots ✅ COMPLETE

**Problem**: CLI jobs lose state when sandbox exits; can't resume long-running coding tasks.

**Solution**: Use Modal's `snapshot_filesystem()` to save/restore CLI sandbox state per job_id.

**Files modified:**

1. `agent_sandbox/config/settings.py` - MODIFIED
   - Added `cli_job_snapshot_store_name` setting (default: "cli-job-snapshots")
   - Added `enable_cli_job_snapshots` setting (default: True)
   - Added `cli_snapshot_min_interval_seconds` setting (default: 60) for throttling

2. `agent_sandbox/jobs.py` - MODIFIED (at end of file)
   - Added `CLI_JOB_SNAPSHOTS` Modal Dict for storing per-job snapshot references
   - Added `store_cli_job_snapshot()` function to save snapshot image reference
   - Added `get_cli_job_snapshot()` function to retrieve snapshot for a job
   - Added `should_snapshot_cli_job()` function for throttling (min interval between snapshots)
   - Added `delete_cli_job_snapshot()` function for cleanup

3. `agent_sandbox/app.py` - MODIFIED
   - Added imports for `get_cli_job_snapshot`, `should_snapshot_cli_job`, `store_cli_job_snapshot`
   - Added `snapshot_cli_job_state()` Modal function to capture CLI sandbox filesystem state
   - Modified `get_or_start_cli_sandbox()` to accept optional `job_id` parameter
     - When job_id is provided and a snapshot exists, creates sandbox from snapshot image
     - Tracks `restored_from_snapshot` in session metadata
   - Modified `get_or_start_cli_sandbox_aio()` with same changes
   - Modified `run_claude_cli_remote()` to:
     - Pass job_id to sandbox getter for snapshot restoration
     - Spawn `snapshot_cli_job_state` after successful execution (fire-and-forget)
   - Modified `run_ralph_remote()` to:
     - Pass job_id to sandbox getter for snapshot restoration
     - Spawn `snapshot_cli_job_state` after execution completes

**How it works:**
1. After each CLI job completes successfully, the function spawns a background task to snapshot the CLI sandbox filesystem
2. The snapshot is stored with the job_id in `CLI_JOB_SNAPSHOTS` Modal Dict
3. Snapshots are throttled (default: 1 per minute per job) to avoid excessive I/O
4. When a job resumes (by passing job_id) and the CLI sandbox needs to be created (e.g., after idle timeout):
   - The system checks for an existing snapshot for that job
   - If found, creates the new CLI sandbox from the snapshot image, preserving filesystem state

**Key Modal API used:**
```python
# After CLI job completes
image = sandbox.snapshot_filesystem()
store_cli_job_snapshot(job_id, image.object_id, sandbox_name)

# On job resume (when creating new CLI sandbox)
snapshot = get_cli_job_snapshot(job_id)
if snapshot:
    sandbox_image = modal.Image.from_id(snapshot["image_id"])
    sandbox = modal.Sandbox.create(image=sandbox_image, ...)
```

## Outstanding Tasks (In Order)

### Phase B: Performance

#### Priority 2: Agent SDK Warm Pool
- Add settings: `warm_pool_size`, `warm_pool_refresh_interval`
- Create background task to maintain pool of warm sandboxes
- Use `Sandbox.list()` with tags to track pool membership
- Grab from pool on request, replenish asynchronously

#### Priority 11: CLI Warm Pool
Same pattern for CLI sandbox.

#### Priority 3: Pre-warm on User Typing
- Add `POST /warm` endpoint
- Client calls when user starts typing
- Use request correlation to match warm request to query

### Phase C: Control & Collaboration

#### Priority 8: Stop/Cancel Mid-Execution
- Add `POST /session/{id}/stop` endpoint
- Set cancellation flag in session state
- Check flag in `can_use_tool` handler

#### Priority 7: Follow-up Prompt Queue
- Add per-session prompt queue in `SESSION_STORE`
- Queue prompts during execution, process after

#### Priority 6: Multiplayer Session Support
- Add `user_id` field for attribution (DONE in QueryBody)
- Store message history with author attribution
- Session becomes shared resource

### Phase D: CLI Enhancements

#### Priority 12: Ralph Loop Improvements
- Progress streaming via SSE
- Pause/Resume endpoints
- Iteration snapshots for rollback

#### Priority 13: CLI Job Workspace Improvements
- Artifact manifest tracking
- Workspace cleanup with retention policy
- `GET /jobs/{job_id}/artifacts/{path}` endpoint

#### Priority 9: VS Code Integration
- Add code-server to CLI image
- Expose on separate tunnel port
- Add endpoint for VS Code URL

### Phase E: Advanced

#### Priority 4: Sub-Session Spawning Tool
- Create MCP tool for spawning child sessions
- Create tool for checking session status

## Key Files Reference

| File | Purpose |
|------|---------|
| `agent_sandbox/app.py` | Modal app, sandbox lifecycle, HTTP gateway, snapshots |
| `agent_sandbox/controllers/controller.py` | Agent SDK microservice, session management |
| `agent_sandbox/controllers/cli_controller.py` | CLI microservice, code execution |
| `agent_sandbox/config/settings.py` | Configuration management |
| `agent_sandbox/jobs.py` | Job queue, async processing, stats, snapshots |
| `agent_sandbox/tools/registry.py` | MCP tool registration |
| `agent_sandbox/ralph/loop.py` | Ralph autonomous coding loop |

## Modal Features in Use

- **Sandboxes**: Long-lived background services with `modal.Sandbox.create()`
- **Encrypted Tunnels**: Service discovery via `sandbox.tunnels()`
- **Volumes**: Persistent storage at `/data` and `/data-cli`
- **Queue**: Job distribution via `modal.Queue`
- **Dict**: Distributed state via `modal.Dict`
- **Secrets**: API key management
- **Snapshots**: `sandbox.snapshot_filesystem()` for state persistence
- **Image.from_id()**: Restore sandbox from snapshot image
- **Sandbox.list()**: Enumerate sandboxes by tags for pool management
- **Sandbox.from_id()**: Retrieve sandbox by object_id for pool claims
- **sandbox.set_tags()**: Tag sandboxes for tracking (pool, status)
- **Cron Schedule**: `modal.Cron()` for periodic pool maintenance

## Commands

```bash
# Run linter
uv run ruff check --fix .
uv run ruff format .

# Test imports
uv run python -c "from agent_sandbox.jobs import get_stats, get_session_snapshot; print('OK')"

# Start dev server
modal serve -m agent_sandbox.app

# Deploy
modal deploy -m agent_sandbox.deploy
```

## Current Todo List State

1. ✅ Statistics & Usage Tracking (Priority 5) - COMPLETE
2. ✅ Agent SDK Sandbox Snapshots (Priority 1) - COMPLETE
3. ✅ CLI Sandbox Snapshots (Priority 10) - COMPLETE
4. ✅ Agent SDK Warm Pool (Priority 2) - COMPLETE
5. 🔄 CLI Warm Pool (Priority 11) - NEXT
6. ⏳ Pre-warm API (Priority 3)
7. ⏳ Stop/Cancel Mid-Execution (Priority 8)
8. ⏳ Follow-up Prompt Queue (Priority 7)
9. ⏳ Multiplayer Session Support (Priority 6)
10. ⏳ Ralph Loop Improvements (Priority 12)
11. ⏳ CLI Job Workspace Improvements (Priority 13)
12. ⏳ VS Code Integration (Priority 9)
13. ⏳ Sub-Session Spawning Tool (Priority 4)

## Next Steps

1. Continue with **Priority 11: CLI Warm Pool**
   - Same pattern as Agent SDK warm pool for CLI sandboxes
   - Add `cli_warm_pool_size` setting
   - Pre-warm CLI sandboxes with development environment ready
   - Tag pool sandboxes with `{"pool": "cli", "status": "warm"}`
   - Claim from pool on `/execute` request

2. After completing Priority 11, move to Priority 3: Pre-warm API

3. Follow the phased implementation order in the plan file
