# Architecture Overview

This document explains the overall architecture of the agent sandbox application, focusing on the relationship between Modal's ingress layer and the dual long-lived background services.

## High-Level Architecture

The application uses a **dual-sandbox architecture pattern** optimized for low latency, resource efficiency, and separation of concerns:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            Modal App (test-sandbox)                          │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │                         HTTP Gateway (web_app)                         │  │
│  │                                                                        │  │
│  │  /health              /query, /query_stream       /claude_cli/*        │  │
│  │  /submit              /claude_cli (proxy)         /ralph/start         │  │
│  │  /jobs/*                                          /ralph/{job_id}      │  │
│  │  /service_info                                                         │  │
│  └───────────────────────────┬────────────────────────────┬───────────────┘  │
│                              │                            │                  │
│              ┌───────────────┘                            └───────────────┐  │
│              │                                                            │  │
│              ▼                                                            ▼  │
│  ┌───────────────────────────────────┐        ┌───────────────────────────────────┐
│  │      Agent SDK Sandbox            │        │       CLI Sandbox                 │
│  │      (svc-runner-8001)            │        │       (claude-cli-runner)         │
│  │                                   │        │                                   │
│  │  ┌─────────────────────────────┐  │        │  ┌─────────────────────────────┐  │
│  │  │  controller.py :8001        │  │        │  │  cli_controller.py :8002    │  │
│  │  │                             │  │        │  │                             │  │
│  │  │  GET  /health_check         │  │        │  │  GET  /health_check         │  │
│  │  │  POST /query                │  │        │  │  POST /execute              │  │
│  │  │  POST /query_stream         │  │        │  │  POST /ralph/execute        │  │
│  │  │  POST /claude_cli           │  │        │  │                             │  │
│  │  └─────────────────────────────┘  │        │  └─────────────────────────────┘  │
│  │                                   │        │                                   │
│  │  Volume: svc-runner-8001-vol      │        │  Volume: claude-cli-runner-vol    │
│  │  Mount:  /data                    │        │  Mount:  /data-cli                │
│  │                                   │        │                                   │
│  │  Image: _base_anthropic_sdk_image │        │  Image: _claude_cli_image         │
│  │  (Claude Agent SDK)               │        │  (Claude Code CLI)                │
│  └───────────────────────────────────┘        └───────────────────────────────────┘
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

### 1. Modal Infrastructure (Ingress Layer)

**What it does:**
- Accepts incoming HTTPS requests from external clients
- Terminates TLS/SSL connections
- Routes requests to the appropriate Modal function
- Provides load balancing and auto-scaling
- Manages authentication and authorization (via Modal Connect tokens)

**Key characteristics:**
- Fully managed by Modal
- No code required in your application
- Automatically handles SSL certificates
- Provides public URLs like `https://<org>--test-sandbox-http-app-dev.modal.run`

**How it works:**
When you deploy with `modal serve` or `modal deploy`, Modal:
1. Provisions a public HTTPS endpoint
2. Routes all traffic to functions decorated with `@modal.asgi_app()`
3. Handles SSL termination, DDoS protection, and routing automatically

### 2. http_app (Ingress Handler)

**Location:** `agent_sandbox/app.py`

**What it does:**
- Acts as the entry point for all HTTP requests
- Lightweight proxy that forwards requests to the appropriate background sandbox
- Handles Modal Connect token generation (optional)
- Manages sandbox lifecycle (creates/reuses both Agent SDK and CLI sandboxes)
- Enqueues async jobs to the job queue
- Retrieves job status from Modal Dict

**Key code:**
```python
@app.function(image=agent_sdk_image, secrets=agent_sdk_secrets)
@modal.asgi_app()
def http_app():
    """ASGI app exposing HTTP endpoints for the agent service."""
    return web_app
```

**Endpoints:**

| Category | Endpoint | Target |
|----------|----------|--------|
| Health | `GET /health` | Gateway health check |
| Agent SDK | `POST /query` | → Agent SDK sandbox :8001/query |
| Agent SDK | `POST /query_stream` | → Agent SDK sandbox :8001/query_stream |
| CLI | `POST /claude_cli` | → CLI sandbox :8002/execute |
| CLI | `POST /claude_cli/submit` | Spawns async CLI function |
| CLI | `GET /claude_cli/result/{call_id}` | Polls CLI function result |
| CLI | `DELETE /claude_cli/{call_id}` | Cancels CLI function |
| Ralph | `POST /ralph/start` | Spawns async Ralph loop |
| Ralph | `GET /ralph/{job_id}` | Polls Ralph status |
| Jobs | `POST /submit` | Enqueue to JOB_QUEUE |
| Jobs | `GET /jobs/{job_id}` | Check job status |
| Jobs | `GET /jobs/{job_id}/artifacts` | List job artifacts |
| Jobs | `GET /jobs/{job_id}/artifacts/{path}` | Download artifact |
| Jobs | `DELETE /jobs/{job_id}` | Cancel a queued job |
| Info | `GET /service_info` | Sandbox info |

**Why it's lightweight:**
- Doesn't run the Claude Agent SDK directly
- Doesn't maintain long-lived connections to Anthropic
- Simply forwards requests and returns responses
- Can scale independently from the background services

### 3. JOB_QUEUE (Modal Queue)

**What it does:**
- Receives async job payloads from `/submit` endpoint
- Stores jobs until a worker picks them up
- Enables fire-and-forget workload pattern

**Key characteristics:**
- Jobs persist until processed or canceled
- Worker (`process_job_queue`) consumes jobs
- Results stored in JOB_RESULTS Modal Dict
- Optional cron scheduling via `job_queue_cron` setting

### 4. Modal Dicts (SESSION_STORE / JOB_RESULTS)

**What they do:**
- **SESSION_STORE**: Maps `session_key` → last `session_id` for resumption
- **JOB_RESULTS**: Stores job metadata and results by `job_id`

**Key characteristics:**
- Distributed key-value storage
- Persists across sandbox restarts
- Enables session continuity and job tracking

### 5. Agent SDK Sandbox (svc-runner-8001)

**Location:** `agent_sandbox/controllers/controller.py`

**What it does:**
- Runs the Claude Agent SDK client
- Executes agent queries and tool calls via MCP servers
- Maintains warm state (avoids cold-start latency)
- Handles MCP server connections
- Manages tool permissions and execution

**Key characteristics:**
- Long-lived process (runs for hours, not seconds)
- Runs inside a `modal.Sandbox` on port 8001
- Uses `_base_anthropic_sdk_image` with Claude Agent SDK
- Volume: `svc-runner-8001-vol` mounted at `/data`
- Timeout: 24h max, 10min idle

**Endpoints:**
- `GET /health_check` - Liveness/readiness probe
- `POST /query` - Execute agent query (non-streaming)
- `POST /query_stream` - Execute agent query (streaming via SSE)
- `POST /claude_cli` - Execute Claude CLI (delegated to CLI sandbox in some configs)

### 6. CLI Sandbox (claude-cli-runner)

**Location:** `agent_sandbox/controllers/cli_controller.py`

**What it does:**
- Runs Claude Code CLI as subprocess
- Executes Ralph autonomous coding loops
- Runs as non-root `claude` user (required for `--dangerously-skip-permissions`)
- Manages CLI workspace and job artifacts

**Key characteristics:**
- Long-lived process (separate from Agent SDK sandbox)
- Runs inside a `modal.Sandbox` on port 8002
- Uses `_claude_cli_image` with Claude Code CLI
- Volume: `claude-cli-runner-vol` mounted at `/data-cli`
- Timeout: 24h max, 30min idle

**Endpoints:**
- `GET /health_check` - Liveness/readiness probe
- `POST /execute` - Execute Claude Code CLI
- `POST /ralph/execute` - Execute Ralph autonomous coding loop

**Why a separate sandbox?**
- Claude Code CLI requires non-root execution for `--dangerously-skip-permissions`
- Agent SDK runs as root; CLI runs as `claude` user
- Separate volumes prevent permission conflicts
- Independent scaling and lifecycle management

## Request Flow

### Example: User sends a query

1. **Client Request:**
   ```bash
   curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/query' \
     -H 'Content-Type: application/json' \
     -d '{"question":"What is the capital of Canada?"}'
   ```

2. **Modal Infrastructure:**
   - Receives HTTPS request
   - Terminates TLS
   - Routes to `http_app` function

3. **http_app Handler:**
   - Receives request at `POST /query`
   - Calls `get_or_start_background_sandbox_aio()` to get/reuse sandbox
   - Discovers encrypted tunnel URL for the background service
   - Optionally generates Modal Connect token for authentication
   - Makes HTTP request to background service: `POST {SERVICE_URL}/query`

4. **Background Service (Controller):**
   - Receives request at `/query` endpoint
   - Creates `ClaudeSDKClient` with configured options
   - Executes `client.query(body.question)`
   - Streams response messages
   - Returns JSON response with messages

5. **Response Path:**
   - Background service → http_app → Modal Infrastructure → Client

### Example: User submits an async job

1. **Client Request:**
   ```bash
   curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/submit' \
     -H 'Content-Type: application/json' \
     -d '{"question":"Analyze this large dataset..."}'
   ```

2. **http_app Handler:**
   - Receives request at `POST /submit`
   - Generates unique `job_id`
   - Creates job record in `JOB_RESULTS` dict (status: `queued`)
   - Enqueues job payload to `JOB_QUEUE`
   - Returns immediately: `{"ok": true, "job_id": "..."}`

3. **Worker Processing:**
   - `process_job_queue` function picks up job from queue
   - Updates job status to `running`
   - Executes agent query via background sandbox
   - Stores result in `JOB_RESULTS` dict
   - Updates status to `complete` or `failed`
   - If webhook config is present, spawns webhook delivery attempts

4. **Client Polling:**
   ```bash
   curl 'https://<org>--test-sandbox-http-app-dev.modal.run/jobs/{job_id}'
   ```
   - Returns job status and result when complete

5. **Artifact Retrieval:**
   ```bash
   curl 'https://<org>--test-sandbox-http-app-dev.modal.run/jobs/{job_id}/artifacts'
   curl -O 'https://<org>--test-sandbox-http-app-dev.modal.run/jobs/{job_id}/artifacts/report.md'
   ```
   - Lists artifacts and downloads generated files

### Example: Claude CLI execution

1. **Client Request:**
   ```bash
   curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/claude_cli' \
     -H 'Content-Type: application/json' \
     -d '{"prompt":"Create hello.py and run it","allowed_tools":["Write","Bash","Read"],"job_id":"550e8400-e29b-41d4-a716-446655440000"}'
   ```

2. **http_app Handler:**
   - Receives request at `POST /claude_cli`
   - Calls `get_or_start_cli_sandbox_aio()` to get/reuse CLI sandbox
   - Forwards request to `POST {CLI_SERVICE_URL}/execute`

3. **CLI Sandbox (cli_controller):**
   - Creates job workspace at `/data-cli/jobs/{job_id}/`
   - Runs Claude CLI as subprocess (`demote_to_claude()`)
   - Returns JSON response with result

### Example: Ralph autonomous coding loop

1. **Client Request:**
   ```bash
   curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/ralph/start' \
     -H 'Content-Type: application/json' \
     -d '{"prd":{"name":"my-project","userStories":[{"id":"task-1","category":"functional","description":"Create hello.txt","steps":["Verify file exists"],"priority":1,"passes":false}]},"max_iterations":10}'
   ```

2. **http_app Handler:**
   - Generates unique `job_id`
   - Spawns `run_ralph_remote` Modal function
   - Returns immediately: `{"job_id":"...","call_id":"...","status":"started"}`

3. **Ralph Loop (in CLI sandbox):**
   - Creates workspace at `/data-cli/jobs/{job_id}/`
   - Iterates through PRD tasks, calling Claude CLI for each
   - Writes `status.json`, `progress.txt`, `prd.json`
   - Creates git commits (if `auto_commit=true`)

4. **Client Polling:**
   ```bash
   curl 'https://<org>--test-sandbox-http-app-dev.modal.run/ralph/{job_id}?call_id={call_id}'
   ```
   - Returns live status from `status.json`
   - Returns full result when complete

### When to use each pattern

| Scenario | Pattern | Endpoint |
|----------|---------|----------|
| Quick queries (< 30s) | Sync | `/query` |
| Real-time streaming UI | Sync | `/query_stream` |
| Long-running analysis | Async | `/submit` + `/jobs/{id}` |
| Background batch processing | Async | `/submit` |
| Fire-and-forget tasks | Async | `/submit` |
| CLI code execution | Sync | `/claude_cli` |
| CLI async execution | Async | `/claude_cli/submit` + `/claude_cli/result/{id}` |
| Autonomous coding (PRD) | Async | `/ralph/start` + `/ralph/{job_id}` |

## Why This Architecture?

### Benefits

1. **Low Latency:**
   - Both background services stay warm (no cold-start for agent runtime)
   - Only the lightweight `http_app` may experience cold-start
   - Agent SDK client is already initialized and ready

2. **Resource Efficiency:**
   - Background services can be reused across many requests
   - Single sandbox can handle multiple concurrent requests
   - Persistent volumes allow stateful operations

3. **Separation of Concerns:**
   - Ingress layer handles routing and authentication
   - Agent SDK sandbox handles conversational queries
   - CLI sandbox handles code execution and autonomous loops
   - Clear boundaries between public API and internal services

4. **Permission Isolation:**
   - Agent SDK runs as root (for MCP server management)
   - CLI runs as non-root `claude` user (required for `--dangerously-skip-permissions`)
   - Separate volumes prevent permission conflicts

5. **Scalability:**
   - `http_app` can scale independently (lightweight functions)
   - Both sandboxes can be shared or replicated as needed
   - Modal handles load balancing automatically

6. **Security:**
   - Each sandbox runs in isolated environment
   - Encrypted ports prevent direct access
   - Modal Connect tokens provide per-request authentication
   - TLS termination handled by Modal infrastructure

### Trade-offs

1. **Complexity:**
   - Three services to manage (gateway + two sandboxes)
   - Requires tunnel discovery and health checking for both sandboxes
   - More moving parts to debug

2. **Latency Overhead:**
   - Extra network hop (http_app → sandbox)
   - Typically < 50ms, but adds some overhead
   - Worth it for the cold-start savings

3. **State Management:**
   - Need to handle two sandbox lifecycles (creation, reuse, cleanup)
   - Must track service URLs and health status for both
   - Requires session management for multi-user scenarios
   - Two separate volumes to manage (/data and /data-cli)

## Production Considerations

### When to Use This Pattern

✅ **Use this pattern when:**
- You need low-latency agent responses
- You have stateful operations (file I/O, persistent tool state)
- You want to minimize cold-start costs
- You need to handle multiple concurrent requests efficiently

❌ **Consider alternatives when:**
- You have very low traffic (simple function might be cheaper)
- You don't need persistent state
- You can tolerate cold-start latency
- You want maximum simplicity

### Deployment

**Development:**
```bash
modal serve -m agent_sandbox.app
```

**Production:**
```bash
modal deploy -m agent_sandbox.deploy
```

Both commands:
1. Deploy `http_app` as a public HTTPS endpoint
2. Create Agent SDK sandbox on first `/query` request
3. Create CLI sandbox on first `/claude_cli` or `/ralph/start` request
4. Handle all routing and lifecycle management automatically

## Sandbox Configuration Summary

| Setting | Agent SDK Sandbox | CLI Sandbox |
|---------|-------------------|-------------|
| Name | `svc-runner-8001` | `claude-cli-runner` |
| Port | 8001 | 8002 |
| Volume | `svc-runner-8001-vol` | `claude-cli-runner-vol` |
| Mount | `/data` | `/data-cli` |
| Timeout | 24h | 24h |
| Idle Timeout | 10m | 30m |
| CPU | 1.0 | 1.0 |
| Memory | 2048 MB | 2048 MB |
| User | root | claude (non-root) |

## Related Documentation

- [Controllers Deep Dive](./controllers.md) - Detailed explanation of both controller services
- [Modal Ingress](./modal-ingress.md) - How Modal handles HTTP ingress
- [Configuration](./configuration.md) - Configuration options and settings
- [API Usage](./api-usage.md) - Complete API reference including Ralph endpoints
