# API Usage Guide: How Users Interact with Endpoints

This guide explains how end users interact with your deployed agent sandbox application, including all available endpoints, request/response formats, authentication, and real-world usage examples.

## Table of Contents

- [Deployment and Public URLs](#deployment-and-public-urls)
- [Available Endpoints](#available-endpoints)
  - [Health & Info](#1-get-health---health-check)
  - [Agent SDK](#2-post-query---execute-agent-query-non-streaming)
  - [Jobs](#4-post-submit---enqueue-agent-job)
  - [Claude CLI](#10-post-claude_cli---execute-claude-code-cli)
  - [Ralph Loop](#14-post-ralphstart---start-ralph-autonomous-coding-loop)
- [Real-World Usage Examples](#real-world-usage-examples)
- [Authentication](#authentication)
- [Error Handling](#error-handling)
- [Production Considerations](#production-considerations)

## Deployment and Public URLs

### Deploying the Application

To deploy your application to production:

```bash
modal deploy -m agent_sandbox.deploy
```

After deployment, Modal automatically provides a public HTTPS URL.

### URL Format

The public URL follows this pattern:

```
https://<your-org>--test-sandbox-http-app.modal.run
```

**Components:**
- `<your-org>`: Your Modal organization name (e.g., `acme-corp`)
- `test-sandbox`: The app name (from `modal.App("test-sandbox")`)
- `http-app`: The function name (from `@modal.asgi_app()`)

**Example:**
```
https://acme-corp--test-sandbox-http-app.modal.run
```

### Finding Your URL

You can find your deployment URL in several ways:

1. **Modal Dashboard**: After deployment, check the dashboard for your app
2. **Terminal Output**: The URL is displayed after `modal deploy` completes
3. **Modal CLI**: Run `modal app list` to see all deployed apps and their URLs

### Development vs Production URLs

**Development** (when using `modal serve`):
```
https://<org>--test-sandbox-http-app-dev.modal.run
```

**Production** (when using `modal deploy`):
```
https://<org>--test-sandbox-http-app.modal.run
```

Note the `-dev` suffix in development URLs.

## Available Endpoints

### 1. GET /health - Health Check

**Purpose:** Verify the service is running and accessible

**Request:**
```bash
curl https://acme-corp--test-sandbox-http-app.modal.run/health
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
curl https://your-url.modal.run/health

# With verbose output
curl -v https://your-url.modal.run/health

# Check response time
time curl -s https://your-url.modal.run/health
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
  "session_id": null,
  "session_key": null,
  "fork_session": false
}
```

**Request Example:**
```bash
curl -X POST https://acme-corp--test-sandbox-http-app.modal.run/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the capital of Canada?"}'
```

**Session Resumption Example:**
```bash
curl -X POST https://acme-corp--test-sandbox-http-app.modal.run/query \
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
- `session_key`: Server-side key used to store or resume the last session for a user.
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
curl -X POST https://your-url.modal.run/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Explain Python"}' \
  -w "\nHTTP Status: %{http_code}\n"
```

---

### 3. POST /query_stream - Execute Agent Query (Streaming)

**Purpose:** Stream agent responses in real-time via Server-Sent Events (SSE)

**Endpoint:** `POST /query_stream`

**Request Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
  "question": "Your question here",
  "session_id": null,
  "session_key": null,
  "fork_session": false
}
```

**Request Example:**
```bash
curl -X POST https://acme-corp--test-sandbox-http-app.modal.run/query_stream \
  -H "Content-Type: application/json" \
  -d '{"question": "Explain quantum computing in detail"}' \
  --no-buffer
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

**Response Format:**
- Each event includes `event:` and `data:` lines
- `data:` payloads are JSON objects (not raw strings)
- Empty line (`\n\n`) separates events
- Final event: `event: done` with a summary payload

**Status Codes:**
- `200 OK`: Stream started successfully
- `400 Bad Request`: Invalid request body
- `401 Unauthorized`: Missing or invalid authentication token
- `500 Internal Server Error`: Agent error
- `503 Service Unavailable`: Sandbox not ready

**Characteristics:**
- **Timeout:** None (streams until complete)
- **Response Type:** Server-Sent Events (SSE)
- **Content-Type:** `text/event-stream`
- **Best For:** Long-form answers, interactive UIs, real-time feedback, better user experience

**Example with Verbose Output:**
```bash
curl -X POST https://your-url.modal.run/query_stream \
  -H "Content-Type: application/json" \
  -d '{"question": "Write a Python function"}' \
  --no-buffer \
  -v
```

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

**Request Example:**
```bash
curl -X POST https://acme-corp--test-sandbox-http-app.modal.run/submit \
  -H "Content-Type: application/json" \
  -d '{"question": "Summarize the latest earnings report", "tenant_id": "acme", "user_id": "user-123"}'
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
curl https://acme-corp--test-sandbox-http-app.modal.run/jobs/4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a
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
curl https://acme-corp--test-sandbox-http-app.modal.run/jobs/4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a/artifacts
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
curl -O https://acme-corp--test-sandbox-http-app.modal.run/jobs/4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a/artifacts/report.md
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
curl -X DELETE https://acme-corp--test-sandbox-http-app.modal.run/jobs/4f7b2a5c-9c2b-4c9d-9b3b-2a1fd2e3c12a
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

### 10. POST /claude_cli - Execute Claude Code CLI

**Purpose:** Execute Claude Code CLI in the dedicated CLI sandbox

**Endpoint:** `POST /claude_cli`

**Request Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
  "prompt": "Create hello.py and run it",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "allowed_tools": ["Read", "Write", "Bash"],
  "disallowed_tools": [],
  "max_turns": 10,
  "timeout_seconds": 300,
  "dangerously_skip_permissions": true,
  "output_format": "json"
}
```

**Request Example:**
```bash
curl -X POST https://acme-corp--test-sandbox-http-app.modal.run/claude_cli \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Create hello.py and run it","job_id":"550e8400-e29b-41d4-a716-446655440000","allowed_tools":["Write","Bash","Read"],"timeout_seconds":300}'
```

**Response:**
```json
{
  "ok": true,
  "output": "I'll create hello.py and run it...",
  "exit_code": 0,
  "cost_usd": 0.0012,
  "duration_ms": 5432,
  "session_id": "session-abc123"
}
```

**Request Fields:**
- `prompt` (required): The task prompt for Claude CLI
- `job_id` (optional): UUID for workspace directory (`/data-cli/jobs/{job_id}/`)
- `allowed_tools` (optional): List of tools the CLI can use (e.g., `["Read", "Write", "Bash", "Glob", "Grep"]`)
- `disallowed_tools` (optional): Tools to explicitly block
- `max_turns` (optional): Maximum conversation turns (default: 10)
- `timeout_seconds` (optional): CLI execution timeout (default: 120)
- `dangerously_skip_permissions` (optional): Skip tool approval prompts (default: true)
- `output_format` (optional): Output format (`"json"` or `"text"`)

**Status Codes:**
- `200 OK`: CLI executed successfully
- `400 Bad Request`: Invalid request body
- `500 Internal Server Error`: CLI execution failed
- `503 Service Unavailable`: CLI sandbox not ready

**Characteristics:**
- **Sandbox:** Runs in dedicated CLI sandbox (`claude-cli-runner`) on port 8002
- **User:** Executes as non-root `claude` user (required for `--dangerously-skip-permissions`)
- **Volume:** Files persist at `/data-cli/jobs/{job_id}/`
- **Timeout:** Default 120 seconds, configurable up to 24 hours

---

### 11. POST /claude_cli/submit - Submit Async CLI Job

**Purpose:** Start a Claude CLI job asynchronously for polling

**Endpoint:** `POST /claude_cli/submit`

**Request Body:** Same as `/claude_cli`

**Request Example:**
```bash
curl -X POST https://acme-corp--test-sandbox-http-app.modal.run/claude_cli/submit \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Create app.py and run it","allowed_tools":["Write","Bash","Read"],"job_id":"550e8400-e29b-41d4-a716-446655440000","timeout_seconds":300}'
```

**Response:**
```json
{
  "ok": true,
  "call_id": "fc-01KEH5JQACATAHE94X4K21A227"
}
```

**Status Codes:**
- `200 OK`: Job submitted
- `400 Bad Request`: Invalid request body
- `500 Internal Server Error`: Failed to spawn job

---

### 12. GET /claude_cli/result/{call_id} - Poll CLI Job Result

**Purpose:** Poll for async CLI job completion

**Endpoint:** `GET /claude_cli/result/{call_id}`

**Request Example:**
```bash
curl https://acme-corp--test-sandbox-http-app.modal.run/claude_cli/result/fc-01KEH5JQACATAHE94X4K21A227
```

**Response (Running):**
```json
{
  "status": "running"
}
```
HTTP Status: `202 Accepted`

**Response (Complete):**
```json
{
  "status": "complete",
  "result": {
    "ok": true,
    "output": "...",
    "exit_code": 0,
    "cost_usd": 0.0012,
    "duration_ms": 5432
  }
}
```
HTTP Status: `200 OK`

**Response (Failed):**
```json
{
  "status": "failed",
  "error": "CLI execution timed out"
}
```
HTTP Status: `500 Internal Server Error`

**Response (Expired):**
```json
{
  "status": "expired"
}
```
HTTP Status: `410 Gone`

**Status Codes:**
- `200 OK`: Job complete
- `202 Accepted`: Job still running
- `410 Gone`: Result expired (TTL passed)
- `500 Internal Server Error`: Job failed

---

### 13. DELETE /claude_cli/{call_id} - Cancel CLI Job

**Purpose:** Cancel a running CLI job

**Endpoint:** `DELETE /claude_cli/{call_id}`

**Request Example:**
```bash
curl -X DELETE https://acme-corp--test-sandbox-http-app.modal.run/claude_cli/fc-01KEH5JQACATAHE94X4K21A227
```

**Response:**
```json
{
  "ok": true,
  "status": "cancelled"
}
```

**Status Codes:**
- `200 OK`: Job cancelled
- `404 Not Found`: Job not found
- `500 Internal Server Error`: Failed to cancel

---

### 14. POST /ralph/start - Start Ralph Autonomous Coding Loop

**Purpose:** Start an autonomous coding loop that iterates through a PRD (Product Requirements Document)

**Endpoint:** `POST /ralph/start`

**Request Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
  "prd": {
    "name": "my-project",
    "userStories": [
      {
        "id": "task-1",
        "category": "functional",
        "description": "Create hello.txt with 'hi'",
        "steps": ["Ensure hello.txt exists", "Verify content is 'hi'"],
        "priority": 1,
        "passes": false
      },
      {
        "id": "task-2",
        "category": "functional",
        "description": "Create goodbye.txt with 'bye'",
        "steps": ["Ensure goodbye.txt exists"],
        "priority": 2,
        "passes": false
      }
    ]
  },
  "workspace_source": {
    "type": "empty"
  },
  "prompt_template": null,
  "max_iterations": 10,
  "timeout_per_iteration": 300,
  "first_iteration_timeout": 600,
  "allowed_tools": ["Read", "Write", "Bash", "Glob", "Grep"],
  "feedback_commands": ["uv run pytest"],
  "feedback_timeout": 120,
  "auto_commit": true,
  "max_consecutive_failures": 3
}
```

**Request Example:**
```bash
curl -X POST https://acme-corp--test-sandbox-http-app.modal.run/ralph/start \
  -H "Content-Type: application/json" \
  -d '{
    "prd": {
      "name": "test-project",
      "userStories": [{
        "id": "task-1",
        "category": "functional",
        "description": "Create hello.txt with hi",
        "steps": ["Ensure hello.txt exists"],
        "priority": 1,
        "passes": false
      }]
    },
    "max_iterations": 5,
    "timeout_per_iteration": 180,
    "auto_commit": false
  }'
```

**Response:**
```json
{
  "job_id": "a36f0318-2823-40fc-ae37-a029532520dc",
  "call_id": "fc-01KEH5JQACATAHE94X4K21A227",
  "status": "started"
}
```

**Request Fields:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `prd` | object | Yes | - | Product Requirements Document with tasks |
| `prd.name` | string | Yes | - | Project name |
| `prd.userStories` | array | Yes | - | List of tasks to complete |
| `workspace_source` | object | No | `{"type": "empty"}` | Workspace initialization source |
| `workspace_source.type` | string | No | `"empty"` | `"empty"` or `"git_clone"` |
| `workspace_source.git_url` | string | No | - | Git URL (if `type: "git_clone"`) |
| `workspace_source.git_branch` | string | No | - | Git branch (if `type: "git_clone"`) |
| `prompt_template` | string | No | Built-in | Custom prompt template |
| `max_iterations` | int | No | 10 | Maximum loop iterations |
| `timeout_per_iteration` | int | No | 300 | Seconds per iteration |
| `first_iteration_timeout` | int | No | - | Timeout for first iteration (for cold starts) |
| `allowed_tools` | array | No | All tools | Tools the CLI can use |
| `feedback_commands` | array | No | `[]` | Commands to validate work |
| `feedback_timeout` | int | No | 120 | Timeout for feedback commands |
| `auto_commit` | bool | No | true | Create git commits after each task |
| `max_consecutive_failures` | int | No | 3 | Stop after N consecutive failures |

**User Story Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique task identifier |
| `category` | string | Yes | Task category (e.g., `"functional"`, `"testing"`) |
| `description` | string | Yes | What the task should accomplish |
| `steps` | array | Yes | Verification steps to confirm completion |
| `priority` | int | Yes | Execution order (lower = first) |
| `passes` | bool | Yes | Whether task is complete (set to `false` initially) |

**Status Codes:**
- `200 OK`: Loop started
- `400 Bad Request`: Invalid PRD or request body
- `500 Internal Server Error`: Failed to start loop

**Important Characteristics:**
- **Autonomous execution**: Ralph always runs with `--dangerously-skip-permissions` enabled (hardcoded). This is required for non-interactive execution - the loop cannot prompt for tool approval mid-iteration.
- **Sandbox**: Runs in CLI sandbox (`claude-cli-runner`) on port 8002 as non-root `claude` user
- **Volume**: All artifacts persist at `/data-cli/jobs/{job_id}/`
- **Security**: Use `allowed_tools` to scope what tools the CLI can use. This is your primary security control since permission prompts are skipped.

---

### 15. GET /ralph/{job_id} - Poll Ralph Loop Status

**Purpose:** Poll the status of a running Ralph loop

**Endpoint:** `GET /ralph/{job_id}?call_id={call_id}`

**Query Parameters:**
- `call_id` (required): The Modal call ID returned from `/ralph/start`

**Request Example:**
```bash
curl "https://acme-corp--test-sandbox-http-app.modal.run/ralph/a36f0318-2823-40fc-ae37-a029532520dc?call_id=fc-01KEH5JQACATAHE94X4K21A227"
```

**Response (Running):**
```json
{
  "job_id": "a36f0318-2823-40fc-ae37-a029532520dc",
  "status": "running",
  "current_iteration": 2,
  "max_iterations": 10,
  "tasks_completed": 1,
  "tasks_total": 3,
  "current_task": "Create authentication module",
  "result": null
}
```

**Response (Complete):**
```json
{
  "job_id": "a36f0318-2823-40fc-ae37-a029532520dc",
  "status": "complete",
  "current_iteration": 5,
  "max_iterations": 10,
  "tasks_completed": 3,
  "tasks_total": 3,
  "current_task": null,
  "result": {
    "job_id": "a36f0318-2823-40fc-ae37-a029532520dc",
    "status": "complete",
    "iterations_completed": 5,
    "iterations_max": 10,
    "tasks_completed": 3,
    "tasks_total": 3,
    "iteration_results": [
      {
        "iteration": 1,
        "task_id": "task-1",
        "cli_exit_code": 0,
        "cli_output": "Created hello.txt...",
        "task_passed": true
      }
    ],
    "final_prd": {
      "name": "my-project",
      "userStories": [
        {"id": "task-1", "passes": true, "...": "..."},
        {"id": "task-2", "passes": true, "...": "..."},
        {"id": "task-3", "passes": true, "...": "..."}
      ]
    },
    "error": null
  }
}
```

**Response (Failed):**
```json
{
  "job_id": "a36f0318-2823-40fc-ae37-a029532520dc",
  "status": "failed",
  "current_iteration": 3,
  "max_iterations": 10,
  "tasks_completed": 1,
  "tasks_total": 3,
  "current_task": null,
  "result": {
    "job_id": "a36f0318-2823-40fc-ae37-a029532520dc",
    "status": "failed",
    "error": "Max consecutive failures reached (3)",
    "iterations_completed": 3,
    "...": "..."
  }
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | string | The Ralph job ID |
| `status` | string | `"running"`, `"complete"`, or `"failed"` |
| `current_iteration` | int | Current iteration number |
| `max_iterations` | int | Maximum iterations configured |
| `tasks_completed` | int | Number of tasks with `passes: true` |
| `tasks_total` | int | Total number of tasks in PRD |
| `current_task` | string | Description of current task (null when done) |
| `result` | object | Full result when complete/failed, null while running |

**Status Codes:**
- `200 OK`: Status returned (includes running, complete, or failed states)
- `404 Not Found`: Job not found
- `500 Internal Server Error`: Failed to poll status

---

### Ralph Workspace Artifacts

Ralph writes artifacts to `/data-cli/jobs/{job_id}/`:

| File | Description |
|------|-------------|
| `status.json` | Machine-readable polling status (used by GET /ralph/{job_id}) |
| `progress.txt` | Human-readable log of loop progress |
| `prd.json` | PRD with updated `passes` status for each task |
| `.git/` | Git repository (if `auto_commit: true`) |
| Generated files | Files created by tasks (e.g., `hello.txt`, `app.py`) |

---

### Ralph Polling Loop Example

**Bash:**
```bash
# Start Ralph
resp=$(curl -s -X POST 'https://acme-corp--test-sandbox-http-app.modal.run/ralph/start' \
  -H 'Content-Type: application/json' \
  -d '{
    "prd": {
      "name": "test",
      "userStories": [{
        "id": "task-1",
        "category": "functional",
        "description": "Create hello.txt with hi",
        "steps": ["Verify file exists"],
        "priority": 1,
        "passes": false
      }]
    },
    "max_iterations": 5
  }')

job_id=$(echo "$resp" | jq -r '.job_id')
call_id=$(echo "$resp" | jq -r '.call_id')

echo "Started job: $job_id"

# Poll until complete
while true; do
  status=$(curl -s "https://acme-corp--test-sandbox-http-app.modal.run/ralph/${job_id}?call_id=${call_id}")
  echo "$status" | jq .

  # Check if done
  if echo "$status" | jq -e '.status == "complete" or .status == "failed"' > /dev/null; then
    break
  fi

  sleep 5
done

echo "Final status: $(echo "$status" | jq -r '.status')"
echo "Tasks completed: $(echo "$status" | jq -r '.tasks_completed')/$(echo "$status" | jq -r '.tasks_total')"
```

**JavaScript:**
```javascript
async function runRalphLoop(prd, baseUrl) {
  // Start the loop
  const startResponse = await fetch(`${baseUrl}/ralph/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      prd,
      max_iterations: 10,
      timeout_per_iteration: 180,
      auto_commit: true
    })
  });

  const { job_id, call_id } = await startResponse.json();
  console.log(`Started Ralph job: ${job_id}`);

  // Poll until complete
  while (true) {
    const statusResponse = await fetch(
      `${baseUrl}/ralph/${job_id}?call_id=${call_id}`
    );
    const status = await statusResponse.json();

    console.log(`Status: ${status.status}, Tasks: ${status.tasks_completed}/${status.tasks_total}`);

    if (status.status === 'complete' || status.status === 'failed') {
      return status;
    }

    // Wait before polling again
    await new Promise(resolve => setTimeout(resolve, 5000));
  }
}

// Usage
const prd = {
  name: 'my-project',
  userStories: [
    {
      id: 'task-1',
      category: 'functional',
      description: 'Create hello.txt with greeting',
      steps: ['File exists', 'Contains greeting text'],
      priority: 1,
      passes: false
    }
  ]
};

const result = await runRalphLoop(prd, 'https://acme-corp--test-sandbox-http-app.modal.run');
console.log('Final result:', result);
```

**Python:**
```python
import requests
import time

def run_ralph_loop(prd: dict, base_url: str, poll_interval: float = 5.0) -> dict:
    """Run a Ralph autonomous coding loop and poll until completion."""

    # Start the loop
    start_response = requests.post(
        f"{base_url}/ralph/start",
        json={
            "prd": prd,
            "max_iterations": 10,
            "timeout_per_iteration": 180,
            "auto_commit": True
        }
    )
    start_response.raise_for_status()
    start_data = start_response.json()

    job_id = start_data["job_id"]
    call_id = start_data["call_id"]
    print(f"Started Ralph job: {job_id}")

    # Poll until complete
    while True:
        status_response = requests.get(
            f"{base_url}/ralph/{job_id}",
            params={"call_id": call_id}
        )
        status_response.raise_for_status()
        status = status_response.json()

        print(f"Status: {status['status']}, Tasks: {status['tasks_completed']}/{status['tasks_total']}")

        if status["status"] in ("complete", "failed"):
            return status

        time.sleep(poll_interval)

# Usage
prd = {
    "name": "my-project",
    "userStories": [
        {
            "id": "task-1",
            "category": "functional",
            "description": "Create hello.txt with greeting",
            "steps": ["File exists", "Contains greeting text"],
            "priority": 1,
            "passes": False
        }
    ]
}

result = run_ralph_loop(prd, "https://acme-corp--test-sandbox-http-app.modal.run")
print(f"Final: {result['status']}, completed {result['tasks_completed']} tasks")
```

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
const baseUrl = 'https://acme-corp--test-sandbox-http-app.modal.run';
const answer = await askAgent("What is Python?");
console.log(answer);
```

### JavaScript/Fetch (Streaming)

```javascript
async function streamAgentResponse(question, baseUrl, onChunk, onComplete, onError) {
  try {
    const response = await fetch(`${baseUrl}/query_stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
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

// Usage
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

### JavaScript/EventSource (Alternative Streaming)

```javascript
// Note: EventSource only supports GET requests, so this requires
// a proxy or modification to support POST. The fetch API approach above
// is recommended for POST requests with streaming.
```

### Next.js Background Jobs (Submit + Poll + Download)

```javascript
// Example server action or API route usage
const baseUrl = 'https://acme-corp--test-sandbox-http-app.modal.run';

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
base_url = "https://acme-corp--test-sandbox-http-app.modal.run"
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
        headers={"Content-Type": "application/json"}
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

# Usage
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

        # Use session_key for server-side session tracking
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

# Usage with session_key (server tracks last session)
client = AgentClient(
    base_url="https://acme-corp--test-sandbox-http-app.modal.run",
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

### JavaScript/Fetch with Session Management

```javascript
class AgentClient {
  constructor(baseUrl, sessionKey = null) {
    this.baseUrl = baseUrl;
    this.sessionKey = sessionKey;
    this.lastSessionId = null;
  }

  async query(question, { fork = false } = {}) {
    const payload = { question };

    if (this.sessionKey) {
      payload.session_key = this.sessionKey;
    } else if (this.lastSessionId) {
      payload.session_id = this.lastSessionId;
    }

    if (fork) {
      payload.fork_session = true;
    }

    const response = await fetch(`${this.baseUrl}/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const result = await response.json();
    this.lastSessionId = result.session_id;
    return result;
  }
}

// Usage
const client = new AgentClient(
  'https://acme-corp--test-sandbox-http-app.modal.run',
  'user-123' // session_key
);

// Conversation with automatic session resumption
const result1 = await client.query('Plan a vacation to Japan');
console.log(result1.summary.text);

const result2 = await client.query('Add restaurant recommendations');
console.log(result2.summary.text);

// Fork to explore alternative
const result3 = await client.query('What about Korea instead?', { fork: true });
console.log(result3.summary.text);
```

### Python/httpx (Async Streaming)

```python
import httpx
import asyncio
import json

async def stream_agent_response_async(question: str, base_url: str):
    """Async version of streaming agent responses."""
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST",
            f"{base_url}/query_stream",
            json={"question": question},
            headers={"Content-Type": "application/json"}
        ) as response:
            response.raise_for_status()
            current_event = None
            async for line in response.aiter_lines():
                if line.startswith('event: '):
                    current_event = line[7:].strip()
                    continue
                if line.startswith('data: '):
                    payload = json.loads(line[6:])
                    if current_event == "assistant":
                        for block in payload.get("content", []):
                            if block.get("type") == "text":
                                yield block.get("text", "")
                    elif current_event == "done":
                        break

# Usage
async def main():
    base_url = "https://acme-corp--test-sandbox-http-app.modal.run"
    async for chunk in stream_agent_response_async("Explain async Python", base_url):
        print(chunk, end='', flush=True)
    print()

asyncio.run(main())
```

### cURL Examples

**Simple Query:**
```bash
curl -X POST https://your-url.modal.run/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Hello, how are you?"}'
```

**Query with Pretty JSON Output:**
```bash
curl -X POST https://your-url.modal.run/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Python?"}' \
  | jq '.'
```

**Streaming Query:**
```bash
curl -X POST https://your-url.modal.run/query_stream \
  -H "Content-Type: application/json" \
  -d '{"question": "Explain quantum computing"}' \
  --no-buffer
```

**Save Response to File:**
```bash
curl -X POST https://your-url.modal.run/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Write a Python tutorial"}' \
  -o response.json
```

### Postman/Insomnia Setup

1. **Create New Request**
   - Method: `POST`
   - URL: `https://acme-corp--test-sandbox-http-app.modal.run/query`

2. **Headers**
   ```
   Content-Type: application/json
   ```

3. **Body** (select "raw" and "JSON")
   ```json
   {
     "question": "What is the capital of France?"
   }
   ```

4. **Send Request**

### React Component Example

```jsx
import React, { useState } from 'react';

function AgentQuery() {
  const [question, setQuestion] = useState('');
  const [response, setResponse] = useState('');
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [summary, setSummary] = useState(null);
  
  const baseUrl = 'https://acme-corp--test-sandbox-http-app.modal.run';

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
  
  const handleStream = async () => {
    setStreaming(true);
    setResponse('');
    
    try {
      const res = await fetch(`${baseUrl}/query_stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question })
      });
      
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = null;
      
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
            continue;
          }
          if (line.startsWith('data: ')) {
            const payload = JSON.parse(line.slice(6));
            if (currentEvent === 'assistant') {
              const text = (payload.content || [])
                .filter((block) => block.type === 'text')
                .map((block) => block.text)
                .join('');
              if (text) {
                setResponse((prev) => prev + text);
              }
            } else if (currentEvent === 'done') {
              setSummary(payload);
            }
          }
        }
      }
    } catch (error) {
      setResponse(`Error: ${error.message}`);
    } finally {
      setStreaming(false);
    }
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

By default, the endpoints are **publicly accessible**. You can enable authentication using one of these methods:

### Option A: Modal Connect Tokens

**How it works:**
1. Your application generates a connect token for each user
2. User includes the token in the `Authorization` header
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

**User Request:**
```bash
curl -X POST https://your-url.modal.run/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <connect-token>" \
  -d '{"question": "..."}'
```

**JavaScript Example:**
```javascript
const response = await fetch(`${baseUrl}/query`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${connectToken}`
  },
  body: JSON.stringify({ question })
});
```

### Option B: Modal Proxy Auth Tokens

**How it works:**
1. Enable proxy auth on the public HTTP endpoint
2. Create a Proxy Auth Token in the Modal workspace (Dashboard > Workspace > Proxy Auth Tokens)
3. Clients include the token ID/secret in `Modal-Key` and `Modal-Secret` headers

**Enable in your code:**

1. **Settings** (`agent_sandbox/config/settings.py` or environment variable):
   ```python
   require_proxy_auth = True
   ```

2. **App** (`agent_sandbox/app.py`):
   ```python
   @modal.asgi_app(requires_proxy_auth=True)
   ```

**User Request:**
```bash
curl -X POST https://your-url.modal.run/query \
  -H "Content-Type: application/json" \
  -H "Modal-Key: <token-id>" \
  -H "Modal-Secret: <token-secret>" \
  -d '{"question": "..."}'
```

For the bundled HTTP examples, set `MODAL_PROXY_KEY` and `MODAL_PROXY_SECRET` so the scripts include these headers
automatically.

**Environment Variable Pattern:**
```bash
# In .env file
MODAL_PROXY_KEY=ak-xxxxx
MODAL_PROXY_SECRET=as-xxxxx

# Load before running commands
set -a; source .env; set +a

# Then curl commands can use:
curl -H "Modal-Key: $MODAL_PROXY_KEY" -H "Modal-Secret: $MODAL_PROXY_SECRET" ...
```

**Token Rotation:**
1. Create a new Proxy Auth token in Modal Dashboard (Workspace > Proxy Auth Tokens)
2. Update `MODAL_PROXY_KEY`/`MODAL_PROXY_SECRET` in your environment, CI secrets, or `.env` file
3. Redeploy or restart the app to pick up new credentials
4. Verify the new token works with a test request
5. Revoke the old token in the Modal dashboard

**Python Client with Proxy Auth:**
```python
import os
import requests

class AuthenticatedAgentClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.headers = {
            "Content-Type": "application/json",
            "Modal-Key": os.environ["MODAL_PROXY_KEY"],
            "Modal-Secret": os.environ["MODAL_PROXY_SECRET"],
        }

    def query(self, question: str) -> dict:
        response = requests.post(
            f"{self.base_url}/query",
            json={"question": question},
            headers=self.headers,
            timeout=120
        )
        response.raise_for_status()
        return response.json()
```

**JavaScript Example:**
```javascript
const response = await fetch(`${baseUrl}/query`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Modal-Key': proxyAuthTokenId,
    'Modal-Secret': proxyAuthTokenSecret
  },
  body: JSON.stringify({ question })
});
```

### Option C: Custom Authentication Middleware

You can add custom authentication middleware to `web_app` in `app.py`:

```python
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    # Your token validation logic here
    if not is_valid_token(token):
        raise HTTPException(status_code=401, detail="Invalid token")
    return token

@web_app.post("/query")
async def query_proxy(
    request: Request,
    body: QueryBody,
    token: str = Depends(verify_token)
):
    # Handler code
    ...
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

### Error Response Format

Error responses follow standard HTTP status codes. Some may include error details:

```json
{
  "detail": "Error message here"
}
```

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

# Usage
result = ask_agent_with_retry(
    "What is Python?",
    "https://your-url.modal.run",
    max_retries=3,
    retry_delay=5.0
)
```

### JavaScript Error Handling

```javascript
async function askAgentWithRetry(question, baseUrl, maxRetries = 3) {
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      const response = await fetch(`${baseUrl}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question })
      });
      
      if (response.status === 503 && attempt < maxRetries - 1) {
        // Service starting up, wait and retry
        await new Promise(resolve => setTimeout(resolve, 5000));
        continue;
      }
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      return await response.json();
    } catch (error) {
      if (attempt === maxRetries - 1) throw error;
      await new Promise(resolve => setTimeout(resolve, 5000));
    }
  }
}
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

**Adjusting Timeouts:**

In `app.py`, modify the `httpx.Timeout` values:

```python
# For /query
async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
    # Increased to 5 minutes

# For /query_stream
async with httpx.AsyncClient(timeout=None) as client:
    # Already no timeout
```

### CORS Configuration

Current CORS settings allow all origins:

```python
web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ⚠️ Too permissive for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**For Production:**

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

### Monitoring

**Health Check Monitoring:**
```bash
# Set up cron job or monitoring service
*/5 * * * * curl -f https://your-url.modal.run/health || alert-admin
```

**Response Time Monitoring:**
```python
import time

start = time.time()
response = requests.post(url, json={"question": question})
duration = time.time() - start

if duration > 5.0:  # Alert if > 5 seconds
    send_alert(f"Slow response: {duration}s")
```

### Logging

Monitor these aspects:
- Request rates and patterns
- Response times (p50, p95, p99)
- Error rates by status code
- Sandbox lifecycle events (startup, shutdown)
- Authentication failures

### Cost Optimization

**Strategies:**
1. **Idle Timeout**: Adjust `sandbox_idle_timeout` to balance cost vs latency
2. **Request Batching**: Combine multiple questions into single requests when possible
3. **Caching**: Cache common queries/responses
4. **Resource Limits**: Right-size CPU/memory allocation

**Monitor Costs:**
- Check Modal dashboard for usage metrics
- Set up billing alerts
- Review function invocation counts
- Monitor sandbox uptime

### Security Best Practices

1. **Enable Authentication**: Use Connect tokens or API keys
2. **Restrict CORS**: Only allow your frontend domains
3. **Validate Input**: Sanitize user questions (already handled by Pydantic)
4. **Rate Limiting**: Prevent abuse
5. **Monitor Access**: Log authentication attempts
6. **Keep Secrets Secure**: Never expose API keys in client-side code

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
- [Controllers](./controllers.md) - How the background service works
- [Modal Ingress](./modal-ingress.md) - How requests reach your application
- [Configuration](./configuration.md) - Configuration options
