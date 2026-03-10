# Architecture Overview

This document explains the Modal execution backend inside Rafiki's Cloudflare-first architecture.
For public ingress, session authority, and control-plane behavior, start with `docs/design-docs/cloudflare-hybrid-architecture.md`.

## Boundary Summary

| Layer | Role | Audience |
|---|---|---|
| Cloudflare Worker + Durable Objects | Public control plane, auth, session state, queueing, streaming | Client-facing |
| Modal `http_app` gateway | Internal Worker-forwarding target and local/operator diagnostic surface | Internal/operator |
| Controller sandbox | Long-lived OpenAI Agents runtime with tools and sessions | Internal |

## High-Level Architecture

The application uses a **single-sandbox architecture pattern** optimized for low latency and resource efficiency:

```
┌────────────────────────────────────────────────────────────────────────────┐
│                          Modal App (modal-backend)                          │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                       HTTP Gateway (web_app)                         │  │
│  │                                                                      │  │
│  │  /health              /query, /query_stream                          │  │
│  │  /submit              /jobs/*                                        │  │
│  │  /service_info                                                       │  │
│  └────────────────────────────────────┬─────────────────────────────────┘  │
│                                       │                                    │
│                                       ▼                                    │
│            ┌───────────────────────────────────────────────┐               │
│            │           Agent SDK Sandbox                   │               │
│            │           (svc-runner-8001)                   │               │
│            │                                               │               │
│            │  ┌─────────────────────────────────────────┐  │               │
│            │  │  controller.py :8001                    │  │               │
│            │  │                                         │  │               │
│            │  │  GET  /health_check                     │  │               │
│            │  │  POST /query                            │  │               │
│            │  │  POST /query_stream                     │  │               │
│            │  └─────────────────────────────────────────┘  │               │
│            │                                               │               │
│            │  Volume: svc-runner-8001-vol                  │               │
│            │  Mount:  /data                                │               │
│            │                                               │               │
│            │  Image: _base_openai_agents_image             │               │
│            │  (OpenAI Agents SDK)                           │               │
│            └───────────────────────────────────────────────┘               │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

### 1. Modal Infrastructure (Internal Gateway Layer)

**What it does:**
- Accepts incoming HTTPS requests for the Modal gateway URL
- Terminates TLS/SSL connections
- Routes requests to the appropriate Modal function
- Provides load balancing and auto-scaling
- Carries internal Worker traffic and optional local/operator diagnostics

**Key characteristics:**
- Fully managed by Modal
- No code required in your application
- Automatically handles SSL certificates
- Provides addressable gateway URLs like `https://<org>--modal-backend-http-app-dev.modal.run`
- In Rafiki Phase 3, this gateway is not the supported client-facing public ingress

**How it works:**
When you deploy with `modal serve` or `modal deploy`, Modal:
1. Provisions an addressable HTTPS endpoint for the Modal gateway
2. Routes all traffic to functions decorated with `@modal.asgi_app()`
3. Handles SSL termination, DDoS protection, and routing automatically

### 2. http_app (Ingress Handler)

**Location:** `modal_backend/main.py`

**What it does:**
- Acts as the Modal-side entry point for internal Worker traffic and local/operator diagnostics
- Lightweight proxy that forwards requests to the background sandbox
- Handles Modal Connect token generation (optional)
- Manages sandbox lifecycle (creates/reuses sandbox)
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

These endpoints exist on the Modal gateway, but client traffic should use the Cloudflare Worker routes documented in `docs/references/api-usage.md`.

| Category | Endpoint | Target |
|----------|----------|--------|
| Health | `GET /health` | Gateway health check |
| Agent SDK | `POST /query` | → Agent SDK sandbox :8001/query |
| Agent SDK | `POST /query_stream` | → Agent SDK sandbox :8001/query_stream |
| Jobs | `POST /submit` | Enqueue to JOB_QUEUE |
| Jobs | `GET /jobs/{job_id}` | Check job status |
| Jobs | `GET /jobs/{job_id}/artifacts` | List job artifacts |
| Jobs | `GET /jobs/{job_id}/artifacts/{path}` | Download artifact |
| Jobs | `DELETE /jobs/{job_id}` | Cancel a queued job |
| Info | `GET /service_info` | Rollout status (active pointer + service lifecycle) |

**Why it's lightweight:**
- Doesn't run the OpenAI Agents SDK directly
- Doesn't maintain long-lived connections to model provider APIs
- Simply forwards requests and returns responses
- Can scale independently from the background service

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

### 4. Modal Dicts (JOB_RESULTS only)

**What they do:**
- **JOB_RESULTS**: Stores job metadata and results by `job_id`

**Key characteristics:**
- Distributed key-value storage
- Persists across sandbox restarts
- Enables job tracking; durable session continuity lives in SessionAgent DO storage, while Cloudflare KV is used for `session_key -> session_id` cache resolution

### 5. Agent SDK Sandbox (svc-runner-8001)

**Location:** `modal_backend/api/controller.py`

**What it does:**
- Runs the OpenAI Agents SDK client
- Executes agent queries and tool calls via MCP servers
- Maintains warm state (avoids cold-start latency)
- Handles MCP server connections
- Manages tool permissions and execution

**Key characteristics:**
- Long-lived process (runs for hours, not seconds)
- Runs inside a `modal.Sandbox` on port 8001
- Uses `_base_openai_agents_image` with OpenAI Agents SDK
- Volume: `svc-runner-8001-vol` mounted at `/data`
- Timeout: 24h max, 10min idle
- Cutover authority: shared active pointer (`controller-rollout-store`) with generation-aware worker cache refresh
- Promotion: private B warmup + readiness verification + atomic pointer flip + explicit A drain
- Drain accounting: per-request leases, not mutable shared counters

**Endpoints:**
- `GET /health_check` - Liveness/readiness probe
- `POST /query` - Execute agent query (non-streaming)
- `POST /query_stream` - Execute agent query (streaming via SSE)

## Request Flow

### Example: User sends a query

1. **Client Request:**
   ```bash
   curl -X POST 'https://<your-worker>.workers.dev/query' \
     -H 'Authorization: Bearer <session-token>' \
     -H 'Content-Type: application/json' \
     -d '{"question":"What is the capital of Canada?"}'
   ```

2. **Cloudflare Control Plane:**
   - Validates client token and request scope
   - Routes the request through SessionAgent DO logic
   - Signs an internal request to the Modal gateway

3. **Modal Infrastructure:**
   - Receives HTTPS request
   - Terminates TLS
   - Routes to `http_app` function

4. **http_app Handler:**
   - Receives request at `POST /query`
   - Calls `get_or_start_background_sandbox_aio()` to get/reuse sandbox
   - Discovers encrypted tunnel URL for the background service
   - Optionally generates Modal Connect token for authentication
   - Makes HTTP request to background service: `POST {SERVICE_URL}/query`

5. **Background Service (Controller):**
   - Receives request at `/query` endpoint
   - Builds an OpenAI `Agent` from configured options
   - Executes a run via `Runner.run_streamed(...)`
   - Maps stream items into compatibility message events
   - Returns JSON response with messages

6. **Response Path:**
   - Background service → http_app → Modal Infrastructure → Worker/DO → Client

### Example: User submits an async job

1. **Client Request:**
   ```bash
   curl -X POST 'https://<your-worker>.workers.dev/submit' \
     -H 'Authorization: Bearer <session-token>' \
     -H 'Content-Type: application/json' \
     -d '{"question":"Analyze this large dataset..."}'
   ```

2. **Cloudflare Control Plane:**
   - Validates auth and forwards the job request to the Modal gateway with internal auth headers

3. **http_app Handler:**
   - Receives internal request at `POST /submit`
   - Receives a Worker-generated `job_id`
   - Creates job record in `JOB_RESULTS` dict (status: `queued`)
   - Enqueues the supplied job payload to `JOB_QUEUE`
   - Returns immediately: `{"ok": true, "job_id": "..."}`

4. **Worker Processing:**
   - `process_job_queue` function picks up job from queue
   - Updates job status to `running`
   - Executes agent query via background sandbox
   - Stores result in `JOB_RESULTS` dict
   - Updates status to `complete` or `failed`
   - If webhook config is present, spawns webhook delivery attempts

5. **Client Polling:**
   ```bash
   curl 'https://<your-worker>.workers.dev/jobs/{job_id}' \
     -H 'Authorization: Bearer <session-token>'
   ```
   - Returns job status and result when complete

6. **Artifact Retrieval:**
   ```bash
   curl 'https://<your-worker>.workers.dev/jobs/{job_id}/artifacts' \
     -H 'Authorization: Bearer <session-token>'
   curl -H 'Authorization: Bearer <session-token>' \
     -O 'https://<your-worker>.workers.dev/jobs/{job_id}/artifacts/report.md'
   ```
   - Lists artifacts and downloads generated files

### When to use each pattern

| Scenario | Pattern | Endpoint |
|----------|---------|----------|
| Quick queries (< 30s) | Sync | `/query` |
| Real-time streaming UI | Sync | `/query_stream` |
| Long-running analysis | Async | `/submit` + `/jobs/{id}` |
| Background batch processing | Async | `/submit` |
| Fire-and-forget tasks | Async | `/submit` |

## Why This Architecture?

### Benefits

1. **Low Latency:**
   - Background service stays warm (no cold-start for agent runtime)
   - Only the lightweight `http_app` may experience cold-start
   - Agent SDK client is already initialized and ready

2. **Resource Efficiency:**
   - Background service can be reused across many requests
   - Single sandbox can handle multiple concurrent requests
   - Persistent volume allows stateful operations

3. **Separation of Concerns:**
   - Ingress layer handles routing and authentication
   - Agent SDK sandbox handles conversational queries
   - Clear boundaries between public API and internal services

4. **Scalability:**
   - `http_app` can scale independently (lightweight functions)
   - Sandbox can be shared or replicated as needed
   - Modal handles load balancing automatically

5. **Security:**
   - Sandbox runs in isolated environment
   - Encrypted ports prevent direct access
   - Cloudflare owns public auth, session scope, and rate limiting
   - Internal Worker -> Modal and gateway -> sandbox auth preserve backend isolation

### Trade-offs

1. **Complexity:**
   - Two services to manage (gateway + sandbox)
   - Requires tunnel discovery and health checking
   - More moving parts to debug

2. **Latency Overhead:**
   - Extra network hop (http_app → sandbox)
   - Typically < 50ms, but adds some overhead
   - Worth it for the cold-start savings

3. **State Management:**
   - Need to handle sandbox lifecycle (creation, reuse, cleanup)
   - Must track service URL and health status
   - Requires session management for multi-user scenarios

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
modal serve -m modal_backend.main
```

**Production:**
```bash
modal deploy -m modal_backend.deploy
```

Both commands:
1. Expose an addressable Modal gateway endpoint for Worker forwarding and operator diagnostics
2. Create Agent SDK sandbox on first internal `/query` request
3. Handle backend routing and lifecycle management automatically

Client-facing production also requires deploying `edge-control-plane/` so Cloudflare remains the only supported public ingress.

## Sandbox Configuration Summary

| Setting | Agent SDK Sandbox |
|---------|-------------------|
| Name | `svc-runner-8001` |
| Port | 8001 |
| Volume | `svc-runner-8001-vol` |
| Mount | `/data` |
| Timeout | 24h |
| Idle Timeout | 10m |
| CPU | 1.0 |
| Memory | 2048 MB |
| User | root |

## Related Documentation

- [Controllers Deep Dive](./controllers-background-service.md) - Detailed explanation of the controller service
- [Modal Ingress](./modal-ingress.md) - How Modal handles HTTP ingress
- [Configuration](../references/configuration.md) - Configuration options and settings
- [API Usage](../references/api-usage.md) - Complete API reference
