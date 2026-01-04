# Controllers: Background Service Architecture

This document explains the role and implementation of the `agent_sandbox.controllers.controller` module, which serves as the long-lived worker that actually runs the agent loop.

## Overview

The controller is a **FastAPI microservice** that runs inside a long-lived `modal.Sandbox`. It's responsible for:

1. **Executing agent queries** using the Claude Agent SDK
2. **Managing tool permissions** and MCP server connections
3. **Maintaining warm state** to avoid cold-start latency
4. **Handling streaming responses** via Server-Sent Events (SSE)

## Location and Structure

**File:** `agent_sandbox/controllers/controller.py`

**Key Components:**

- FastAPI application instance (`app`)
- Permission handlers (`allow_web_only`, `allow_web_only_with_updates`)
- Configuration builder (`_options()`)
- HTTP endpoints (`/health_check`, `/query`, `/query_stream`)

## How It's Started

The controller is started inside a Modal Sandbox via uvicorn:

```python
# From agent_sandbox/app.py
SANDBOX = modal.Sandbox.create(
    "uvicorn",
    "agent_sandbox.controllers.controller:app",
    "--host", "0.0.0.0",
    "--port", str(SERVICE_PORT),  # Default: 8001
    ...
)
```

**Process flow:**

1. Modal creates a sandbox environment
2. Runs `uvicorn agent_sandbox.controllers.controller:app --host 0.0.0.0 --port 8001`
3. Uvicorn loads the FastAPI app from `controller.py`
4. App listens on port 8001 (encrypted via Modal tunnel)
5. `http_app` discovers the tunnel URL and proxies requests

## Key Functions

### Permission Handlers

The controller implements permission handlers to control which tools the agent can use:

```python
async def allow_web_only(
    tool_name: str,
    tool_input: Dict[str, Any],
    ctx: ToolPermissionContext,
):
    """Allows only web-related tools (WebSearch, WebFetch)."""
    if tool_name.startswith("WebSearch") or tool_name.startswith("WebFetch"):
        return PermissionResultAllow(updated_input=tool_input)
    return PermissionResultDeny(message=f"Tool {tool_name} is not allowed")
```

**Current configuration:**

- Uses `permission_mode="acceptEdits"` (allows user to approve tool usage)
- `can_use_tool=allow_web_only` restricts to web tools only
- Can be modified to allow all tools or implement custom permission logic

### Configuration Builder

The `_options()` function centralizes Claude Agent SDK configuration:

```python
def _options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers=get_mcp_servers(),
        allowed_tools=get_allowed_tools(),
        can_use_tool=allow_web_only,
        permission_mode="acceptEdits"
    )
```

**What it configures:**

- System prompt (from `agent_sandbox.prompts.prompts`)
- MCP servers (from `agent_sandbox.tools`)
- Allowed tools list
- Permission handling strategy

## HTTP Endpoints

### GET /health_check

**Purpose:** Liveness/readiness probe

**Response:**

```json
{ "ok": true }
```

**Usage:**

- Called by `http_app` to verify service is ready
- Used during sandbox startup to wait for service availability
- Simple check that uvicorn is running and FastAPI is ready

**Implementation:**

```python
@app.get("/health_check")
def health_check():
    return {"ok": True}
```

### POST /query

**Purpose:** Execute a non-streaming agent query

**Request Body:**

```json
{
  "question": "What is the capital of Canada?"
}
```

**Response:**

```json
{
  "ok": true,
  "messages": [
    {
      "type": "assistant",
      "content": [{ "type": "text", "text": "The capital of Canada is Ottawa." }],
      "model": "claude-..."
    },
    {
      "type": "result",
      "duration_ms": 1234,
      "total_cost_usd": 0.0001,
      "usage": { "input_tokens": 12, "output_tokens": 24 }
    }
  ],
  "summary": {
    "text": "The capital of Canada is Ottawa.",
    "is_complete": true,
    "duration_ms": 1234,
    "total_cost_usd": 0.0001
  }
}
```

**Flow:**

1. Receives `QueryBody` with question
2. Optionally validates Modal Connect token (if `ENFORCE_CONNECT_TOKEN=True`)
3. Creates `ClaudeSDKClient` with configured options
4. Executes `client.query(body.question)`
5. Collects all response messages
6. Serializes SDK messages into structured JSON
7. Returns JSON with `messages` plus a `summary` object

**Implementation:**

```python
@app.post("/query")
async def query_agent(body: QueryBody, request: Request):
    messages = []
    async with ClaudeSDKClient(options=_options()) as client:
        await client.query(body.question)
        async for msg in client.receive_response():
            messages.append(msg)

    return {
        "ok": True,
        "messages": [serialize_message(message) for message in messages],
        "summary": build_final_summary(...),
    }
```

### POST /query_stream

**Purpose:** Execute a streaming agent query via Server-Sent Events (SSE)

**Request Body:**

```json
{
  "question": "What is the capital of Canada?"
}
```

**Response:** Server-Sent Events stream

```text
event: assistant
data: {"type":"assistant","content":[{"type":"text","text":"..."}],"model":"claude-..."}

event: result
data: {"type":"result","duration_ms":1234,"total_cost_usd":0.0001}

event: done
data: {"text":"...","is_complete":true,"duration_ms":1234}
```

**Flow:**

1. Receives `QueryBody` with question
2. Optionally validates Modal Connect token
3. Creates `ClaudeSDKClient` with configured options
4. Executes query and streams structured SSE events
5. Emits `event: done` with summary when complete

**Implementation:**

```python
@app.post("/query_stream")
async def query_agent_stream(body: QueryBody, request: Request):
    async def sse():
        async with ClaudeSDKClient(options=_options()) as client:
            await client.query(body.question)
            async for msg in client.receive_response():
                serialized = serialize_message(msg)
                yield _format_sse(serialized["type"], serialized)
        yield _format_sse("done", build_final_summary(...))

    return StreamingResponse(sse(), media_type="text/event-stream")
```

## Security Features

### Modal Connect Token Support

The controller can enforce authentication via Modal Connect tokens:

```python
ENFORCE_CONNECT_TOKEN = False  # Set to True to enable

@app.post("/query")
async def query_agent(body: QueryBody, request: Request):
    if ENFORCE_CONNECT_TOKEN:
        if not request.headers.get("X-Verified-User-Data"):
            raise HTTPException(status_code=401, detail="Missing or invalid connect token")
    # ... rest of handler
```

**How it works:**

- `http_app` generates a connect token per request (if enabled)
- Token is passed in `Authorization: Bearer <token>` header
- Modal infrastructure validates token and injects `X-Verified-User-Data` header
- Controller checks for this header to verify authentication

**To enable:**

1. Set `ENFORCE_CONNECT_TOKEN = True` in `controller.py`
2. Set `enforce_connect_token = True` in settings
3. `http_app` will automatically generate tokens

### Encrypted Ports

The controller runs on an encrypted port (default: 8001) that's only accessible via Modal's tunnel discovery:

- Port is encrypted at the network level
- Only `http_app` can discover and access the tunnel URL
- External clients cannot directly access the controller
- Provides isolation and security by default

## Why It's a Long-Lived Worker

### Cold-Start Avoidance

**Without controller (short-lived function):**

- Every request spawns new Modal function
- Must initialize Claude Agent SDK client
- Must connect to MCP servers
- Must load tools and configuration
- **Latency:** 5-15 seconds per request

**With controller (long-lived sandbox):**

- Service stays warm between requests
- Claude Agent SDK client can be reused
- MCP connections are maintained
- Tools are already loaded
- **Latency:** < 1 second per request

### Resource Efficiency

- Single sandbox can handle multiple concurrent requests
- Shared state (MCP servers, tool registry) across requests
- Persistent volume allows file operations to persist
- Better resource utilization than per-request functions

### Stateful Operations

The controller enables stateful agent operations:

- File I/O to persistent volume (`/data`)
- Tool state that persists across queries
- MCP server connections that stay alive
- Agent memory/session state (if implemented)

## Lifecycle Management

### Creation

The controller is created by `get_or_start_background_sandbox()` in `app.py`:

1. Checks if sandbox already exists (by name)
2. If exists, reuses it and discovers tunnel URL
3. If not, creates new sandbox with uvicorn command
4. Waits for `/health_check` to respond
5. Stores sandbox and URL in global variables

### Reuse

The sandbox is reused across multiple requests:

- Global variables (`SANDBOX`, `SERVICE_URL`) cache the reference
- `get_or_start_background_sandbox()` checks for existing sandbox first
- Same sandbox handles all requests until timeout or termination

### Cleanup

The sandbox is cleaned up automatically:

- **Timeout:** After `sandbox_timeout` (default: 12 hours)
- **Idle timeout:** After `sandbox_idle_timeout` (default: 10 minutes) of inactivity
- **Manual termination:** Via `terminate_service_sandbox()` function
- **Cron cleanup:** `cleanup_sessions()` checks sandbox health every 2 minutes

## Customization

### Modify Permission Handling

Edit `allow_web_only()` or create new permission handlers:

```python
async def allow_all_tools(tool_name: str, tool_input: Dict[str, Any], ctx: ToolPermissionContext):
    """Allow all tools without restriction."""
    return PermissionResultAllow(updated_input=tool_input)

# Then update _options():
can_use_tool=allow_all_tools,
permission_mode="bypassPermissions"  # If you want to skip permission checks
```

### Add New Endpoints

Add new FastAPI routes to the controller:

```python
@app.post("/custom_endpoint")
async def custom_handler(body: CustomBody):
    # Your custom logic here
    return {"result": "success"}
```

### Modify System Prompt

Edit `agent_sandbox/prompts/prompts.py` - the controller imports `SYSTEM_PROMPT` from there.

### Change Tool Configuration

Modify `agent_sandbox/tools/` to add/remove tools or MCP servers.

## Session Handling

The controller supports session resumption for multi-turn conversations:

### Request Fields

- `session_id`: Resume from a specific session returned by a prior response
- `session_key`: Server-side key that maps to the last session for a user
- `fork_session`: When resuming, start a new branched session instead of continuing the original

### How It Works

1. **First request** (no session fields):
   - Controller creates a new agent session
   - Returns `session_id` in the response

2. **With `session_key`**:
   - Controller looks up the last `session_id` from `SESSION_STORE` Modal Dict
   - Uses that session for context resumption
   - After query, stores the new `session_id` back to `SESSION_STORE`

3. **With explicit `session_id`**:
   - Controller directly resumes from that specific session
   - Ignores `session_key` if both are provided

4. **With `fork_session=true`**:
   - Creates a new session branched from the prior context
   - Original session remains unchanged
   - Useful for exploring alternative paths

### Storage

Session mappings are stored in a Modal Dict:
- Default name: `agent-session-store` (configurable via `session_store_name`)
- Key: `session_key` value
- Value: Latest `session_id` for that key

## Volume Commit Behavior

When `volume_commit_interval` is configured, the controller manages volume persistence:

### Before Each Query

1. If `volume_commit_interval` is set, the persistent volume is **reloaded**
2. This ensures the controller sees the latest committed state
3. Changes from other processes or prior commits become visible

### After Each Query

1. Controller checks if enough time has passed since last commit
2. If `volume_commit_interval` seconds have elapsed:
   - Calls `volume.commit()` to persist all `/data` changes
   - Resets the commit timer
3. If interval not reached, skips commit (will commit on next eligible request)

### Benefits

- Writes persist without terminating the sandbox
- Multiple queries can accumulate changes before commit
- Read operations see consistent state via reload

### Trade-offs

- Commits add latency (~100-500ms depending on data size)
- Very frequent queries may hit commit overhead
- Set `volume_commit_interval=None` to disable (commits only on sandbox termination)

## Troubleshooting

### Service Not Starting

**Symptoms:** `http_app` can't connect to controller

**Debug steps:**

1. Check sandbox logs: `modal sandbox logs <sandbox-id>`
2. Verify uvicorn is running: Check for "Application startup complete"
3. Verify port 8001 is listening: Check tunnel discovery
4. Check `/health_check` endpoint directly (if you have tunnel URL)

### Permission Denied Errors

**Symptoms:** Agent can't use tools

**Solutions:**

1. Check `can_use_tool` handler logic
2. Verify `permission_mode` setting
3. Check `allowed_tools` list in `_options()`
4. Review tool names match permission handler logic

### High Latency

**Symptoms:** Requests take > 5 seconds

**Possible causes:**

1. Cold-start (sandbox was terminated, new one starting)
2. MCP server connection issues
3. Tool execution taking time
4. Network latency between `http_app` and controller

**Solutions:**

1. Check sandbox status (should be "running")
2. Review MCP server logs
3. Profile tool execution time
4. Consider increasing sandbox idle timeout

## Related Documentation

- [Architecture Overview](./architecture.md) - Overall system architecture
- [Modal Ingress](./modal-ingress.md) - How requests reach the controller
- [Configuration](./configuration.md) - Configuration options
