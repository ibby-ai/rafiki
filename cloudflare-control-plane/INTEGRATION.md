# Integration Mapping: Cloudflare DO ↔ Modal Backend

This document maps how Cloudflare Durable Objects integrate with the existing Modal backend, detailing which responsibilities move to DOs vs. stay in Modal.

## Responsibility Matrix

| Component                 | Current (Modal Only)          | Future (Cloudflare + Modal)               | Owner      |
| ------------------------- | ----------------------------- | ----------------------------------------- | ---------- |
| **HTTP Gateway**          | Modal `@modal.asgi_app()`     | Cloudflare Worker                         | Cloudflare |
| **Session State**         | Modal Dict + in-memory        | SessionAgent DO (SQLite)                  | Cloudflare |
| **Message History**       | In-memory during execution    | SessionAgent DO (persistent)              | Cloudflare |
| **Prompt Queue**          | Modal Dict (`PROMPT_QUEUE`)   | SessionAgent DO (SQLite)                  | Cloudflare |
| **WebSocket Connections** | N/A (SSE only)                | EventBus DO + SessionAgent DO             | Cloudflare |
| **Job Queue**             | Modal Queue (`JOB_QUEUE`)     | Modal Queue (unchanged)                   | Modal      |
| **Job Results**           | Modal Dict (`JOB_RESULTS`)    | Modal Dict (unchanged)                    | Modal      |
| **Sandbox Lifecycle**     | Modal functions               | Modal functions (unchanged)               | Modal      |
| **Volume Management**     | Modal Volume API              | Modal Volume API (unchanged)              | Modal      |
| **Agent Execution**       | Modal Sandbox + controller.py | Modal Sandbox + controller.py (unchanged) | Modal      |
| **Artifact Storage**      | Modal Volume                  | Modal Volume (unchanged)                  | Modal      |
| **Authentication**        | Optional Connect tokens       | Cloudflare Worker + signed tokens         | Cloudflare |
| **Rate Limiting**         | None                          | Cloudflare KV                             | Cloudflare |
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
                  ↓
               EventBus DO (broadcast updates)
```

**Changes:**

- **CF Worker**: Validates auth, resolves session ID, routes to SessionAgent DO
- **SessionAgent DO**:
  - Stores session state (status, current prompt)
  - Stores message history in SQLite
  - Forwards query to Modal backend
  - Broadcasts updates via EventBus DO
  - Returns response to client
- **Modal Backend**:
  - Receives authenticated request from DO
  - Executes agent query (unchanged)
  - Returns result to DO
- **EventBus DO**: Broadcasts session updates to subscribed WebSocket clients

**Modal Changes Required:**

- Add internal auth token validation middleware
- Accept requests from Cloudflare Worker IP range
- Return same response format (no breaking changes)

---

#### `/query_stream` (Streaming)

**Current Flow:**

```
Client → Modal Gateway → Modal Sandbox (SSE) → Client
```

**Target Flow:**

```
Client ←→ CF Worker (WS) ←→ SessionAgent DO (WS) → Modal Sandbox (SSE)
                                 ↓
                              EventBus DO (broadcast)
```

**Changes:**

- **CF Worker**: Accepts WebSocket upgrade and forwards to SessionAgent DO
- **SessionAgent DO**:
  - Accepts WebSocket connection from Worker
  - Calls Modal `/query_stream` SSE endpoint
  - Converts SSE events to WebSocket messages
  - Broadcasts to connected session WebSockets
  - Stores final messages in SQLite
- **Modal Backend**:
  - SSE endpoint unchanged
  - WebSocket remains internal-only

**Modal Changes Required:**

- None (SSE endpoint remains compatible)

---

### Job Endpoints

#### `/submit` (Job Submission)

**Current Flow:**

```
Client → Modal Gateway → Modal Queue (JOB_QUEUE) → Worker picks up
```

**Future Flow:**

```
Client → CF Worker → SessionAgent DO → Modal Queue (JOB_QUEUE) → Worker picks up
              ↓
           EventBus DO (notify job queued)
```

**Changes:**

- **CF Worker**: Validates auth, generates job ID, routes to SessionAgent DO
- **SessionAgent DO**:
  - Associates job with session
  - Stores job metadata in session state
  - Forwards to Modal backend for queueing
  - Broadcasts job queued event
- **Modal Backend**:
  - Enqueues job (unchanged)
  - Returns job ID confirmation
- **EventBus DO**: Notifies subscribed clients of job submission

**Modal Changes Required:**

- Accept job submissions from Cloudflare (with auth)
- Optional: Webhook back to Cloudflare for job updates

---

#### `/jobs/{job_id}` (Job Status)

**Current Flow:**

```
Client → Modal Gateway → Modal Dict (JOB_RESULTS) → Response
```

**Future Flow:**

```
Client → CF Worker → Modal Backend → Modal Dict (JOB_RESULTS) → Response
```

**Changes:**

- **CF Worker**: Proxies directly to Modal backend (no DO involvement)
- **Modal Backend**: Returns job status (unchanged)
- **SessionAgent DO**: Not involved (job status is read-only, no state change)

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

**Before (Modal Dict):**

```python
# agent_sandbox/controllers/controller.py
SESSION_STORE = modal.Dict.from_name("session-store-dict")

# Store session_key → session_id mapping
await SESSION_STORE.put.aio(session_key, session_id)
```

**After (SessionAgent DO):**

```sql
-- Cloudflare DO SQLite
CREATE TABLE session_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

INSERT INTO session_metadata (key, value) VALUES ('session_id', 'sess_abc123');
INSERT INTO session_metadata (key, value) VALUES ('session_key', 'user-123-session');
```

**Migration Strategy:**

1. Phase 1: Dual-write to both Modal Dict and DO SQLite
2. Phase 2: Read from DO, fallback to Modal Dict
3. Phase 3: Stop writing to Modal Dict
4. Phase 4: Remove Modal Dict entirely

---

### Prompt Queue

**Before (Modal Dict):**

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

**Migration Strategy:**

1. Phase 1: New sessions use DO queue, existing sessions use Modal Dict
2. Phase 2: Migrate active sessions to DO (background job)
3. Phase 3: Remove Modal Dict queue code

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
     │ - result
     │ - done
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

### Phase 0: Preparation (Week 1)

- [ ] Deploy Cloudflare Worker infrastructure
- [ ] Add internal auth middleware to Modal
- [ ] Test authentication flow
- [ ] Document API endpoints
- [ ] Create monitoring dashboards

### Phase 1: Read-Only Integration (Week 2)

- [ ] Route `/health` through Cloudflare
- [ ] Route `/jobs/{id}` reads through Cloudflare
- [ ] Verify metrics and latency
- [ ] Test error handling and fallback

### Phase 2: Query Integration (Week 3-4)

- [ ] Route `/query` through Cloudflare
- [ ] Enable SessionAgent DO for new sessions
- [ ] Test session resumption
- [ ] Monitor DO performance and costs

### Phase 3: Streaming Integration (Week 5-6)

- [ ] Route `/query_stream` through Cloudflare
- [ ] Enable WebSocket → SSE bridging
- [ ] Test with multiple clients
- [ ] Optimize message throughput

### Phase 4: Job Integration (Week 7-8)

- [ ] Route `/submit` through Cloudflare
- [ ] Enable EventBus DO for notifications
- [ ] Test webhook callbacks
- [ ] Monitor job queue health

### Phase 5: Full Migration (Week 9-10)

- [ ] Route 100% traffic to Cloudflare
- [ ] Remove Modal gateway code
- [ ] Archive old configuration
- [ ] Update documentation
- [ ] Celebrate! 🎉

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
