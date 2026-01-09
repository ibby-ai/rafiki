# Agent Sandbox Starter (Modal + Claude Agent SDK)

![CI](https://github.com/Saidiibrahim/agent-sandbox-starter/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Modal](https://img.shields.io/badge/Modal-1.2.1+-8B5CF6.svg)
![Claude Agent SDK](https://img.shields.io/badge/Claude%20Agent%20SDK-0.1.4+-FF6B35.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

![Agent Sandbox Starter](docs/images/readme-image.png)

A Modal-based agent sandbox starter that runs the **Claude Agent SDK** in isolated, secure sandboxed environments. Infrastructure for running autonomous AI agents with:

- **Secure execution** via Modal sandboxes
- **HTTP API endpoints** for querying agents
- **MCP (Model Context Protocol)** tool integration
- **Two execution patterns**: short-lived sandboxes (ephemeral, for batch jobs) and long-lived background services (persistent, for low-latency APIs)

## Table of Contents

- [Requirements](#requirements)
- [Setup](#setup)
- [Project Structure](#project-structure)
- [Quickstart](#quickstart)
  - [Development Mode](#development-mode)
  - [Production Mode (Persistent Service)](#production-mode-persistent-service)
  - [Service Management](#service-management)
- [Execution Patterns](#execution-patterns)
  - [Pattern 1: Short-Lived Sandbox](#pattern-1-short-lived-sandbox)
  - [Pattern 2: Long-Lived Service](#pattern-2-long-lived-service)
  - [Which Should I Use?](#which-should-i-use)
- [Modal Concepts for New Users](#modal-concepts-for-new-users)
- [Architecture](#architecture)
  - [Understanding the Diagram](#understanding-the-diagram)
  - [How It Works](#how-it-works)
  - [Key Modules](#key-modules)
  - [Persistent Storage](#persistent-storage)
- [Configuration](#configuration)
  - [Makefile](#makefile)
  - [Customization](#customization)
- [Troubleshooting](#troubleshooting)
- [Documentation](#documentation)
- [Additional Resources](#additional-resources)

## Requirements

- **Modal CLI**: `pip install modal` and `modal setup`
- **Anthropic API key**: store in a Modal Secret named `anthropic-secret` with key `ANTHROPIC_API_KEY`

## Setup

```bash
# Clone and enter the repository
git clone <repo-url>
cd agent-sandbox-starter

# Activate virtual environment and sync dependencies
source .venv/bin/activate
uv sync

# Install pre-commit hooks (enables automatic linting/formatting on commit)
uv run pre-commit install
```

## Project Structure

```
agent_sandbox/

├── __init__.py              # Package initialization & module registration
├── app.py                   # Main Modal app definition
├── deploy.py                # Deployment composition
├── jobs.py                  # Async job queue processing
│
├── config/                  # Configuration management
│   └── settings.py         # Pydantic Settings for env vars & Modal secrets
│
├── agents/                  # Agent execution logic
│   └── loop.py             # Single-shot agent interaction runner
│
├── controllers/            # FastAPI service for background sandbox
│   └── controller.py       # HTTP endpoints (/query, /query_stream, /health_check)
│
├── prompts/                # Prompt definitions
│   └── prompts.py          # SYSTEM_PROMPT and DEFAULT_QUESTION
│
├── schemas/                 # Pydantic models
│   ├── base.py             # Base schema with validation config
│   └── sandbox.py          # QueryBody and sandbox-specific schemas
│
└── tools/                   # MCP tool system
    ├── registry.py          # ToolRegistry class & MCP server management
    ├── calculate_tool.py    # Example tool implementation
    └── __init__.py          # Exports get_mcp_servers, get_allowed_tools
```

## Quickstart

### Development Mode

- **Run locally (spawns a short-lived Sandbox and executes agent loop)**

```bash
modal run -m agent_sandbox.app
```

- **Run the agent as a remote function (one-off execution)**

```bash
modal run -m agent_sandbox.app::run_agent_remote --question "Explain REST vs gRPC"
```

### Production Mode (Persistent Service)

- **Start dev server with hot-reload (recommended for development)**

```bash
modal serve -m agent_sandbox.app
```

Or use the Makefile:

```bash
make serve
```

Once the server is running, you'll get a dev endpoint URL like:

```text
https://<org>--test-sandbox-http-app-dev.modal.run
```

- **Test the HTTP endpoint with curl**

```bash
curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/query' \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the capital of Canada?"}'
```

Or use the Makefile (set `DEV_URL` in Makefile first):

```bash
make curl Q="What is the capital of Canada?"
```

- **Deploy to production**

```bash
modal deploy -m agent_sandbox.deploy
```

### Service Management

- **Terminate the background sandbox** (forces a final flush to persistent volume; optional if volume commits are enabled)

```bash
modal run -m agent_sandbox.app::terminate_service_sandbox
```

By default, the controller commits `/data` every 60 seconds (`volume_commit_interval=60`). Unset
`VOLUME_COMMIT_INTERVAL` (or remove it from `.env`) to revert to commit-on-termination only.

- **Create a filesystem snapshot** (captures current state)

```bash
modal run -m agent_sandbox.app::snapshot_service
```

### Authentication

By default, the public HTTP endpoints are accessible without authentication. To enable Modal Proxy Auth, set
`require_proxy_auth = True` in `agent_sandbox/config/settings.py` (or via `REQUIRE_PROXY_AUTH=true`). Clients must
include Proxy Auth Token headers (`Modal-Key` and `Modal-Secret`) on each request. The HTTP examples accept
`MODAL_PROXY_KEY` and `MODAL_PROXY_SECRET` environment variables to send these headers. See `docs/api-usage.md` for
end-user examples.

If you store Proxy Auth credentials in `.env`, run:

```bash
set -a; source .env; set +a
```

before running the HTTP examples or Makefile curl commands so the headers are picked up.

### Session Resumption (Hybrid)

The `/query` and `/query_stream` endpoints accept optional session fields to resume prior context:

- `session_id`: resume a specific prior session returned by the API
- `session_key`: a server-side key that maps to the last session for a user (stored in a Modal Dict)
- `fork_session`: when resuming, start a new branched session instead of continuing the original

Example (server remembers the last session for `user-123`):

```bash
curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/query' \
  -H 'Content-Type: application/json' \
  -d '{"question":"Continue the plan","session_key":"user-123"}'
```

The response includes a top-level `session_id` you can store for explicit resumption later. You can
configure the backing Modal Dict name with `SESSION_STORE_NAME` in `agent_sandbox/config/settings.py`.

### Job Queue (Async Processing)

For long-running tasks, use the job queue to avoid blocking:

```bash
# Submit a job
curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/submit' \
  -H 'Content-Type: application/json' \
  -d '{"question":"Analyze this large dataset..."}'
# Returns: {"ok": true, "job_id": "abc123..."}

# Check job status
curl 'https://<org>--test-sandbox-http-app-dev.modal.run/jobs/abc123...'
# Returns: {"ok": true, "status": "running", ...}

# Cancel a queued job
curl -X DELETE 'https://<org>--test-sandbox-http-app-dev.modal.run/jobs/abc123...'
```

**Job lifecycle:** `queued` → `running` → `complete` | `failed` | `canceled`

**When to use:**
- Long-running analysis or generation tasks
- Background processing where immediate response isn't needed
- Batch operations that may take minutes

**Note:** In development, run `modal run -m agent_sandbox.app::process_job_queue` to consume queued jobs.
Set `job_queue_cron` in settings to schedule automatic processing.

## Execution Patterns

This starter supports **two patterns** for running the agent. Choose based on your use case:

### Pattern 1: Short-Lived Sandbox

```bash
# One-off execution
modal run -m agent_sandbox.app::run_agent_remote --question "Your question"

# Or run the default question
modal run -m agent_sandbox.app
```

**How it works:**
1. Creates a new sandbox
2. Runs the agent
3. Returns the response
4. Terminates the sandbox

**Best for:**
- Quick testing during development
- CI/CD pipelines
- Batch processing jobs
- Scheduled tasks (cron jobs)
- Background agents (queue-based, fire-and-forget tasks)

**Trade-offs:**
- Cold-start delay (~5-15 seconds) on each execution
- Tasks can still run for hours; "short-lived" refers to the sandbox lifecycle pattern, not task duration

### Pattern 2: Long-Lived Service

```bash
# Development (with hot-reload)
modal serve -m agent_sandbox.app

# Production
modal deploy -m agent_sandbox.deploy
```

**How it works:**
1. Creates a persistent sandbox running a FastAPI service
2. Sandbox stays warm between requests
3. HTTP gateway proxies requests to the sandbox
4. Sandbox terminates after idle timeout (default: 10 minutes)

**Best for:**
- Production APIs requiring low latency
- Interactive applications
- High-frequency queries
- Stateful operations using `/data` volume

**Trade-off:** Costs continue while sandbox is idle; more complex architecture.

### Which Should I Use?

| Scenario | Recommended Pattern |
|----------|---------------------|
| Just exploring / learning | Pattern 1 (simplest) |
| Building a production API | Pattern 2 (low latency) |
| Running in CI/CD | Pattern 1 (clean isolation) |
| Need persistent file storage | Pattern 2 (volume access) |
| Cost-sensitive, low traffic | Pattern 1 (pay per use) |
| High traffic, latency-sensitive | Pattern 2 (warm sandbox) |
| Background agents (queue-based) | Pattern 1 (isolated per task) |

## Modal Concepts for New Users

If you're new to Modal, here's what you need to know before diving into the architecture:

| Term | What It Means |
|------|---------------|
| **Modal App** | A collection of serverless functions that run in Modal's cloud infrastructure |
| **Sandbox** | An isolated container environment where your code runs safely with its own filesystem |
| **Volume** | Persistent storage that survives container restarts (like a cloud disk mounted at `/data`) |
| **Encrypted Port/Tunnel** | A secure internal connection between Modal components; used for service-to-service communication |
| **`@modal.asgi_app()`** | A decorator that turns a Python web app (like FastAPI) into a public HTTPS endpoint |
| **Cold Start** | The delay when a new container spins up; this project uses a long-lived sandbox to avoid it |

For more details, see [Modal's Getting Started Guide](https://modal.com/docs/guide).

## Architecture

### Quick Overview

```
                         ┌──────────────────────────────────────────────────────────────┐
                         │                    Modal HTTP Gateway                         │
User Request ───────────▶│                                                              │
(+ Proxy Auth headers)   │  /query, /query_stream ────────▶ Background Sandbox Proxy    │
                         │  /submit, /jobs/{id} ──────────▶ Job Queue / Status          │
                         │  /claude_cli ──────────────────▶ Direct Function Call        │
                         └──────────────┬───────────────────────────┬──────────────────┘
                                        │                           │
               ┌────────────────────────┘                           └────────────────────┐
               │                                                                         │
               ▼                                                                         ▼
┌──────────────────────────────────────────┐      ┌────────────────────────────────────────────┐
│   Background Sandbox (Agent SDK Image)   │      │     Claude CLI Function (CLI Image)        │
│   ────────────────────────────────────   │      │   ──────────────────────────────────────   │
│   FastAPI Controller on :8001            │      │   Short-lived Modal function               │
│   Runs as: root in /root/app             │      │   Runs as: claude user (non-root)          │
│                                          │      │   Working dir: /home/claude/app            │
│   Claude Agent SDK ─── MCP Tools         │      │                                            │
│          │                               │      │   Claude Code CLI subprocess               │
│   ┌──────┴──────┐                        │      │   (supports --dangerously-skip-permissions)│
│   │ /data vol   │  Session Store         │      │          │                                 │
│   └─────────────┘  (Modal Dict)          │      │   ┌──────┴──────┐                          │
└──────────────────────────────────────────┘      │   │ /data vol   │                          │
                                                  │   └─────────────┘                          │
                                                  └────────────────────────────────────────────┘
```

### Detailed Architecture

This project uses a **dual-image architecture** with separate containers for the Agent SDK and Claude CLI:

```
                                 ┌─────────────────────────────────────────────────────────────────┐
                                 │                           Modal Cloud                           │
   ┌──────────────┐              │  ┌─────────────────────────────────────────────────────────┐   │
   │              │   HTTP POST  │  │              http_app (FastAPI Gateway)                 │   │
   │    Client    │─────────────────▶  /query, /query_stream  │  /claude_cli                 │   │
   │              │◀─ Proxy Auth ──│  /submit, /jobs/{id}     │                              │   │
   └──────────────┘              │  └────────────┬────────────┴────────────────┬─────────────┘   │
                                 │               │                             │                  │
                                 │     ┌─────────┴─────────┐         ┌─────────┴─────────┐       │
                                 │     │                   │         │                   │       │
                                 │     ▼ proxy             │         ▼ .remote()         │       │
                                 │  ┌──────────────────────┴──┐   ┌──────────────────────┴──┐    │
                                 │  │    Agent SDK Image      │   │    Claude CLI Image     │    │
                                 │  │   (Background Sandbox)  │   │  (Short-lived Function) │    │
                                 │  │  ────────────────────   │   │  ────────────────────   │    │
                                 │  │                         │   │                         │    │
                                 │  │  ┌───────────────────┐  │   │  User: claude (non-root)│    │
                                 │  │  │ FastAPI :8001     │  │   │  Dir: /home/claude/app  │    │
                                 │  │  │ (uvicorn)         │  │   │                         │    │
                                 │  │  └─────────┬─────────┘  │   │  ┌───────────────────┐  │    │
                                 │  │            │            │   │  │ Claude Code CLI   │  │    │
                                 │  │  User: root             │   │  │ subprocess        │  │    │
                                 │  │  Dir: /root/app         │   │  └─────────┬─────────┘  │    │
                                 │  │            │            │   │            │            │    │
                                 │  │            ▼            │   │            │            │    │
                                 │  │  ┌───────────────────┐  │   │            │            │    │
                                 │  │  │ Claude Agent SDK  │  │   │            │            │    │
                                 │  │  │ ┌─────┐ ┌───────┐ │  │   │            │            │    │
                                 │  │  │ │ MCP │ │ Tools │ │  │   │            │            │    │
                                 │  │  │ └─────┘ └───────┘ │  │   │            │            │    │
                                 │  │  └───────────────────┘  │   │            │            │    │
                                 │  │            │            │   │            │            │    │
                                 │  └────────────┼────────────┘   └────────────┼────────────┘    │
                                 │               │                             │                  │
                                 │               └──────────────┬──────────────┘                  │
                                 │                              ▼                                 │
                                 │               ┌──────────────────────────────┐                 │
                                 │               │  Shared Resources            │                 │
                                 │               │  • /data Volume (persistent) │                 │
                                 │               │  • Modal Dicts (session/job) │                 │
                                 │               └──────────────────────────────┘                 │
                                 └────────────────────────────────────────────────────────────────┘
```

### Understanding the Diagram

| Component | Purpose | Why It Exists |
|-----------|---------|---------------|
| **Modal Cloud** | Fully managed infrastructure | You don't deploy or manage servers; Modal handles scaling, networking, and SSL |
| **http_app (FastAPI Gateway)** | Lightweight HTTP entry point | Scales to zero when idle; handles routing without running the full agent |
| **Proxy Auth** | API authentication | Secure production endpoints with `Modal-Key`/`Modal-Secret` token headers |
| **Agent SDK Image** | Container for agent queries | Runs as root in `/root/app`; long-lived sandbox with FastAPI controller |
| **Claude CLI Image** | Container for CLI requests | Runs as non-root `claude` user in `/home/claude/app`; short-lived function |
| **run_claude_cli_remote** | Dedicated CLI function | Executes Claude Code CLI in isolated container with skip-permissions support |
| **JOB_QUEUE (Modal Queue)** | Async job processing | Fire-and-forget workloads; long-running tasks processed by workers |
| **Proxy connection** | Internal forwarding | Decouples the public API from the agent runtime; enables independent scaling |
| **Long-lived Modal Sandbox** | Persistent agent environment | Stays warm for hours; eliminates cold-start delays on each request |
| **FastAPI Controller** | Agent orchestration service | Manages Claude SDK client, tool permissions, and streaming responses |
| **Claude Agent SDK + MCP Tools** | AI agent capabilities | The actual agent logic with its configured tools (WebSearch, file operations, etc.) |
| **/data vol (persist)** | Durable file storage | Files written here survive sandbox restarts; critical for stateful operations |
| **Modal Dicts (SESSION/JOB)** | Session & job state storage | Resume conversations; track async job status |

### How It Works

1. **Background Service**: A long-lived `modal.Sandbox` runs a FastAPI microservice (`agent_sandbox.controllers.controller`) that handles agent queries
2. **HTTP Endpoint**: `http_app` in `agent_sandbox.app` proxies requests to the background service
3. **Volume Persistence**: Files written to `/data` are persisted across sandbox restarts
4. **Low Latency**: The background service avoids cold-start delays while keeping the HTTP endpoint responsive

### Key Modules

- `agent_sandbox/app.py`: Defines the Modal `App`, persistent sandbox management, and HTTP endpoints
- `agent_sandbox/controllers/controller.py`: FastAPI microservice running inside the sandbox with `/health_check`, `/query`, and `/query_stream` endpoints
- `agent_sandbox/agents/loop.py`: Standalone agent runner (used by `run_agent_remote` for one-off executions)
- `agent_sandbox/config/settings.py`: Pydantic Settings for configuration and Modal secrets management
- `agent_sandbox/tools/`: MCP tool system with registry and individual tool implementations (WebSearch, WebFetch, Read, Write). Obtained from [Claude Agent SDK Documentation](https://docs.claude.com/en/api/agent-sdk/python)
- `agent_sandbox/prompts/prompts.py`: System prompt and default question

### Persistent Storage

**Important**: Files must be written to `/data` to persist across sandbox restarts:

```python
# ✅ Persisted
with open("/data/myfile.py", "w") as f:
    f.write("code here")

# ❌ Not persisted (lost on restart)
with open("/tmp/myfile.py", "w") as f:
    f.write("code here")
```

The system prompt automatically instructs the agent to use `/data` for file operations.

### Claude Code CLI: Running Code

Claude Code CLI runs in a dedicated Modal function as a non-root user. To create files
and execute code, use a job workspace and allow the required tools:

- **Pass `--job-id`** so files land under `/data/jobs/<job_id>/` and persist.
- **Allow tools**: `Write` to create files and `Bash` to execute them.
- **Increase timeouts** for longer tasks (default CLI timeout is 120 seconds).
- **Default skip-permissions**: `run_claude_cli_remote` defaults to `--dangerously-skip-permissions`.
- **CLI output capture**: `modal run` only writes return values when they are strings/bytes. Use `--return-stdout` with `--write-result` (JSON fallback is returned if stdout/stderr is empty).
- **If output is empty**: Check the Modal run logs and confirm `anthropic-secret` is configured with `ANTHROPIC_API_KEY` so the Claude CLI can authenticate.

Examples:

```bash
# Python: create and run a file
modal run -m agent_sandbox.app::run_claude_cli_remote \
  --job-id "550e8400-e29b-41d4-a716-446655440000" \
  --prompt "Create game.py and run it to show sample output" \
  --allowed-tools "Write,Bash,Read" \
  --timeout-seconds 300

# Node: create and run a file
modal run -m agent_sandbox.app::run_claude_cli_remote \
  --job-id "550e8400-e29b-41d4-a716-446655440000" \
  --prompt "Create index.js and run it with node" \
  --allowed-tools "Write,Bash,Read" \
  --timeout-seconds 300

# Full bypass (use sparingly)
modal run -m agent_sandbox.app::run_claude_cli_remote \
  --job-id "550e8400-e29b-41d4-a716-446655440000" \
  --prompt "Create app.py and run it" \
  --dangerously-skip-permissions \
  --timeout-seconds 300

# Capture CLI output to a file (modal run only writes string/bytes results)
modal run -m agent_sandbox.app::run_claude_cli_remote \
  --prompt "Say hello in one sentence" \
  --return-stdout \
  --write-result ./claude_cli_output.txt
```

Without `--job-id`, files are written under `/home/claude/app` and are not persisted.
The Claude CLI container mounts the shared `/data` Modal volume for persisted artifacts.

For long-running runs, use async submission and polling:

```bash
# Start a run and get a call_id
curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/claude_cli/submit' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Create app.py and run it","allowed_tools":["Write","Bash","Read"],"job_id":"550e8400-e29b-41d4-a716-446655440000","timeout_seconds":300}'

# Poll for completion
curl -X GET 'https://<org>--test-sandbox-http-app-dev.modal.run/claude_cli/result/<call_id>'
```

Example polling loop:

```bash
call_id="<call_id>"
while true; do
  resp=$(curl -s "https://<org>--test-sandbox-http-app-dev.modal.run/claude_cli/result/${call_id}")
  echo "$resp"
  if echo "$resp" | grep -q '"status":"complete"\|"status":"failed"\|"status":"expired"'; then
    break
  fi
  sleep 2
done
```

Status polling behavior:

- `202` + `{"status":"running"}` while the run is still executing
- `200` + `{"status":"complete","result":{...}}` when finished
- `410` + `{"status":"expired"}` if the result TTL has passed
- `500` + `{"status":"failed","error":"..."}` on execution errors

## Configuration

### Makefile

The Makefile provides convenient commands for development:

```bash
# Start dev server
make serve

# Test endpoint with a question
make curl Q="Your question here"

# Show dev URL
make dev-url
```

Update `DEV_URL` in the Makefile to match your dev endpoint.

### Customization

- **Change the system prompt**: edit `agent_sandbox/prompts/prompts.py`
- **Add or modify tools**: edit `agent_sandbox/tools/` (add new tool files and register in `registry.py`)
- **Adjust runtime configuration**: edit `agent_sandbox/config/settings.py` for settings, or `agent_sandbox/app.py` for image configuration
- **Modify service behavior**: edit `agent_sandbox/controllers/controller.py`
- **Change sandbox settings**: edit `agent_sandbox/config/settings.py` (timeouts, memory, CPU, etc.)

## Troubleshooting

- **Ensure the Modal secret exists**: `modal secret create anthropic-secret ANTHROPIC_API_KEY=<your-key>`
- **Run `modal setup`**: Required if you haven't logged in or configured Modal locally
- **Check service health**: Once `modal serve` is running, verify with:

  ```bash
  curl "${DEV_URL}/health_check"
  ```

- **Volume persistence**: Remember to write files to `/data`, not `/tmp` or other ephemeral locations
- **Sandbox timeout**: The background service is configured with a 12-hour max lifetime and 10-minute idle timeout (Modal's default is 5 minutes, max 24 hours; adjust in `agent_sandbox/config/settings.py`)
- **Service URL discovery**: The endpoint waits up to 30 seconds for the encrypted tunnel URL to be available

## Documentation

Comprehensive documentation is available in the [`docs/`](./docs/) directory:

- **[Architecture Overview](./docs/architecture.md)** - System architecture, component responsibilities, and request flow
- **[Controllers: Background Service](./docs/controllers.md)** - Deep dive into the controller service that runs the agent
- **[Modal Ingress](./docs/modal-ingress.md)** - How Modal handles HTTP ingress and routes requests
- **[API Usage Guide](./docs/api-usage.md)** - Complete guide for end users: endpoints, examples, authentication, error handling
- **[Configuration Guide](./docs/configuration.md)** - Configuration options and environment setup
- **[Troubleshooting Guide](./docs/troubleshooting.md)** - Common issues and solutions
- **[Tool Development Guide](./docs/tool-development.md)** - Creating custom MCP tools
- **[Documentation Index](./docs/README.md)** - Complete documentation index

## Additional Resources

- [Modal Documentation](https://modal.com/docs)
- [Claude Agent SDK Documentation](https://docs.claude.com/en/api/agent-sdk/python)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
