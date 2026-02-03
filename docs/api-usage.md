# API Usage Guide: How Users Interact with Endpoints

This guide explains how end users interact with your deployed agent sandbox application, including all available endpoints, request/response formats, authentication, and real-world usage examples.

## Table of Contents

- [Deployment and Public URLs](#deployment-and-public-urls)
- [Available Endpoints](#available-endpoints)
  - [Health & Info](#1-get-health---health-check)
  - [Agent SDK](#2-post-query---execute-agent-query-non-streaming)
  - [Jobs](#4-post-submit---enqueue-agent-job)
- [Real-World Usage Examples](#real-world-usage-examples)
- [Authentication](#authentication)
- [Error Handling](#error-handling)
- [Production Considerations](#production-considerations)

## Deployment and Public URLs

### Public API (Cloudflare Worker)

The **public** API surface is the Cloudflare Worker. Your public URL will look like:

```
https://<your-worker>.workers.dev
```

If you use a custom domain, replace it with your own host (for example `https://api.example.com`).

### Internal API (Modal Gateway)

Modal still provides a gateway URL, but it is **internal-only** and requires
`X-Internal-Auth` on all non-health endpoints:

```
https://<your-org>--test-sandbox-http-app.modal.run
```

Use this URL only for internal integration or debugging, not for public clients.

### Deploying

```bash
modal deploy -m agent_sandbox.deploy
```

For the Cloudflare Worker, deploy via `wrangler` in `cloudflare-control-plane/`.

## Available Endpoints

Endpoints below assume the **Cloudflare Worker** base URL unless explicitly marked as internal-only.

### 1. GET /health - Health Check

**Purpose:** Verify the service is running and accessible

**Request:**
```bash
curl https://your-worker.workers.dev/health
```

**Response:**
```json
{
  "ok": true
}
```

**Status Codes:**
- `200 OK`: Service is healthy

**Use Cases:**
- Monitoring and uptime checks
- Load balancer health checks
- Quick verification that the service is live
- Integration testing

**Example Usage:**
```bash
# Simple health check
curl https://your-worker.workers.dev/health

# With verbose output
curl -v https://your-worker.workers.dev/health

# Check response time
time curl -s https://your-worker.workers.dev/health
```

---

### 2. POST /query - Execute Agent Query (Non-Streaming)

**Purpose:** Send a question to the agent and receive a complete response

**Endpoint:** `POST /query`

**Request Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
  "question": "Your question here",
  "agent_type": "default",
  "session_id": null,
  "session_key": null,
  "fork_session": false
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `question` | string | (required) | The question to ask the agent |
| `agent_type` | string | `"default"` | Agent type: `"default"`, `"marketing"`, `"research"` |
| `session_id` | string | `null` | Resume from a specific session |
| `session_key` | string | `null` | Session key alias for `session_id` (no KV lookup yet in Cloudflare) |
| `fork_session` | boolean | `false` | Fork session instead of continuing |

**Request Example:**
```bash
curl -X POST https://your-worker.workers.dev/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the capital of Canada?"}'
```

**Agent Type Example:**
```bash
# Marketing agent for content creation
curl -X POST https://your-worker.workers.dev/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Write a tagline for a productivity app", "agent_type": "marketing"}'

# Research agent for multi-agent investigation
curl -X POST https://your-worker.workers.dev/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Research AI agent frameworks", "agent_type": "research"}'
```

**Session Resumption Example:**
```bash
curl -X POST https://your-worker.workers.dev/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Continue the plan", "session_key": "user-123"}'
```

**Response:**
```json
{
  "ok": true,
  "messages": [
    {
      "type": "assistant",
      "content": [
        { "type": "text", "text": "The capital of Canada is Ottawa." }
      ],
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

**Response Fields:**
- `ok` (boolean): Indicates success
- `messages` (array of objects): Structured agent messages (`assistant`, `result`, etc.)
- `summary` (object): Convenience summary of the completed run
- `session_id` (string, optional): Session identifier for resumption

**Session fields:**
- `session_id`: Resume from a specific session returned by a prior response.
- `session_key`: Cloudflare Worker treats this as a session_id alias (no KV lookup yet). Modal gateway uses a server-side session store.
- `fork_session`: When resuming, start a new branched session instead of continuing the original.

**Status Codes:**
- `200 OK`: Query executed successfully
- `400 Bad Request`: Invalid request body (missing `question` field)
- `401 Unauthorized`: Missing or invalid authentication token
- `500 Internal Server Error`: Agent error or sandbox issue
- `503 Service Unavailable`: Sandbox not ready (typically on first request)

**Characteristics:**
- **Timeout:** 120 seconds
- **Response Type:** Complete response (all messages at once)
- **Best For:** Simple questions, synchronous workflows, when you need the full response before proceeding

**Example with Error Handling:**
```bash
curl -X POST https://your-worker.workers.dev/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Explain Python"}' \
  -w "\nHTTP Status: %{http_code}\n"
```

---

### 3. /query_stream - Execute Agent Query (Streaming)

#### Cloudflare Worker (WebSocket, Public)

**Endpoint:** `GET /query_stream` (WebSocket upgrade required)

**Request:** Open a WebSocket and send the query as the first message:

```json
{
  "question": "Your question here",
  "agent_type": "default",
  "session_id": null,
  "session_key": null,
  "user_id": null,
  "tenant_id": null
}
```

**Example (wscat):**
```bash
wscat -c wss://your-worker.workers.dev/query_stream
> {"question":"Explain quantum computing in detail"}
```

**Server → Client Events:**
- `connection_ack`
- `session_update`
- `query_start`
- `assistant_message` (always `partial: false`)
- `tool_use`
- `tool_result`
- `execution_state` (system/result/unknown SSE events)
- `query_complete`
- `query_error`
- `prompt_queued`

**Status Codes:**
- `101 Switching Protocols`: WebSocket upgrade successful
- `426 Upgrade Required`: Missing WebSocket upgrade

#### Modal Gateway (SSE, Internal Only)

**Endpoint:** `POST /query_stream`

**Requires:** `X-Internal-Auth` header (raw token, no `Bearer` prefix)

**Request Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
  "question": "Your question here",
  "agent_type": "default",
  "session_id": null,
  "session_key": null,
  "fork_session": false
}
```

**Response:** Server-Sent Events (SSE) stream
```
event: assistant
data: {"type":"assistant","content":[{"type":"text","text":"Quantum computing is..."}],"model":"claude-..."}

event: result
data: {"type":"result","duration_ms":1234,"total_cost_usd":0.0001}

event: done
data: {"text":"...","is_complete":true,"duration_ms":1234}
```

**Status Codes:**
- `200 OK`: Stream started successfully
- `400 Bad Request`: Invalid request body
- `401 Unauthorized`: Missing or invalid authentication token
- `500 Internal Server Error`: Agent error
- `503 Service Unavailable`: Sandbox not ready

---

### 4. POST /submit - Enqueue Agent Job

**Purpose:** Enqueue a background job for asynchronous processing

**Endpoint:** `POST /submit`

**Request Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
  "question": "Your question here",
  "agent_type": "default",
  "tenant_id": "acme",
  "user_id": "user-123",
  "schedule_at": 1735840200,
  "webhook": {
    "url": "https://example.com/api/agent-callbacks",
    "headers": { "X-App-Id": "acme-app" },
    "signing_secret": "optional-shared-secret"
  },
  "metadata": {
    "project_id": "proj-789",
    "request_source": "nextjs-ui"
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `question` | string | (required) | The task for the agent |
| `agent_type` | string | `"default"` | Agent type: `"default"`, `"marketing"`, `"research"` |
| `tenant_id` | string | `null` | Tenant or workspace identifier |
| `user_id` | string | `null` | End-user identifier |
| `schedule_at` | integer | `null` | Unix timestamp to schedule execution |
| `webhook` | object | `null` | Callback configuration |
| `metadata` | object | `null` | Client-defined metadata |

**Request Example:**
```bash
curl -X POST https://your-worker.workers.dev/submit \
  -H "Content-Type: application/json" \
  -d '{"question": "Summarize the latest earnings report", "tenant_id": "acme", "user_id": "user-123"}'
```

**Background Job with Agent Type:**
```bash
# Submit a marketing content job
curl -X POST https://your-worker.workers.dev/submit \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Write a comprehensive blog post about AI productivity tools",
    "agent_type": "marketing",
    "tenant_id": "acme",
    "user_id": "user-123"
  }'
```

**Response:**
```json
{
  "ok": true,
  "job_id": "4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a"
}
```

**Optional Fields:**
- `tenant_id`: Tenant or workspace identifier for multi-tenant apps
- `user_id`: End-user identifier for attribution
- `schedule_at`: Unix timestamp to schedule execution (omit for immediate)
- `webhook`: Callback configuration when the job completes or fails
- `metadata`: Client-defined metadata that is returned in status responses

**Webhook Signing Headers (if enabled):**
- `X-Agent-Timestamp`: Unix timestamp used in signature
- `X-Agent-Signature`: `t=<timestamp>,v1=<hmac>` where `hmac = HMAC_SHA256(secret, "<timestamp>.<payload>")`

**Note:** Jobs are processed by the `process_job_queue` Modal function. In dev, run
`modal run -m agent_sandbox.app::process_job_queue` to consume queued jobs, or set
`job_queue_cron` to schedule automatic processing.

**Status Codes:**
- `200 OK`: Job enqueued
- `400 Bad Request`: Invalid request body
- `401 Unauthorized`: Missing or invalid authentication token
- `500 Internal Server Error`: Failed to enqueue job

---

### 5. GET /jobs/{job_id} - Job Status

**Purpose:** Check job status and retrieve results when complete

**Endpoint:** `GET /jobs/{job_id}`

**Request Example:**
```bash
curl https://your-worker.workers.dev/jobs/4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a
```

**Response (Queued):**
```json
{
  "ok": true,
  "job_id": "4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a",
  "status": "queued",
  "created_at": 1735840000,
  "updated_at": 1735840000,
  "tenant_id": "acme",
  "user_id": "user-123",
  "schedule_at": 1735840200,
  "metadata": {
    "project_id": "proj-789"
  }
}
```

**Response (Complete):**
```json
{
  "ok": true,
  "job_id": "4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a",
  "status": "complete",
  "result": {
    "ok": true,
    "messages": [...],
    "summary": {...}
  },
  "artifacts": {
    "root": "/data/jobs/4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a",
    "files": [
      { "path": "report.md", "size_bytes": 1200 }
    ]
  },
  "started_at": 1735840210,
  "completed_at": 1735840234,
  "queue_latency_ms": 500,
  "duration_ms": 24000,
  "agent_duration_ms": 23000,
  "agent_duration_api_ms": 22000,
  "usage": { "input_tokens": 120, "output_tokens": 220 },
  "total_cost_usd": 0.012,
  "num_turns": 5,
  "session_id": "session-abc123",
  "tool_call_count": 2,
  "models": ["claude-3.5-sonnet"],
  "sandbox_id": "sb-abc123xyz",
  "webhook": {
    "url": "https://example.com/api/agent-callbacks",
    "attempts": 1,
    "last_status": 200,
    "delivered_at": 1735840300
  }
}
```

**Timing Fields:**
- `duration_ms`: Wall-clock execution time for the job (includes agent runtime + overhead)
- `agent_duration_ms`: Agent-reported duration from the SDK summary
- `agent_duration_api_ms`: Agent API duration from the SDK summary

**Status Codes:**
- `200 OK`: Job status returned
- `401 Unauthorized`: Missing or invalid authentication token
- `404 Not Found`: Job does not exist

---

### 6. GET /jobs/{job_id}/artifacts - List Job Artifacts

**Purpose:** List artifacts generated by a job

**Endpoint:** `GET /jobs/{job_id}/artifacts`

**Request Example:**
```bash
curl https://your-worker.workers.dev/jobs/4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a/artifacts
```

**Response:**
```json
{
  "ok": true,
  "job_id": "4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a",
  "artifacts": {
    "root": "/data/jobs/4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a",
    "files": [
      { "path": "report.md", "size_bytes": 1200, "content_type": "text/markdown" },
      { "path": "data/output.json", "size_bytes": 3400 }
    ]
  }
}
```

**Status Codes:**
- `200 OK`: Artifacts listed
- `401 Unauthorized`: Missing or invalid authentication token
- `404 Not Found`: Job does not exist

---

### 7. GET /jobs/{job_id}/artifacts/{path} - Download Artifact

**Purpose:** Download a specific artifact file

**Endpoint:** `GET /jobs/{job_id}/artifacts/{path}`

**Request Example:**
```bash
curl -O https://your-worker.workers.dev/jobs/4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a/artifacts/report.md
```

**Response:**
- Binary file contents with `Content-Disposition: attachment`

**Status Codes:**
- `200 OK`: File downloaded
- `401 Unauthorized`: Missing or invalid authentication token
- `404 Not Found`: Job or artifact does not exist

---

### 8. DELETE /jobs/{job_id} - Cancel Job

**Purpose:** Cancel a queued job before it starts

**Endpoint:** `DELETE /jobs/{job_id}`

**Request Example:**
```bash
curl -X DELETE https://your-worker.workers.dev/jobs/4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a
```

**Response:**
```json
{
  "ok": true,
  "job_id": "4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a",
  "status": "canceled",
  "canceled_at": 1735840100
}
```

**Status Codes:**
- `200 OK`: Job canceled
- `401 Unauthorized`: Missing or invalid authentication token
- `404 Not Found`: Job does not exist

---

### 9. GET /service_info - Service Information

**Purpose:** Get information about the background sandbox service

**Endpoint:** `GET /service_info`

**Internal Only:** Modal gateway endpoint (requires `X-Internal-Auth`).

**Request:**
```bash
curl https://acme-corp--test-sandbox-http-app.modal.run/service_info
```

**Response:**
```json
{
  "url": "https://...encrypted-tunnel-url...",
  "sandbox_id": "sb-abc123xyz"
}
```

**Response Fields:**
- `url` (string): Encrypted tunnel URL for the background service
- `sandbox_id` (string): Unique identifier for the sandbox instance

**Status Codes:**
- `200 OK`: Information retrieved successfully
- `503 Service Unavailable`: Sandbox not available

**Use Cases:**
- Debugging and troubleshooting
- Monitoring sandbox status
- Internal tooling and administration
- Understanding which sandbox instance is handling requests

**Note:** The `url` field contains an encrypted tunnel URL that's only accessible from within Modal's infrastructure. External clients cannot directly access this URL.

---

## Real-World Usage Examples

### JavaScript/Fetch (Non-Streaming)

```javascript
function extractText(messages) {
  return messages
    .filter((message) => message.type === 'assistant')
    .flatMap((message) => message.content || [])
    .filter((block) => block.type === 'text')
    .map((block) => block.text)
    .join('\n');
}

async function askAgent(question, baseUrl) {
  try {
    const response = await fetch(`${baseUrl}/query`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ question })
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data = await response.json();
    return data.summary?.text ?? extractText(data.messages);
  } catch (error) {
    console.error('Error asking agent:', error);
    throw error;
  }
}

// Usage
const baseUrl = 'https://your-worker.workers.dev';
const answer = await askAgent("What is Python?");
console.log(answer);
```

### JavaScript/WebSocket (Streaming, Cloudflare)

```javascript
function streamAgentWebSocket(question, baseUrl, onEvent) {
  const ws = new WebSocket(`${baseUrl.replace('https://', 'wss://')}/query_stream`);

  ws.onopen = () => {
    ws.send(JSON.stringify({ question }));
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    onEvent(msg);
  };

  ws.onerror = (err) => console.error('WebSocket error:', err);
  ws.onclose = () => console.log('WebSocket closed');

  return ws;
}

// Usage
const baseUrl = 'https://your-worker.workers.dev';
streamAgentWebSocket("Explain machine learning", baseUrl, (event) => {
  if (event.type === 'assistant_message') {
    process.stdout.write(event.data.content);
  }
  if (event.type === 'query_complete') {
    console.log('\n\nDone!', event.data.summary);
  }
});
```

### JavaScript/Fetch (Streaming, Modal SSE internal-only)

```javascript
async function streamAgentResponse(question, baseUrl, onChunk, onComplete, onError) {
  try {
    const response = await fetch(`${baseUrl}/query_stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Internal-Auth': '<internal-token>',
      },
      body: JSON.stringify({ question })
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let currentEvent = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // Keep incomplete line in buffer

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
          continue;
        }

        if (line.startsWith('data: ')) {
          const raw = line.slice(6);
          if (!raw) continue;

          const payload = JSON.parse(raw);
          if (currentEvent === 'assistant') {
            const textBlocks = (payload.content || [])
              .filter((block) => block.type === 'text')
              .map((block) => block.text);
            textBlocks.forEach((text) => onChunk(text));
          } else if (currentEvent === 'done') {
            onComplete(payload);
            return;
          }
        }
      }
    }
  } catch (error) {
    console.error('Error streaming agent response:', error);
    if (onError) onError(error);
  }
}

// Usage (internal Modal URL)
const baseUrl = 'https://acme-corp--test-sandbox-http-app.modal.run';
streamAgentResponse(
  "Explain machine learning",
  baseUrl,
  (chunk) => {
    // Called for each chunk
    process.stdout.write(chunk);
  },
  (summary) => {
    // Called when complete
    console.log('\n\nDone!', summary);
  },
  (error) => {
    // Called on error
    console.error('Stream error:', error);
  }
);
```

### Next.js Background Jobs (Submit + Poll + Download)

```javascript
// Example server action or API route usage
const baseUrl = 'https://your-worker.workers.dev';

export async function submitJob(question, userId) {
  const response = await fetch(`${baseUrl}/submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question,
      tenant_id: 'acme',
      user_id: userId,
      webhook: {
        url: 'https://example.com/api/agent-callbacks',
        signing_secret: process.env.WEBHOOK_SECRET,
      },
      metadata: { source: 'nextjs' },
    }),
  });
  if (!response.ok) throw new Error('Failed to enqueue');
  return response.json(); // { job_id }
}

export async function pollJob(jobId) {
  const response = await fetch(`${baseUrl}/jobs/${jobId}`);
  if (!response.ok) throw new Error('Failed to fetch status');
  return response.json();
}

export async function listArtifacts(jobId) {
  const response = await fetch(`${baseUrl}/jobs/${jobId}/artifacts`);
  if (!response.ok) throw new Error('Failed to list artifacts');
  return response.json();
}

export async function downloadArtifact(jobId, path) {
  const response = await fetch(`${baseUrl}/jobs/${jobId}/artifacts/${path}`);
  if (!response.ok) throw new Error('Failed to download');
  return response.blob(); // or stream to storage
}
```

### Python/Requests (Non-Streaming)

```python
import requests
from typing import Dict, List

def extract_text(messages: List[Dict]) -> str:
    parts = []
    for message in messages:
        if message.get("type") == "assistant":
            for block in message.get("content", []):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return "\n".join(parts)

def ask_agent(question: str, base_url: str) -> Dict:
    """Send a question to the agent and get response.

    Args:
        question: The question to ask the agent
        base_url: Base URL of the deployed service

    Returns:
        Dictionary with 'ok', 'messages', and 'summary' keys

    Raises:
        requests.HTTPError: If the request fails
    """
    response = requests.post(
        f"{base_url}/query",
        json={"question": question},
        timeout=120,
        headers={"Content-Type": "application/json"}
    )
    response.raise_for_status()
    return response.json()

# Usage
base_url = "https://your-worker.workers.dev"
result = ask_agent("What is the weather like?", base_url)

if result["ok"]:
    summary = result.get("summary", {})
    text = summary.get("text") or extract_text(result.get("messages", []))
    print(text)
```

### Python/Requests (Streaming)

```python
import requests
import json

def stream_agent_response(question: str, base_url: str):
    """Stream agent responses as they're generated.

    Args:
        question: The question to ask the agent
        base_url: Base URL of the deployed service

    Yields:
        String chunks of the agent's response
    """
    response = requests.post(
        f"{base_url}/query_stream",
        json={"question": question},
        stream=True,
        timeout=None,
        headers={
            "Content-Type": "application/json",
            "X-Internal-Auth": "<internal-token>",
        }
    )
    response.raise_for_status()

    current_event = None
    for line in response.iter_lines():
        if not line:
            continue

        line_str = line.decode('utf-8')
        if line_str.startswith('event: '):
            current_event = line_str[7:].strip()
            continue

        if line_str.startswith('data: '):
            payload = json.loads(line_str[6:])
            if current_event == "assistant":
                for block in payload.get("content", []):
                    if block.get("type") == "text":
                        yield block.get("text", "")
            elif current_event == "done":
                break

# Usage (internal Modal URL)
base_url = "https://acme-corp--test-sandbox-http-app.modal.run"
for chunk in stream_agent_response("Explain AI", base_url):
    print(chunk, end='', flush=True)
print()  # Newline at end
```

### Python/Requests with Session Resumption

```python
import requests
from typing import Optional

class AgentClient:
    """Client with session management for multi-turn conversations."""

    def __init__(self, base_url: str, session_key: Optional[str] = None):
        self.base_url = base_url
        self.session_key = session_key
        self.last_session_id = None

    def query(self, question: str, fork: bool = False) -> dict:
        """Send a query, optionally resuming prior context.

        Args:
            question: The question to ask
            fork: If True, branch from prior session instead of continuing it

        Returns:
            Response dict with messages and session_id
        """
        payload = {"question": question}

        # Use session_key (Cloudflare treats as session_id alias; Modal can map server-side)
        if self.session_key:
            payload["session_key"] = self.session_key
        # Or use explicit session_id from prior response
        elif self.last_session_id:
            payload["session_id"] = self.last_session_id

        if fork:
            payload["fork_session"] = True

        response = requests.post(
            f"{self.base_url}/query",
            json=payload,
            timeout=120
        )
        response.raise_for_status()
        result = response.json()

        # Store session_id for future resumption
        self.last_session_id = result.get("session_id")
        return result

# Usage with session_key (Cloudflare alias behavior)
client = AgentClient(
    base_url="https://your-worker.workers.dev",
    session_key="user-123"
)

# First message
result = client.query("Create a plan for building a web app")
print(result["summary"]["text"])

# Follow-up (automatically resumes prior context)
result = client.query("Add user authentication to the plan")
print(result["summary"]["text"])

# Fork to try a different direction
result = client.query("Actually, make it a mobile app instead", fork=True)
print(result["summary"]["text"])
```

### cURL Examples

**Simple Query:**
```bash
curl -X POST https://your-worker.workers.dev/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Hello, how are you?"}'
```

**Query with Pretty JSON Output:**
```bash
curl -X POST https://your-worker.workers.dev/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Python?"}' \
  | jq '.'
```

**Streaming Query (WebSocket):**
```bash
wscat -c wss://your-worker.workers.dev/query_stream
> {"question":"Explain quantum computing"}
```

**Save Response to File:**
```bash
curl -X POST https://your-worker.workers.dev/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Write a Python tutorial"}' \
  -o response.json
```

### React Component Example

```jsx
import React, { useState } from 'react';

function AgentQuery() {
  const [question, setQuestion] = useState('');
  const [response, setResponse] = useState('');
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [summary, setSummary] = useState(null);

  const baseUrl = 'https://your-worker.workers.dev';

  const extractText = (messages) =>
    messages
      .filter((message) => message.type === 'assistant')
      .flatMap((message) => message.content || [])
      .filter((block) => block.type === 'text')
      .map((block) => block.text)
      .join('\n');

  const handleQuery = async () => {
    setLoading(true);
    setResponse('');

    try {
      const res = await fetch(`${baseUrl}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question })
      });

      const data = await res.json();
      setSummary(data.summary || null);
      setResponse(data.summary?.text ?? extractText(data.messages));
    } catch (error) {
      setResponse(`Error: ${error.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleStream = () => {
    setStreaming(true);
    setResponse('');

    const wsUrl = `${baseUrl.replace('https://', 'wss://')}/query_stream`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      ws.send(JSON.stringify({ question }));
    };

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === 'assistant_message') {
        setResponse((prev) => prev + msg.data.content);
      }
      if (msg.type === 'query_complete') {
        setSummary(msg.data.summary || null);
        ws.close();
      }
      if (msg.type === 'query_error') {
        setResponse(`Error: ${msg.data.error}`);
        ws.close();
      }
    };

    ws.onerror = (error) => {
      setResponse(`Error: ${error.message || 'WebSocket error'}`);
      ws.close();
    };

    ws.onclose = () => {
      setStreaming(false);
    };
  };

  return (
    <div>
      <textarea
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        placeholder="Ask a question..."
      />
      <button onClick={handleQuery} disabled={loading || streaming}>
        {loading ? 'Loading...' : 'Query'}
      </button>
      <button onClick={handleStream} disabled={loading || streaming}>
        {streaming ? 'Streaming...' : 'Stream'}
      </button>
      {summary ? <pre>{JSON.stringify(summary, null, 2)}</pre> : null}
      <pre>{response}</pre>
    </div>
  );
}

export default AgentQuery;
```

## Authentication

### Cloudflare Worker (Public)

The Worker accepts `Authorization: Bearer <token>` today, but **does not enforce** it yet.
Client auth enforcement is planned. TODO: enforce client auth in the Worker.

```bash
curl -X POST https://your-worker.workers.dev/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"question": "..."}'
```

### Modal Gateway (Internal Only)

All non-health Modal endpoints require **`X-Internal-Auth`** (raw token, no `Bearer` prefix).
This header is injected by the Cloudflare control plane and by internal gateways.

```bash
curl -X POST https://<org>--test-sandbox-http-app.modal.run/query \
  -H "Content-Type: application/json" \
  -H "X-Internal-Auth: <internal-token>" \
  -d '{"question": "..."}'
```

### Optional Modal Auth (Internal)

These options add **additional** checks on top of `X-Internal-Auth` for internal-only usage.

#### Option A: Modal Connect Tokens (Internal)

**How it works:**
1. Your application generates a connect token for each user
2. Internal gateway includes the token in the `Authorization` header
3. Modal validates the token and injects `X-Verified-User-Data` header
4. Controller verifies the header

**Enable in your code:**

1. **Settings** (`agent_sandbox/config/settings.py` or environment variable):
   ```python
   enforce_connect_token = True
   ```

2. **Controller** (`agent_sandbox/controllers/controller.py`):
   ```python
   ENFORCE_CONNECT_TOKEN = True
   ```

#### Option B: Modal Proxy Auth Tokens (Internal)

**How it works:**
1. Enable proxy auth on the Modal HTTP endpoint
2. Create a Proxy Auth Token in the Modal workspace (Dashboard > Workspace > Proxy Auth Tokens)
3. Internal callers include the token ID/secret in `Modal-Key` and `Modal-Secret` headers

**Enable in your code:**

1. **Settings** (`agent_sandbox/config/settings.py` or environment variable):
   ```python
   require_proxy_auth = True
   ```

2. **App** (`agent_sandbox/app.py`):
   ```python
   @modal.asgi_app(requires_proxy_auth=True)
   ```

## Error Handling

### HTTP Status Codes

Users should handle these status codes:

| Code | Meaning | Action |
|------|---------|--------|
| `200 OK` | Success | Process response normally |
| `400 Bad Request` | Invalid request | Check request body format |
| `401 Unauthorized` | Authentication failed | Verify token/credentials |
| `500 Internal Server Error` | Server error | Retry or contact support |
| `503 Service Unavailable` | Service not ready | Retry after delay (first request) |

### Retry Logic Example

```python
import requests
import time
from typing import Optional

def ask_agent_with_retry(
    question: str,
    base_url: str,
    max_retries: int = 3,
    retry_delay: float = 5.0
) -> Optional[dict]:
    """Ask agent with automatic retry on transient errors."""
    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{base_url}/query",
                json={"question": question},
                timeout=120
            )

            if response.status_code == 503:
                # Service starting up, wait and retry
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                else:
                    raise Exception("Service unavailable after retries")

            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [500, 503]:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
            raise
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            raise

    return None
```

## Production Considerations

### Rate Limiting

Modal provides DDoS protection, but you may want to implement per-user rate limiting:

```python
from fastapi import Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
web_app.state.limiter = limiter
web_app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@web_app.post("/query")
@limiter.limit("10/minute")  # 10 requests per minute per IP
async def query_proxy(request: Request, body: QueryBody):
    # Handler code
    ...
```

### Timeouts

**Current Settings:**
- `/query`: 120 seconds timeout
- `/query_stream`: No timeout (streams until complete)

### CORS Configuration

Current CORS settings allow all origins. For production, restrict to your domains:

```python
web_app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://your-frontend-domain.com",
        "https://www.your-frontend-domain.com",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)
```

### Performance Optimization

**First Request Latency:**
- First request: ~2-5 seconds (sandbox startup)
- Subsequent requests: < 1 second (sandbox is warm)

**Optimization Tips:**
1. Keep sandbox warm with periodic health checks
2. Increase `sandbox_idle_timeout` for high-traffic scenarios
3. Use streaming for better perceived performance
4. Implement client-side caching for common queries

## Related Documentation

- [Architecture Overview](./architecture.md) - Understanding the system architecture
- [Multi-Agent Architecture](./multi-agent.md) - Agent types, custom agents, and orchestration
- [Controllers](./controllers.md) - How the background service works
- [Modal Ingress](./modal-ingress.md) - How requests reach your application
- [Configuration](./configuration.md) - Configuration options
