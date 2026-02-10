# Cloudflare Control Plane API Documentation

This document describes the API contracts for the Cloudflare Worker + Durable Objects control plane.

## Architecture Overview

```
Client → Cloudflare Worker → SessionAgent DO → Modal Backend → Sandbox
                           ↓
                        EventBus DO (real-time fan-out)
```

## Authentication

All public endpoints (everything except `GET /health`) require an
`Authorization: Bearer <token>` header. For WebSockets, the token may also be
passed as a `token` query parameter. Phase 3 supports **session tokens only**,
signed with `SESSION_SIGNING_SECRET`.

```http
Authorization: Bearer <token>
```

The Worker/DOs generate internal auth tokens (`X-Internal-Auth`) for Modal backend requests.

## REST API Endpoints

### Health Check

**GET** `/health`

Returns service health status.

**Response:**

```json
{
  "ok": true,
  "service": "edge-control-plane"
}
```

---

### Query (Synchronous)

**POST** `/query`

Execute an agent query and return the complete response.

**Request Body:**

```json
{
  "question": "What is the capital of Canada?",
  "agent_type": "default",
  "session_id": "sess_abc123",
  "session_key": "user-123-session",
  "fork_session": false,
  "job_id": "job-uuid",
  "user_id": "user-123",
  "warm_id": "warm-uuid"
}
```

**Fields:**

- `question` (required): The query text
- `agent_type` (optional): Agent type to use (default: "default")
- `session_id` (optional): Existing session ID to resume
- `session_key` (optional): Stable client key resolved via KV to a session_id
- `fork_session` (optional): Fork from existing session (default: false)
- `job_id` (optional): Associate with a job workspace
- `user_id` (optional): User identifier for statistics
- `warm_id` (optional): Pre-warm correlation ID

**Session resolution (current behavior):**

1. Use explicit `session_id` when provided
2. Resolve `session_key` via `SESSION_CACHE` using a scoped key:
   `session_key:<scope>:<session_key>` (scope = `tenant_id` → `user_id` → `anonymous`)
3. Create a new `session_id` and persist the mapping in KV (default TTL: 30 days,
   configurable via `SESSION_KEY_TTL_SECONDS`)

**Response:**

```json
{
  "ok": true,
  "session_id": "sess_abc123",
  "messages": [
    {
      "role": "user",
      "content": [{ "type": "text", "text": "What is the capital of Canada?" }]
    },
    {
      "role": "assistant",
      "content": [
        { "type": "text", "text": "The capital of Canada is Ottawa." }
      ]
    }
  ]
}
```

---

### Query (Streaming)

**GET** `/query_stream` (WebSocket upgrade)

Execute an agent query with real-time streaming via WebSocket.

If the request is not a WebSocket upgrade, the Worker returns `426 Upgrade Required`.

**Request:** Send the query as the first WebSocket message:

```json
{
  "question": "What is the capital of Canada?",
  "session_id": "sess_abc123"
}
```

**Recommended:** Include `session_id` as a query string parameter for session resume
and pass auth via `Authorization` header or `token` query param:
`/query_stream?session_id=sess_abc123&token=<session_token>`

**WebSocket Messages (Server → Client):**

```json
{
  "type": "connection_ack",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "status": "idle"
  }
}
```

```json
{
  "type": "session_update",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "status": "executing",
    "current_prompt": "What is the capital of Canada?"
  }
}
```

```json
{
  "type": "assistant_message",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "content": "The capital of Canada is Ottawa.",
    "partial": false
  }
}
```

Note: `assistant_message.data.content` is plain text extracted from assistant content blocks.
`assistant_message.data.partial` is always `false` in the current implementation.

System/result/unknown SSE events are forwarded as `execution_state` messages.

```json
{
  "type": "query_complete",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "messages": [...],
    "duration_ms": 1234,
    "summary": {
      "text": "The capital of Canada is Ottawa.",
      "is_complete": true
    }
  }
}
```

**WebSocket Messages (Client → Server):**

```json
{
  "type": "ping",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {}
}
```

Stop current execution:

```json
{
  "type": "stop",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {}
}
```

---

### Submit Job

**POST** `/submit`

Submit a job for background processing.

**Request Body:**

```json
{
  "question": "Analyze this dataset...",
  "agent_type": "default",
  "session_id": "sess_abc123",
  "session_key": "user-123-session",
  "job_id": "job-uuid",
  "user_id": "user-123",
  "tenant_id": "tenant-456",
  "schedule_at": 1234567890,
  "webhook": {
    "url": "https://example.com/webhook",
    "headers": { "X-Custom": "value" },
    "signing_secret": "secret",
    "timeout_seconds": 15,
    "max_attempts": 5
  }
}
```

**Response:**

```json
{
  "ok": true,
  "job_id": "job-uuid"
}
```

---

### Job Status

**GET** `/jobs/{job_id}`

Get job status and result.

**Response:**

```json
{
  "job_id": "job-uuid",
  "status": "complete",
  "created_at": 1234567890,
  "started_at": 1234567891,
  "completed_at": 1234567895,
  "question": "Analyze this dataset...",
  "agent_type": "default",
  "session_id": "sess_abc123",
  "user_id": "user-123",
  "tenant_id": "tenant-456",
  "result": {
    "ok": true,
    "session_id": "sess_abc123",
    "messages": [...]
  },
  "artifacts": {
    "job_id": "job-uuid",
    "workspace_path": "/data/jobs/job-uuid",
    "files": [
      {
        "path": "output.txt",
        "size_bytes": 1024,
        "modified_at": 1234567895,
        "mime_type": "text/plain"
      }
    ],
    "total_size_bytes": 1024,
    "collected_at": 1234567895
  }
}
```

---

### Job Artifacts

**GET** `/jobs/{job_id}/artifacts`

List job artifacts.

**GET** `/jobs/{job_id}/artifacts/{path}`

Download specific artifact.

---

### Cancel Job

**DELETE** `/jobs/{job_id}`

Cancel a queued or running job.

---

### Schedules API

Schedule CRUD + manual dispatch are available through the public Cloudflare Worker API.
Authentication uses the same `Authorization: Bearer <session_token>` flow as other endpoints.
The Worker derives `user_id` and `tenant_id` from the token and forwards them to Modal for scope isolation.

#### Create Schedule

**POST** `/schedules`

Create a one-off or cron schedule.

**Request Body (one-off):**

```json
{
  "name": "daily summary once",
  "question": "send me yesterday's summary",
  "schedule_type": "one_off",
  "run_at": 1770729600,
  "timezone": "UTC",
  "enabled": true
}
```

**Request Body (cron):**

```json
{
  "name": "hourly heartbeat",
  "question": "status check",
  "schedule_type": "cron",
  "cron": "0 * * * *",
  "timezone": "America/New_York",
  "enabled": true
}
```

**Response:**

```json
{
  "schedule_id": "567d797d-4e9c-4b5d-a04f-10962ac16cf7",
  "name": "hourly heartbeat",
  "question": "status check",
  "agent_type": null,
  "schedule_type": "cron",
  "run_at": null,
  "cron": "0 * * * *",
  "timezone": "America/New_York",
  "enabled": true,
  "webhook": null,
  "metadata": null,
  "user_id": "user-123",
  "tenant_id": "tenant-456",
  "created_at": 1770726169,
  "updated_at": 1770726169,
  "last_run_at": null,
  "next_run_at": 1770729600,
  "last_job_id": null,
  "last_error": null
}
```

Validation errors return `400` with details, for example:

```json
{ "detail": "run_at is required for one_off schedules" }
```

```json
{ "detail": "cron is required for cron schedules" }
```

#### List Schedules

**GET** `/schedules`

Optional query parameters:
- `enabled=true|false`
- `schedule_type=one_off|cron`

Example:

```http
GET /schedules?enabled=true&schedule_type=cron
```

**Response:**

```json
{
  "ok": true,
  "schedules": [
    {
      "schedule_id": "567d797d-4e9c-4b5d-a04f-10962ac16cf7",
      "name": "hourly heartbeat",
      "question": "status check",
      "schedule_type": "cron",
      "cron": "0 * * * *",
      "timezone": "America/New_York",
      "enabled": true,
      "user_id": "user-123",
      "tenant_id": "tenant-456",
      "next_run_at": 1770729600
    }
  ]
}
```

#### Get Schedule

**GET** `/schedules/{schedule_id}`

**Response:**

```json
{
  "schedule_id": "567d797d-4e9c-4b5d-a04f-10962ac16cf7",
  "name": "hourly heartbeat",
  "question": "status check",
  "schedule_type": "cron",
  "cron": "0 * * * *",
  "timezone": "America/New_York",
  "enabled": true,
  "next_run_at": 1770729600
}
```

Errors:
- `400` for malformed IDs (`{"detail":"Invalid schedule_id"}`)
- `404` for unknown/inaccessible schedules (`{"detail":"Schedule not found"}`)

#### Update Schedule

**PATCH** `/schedules/{schedule_id}`

Partial update example:

```json
{
  "enabled": false
}
```

**Response:**

```json
{
  "schedule_id": "567d797d-4e9c-4b5d-a04f-10962ac16cf7",
  "enabled": false,
  "next_run_at": null,
  "updated_at": 1770727000
}
```

Re-enabling a schedule recomputes `next_run_at`.

#### Delete Schedule

**DELETE** `/schedules/{schedule_id}`

**Response:**

```json
{
  "ok": true,
  "schedule_id": "567d797d-4e9c-4b5d-a04f-10962ac16cf7",
  "deleted": true
}
```

#### Manual Dispatch

**POST** `/schedules/dispatch`

Immediately scans and dispatches due schedules.

**Response:**

```json
{
  "scanned": 14,
  "dispatched": 1,
  "failed": 0
}
```

For dispatched schedules, resulting jobs include metadata keys:
- `schedule_id`
- `schedule_name`
- `triggered_at`

---

### Session State

**GET** `/session/{session_id}/state`

Get current session state.

**Response:**

```json
{
  "ok": true,
  "state": {
    "session_id": "sess_abc123",
    "session_key": "user-123-session",
    "user_id": "user-123",
    "tenant_id": "tenant-456",
    "created_at": 1234567890,
    "last_active_at": 1234567895,
    "status": "idle",
    "modal_sandbox_id": "sandbox-123",
    "modal_sandbox_url": "https://sandbox.modal.run"
  }
}
```

---

### Session Messages

**GET** `/session/{session_id}/messages`

Get all messages in the session.

**Response:**

```json
{
  "ok": true,
  "messages": [
    {
      "id": "msg-uuid",
      "session_id": "sess_abc123",
      "role": "user",
      "content": [...],
      "created_at": 1234567890
    }
  ]
}
```

---

### Stop Session

**POST** `/session/{session_id}/stop`

Stop current execution.

**Response:**

```json
{
  "ok": true
}
```

---

### Session Prompt Queue

Queue follow-up prompts for sequential processing. These endpoints are served by
the SessionAgent Durable Object (Cloudflare) — they no longer exist on the Modal
gateway.

**GET** `/session/{session_id}/queue`

List queued prompts.

**POST** `/session/{session_id}/queue`

Queue a new prompt.

**Request Body:**

```json
{
  "question": "Follow-up question",
  "agent_type": "default",
  "user_id": "user-123"
}
```

**DELETE** `/session/{session_id}/queue`

Clear the entire queue.

**DELETE** `/session/{session_id}/queue/{prompt_id}`

Remove a specific queued prompt.

**Response (GET example):**

```json
{
  "ok": true,
  "session_id": "sess_abc123",
  "is_executing": false,
  "queue_size": 1,
  "max_queue_size": 10,
  "prompts": [
    {
      "prompt_id": "prompt-uuid",
      "question": "Follow-up question",
      "user_id": "user-123",
      "queued_at": 1234567890,
      "expires_at": 1234571490,
      "position": 1
    }
  ]
}
```

**Notes:**

- Queue limits and expiry are configurable via `MAX_QUEUED_PROMPTS_PER_SESSION`
  and `PROMPT_QUEUE_ENTRY_EXPIRY_SECONDS`.
- After a non-streaming `/query` finishes, the SessionAgent drains queued prompts
  sequentially.

---

## WebSocket Event Bus

### Connect to Event Bus

**WebSocket** `/ws` or `/events`

Connect to the event bus for multi-session real-time updates.

Authentication is required via `Authorization: Bearer <token>` header or
`token=<session_token>` query parameter.

**Query Parameters:**

- `user_id`: User identifier for filtering
- `tenant_id`: Tenant identifier for scoping
- `session_id`: Single session ID to subscribe to
- `session_ids`: Comma-separated list of session IDs to subscribe to

**Example:**

```
wss://worker.example.com/ws?user_id=user-123&session_ids=sess_abc,sess_def&token=<session_token>
```

**Messages (Server → Client):**

Connection acknowledgment:

```json
{
  "type": "connection_ack",
  "session_id": "",
  "timestamp": 1234567890000,
  "data": {
    "connection_id": "conn-uuid",
    "session_ids": ["sess_abc", "sess_def"]
  }
}
```

Session updates (broadcasted to all subscribed connections):

```json
{
  "type": "session_update",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "status": "executing",
    "current_prompt": "Query text",
    "queue_length": 2
  }
}
```

Presence updates:

```json
{
  "type": "presence_update",
  "session_id": "",
  "timestamp": 1234567890000,
  "data": {
    "users_online": ["user-123", "user-456"],
    "connection_count": 4,
    "session_ids": ["sess_abc", "sess_def"],
    "user_joined": "user-456"
  }
}
```

Job events:

```json
{
  "type": "job_submitted",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "job_id": "job-uuid",
    "status": "queued",
    "user_id": "user-123",
    "tenant_id": "tenant-456"
  }
}
```

```json
{
  "type": "job_status",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "job_id": "job-uuid",
    "status": "running",
    "payload": {
      "job_id": "job-uuid",
      "status": "running"
    }
  }
}
```

**Messages (Client → Server):**

Subscribe to session:

```json
{
  "type": "subscribe_session",
  "session_id": "",
  "timestamp": 1234567890000,
  "data": {
    "session_id": "sess_xyz"
  }
}
```

Unsubscribe from session:

```json
{
  "type": "unsubscribe_session",
  "session_id": "",
  "timestamp": 1234567890000,
  "data": {
    "session_id": "sess_xyz"
  }
}
```

Ping/pong:

```json
{
  "type": "ping",
  "session_id": "",
  "timestamp": 1234567890000,
  "data": {}
}
```

---

## Durable Object Data Models

### SessionAgent DO

**SQLite Schema:**

```sql
-- Session metadata
CREATE TABLE session_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Messages
CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

-- Prompt queue
CREATE TABLE prompt_queue (
  id TEXT PRIMARY KEY,
  question TEXT NOT NULL,
  agent_type TEXT NOT NULL,
  user_id TEXT,
  queued_at INTEGER NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0
);

-- Execution state
CREATE TABLE execution_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
```

**Session Metadata Keys:**

- `session_id`: Unique session identifier
- `session_key`: Client-provided session key
- `user_id`: User identifier
- `tenant_id`: Tenant identifier
- `created_at`: Unix timestamp of session creation
- `last_active_at`: Unix timestamp of last activity
- `status`: Session status (idle, executing, waiting_approval, error)
- `current_prompt`: Currently executing prompt
- `modal_sandbox_id`: Associated Modal sandbox ID
- `modal_sandbox_url`: Modal sandbox tunnel URL

---

### EventBus DO

**Durable Storage:**

```typescript
{
  "connections": {
    "conn-uuid": {
      "connection_id": "conn-uuid",
      "user_id": "user-123",
      "tenant_id": "tenant-456",
      "session_ids": ["sess_abc", "sess_def"],
      "connected_at": 1234567890000,
      "last_ping_at": 1234567895000
    }
  }
}
```

---

## Modal Backend Integration

### Internal Authentication

The Worker generates signed tokens for Modal backend requests:

**Token Format:**

```
<base64(payload bytes)>.<base64(signature bytes)>
```

**Payload:**

```json
{
  "service": "cloudflare-worker",
  "issued_at": 1234567890000,
  "expires_at": 1234567890000
}
```

**Signature:** HMAC-SHA256 of payload using `INTERNAL_AUTH_SECRET`
**TTL:** 5 minutes

The token is sent as `X-Internal-Auth` (raw value, no `Bearer` prefix).

### Modal Backend Endpoints

The Worker forwards requests to these **internal** Modal endpoints (all require `X-Internal-Auth`):

- `POST /query` - Execute agent query
- `POST /query_stream` - Streaming query (SSE)
- `POST /submit` - Submit job
- `GET /jobs/{job_id}` - Job status
- `GET /jobs/{job_id}/artifacts` - List artifacts
- `GET /jobs/{job_id}/artifacts/{path}` - Download artifact
- `DELETE /jobs/{job_id}` - Cancel job
- `POST /session/{session_id}/stop` - Stop session

---

## Error Handling

All errors return JSON with this format:

```json
{
  "ok": false,
  "error": "Error message"
}
```

**Common HTTP Status Codes:**

- `200` - Success
- `400` - Bad request (invalid input)
- `401` - Unauthorized (missing/invalid token)
- `404` - Not found (session/job not found)
- `500` - Internal server error

---

## Rate Limiting

Edge rate limits are enforced using `SESSION_CACHE` counters:

- **Query endpoints**: 60 requests/minute per user
- **WebSocket connections**: 10 connections/minute per user
- **Job submissions**: 100 requests/hour per user

When exceeded, the Worker returns `429 Too Many Requests` with a JSON error body.

---

## Deployment

### Setup Secrets

```bash
# Modal API credentials
# wrangler secret put MODAL_TOKEN_ID
# wrangler secret put MODAL_TOKEN_SECRET

# Internal signing secrets
wrangler secret put INTERNAL_AUTH_SECRET
wrangler secret put SESSION_SIGNING_SECRET
```

### Deploy

```bash
# Development
wrangler dev

# Production
wrangler deploy
```

### Test

```bash
# Health check
curl https://worker.example.com/health

# Query
curl -X POST https://worker.example.com/query \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the capital of Canada?"}'

# WebSocket (use wscat or similar)
wscat -c "wss://worker.example.com/ws?user_id=user-123&session_ids=sess_abc"
```
