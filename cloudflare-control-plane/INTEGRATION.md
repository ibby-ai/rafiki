# Integration Mapping: Cloudflare DO ↔ Modal Backend

This document maps how Cloudflare Durable Objects integrate with the existing Modal backend, detailing which responsibilities move to DOs vs. stay in Modal.

## Responsibility Matrix

| Component                 | Current (Modal Only)          | Future (Cloudflare + Modal)               | Owner      |
| ------------------------- | ----------------------------- | ----------------------------------------- | ---------- |
| **HTTP Gateway**          | Modal `@modal.asgi_app()`     | Cloudflare Worker                         | Cloudflare |
| **Session State**         | Modal Dict + in-memory        | SessionAgent DO (SQLite)                  | Cloudflare |
| **Message History**       | In-memory during execution    | SessionAgent DO (persistent)              | Cloudflare |
| **Prompt Queue**          | Deprecated (Modal Dict removed) | SessionAgent DO (SQLite)                | Cloudflare |
| **WebSocket Connections** | N/A (SSE only)                | EventBus DO + SessionAgent DO             | Cloudflare |
| **Job Queue**             | Modal Queue (`JOB_QUEUE`)     | Modal Queue (unchanged)                   | Modal      |
| **Job Results**           | Modal Dict (`JOB_RESULTS`)    | Modal Dict (unchanged)                    | Modal      |
| **Sandbox Lifecycle**     | Modal functions               | Modal functions (unchanged)               | Modal      |
| **Volume Management**     | Modal Volume API              | Modal Volume API (unchanged)              | Modal      |
| **Agent Execution**       | Modal Sandbox + controller.py | Modal Sandbox + controller.py (unchanged) | Modal      |
| **Artifact Storage**      | Modal Volume                  | Modal Volume (unchanged)                  | Modal      |
| **Authentication**        | Optional Connect tokens       | Cloudflare Worker (session tokens) + signed tokens (internal) | Cloudflare |
| **Rate Limiting**         | None                          | Cloudflare Rate Limiting binding          | Cloudflare |
| **Real-time Fan-out**     | N/A                           | EventBus DO                               | Cloudflare |

---

## Endpoint Mapping

### Query Endpoints

#### `/query` (Synchronous)

**Current Flow:**

```
Client → Modal Gateway → Modal Sandbox (controller.py) → Agent SDK
```

**Future Flow:**

```
Client → CF Worker → SessionAgent DO → Modal Sandbox (controller.py) → Agent SDK
```

**Changes:**

- **CF Worker**: Validates client auth, resolves session ID (`session_id` → scoped KV lookup for `session_key` → `randomUUID()`), routes to SessionAgent DO
- **SessionAgent DO**:
  - Stores session state (status, current prompt)
  - Stores message history in SQLite
  - Forwards query to Modal backend
  - Returns response to client
- **Modal Backend**:
  - Receives authenticated request from DO
  - Executes agent query (unchanged)
  - Returns result to DO

**Modal Changes Required:**

- Add internal auth token validation middleware
- Accept requests from Cloudflare Worker IP range
- Return same response format (no breaking changes)

---

#### `/query_stream` (Streaming)

**Current Flow (Cloudflare WS):**

```
Client ←→ CF Worker (WS) ←→ SessionAgent DO (WS) → Modal Sandbox (SSE)
                                 ↓
                              EventBus DO (broadcast)
```

**Internal-Only Flow (Modal SSE):**

```
Client → Modal Gateway → Modal Sandbox (SSE) → Client
```

**Notes:**

- **CF Worker**: Accepts WebSocket upgrade and forwards to SessionAgent DO.
- **SessionAgent DO**:
  - Accepts WebSocket connection from Worker.
  - Calls Modal `/query_stream` SSE endpoint.
  - Converts SSE events to WebSocket messages.
  - Broadcasts to EventBus DO.
  - Stores final messages in SQLite.
- **Modal Backend**: SSE endpoint unchanged; WebSocket remains internal-only.

---

#### `/ws` or `/events` (EventBus WebSocket)

- WebSocket upgrade endpoint for real-time fan-out.
- Alias endpoints: `/ws` and `/events`.
- Query params: `user_id`, `tenant_id`, `session_ids` (comma-separated).

### Job Endpoints

#### `/submit` (Job Submission)

**Current Flow:**

```
Client → CF Worker → Modal Queue (JOB_QUEUE) → Worker picks up
```

**Changes:**

- **CF Worker**: Generates job ID and forwards to Modal backend
- **Modal Backend**: Enqueues job (unchanged) and returns job ID confirmation
- **EventBus DO**: Receives `job_submitted` event broadcast for the session/user scope

---

#### `/jobs/{job_id}` (Job Status)

**Current Flow:**

```
Client → CF Worker → Modal Backend → Modal Dict (JOB_RESULTS) → Response
```

**Changes:**

- **CF Worker**: Proxies directly to Modal backend
- **Modal Backend**: Returns job status (unchanged)
- **EventBus DO**: Emits `job_status` events on successful status reads

**Modal Changes Required:**

- Accept authenticated requests from Cloudflare

---

#### `/jobs/{job_id}/artifacts` (Artifacts)

**Current Flow:**

```
Client → Modal Gateway → Modal Volume (read) → Response
```

**Future Flow:**

```
Client → CF Worker → Modal Backend → Modal Volume (read) → Response
```

**Changes:**

- **CF Worker**: Proxies to Modal backend
- **Modal Backend**: Serves artifacts from volume (unchanged)

**Modal Changes Required:**

- None

---

### Session Endpoints

#### `/session/{session_id}/stop` (Stop Execution)

**Current Flow:**

```
Client → Modal Gateway → Modal Sandbox (controller.py) → Stop agent
```

**Future Flow:**

```
Client → CF Worker → SessionAgent DO → Modal Sandbox → Stop agent
              ↓
           EventBus DO (broadcast stopped event)
```

**Changes:**

- **CF Worker**: Routes to SessionAgent DO
- **SessionAgent DO**:
  - Updates session state to "idle"
  - Clears current prompt
  - Forwards stop request to Modal
  - Broadcasts stopped event
- **Modal Backend**: Stops agent execution (unchanged)
- **EventBus DO**: Notifies subscribed clients

**Modal Changes Required:**

- None

---

#### `/session/{session_id}/state` (Get State) [NEW]

**Current Flow:**

```
N/A (session state was ephemeral or in Modal Dict)
```

**Future Flow:**

```
Client → CF Worker → SessionAgent DO → Response
```

**Changes:**

- **SessionAgent DO**: Returns session state from SQLite
- **Modal Backend**: Not involved

**Modal Changes Required:**

- None

---

#### `/session/{session_id}/messages` (Get Messages) [NEW]

**Current Flow:**

```
N/A (messages not persisted)
```

**Future Flow:**

```
Client → CF Worker → SessionAgent DO → Response
```

**Changes:**

- **SessionAgent DO**: Returns message history from SQLite
- **Modal Backend**: Not involved

**Modal Changes Required:**

- None

---

## State Migration

### Session State

**Legacy (Modal Dict - Removed in Phase 3):**

Modal previously stored `session_key` → `session_id` in a Dict. This mapping is
now handled by Cloudflare KV at the edge.

**Current (Phase 3):**

- **Session metadata** lives in SessionAgent DO SQLite.
- **Session key mappings** are cached in `SESSION_CACHE` KV using
  `session_key:<scope>:<session_key>` keys (scope = tenant → user → anonymous).

---

### Prompt Queue

**Legacy (Modal Dict - Removed in Phase 3):**

```python
# agent_sandbox/jobs.py
PROMPT_QUEUE = modal.Dict.from_name("prompt-queue-dict")

# Queue structure: session_id → [prompt1, prompt2, ...]
queue = await PROMPT_QUEUE.get.aio(session_id, default=[])
queue.append({"question": "...", "priority": 0})
await PROMPT_QUEUE.put.aio(session_id, queue)
```

**After (SessionAgent DO):**

```sql
-- Cloudflare DO SQLite
CREATE TABLE prompt_queue (
  id TEXT PRIMARY KEY,
  question TEXT NOT NULL,
  agent_type TEXT NOT NULL,
  user_id TEXT,
  queued_at INTEGER NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0
);

INSERT INTO prompt_queue (id, question, agent_type, queued_at, priority)
VALUES ('prompt-uuid', 'Query text', 'default', 1234567890, 0);
```

**Phase 3 Status:**

- Prompt queues live exclusively in SessionAgent DO SQLite.
- Modal Dict prompt queues are removed.

---

### Message History

**Before (In-memory):**

```python
# agent_sandbox/controllers/controller.py
# Messages only exist during request, not persisted
messages = await client.query(question)
return {"messages": messages}  # Lost after response
```

**After (SessionAgent DO):**

```sql
-- Cloudflare DO SQLite
CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

-- Messages persisted across requests
INSERT INTO messages (id, role, content, created_at)
VALUES ('msg-uuid', 'user', '[{"type":"text","text":"..."}]', 1234567890);
```

**Migration Strategy:**

- No migration needed (new feature)
- Historical messages lost (expected)

---

## Modal Backend API Changes

### Add Internal Auth Middleware

All non-health endpoints must include `X-Internal-Auth`.

**File:** `agent_sandbox/middleware/cloudflare_auth.py` (new)

```python
"""Internal authentication middleware for Cloudflare Worker requests."""
import os
import hmac
import hashlib
import json
import base64
import time
from fastapi import HTTPException, Header, Request
from typing import Optional

def verify_internal_token(raw_token: str) -> dict:
    """Verify internal auth token from Cloudflare Worker."""
    parts = raw_token.split(".")
    if len(parts) != 2:
        raise HTTPException(401, "Invalid token format")

    payload_b64, signature_b64 = parts

    # Decode payload
    try:
        payload_bytes = base64.b64decode(payload_b64, validate=True)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as e:
        raise HTTPException(401, f"Invalid token payload: {e}")

    # Check service
    if payload.get("service") != "cloudflare-worker":
        raise HTTPException(401, "Invalid service")

    issued_at = int(payload.get("issued_at", 0))
    expires_at = int(payload.get("expires_at", 0))
    now_ms = int(time.time() * 1000)

    if issued_at > now_ms + 60_000:
        raise HTTPException(401, "Token issued in the future")
    if expires_at < now_ms - 60_000:
        raise HTTPException(401, "Token expired")
    if expires_at < issued_at:
        raise HTTPException(401, "Invalid token timestamps")

    # Verify signature
    secret = os.environ.get("INTERNAL_AUTH_SECRET")
    if not secret:
        raise HTTPException(500, "Internal auth secret not configured")

    signature_bytes = base64.b64decode(signature_b64, validate=True)
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256
    ).digest()

    # Use constant-time comparison
    if not hmac.compare_digest(expected_sig, signature_bytes):
        raise HTTPException(401, "Invalid signature")

    return payload

async def internal_auth_middleware(request: Request, call_next):
    """Middleware to verify internal auth for all Cloudflare requests."""
    if request.method == "OPTIONS":
        return await call_next(request)
    if request.url.path in {"/health", "/health_check"}:
        return await call_next(request)
    # Require X-Internal-Auth on all non-health endpoints
    internal_auth_header = request.headers.get("X-Internal-Auth")
    if not internal_auth_header:
        return Response(
            content=json.dumps({"ok": False, "error": "Missing internal auth token"}),
            status_code=401,
            media_type="application/json"
        )
    try:
        verify_internal_token(internal_auth_header)
    except HTTPException as e:
        return Response(
            content=json.dumps({"ok": False, "error": e.detail}),
            status_code=e.status_code,
            media_type="application/json"
        )

    response = await call_next(request)
    return response
```

### Update FastAPI App

**File:** `agent_sandbox/controllers/controller.py`

```python
from agent_sandbox.middleware.cloudflare_auth import internal_auth_middleware

# Add middleware
app.middleware("http")(internal_auth_middleware)

# Endpoints remain unchanged
@app.post("/query")
async def query_agent(body: QueryBody, request: Request):
    # Existing logic unchanged
    # Token already verified by middleware
    ...
```

### Add to Modal Secrets

```bash
modal secret create internal-auth-secret \
  INTERNAL_AUTH_SECRET="<same-secret-as-cloudflare>"
```

**File:** `agent_sandbox/config/settings.py`

```python
class Settings(BaseSettings):
    # ... existing settings ...

    # Internal auth secret for Cloudflare Worker (required)
    internal_auth_secret: str | None = None

    def get_modal_secrets(self) -> list[modal.Secret]:
        secrets = [
            modal.Secret.from_name("anthropic-secret"),
        ]

        secrets.append(modal.Secret.from_name("internal-auth-secret"))

        return secrets
```

---

## Data Flow Diagrams

### Query Execution Flow

```
┌──────────┐
│  Client  │
└────┬─────┘
     │ POST /query
     │ {"question": "..."}
     ▼
┌──────────────────┐
│ Cloudflare Worker│
│ - Validate token │
│ - Resolve session│
└────┬─────────────┘
     │
     ▼
┌──────────────────┐
│ SessionAgent DO  │
│ - Update state   │
│ - Store message  │
└────┬─────────────┘
     │ POST /query
     │ + Internal auth token
     ▼
┌──────────────────┐
│ Modal Backend    │
│ - Verify token   │
│ - Execute agent  │
└────┬─────────────┘
     │
     ▼
┌──────────────────┐
│ Modal Sandbox    │
│ - Agent SDK exec │
│ - Tool calls     │
└────┬─────────────┘
     │ Result
     ▼
┌──────────────────┐
│ SessionAgent DO  │
│ - Store messages │
│ - Update state   │
│ - Broadcast done │
└────┬─────────────┘
     │
     ├──────────────┬─────────────┐
     │              │             │
     ▼              ▼             ▼
┌─────────┐  ┌──────────┐  ┌──────────┐
│ Client  │  │EventBus  │  │ Other    │
│ (orig)  │  │   DO     │  │ Clients  │
└─────────┘  └────┬─────┘  └──────────┘
                  │ WebSocket
                  ▼
             [Subscribed Clients]
```

### Streaming Flow

```
┌──────────┐
│  Client  │
└────┬─────┘
     │ WebSocket /query_stream
     ▼
┌──────────────────┐
│ Cloudflare Worker│
│ - WS upgrade     │
└────┬─────────────┘
     │ WS proxy
     ▼
┌──────────────────┐
│ SessionAgent DO  │
│ - Accept WS      │
│ - Poll Modal SSE │
└────┬─────────────┘
     │ SSE /query_stream
     │ + Internal auth
     ▼
┌──────────────────┐
│ Modal Backend    │
│ - Stream events  │
└────┬─────────────┘
     │ SSE events:
     │ - assistant
     │ - tool_use
     │ - tool_result
     │ - system
     │ - result
     │ - done
     │ - error
     ▼
┌──────────────────┐
│ SessionAgent DO  │
│ - Convert to WS  │
│ - Store messages │
└────┬─────────────┘
     │ WS messages
     ├──────────────┬─────────────┐
     │              │             │
     ▼              ▼             ▼
┌─────────┐  ┌──────────┐  ┌──────────┐
│ Client  │  │EventBus  │  │ Other    │
│ (orig)  │  │   DO     │  │ Clients  │
└─────────┘  └──────────┘  └──────────┘
```

---

## Rollout Plan

**Status note:** In this repository, `/query`, `/query_stream`, `/submit`, and `/jobs/*` are already routed through the Cloudflare Worker.

### Phase 0-2: Preparation + Query Integration (Complete)

- [x] Deploy Cloudflare Worker infrastructure
- [x] Add internal auth middleware to Modal
- [x] Route `/health`, `/jobs/{id}`, `/query` through Cloudflare
- [x] Enable SessionAgent DO for new sessions
- [x] Document API endpoints and monitoring dashboards

### Phase 3: Streaming Integration (Complete - Cloudflare-first)

- [x] Route `/query_stream` through Cloudflare
- [x] Enable WebSocket → SSE bridging
- [x] Validate multiplayer fan-out

### Phase 4: Job Integration (Complete)

- [x] Route `/submit` through Cloudflare
- [x] EventBus notifications for job lifecycle events (`job_submitted`, `job_status`)
- [ ] Webhook delivery visibility in EventBus

### Phase 5: Optimization (Ongoing)

- [ ] Tune KV caching and rate limiting
- [ ] Optimize DO storage/hibernation costs

---

## Monitoring & Observability

### Key Metrics

**Cloudflare Worker:**

- Request latency (p50, p95, p99)
- Error rate by endpoint
- WebSocket connection count
- DO invocation count and duration
- KV read/write operations

**Modal Backend:**

- Request latency from Cloudflare
- Auth verification failures
- Sandbox execution time
- Volume I/O operations

**Durable Objects:**

- SQLite query performance
- WebSocket message throughput
- Storage usage per session
- Alarm execution time (EventBus cleanup)

### Logging

```typescript
// Cloudflare Worker
console.log({
  level: "info",
  event: "query_start",
  session_id: sessionId,
  user_id: userId,
  timestamp: Date.now(),
});

// Modal Backend
logger.info(
  "query_execution",
  (extra = {
    session_id: session_id,
    user_id: user_id,
    agent_type: agent_type,
    duration_ms: duration_ms,
  })
);
```

### Alerts

- Auth failure rate > 5% (1 min window)
- P99 latency > 10s (5 min window)
- DO error rate > 1% (1 min window)
- WebSocket disconnect rate > 10% (5 min window)
- Modal backend unavailable (immediate)
