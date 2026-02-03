# Cloudflare Control Plane API Documentation

This document describes the API contracts for the Cloudflare Worker + Durable Objects control plane.

## Architecture Overview

```
Client → Cloudflare Worker → SessionAgent DO → Modal Backend → Sandbox
                           ↓
                        EventBus DO (real-time fan-out)
```

## Authentication

All requests require authentication via Bearer token:

```http
Authorization: Bearer <token>
```

The Worker validates tokens and generates internal auth tokens for Modal backend requests.

## REST API Endpoints

### Health Check

**GET** `/health`

Returns service health status.

**Response:**

```json
{
  "ok": true,
  "service": "cloudflare-control-plane"
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
- `session_key` (optional): Client-provided session key (mapped to session_id)
- `fork_session` (optional): Fork from existing session (default: false)
- `job_id` (optional): Associate with a job workspace
- `user_id` (optional): User identifier for statistics
- `warm_id` (optional): Pre-warm correlation ID

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

**POST** `/query_stream` (WebSocket upgrade)

Execute an agent query with real-time streaming via WebSocket.

**Request:** Same as `/query` endpoint

**WebSocket Messages (Server → Client):**

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

```json
{
  "type": "query_complete",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "messages": [...],
    "duration_ms": 1234
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

## WebSocket Event Bus

### Connect to Event Bus

**WebSocket** `/ws` or `/events`

Connect to the event bus for multi-session real-time updates.

**Query Parameters:**

- `user_id`: User identifier for filtering
- `tenant_id`: Tenant identifier for scoping
- `session_ids`: Comma-separated list of session IDs to subscribe to

**Example:**

```
wss://worker.example.com/ws?user_id=user-123&session_ids=sess_abc,sess_def
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
<base64(payload)>.<base64(signature)>
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

### Modal Backend Endpoints

The Worker forwards requests to these Modal endpoints:

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

(To be implemented)

Recommended rate limits:

- Query endpoints: 60 requests/minute per user
- WebSocket connections: 10 connections per user
- Job submissions: 100 requests/hour per user

---

## Deployment

### Setup Secrets

```bash
# Modal API credentials
wrangler secret put MODAL_TOKEN_ID
wrangler secret put MODAL_TOKEN_SECRET

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
