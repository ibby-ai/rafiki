# Handoff Document: Agent Sandbox Improvements

## Project Overview

This is a Modal-based agent sandbox starter that runs Claude Agent SDK in isolated sandboxed environments. The project uses a dual-sandbox architecture:
- **Agent SDK Sandbox** (`svc-runner-8001`): Long-lived service for conversational queries via Claude Agent SDK
- **CLI Sandbox** (`claude-cli-runner`): Code execution via Claude Code CLI

## Recent Commits (This Session)

The following commits were created for **Priority 6: Multiplayer Session Support**:

```
76f964e feat: add multiplayer session support with user attribution
```

**Previous session commits:**
```
d3e87a3 feat: add follow-up prompt queue for sessions
6a3d70c feat: add session stop/cancel for graceful mid-execution termination
```

**Earlier commits:**
```
57a3514 feat: add pre-warm API for speculative sandbox warming
1340824 feat: add CLI warm pool for reduced cold-start latency
22eb84b feat: add Agent SDK warm pool for reduced cold-start latency
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

### Priority 11: CLI Warm Pool ✅ COMPLETE

**Problem**: CLI sandbox cold starts add latency to code execution tasks.

**Solution**: Maintain a separate warm pool for CLI sandboxes.

**Files modified:**

1. `agent_sandbox/config/settings.py` - MODIFIED
   - Added `cli_warm_pool_store_name` setting (default: "cli-warm-pool")
   - Added `enable_cli_warm_pool` setting (default: True)
   - Added `cli_warm_pool_size` setting (default: 2)
   - Added `cli_warm_pool_refresh_interval` setting (default: 300 seconds)
   - Added `cli_warm_pool_sandbox_max_age` setting (default: 3600 seconds)
   - Added `cli_warm_pool_claim_timeout` setting (default: 5 seconds)

2. `agent_sandbox/jobs.py` - MODIFIED (at end of file)
   - Added `CLI_WARM_POOL` Modal Dict for storing CLI pool metadata
   - Added `generate_cli_pool_sandbox_name()` function to create unique pool sandbox names
   - Added `register_cli_warm_sandbox()` function to add CLI sandbox to pool
   - Added `claim_cli_warm_sandbox()` function to atomically claim a CLI sandbox
   - Added `release_cli_warm_sandbox()` function to return sandbox to pool
   - Added `remove_from_cli_pool()` function to delete pool entries
   - Added `get_cli_warm_pool_entries()` function to list all entries
   - Added `get_cli_warm_pool_status()` function for monitoring
   - Added `get_expired_cli_pool_entries()` function to find old sandboxes
   - Added `cleanup_stale_cli_pool_entries()` function to remove dead entries

3. `agent_sandbox/app.py` - MODIFIED
   - Added imports for CLI warm pool functions
   - Added `_create_cli_warm_sandbox_sync()` helper to create CLI pool sandboxes
   - Added `replenish_cli_warm_pool` Modal function to add CLI sandboxes to pool
   - Added `maintain_cli_warm_pool` scheduled Modal function for CLI pool maintenance
   - Added `GET /cli/pool/status` HTTP endpoint for monitoring
   - Modified `get_or_start_cli_sandbox()` to try claiming from CLI pool
   - Modified `get_or_start_cli_sandbox_aio()` with same changes

**How it works:**

1. CLI pool sandboxes are pre-created with uvicorn running the CLI controller
2. Each pool sandbox is registered in `CLI_WARM_POOL` Modal Dict with status="warm"
3. When a request needs a new CLI sandbox (no existing one):
   - First tries to claim from the CLI warm pool
   - If claimed, uses that sandbox's tunnel URL directly
   - If pool empty, falls back to creating a new sandbox
4. After claiming, triggers async replenishment via `replenish_cli_warm_pool.spawn()`
5. Scheduled `maintain_cli_warm_pool` runs every N minutes to:
   - Clean up stale entries for terminated sandboxes
   - Expire old sandboxes (max age) to pick up image changes
   - Replenish pool to target size

**Pool Entry Structure:**

```python
CLI_WARM_POOL[sandbox_id] = {
    "sandbox_id": "sb-xxx",           # Modal sandbox object_id
    "sandbox_name": "cli-pool-abc123", # Unique name for this sandbox
    "status": "warm" | "claimed",     # Current status
    "created_at": 1704067200,         # Unix timestamp when added to pool
    "claimed_at": None | 1704067300,  # Unix timestamp when claimed
    "claimed_by": None | "job_id",    # Job that claimed this sandbox
}
```

**Key Modal API used:**

```python
# Create CLI pool sandbox
sb = modal.Sandbox.create(name=pool_name, ...)
sb.set_tags({"pool": "cli", "status": "warm"})
register_cli_warm_sandbox(sb.object_id, pool_name)

# Claim from CLI pool
claim = claim_cli_warm_sandbox(job_id=job_id)
if claim:
    sb = modal.Sandbox.from_id(claim["sandbox_id"])
    # Use sb.tunnels() to get service URL

# List CLI pool sandboxes
for sb in modal.Sandbox.list(tags={"pool": "cli"}):
    if sb.poll() is None:  # Still running
        ...
```

**HTTP Endpoints:**

- `GET /cli/pool/status` - Returns CLI pool statistics (warm/claimed counts, entries)

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

### Priority 8: Stop/Cancel Mid-Execution ✅ COMPLETE

**Problem**: No way to stop agent mid-execution if it's going off-track.

**Solution**: Add graceful stop mechanism for running sessions that checks a cancellation flag before each tool call.

**Files modified:**

1. `agent_sandbox/config/settings.py` - MODIFIED
   - Added `session_cancellation_store_name` setting (default: "agent-session-cancellations")
   - Added `enable_session_cancellation` setting (default: True)
   - Added `cancellation_expiry_seconds` setting (default: 3600) for cancellation flag lifetime

2. `agent_sandbox/jobs.py` - MODIFIED (at end of file)
   - Added `SESSION_CANCELLATIONS` Modal Dict for storing cancellation flags
   - Added `cancel_session()` function to request session cancellation
   - Added `is_session_cancelled()` function to check if session is cancelled
   - Added `get_session_cancellation()` function to get cancellation details
   - Added `acknowledge_session_cancellation()` function to mark cancellation as acknowledged
   - Added `clear_session_cancellation()` function to clear cancellation flag
   - Added `cleanup_expired_cancellations()` function for maintenance
   - Added `get_cancellation_status()` function for monitoring

3. `agent_sandbox/controllers/controller.py` - MODIFIED
   - Added imports for `is_session_cancelled`, `acknowledge_session_cancellation`
   - Added `_make_can_use_tool_handler()` factory function that creates a closure-based handler
   - Modified `_options()` to use the factory instead of static `allow_web_only`
   - The handler checks for cancellation before each tool call
   - When cancelled, returns `PermissionResultDeny` with a message asking agent to stop

4. `agent_sandbox/schemas/sandbox.py` - MODIFIED
   - Added `SessionStopRequest` schema for stop request body
   - Added `SessionStopResponse` schema for stop response
   - Added `SessionCancellationStatusResponse` schema for status endpoint

5. `agent_sandbox/schemas/__init__.py` - MODIFIED
   - Added exports for new session cancellation schemas

6. `agent_sandbox/app.py` - MODIFIED
   - Added imports for `cancel_session`, `get_session_cancellation`, `get_cancellation_status`
   - Added imports for `SessionStopRequest`, `SessionStopResponse`, `SessionCancellationStatusResponse`
   - Added `POST /session/{session_id}/stop` endpoint to request session stop
   - Added `GET /session/{session_id}/stop` endpoint to check stop status
   - Added `GET /session/cancellations/status` endpoint for overall statistics

**How it works:**
1. Client calls `POST /session/{session_id}/stop` when user wants to stop an agent
2. Server sets a cancellation flag in `SESSION_CANCELLATIONS` Modal Dict
3. The agent's `can_use_tool` handler (created via `_make_can_use_tool_handler`) checks for cancellation before each tool call
4. If cancelled, the handler returns `PermissionResultDeny` with a message
5. The Agent SDK receives the denial and should terminate gracefully
6. Cancellation flags expire after `cancellation_expiry_seconds` to prevent stale flags

**Cancellation Entry Structure:**

```python
SESSION_CANCELLATIONS[session_id] = {
    "session_id": "sess_abc123",       # Session being cancelled
    "status": "requested",              # "requested" | "acknowledged"
    "requested_at": 1704067200,         # Unix timestamp
    "expires_at": 1704070800,           # Unix timestamp (created + expiry)
    "requested_by": "user_123",         # Optional requester identifier
    "reason": "User requested stop",    # Optional reason
}
```

**HTTP Endpoints:**

- `POST /session/{session_id}/stop` - Request session stop
- `GET /session/{session_id}/stop` - Check stop status for a session
- `GET /session/cancellations/status` - Get overall cancellation statistics

**Example Usage:**

```bash
# Request session stop
curl -X POST 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/stop' \
  -H 'Content-Type: application/json' \
  -d '{"reason": "Taking too long", "requested_by": "user_123"}'

# Response: {"ok": true, "session_id": "sess_abc123", "status": "requested", ...}

# Check stop status
curl 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/stop'

# Response: {"ok": true, "session_id": "sess_abc123", "status": "acknowledged", ...}

# Get overall statistics
curl 'https://<org>--test-sandbox-http-app.modal.run/session/cancellations/status'

# Response: {"enabled": true, "total": 5, "requested": 2, "acknowledged": 3, ...}
```

**Note on CLI Sandbox:**
CLI sandbox cancellation is handled differently since it runs Claude Code CLI as a subprocess. The existing `DELETE /claude_cli/{call_id}` endpoint cancels async CLI function calls. For synchronous CLI execution, process-level termination would require a different approach (e.g., subprocess kill signals).

### Priority 7: Follow-up Prompt Queue ✅ COMPLETE

**Problem**: Can't send follow-up prompts while agent is still executing.

**Solution**: Queue follow-up prompts to run after current execution.

**Files modified:**

1. `agent_sandbox/config/settings.py` - MODIFIED
   - Added `prompt_queue_store_name` setting (default: "agent-prompt-queue")
   - Added `enable_prompt_queue` setting (default: True)
   - Added `max_queued_prompts_per_session` setting (default: 10)
   - Added `prompt_queue_entry_expiry_seconds` setting (default: 3600)

2. `agent_sandbox/jobs.py` - MODIFIED (at end of file)
   - Added `PROMPT_QUEUE` Modal Dict for storing per-session prompt queues
   - Added `SESSION_EXECUTION_STATE` Modal Dict for tracking session execution status
   - Added `mark_session_executing()` function to mark session as running
   - Added `mark_session_idle()` function to mark session as idle
   - Added `is_session_executing()` function to check session status
   - Added `queue_prompt()` function to add prompt to queue
   - Added `dequeue_prompt()` function to get and remove next prompt
   - Added `peek_next_prompt()` function to view next prompt without removing
   - Added `get_session_queue()` function to list all queued prompts
   - Added `get_queue_size()` function to get queue length
   - Added `clear_session_queue()` function to clear all prompts
   - Added `remove_queued_prompt()` function to remove specific prompt
   - Added `cleanup_expired_queue_entries()` function for maintenance
   - Added `get_prompt_queue_status()` function for overall statistics

3. `agent_sandbox/schemas/sandbox.py` - MODIFIED
   - Added `QueuedPromptEntry` schema for queued prompt entries
   - Added `QueuePromptRequest` schema for queue request body
   - Added `QueuePromptResponse` schema for queue response
   - Added `PromptQueueListResponse` schema for listing queue
   - Added `PromptQueueClearResponse` schema for clearing queue
   - Added `PromptQueueStatusResponse` schema for status endpoint

4. `agent_sandbox/schemas/__init__.py` - MODIFIED
   - Added exports for all new prompt queue schemas

5. `agent_sandbox/controllers/controller.py` - MODIFIED
   - Added imports for `mark_session_executing`, `mark_session_idle`
   - Modified `/query` endpoint to mark session executing/idle around query
   - Modified `/query_stream` endpoint similarly

6. `agent_sandbox/app.py` - MODIFIED
   - Added imports for prompt queue functions and schemas
   - Added `GET /session/{session_id}/queue` endpoint to list queued prompts
   - Added `POST /session/{session_id}/queue` endpoint to queue a prompt
   - Added `DELETE /session/{session_id}/queue` endpoint to clear queue
   - Added `DELETE /session/{session_id}/queue/{prompt_id}` endpoint to remove specific prompt
   - Added `GET /session/{session_id}/executing` endpoint to check execution status
   - Added `GET /session/queue/status` endpoint for overall statistics

**How it works:**

1. Controller marks session as "executing" when starting a query
2. Client can check `GET /session/{id}/executing` to see if session is busy
3. If session is executing, client queues prompt via `POST /session/{id}/queue`
4. When query completes, controller marks session as "idle"
5. Client can poll queue and decide when to process next prompt
6. Queued prompts expire after configured time (default: 1 hour)

**Queue Entry Structure:**

```python
PROMPT_QUEUE[session_id] = {
    "session_id": "sess_abc123",        # Session this queue belongs to
    "prompts": [                         # List of queued prompts (FIFO)
        {
            "prompt_id": "prompt-uuid",  # Unique ID for this prompt
            "question": "follow-up...",  # The prompt text
            "user_id": "user_123",       # Who submitted (optional)
            "queued_at": 1704067200,     # Unix timestamp when queued
            "expires_at": 1704070800,    # Unix timestamp when expires
            "metadata": {},              # Optional metadata
        }
    ],
    "updated_at": 1704067200,            # Last update timestamp
}
```

**Execution State Structure:**

```python
SESSION_EXECUTION_STATE[session_id] = {
    "session_id": "sess_abc123",  # Session ID
    "status": "executing",        # "executing" | "idle"
    "started_at": 1704067200,     # When execution started
    "updated_at": 1704067200,     # Last status update
}
```

**HTTP Endpoints:**

- `GET /session/{session_id}/queue` - List queued prompts
- `POST /session/{session_id}/queue` - Queue a prompt
- `DELETE /session/{session_id}/queue` - Clear all queued prompts
- `DELETE /session/{session_id}/queue/{prompt_id}` - Remove specific prompt
- `GET /session/{session_id}/executing` - Check if session is executing
- `GET /session/queue/status` - Get overall queue statistics

**Example Usage:**

```bash
# Check if session is executing
curl 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/executing'
# Response: {"ok": true, "session_id": "sess_abc123", "is_executing": true, "queue_size": 0, ...}

# Queue a follow-up prompt
curl -X POST 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/queue' \
  -H 'Content-Type: application/json' \
  -d '{"question": "What about the next step?"}'
# Response: {"ok": true, "queued": true, "prompt_id": "abc-123", "position": 1, ...}

# View queued prompts
curl 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/queue'
# Response: {"ok": true, "session_id": "sess_abc123", "is_executing": false, "queue_size": 1, "prompts": [...], ...}

# Clear the queue
curl -X DELETE 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/queue'
# Response: {"ok": true, "session_id": "sess_abc123", "cleared_count": 1, ...}

# Get overall statistics
curl 'https://<org>--test-sandbox-http-app.modal.run/session/queue/status'
# Response: {"enabled": true, "sessions_with_queues": 5, "total_queued_prompts": 12, ...}
```

**Client-side Usage Pattern:**

```javascript
// When user submits a prompt
async function submitPrompt(sessionId, question) {
  // Check if session is executing
  const statusResp = await fetch(`/session/${sessionId}/executing`);
  const status = await statusResp.json();

  if (status.is_executing) {
    // Session is busy, queue the prompt
    const queueResp = await fetch(`/session/${sessionId}/queue`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    const result = await queueResp.json();
    return { queued: true, position: result.position };
  } else {
    // Session is idle, submit directly
    const queryResp = await fetch('/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, session_id: sessionId }),
    });
    return { queued: false, response: await queryResp.json() };
  }
}
```

### Priority 3: Pre-warm API ✅ COMPLETE

**Problem**: Users wait for sandbox to be ready after submitting prompt.

**Solution**: Add `POST /warm` endpoint that clients call when user starts typing to speculatively prepare a sandbox before the actual query arrives.

**Files modified:**

1. `agent_sandbox/config/settings.py` - MODIFIED
   - Added `prewarm_store_name` setting (default: "agent-prewarm-store")
   - Added `enable_prewarm` setting (default: True)
   - Added `prewarm_timeout_seconds` setting (default: 60) for pre-warm expiry

2. `agent_sandbox/jobs.py` - MODIFIED (at end of file)
   - Added `PREWARM_STORE` Modal Dict for tracking pre-warm requests
   - Added `generate_warm_id()` function to create unique correlation IDs
   - Added `register_prewarm()` function to register pre-warm requests
   - Added `update_prewarm_ready()` function to mark pre-warm as ready
   - Added `get_prewarm()` function to retrieve pre-warm entry
   - Added `claim_prewarm()` function to claim a ready pre-warm
   - Added `expire_prewarm()` function to remove expired entries
   - Added `get_prewarm_status()` function for monitoring
   - Added `cleanup_expired_prewarms()` function for maintenance

3. `agent_sandbox/schemas/sandbox.py` - MODIFIED
   - Added `warm_id` field to `QueryBody` for correlation
   - Added `warm_id` field to `ClaudeCliRequest` for correlation
   - Added `WarmRequest` schema for `POST /warm` requests
   - Added `WarmResponse` schema for `POST /warm` responses
   - Added `WarmStatusResponse` schema for `/warm/status` endpoint

4. `agent_sandbox/schemas/__init__.py` - MODIFIED
   - Added exports for `WarmRequest`, `WarmResponse`, `WarmStatusResponse`

5. `agent_sandbox/app.py` - MODIFIED
   - Added imports for pre-warm functions and schemas
   - Added `POST /warm` endpoint to initiate sandbox pre-warming
   - Added `GET /warm/{warm_id}` endpoint to check pre-warm status
   - Added `GET /warm/status` endpoint for overall pre-warm statistics
   - Added `prewarm_agent_sdk_sandbox()` Modal function for background warming
   - Added `prewarm_cli_sandbox()` Modal function for background CLI warming
   - Modified `query_proxy()` to claim pre-warm when warm_id provided
   - Modified `query_stream()` to claim pre-warm when warm_id provided
   - Modified `run_claude_cli_remote()` to accept and claim warm_id
   - Modified `claude_cli_proxy()` to pass warm_id
   - Modified `claude_cli_submit()` to pass warm_id

**How it works:**

1. Client calls `POST /warm` when user starts typing (e.g., focus on input field)
2. Server generates a `warm_id` and registers the pre-warm request in `PREWARM_STORE`
3. Server spawns background task to prepare sandbox (claims from pool or creates new)
4. Background task updates pre-warm entry with sandbox_id and URL when ready
5. When actual query arrives with `warm_id`:
   - Server claims the pre-warm (marks as claimed, for metrics)
   - Uses the pre-warmed sandbox (already available in worker globals)
6. Pre-warm entries expire after `prewarm_timeout_seconds` (default: 60s)

**Pre-warm Entry Structure:**

```python
PREWARM_STORE[warm_id] = {
    "warm_id": "abc-123",           # Unique correlation ID
    "sandbox_type": "agent_sdk",    # "agent_sdk" or "cli"
    "sandbox_id": "sb-xxx",         # Modal sandbox object_id (when ready)
    "sandbox_url": "https://...",   # Tunnel URL (when ready)
    "status": "warming",            # "warming" | "ready" | "claimed" | "expired"
    "created_at": 1704067200,       # Unix timestamp
    "expires_at": 1704067260,       # Unix timestamp (created_at + timeout)
    "claimed_by": None,             # session_id/job_id (when claimed)
    "session_id": "sess_123",       # Optional session_id for Agent SDK
    "job_id": None,                 # Optional job_id for CLI
}
```

**HTTP Endpoints:**

- `POST /warm` - Initiate sandbox pre-warming, returns warm_id
- `GET /warm/{warm_id}` - Check status of a specific pre-warm request
- `GET /warm/status` - Get overall pre-warm statistics

**Example Usage:**

```bash
# Client calls when user focuses on input
curl -X POST 'https://<org>--test-sandbox-http-app.modal.run/warm' \
  -H 'Content-Type: application/json' \
  -d '{"sandbox_type": "agent_sdk", "session_id": "sess_123"}'

# Response: {"warm_id": "abc-123", "status": "warming", "expires_at": 1704067260, ...}

# Then pass warm_id with the actual query
curl -X POST 'https://<org>--test-sandbox-http-app.modal.run/query' \
  -H 'Content-Type: application/json' \
  -d '{"question": "What is 2+2?", "warm_id": "abc-123", "session_id": "sess_123"}'
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

### Priority 6: Multiplayer Session Support ✅ COMPLETE

**Problem**: Sessions are single-user; no collaboration support.

**Solution**: Allow multiple users to interact with the same session with user attribution and message history tracking.

**Files modified:**

1. `agent_sandbox/config/settings.py` - MODIFIED
   - Added `session_metadata_store_name` setting (default: "agent-session-metadata")
   - Added `enable_multiplayer_sessions` setting (default: True)
   - Added `max_message_history_per_session` setting (default: 100)
   - Added `message_content_max_length` setting (default: 1000)
   - Added `max_authorized_users_per_session` setting (default: 20)

2. `agent_sandbox/schemas/sandbox.py` - MODIFIED (at end of file)
   - Added `MessageHistoryEntry` schema for messages with user attribution
   - Added `SessionMetadata` schema for ownership and access control
   - Added `SessionShareRequest` / `SessionShareResponse` schemas
   - Added `SessionUnshareRequest` / `SessionUnshareResponse` schemas
   - Added `SessionMetadataResponse` schema
   - Added `SessionHistoryResponse` schema
   - Added `SessionUsersResponse` schema
   - Added `MultiplayerStatusResponse` schema

3. `agent_sandbox/schemas/__init__.py` - MODIFIED
   - Added exports for all new multiplayer session schemas

4. `agent_sandbox/jobs.py` - MODIFIED (at end of file)
   - Added `SESSION_METADATA` Modal Dict for storing session metadata
   - Added `create_session_metadata()` function to create session with owner
   - Added `get_session_metadata()` function to retrieve session info
   - Added `update_session_metadata()` function to update session fields
   - Added `authorize_session_user()` function to share session with users
   - Added `revoke_session_user()` function to remove user access
   - Added `is_user_authorized()` function to check access permissions
   - Added `get_session_users()` function to list users with access
   - Added `add_message_to_history()` function to record messages with attribution
   - Added `get_session_history()` function to retrieve conversation history
   - Added `get_session_message_count()` function for history size
   - Added `clear_session_history()` function to clear messages
   - Added `delete_session_metadata()` function for cleanup
   - Added `get_multiplayer_status()` function for overall statistics

5. `agent_sandbox/app.py` - MODIFIED
   - Added imports for multiplayer session functions and schemas
   - Added `GET /session/{session_id}/metadata` endpoint to get session info
   - Added `GET /session/{session_id}/users` endpoint to list authorized users
   - Added `POST /session/{session_id}/share` endpoint to share session
   - Added `POST /session/{session_id}/unshare` endpoint to revoke access
   - Added `GET /session/{session_id}/history` endpoint to get message history
   - Added `GET /session/multiplayer/status` endpoint for statistics

6. `agent_sandbox/controllers/controller.py` - MODIFIED
   - Added imports for `add_message_to_history`, `create_session_metadata`
   - Modified `/query` endpoint to track message history with user attribution
   - Modified `/query_stream` endpoint similarly

**How it works:**

1. When a new session is created, metadata is stored with the owner's user_id
2. Sessions can be shared with other users via `POST /session/{id}/share`
3. User access can be revoked via `POST /session/{id}/unshare`
4. All query messages (user and assistant) are recorded with user attribution
5. Message history can be retrieved via `GET /session/{id}/history`
6. Session metadata includes ownership, authorized users, and message counts

**Session Metadata Structure:**

```python
SESSION_METADATA[session_id] = {
    "session_id": "sess_abc123",       # Session identifier
    "owner_id": "user_123",            # User who created the session
    "created_at": 1704067200,          # Unix timestamp
    "updated_at": 1704067200,          # Last activity timestamp
    "name": "My Session",              # Optional human-readable name
    "description": None,               # Optional description
    "authorized_users": ["user_456"],  # Users with access (excludes owner)
    "messages": [...],                 # Message history with attribution
}
```

**Message Entry Structure:**

```python
{
    "message_id": "uuid-string",       # Unique message identifier
    "role": "user" | "assistant",      # Who sent the message
    "content": "What is 2+2?",         # Message content (truncated)
    "user_id": "user_123",             # Who sent (for user role)
    "timestamp": 1704067200,           # Unix timestamp
    "turn_number": 1,                  # Conversation turn
    "tokens_used": 50,                 # Tokens consumed (assistant only)
}
```

**HTTP Endpoints:**

- `GET /session/{session_id}/metadata` - Get session metadata
- `GET /session/{session_id}/users` - List users with access
- `POST /session/{session_id}/share` - Share session with a user
- `POST /session/{session_id}/unshare` - Revoke user access
- `GET /session/{session_id}/history` - Get message history
- `GET /session/multiplayer/status` - Get overall statistics

**Example Usage:**

```bash
# Get session metadata
curl 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/metadata'
# Response: {"ok": true, "session_id": "sess_abc123", "owner_id": "user_123", ...}

# Share session with another user
curl -X POST 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/share' \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "user_456", "requested_by": "user_123"}'
# Response: {"ok": true, "shared_with": "user_456", "authorized_users": ["user_456"], ...}

# Get message history
curl 'https://<org>--test-sandbox-http-app.modal.run/session/sess_abc123/history?limit=10'
# Response: {"ok": true, "message_count": 5, "messages": [...], ...}

# Query with user attribution (regular /query endpoint)
curl -X POST 'https://<org>--test-sandbox-http-app.modal.run/query' \
  -H 'Content-Type: application/json' \
  -d '{"question": "What is 2+2?", "session_id": "sess_abc123", "user_id": "user_123"}'
# Messages are automatically recorded with user attribution

# Get multiplayer statistics
curl 'https://<org>--test-sandbox-http-app.modal.run/session/multiplayer/status'
# Response: {"enabled": true, "total_sessions": 10, "shared_sessions": 3, ...}
```

## Current Todo List State

1. ✅ Statistics & Usage Tracking (Priority 5) - COMPLETE
2. ✅ Agent SDK Sandbox Snapshots (Priority 1) - COMPLETE
3. ✅ CLI Sandbox Snapshots (Priority 10) - COMPLETE
4. ✅ Agent SDK Warm Pool (Priority 2) - COMPLETE
5. ✅ CLI Warm Pool (Priority 11) - COMPLETE
6. ✅ Pre-warm API (Priority 3) - COMPLETE
7. ✅ Stop/Cancel Mid-Execution (Priority 8) - COMPLETE
8. ✅ Follow-up Prompt Queue (Priority 7) - COMPLETE
9. ✅ Multiplayer Session Support (Priority 6) - COMPLETE
10. 🔄 Ralph Loop Improvements (Priority 12) - NEXT
11. ⏳ CLI Job Workspace Improvements (Priority 13)
12. ⏳ VS Code Integration (Priority 9)
13. ⏳ Sub-Session Spawning Tool (Priority 4)

## Next Steps

1. Continue with **Priority 12: Ralph Loop Improvements**
   - Progress streaming via SSE
   - Pause/Resume endpoints
   - Iteration snapshots for rollback

2. After completing Priority 12, move to CLI Job Workspace Improvements (Priority 13)

3. Follow the phased implementation order in the plan file
