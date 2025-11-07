# Architecture Overview

This document explains the overall architecture of the agent sandbox application, focusing on the relationship between Modal's ingress layer and the long-lived background service.

## High-Level Architecture

The application uses a **two-tier architecture pattern** optimized for low latency and resource efficiency:

```
┌─────────────────────────────────────────────────────────────┐
│                    Modal Infrastructure                      │
│  (Handles TLS termination, routing, load balancing)          │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            │ HTTPS Request
                            │ (e.g., POST /query)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              http_app (Modal ASGI Function)                  │
│  - Lightweight FastAPI app                                  │
│  - Decorated with @modal.asgi_app()                         │
│  - Receives all incoming HTTP traffic                        │
│  - Proxies requests to background service                   │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            │ HTTP Request via Encrypted Tunnel
                            │ (Modal Sandbox tunnel discovery)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│         Background Sandbox (Long-lived Process)              │
│  - modal.Sandbox running uvicorn                            │
│  - FastAPI service: agent_sandbox.controllers.controller     │
│  - Hosts Claude Agent SDK client                            │
│  - Maintains warm state for low latency                      │
│  - Persistent volume mounted at /data                       │
└─────────────────────────────────────────────────────────────┘
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
- Lightweight proxy that forwards requests to the background service
- Handles Modal Connect token generation (optional)
- Manages sandbox lifecycle (creates/reuses background sandbox)

**Key code:**
```python
@app.function(image=agent_sdk_image, secrets=agent_sdk_secrets)
@modal.asgi_app()
def http_app():
    """ASGI app exposing HTTP endpoints for the agent service."""
    return web_app
```

**Endpoints:**
- `GET /health` - Health check for the ingress layer
- `POST /query` - Proxies to background service `/query` endpoint
- `POST /query_stream` - Proxies to background service `/query_stream` endpoint
- `GET /service_info` - Returns information about the background sandbox

**Why it's lightweight:**
- Doesn't run the Claude Agent SDK directly
- Doesn't maintain long-lived connections to Anthropic
- Simply forwards requests and returns responses
- Can scale independently from the background service

### 3. Background Sandbox Service (Controller)

**Location:** `agent_sandbox/controllers/controller.py`

**What it does:**
- Runs the actual Claude Agent SDK client
- Executes agent queries and tool calls
- Maintains warm state (avoids cold-start latency)
- Handles MCP server connections
- Manages tool permissions and execution

**Key characteristics:**
- Long-lived process (runs for hours, not seconds)
- Runs inside a `modal.Sandbox` with encrypted ports
- Accessible only via Modal's tunnel discovery mechanism
- Has persistent volume mounted for file storage

**Endpoints:**
- `GET /health_check` - Liveness/readiness probe
- `POST /query` - Execute agent query (non-streaming)
- `POST /query_stream` - Execute agent query (streaming via SSE)

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
   - Background service handles agent execution
   - Clear boundaries between public API and internal service

4. **Scalability:**
   - `http_app` can scale independently (lightweight functions)
   - Background service can be shared or replicated as needed
   - Modal handles load balancing automatically

5. **Security:**
   - Background service runs in isolated sandbox
   - Encrypted ports prevent direct access
   - Modal Connect tokens provide per-request authentication
   - TLS termination handled by Modal infrastructure

### Trade-offs

1. **Complexity:**
   - Two services to manage instead of one
   - Requires tunnel discovery and health checking
   - More moving parts to debug

2. **Latency Overhead:**
   - Extra network hop (http_app → background service)
   - Typically < 50ms, but adds some overhead
   - Worth it for the cold-start savings

3. **State Management:**
   - Need to handle sandbox lifecycle (creation, reuse, cleanup)
   - Must track service URLs and health status
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
modal serve -m agent_sandbox.app
```

**Production:**
```bash
modal deploy -m agent_sandbox.deploy
```

Both commands:
1. Deploy `http_app` as a public HTTPS endpoint
2. Create background sandbox on first request (or via `get_or_start_background_sandbox()`)
3. Handle all routing and lifecycle management automatically

## Related Documentation

- [Controllers Deep Dive](./controllers.md) - Detailed explanation of the controller service
- [Modal Ingress](./modal-ingress.md) - How Modal handles HTTP ingress
- [Configuration](./configuration.md) - Configuration options and settings

