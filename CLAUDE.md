# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Modal-based agent sandbox starter that runs Claude Agent SDK in isolated sandboxed environments. The architecture enables running autonomous AI agents with secure execution, persistent background services, and HTTP API endpoints.

**Key Technologies:**

- Modal (serverless infrastructure and sandboxing)
- Claude Agent SDK (`claude-agent-sdk`)
- FastAPI (HTTP endpoints and internal service)
- MCP (Model Context Protocol) for tool integration
- uv (Python package manager)

## Prerequisites

Before working with this codebase:

1. uv must be installed: <https://docs.astral.sh/uv/getting-started/installation/>
2. Activate the virtual environment: `source .venv/bin/activate`
3. Sync dependencies: `uv sync`
4. Modal must be configured: `modal setup`
5. Anthropic API key must be stored in Modal Secret named `anthropic-secret` with key `ANTHROPIC_API_KEY`

**Important:** Always activate the `.venv` before running commands. Use `uv run` to run Python commands (e.g., `uv run pytest`).

## Code Quality

The project uses pre-commit hooks with ruff for linting and formatting. Hooks run automatically on commit.

**Important:** Always run the ruff linter and formatter after making code changes:

```bash
uv run ruff check --fix .
uv run ruff format .
```

Other useful commands:

```bash
# Run all pre-commit hooks on all files
uv run pre-commit run --all-files
```

## Common Commands

### Development & Testing

```bash
# Run agent locally (spawns sandbox and executes agent loop)
modal run -m agent_sandbox.app

# Run agent as remote function
modal run -m agent_sandbox.app::run_agent_remote --question "Your question here"

# Run Claude Code CLI via the background sandbox
modal run -m agent_sandbox.app::run_claude_cli_remote \
  --prompt "Summarize repo layout" \
  --allowed-tools "Read"

# Run Claude Code CLI and allow code execution
modal run -m agent_sandbox.app::run_claude_cli_remote \
  --job-id "550e8400-e29b-41d4-a716-446655440000" \
  --prompt "Create hello.py and run it" \
  --allowed-tools "Write,Bash,Read" \
  --timeout-seconds 300

# Capture CLI output to a file (modal run only writes string/bytes results)
modal run -m agent_sandbox.app::run_claude_cli_remote \
  --prompt "Say hello in one sentence" \
  --return-stdout \
  --write-result ./claude_cli_output.txt

# Start dev server with hot reload (enables HTTP endpoints)
modal serve -m agent_sandbox.app

# Deploy to production
modal deploy -m agent_sandbox.deploy
```

### Testing HTTP Endpoints

When `modal serve -m agent_sandbox.app` or `modal deploy -m agent_sandbox.deploy` is running:

```bash
# Test the query endpoint
curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/query' \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the capital of Canada?"}'

# Test the Claude Code CLI endpoint
curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/claude_cli' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Summarize repo layout","allowed_tools":["Read"],"output_format":"json"}'

# Test streaming endpoint
curl -N -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/query_stream' \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the capital of Canada?"}'
```

## Architecture

### Execution Patterns

The codebase demonstrates two distinct patterns for running agents:

**1. Short-lived Sandbox Pattern** (`main` entrypoint)

- Creates ephemeral sandbox
- Executes `agent_sandbox.agents.loop` as a module
- Captures stdout/stderr
- Terminates sandbox after completion
- Use for: batch jobs, scheduled tasks, one-off queries

**2. Long-lived Background Service Pattern** (`http_app` + `agent_sandbox.controllers.controller`)

- Maintains persistent sandbox running FastAPI service via uvicorn
- Service runs on encrypted port 8001 with tunnel URL
- HTTP endpoint proxies requests to service
- Sandbox persists for 12 hours (timeout) or 10 minutes idle
- Use for: low-latency serving, repeated queries, production APIs

### Key Components

**`agent_sandbox/app.py`** - Modal app definition and entry points

- Defines `modal.App("test-sandbox")`
- `get_or_start_background_sandbox()`: Manages persistent sandbox lifecycle with health checks
- `http_app`: ASGI app exposing HTTP endpoints that proxy to background service
- `run_agent_remote`: Short-lived function for single queries
- `main`: Local CLI entry point for `modal run`

**`agent_sandbox/agents/loop.py`** - CLI-based agent execution

- Builds `ClaudeAgentOptions` from MCP servers and prompts
- `run_agent()`: Single query execution with streaming output
- Used by short-lived sandboxes via `sb.exec("python", "-m", "agent_sandbox.agents.loop", ...)`

**`agent_sandbox/controllers/controller.py`** - FastAPI microservice for background sandbox

- `GET /health_check`: Liveness probe used by controller
- `POST /query`: Agent query endpoint that returns responses
- `POST /query_stream`: Agent query endpoint that streams responses as SSE
- `POST /claude_cli`: Claude Code CLI endpoint for programmatic CLI calls
- Runs via `uvicorn agent_sandbox.controllers.controller:app --host 0.0.0.0 --port 8001`
- Uses `permission_mode="acceptEdits"` with `can_use_tool` handler for controlled tool access
- Supports session resumption via `session_id`, `session_key`, `fork_session`

**`agent_sandbox/app.py`** - HTTP Gateway endpoints (in addition to above)

- `POST /submit`: Enqueue async job to `JOB_QUEUE`
- `GET /jobs/{job_id}`: Check job status from `JOB_RESULTS` dict
- `DELETE /jobs/{job_id}`: Cancel a queued job

**`agent_sandbox/config/settings.py`** - Configuration management

- `Settings`: Pydantic Settings class for environment variables and configuration
- `get_modal_secrets()`: Returns Modal Secret objects
- Centralized configuration for sandbox settings, timeouts, resources, etc.

**`agent_sandbox/app.py`** - Image building

- `_base_anthropic_sdk_image()`: Builds container with Python 3.11, FastAPI, uvicorn, httpx, claude-agent-sdk, Node.js 20, @anthropic-ai/claude-agent-sdk, and the Claude Code CLI
- Working directory: `/root/app`
- Copies local project and installs dependencies

**`agent_sandbox/tools/`** - MCP tool system

- `registry.py`: `ToolRegistry` class managing MCP servers and allowed tools
- `calculate_tool.py`: Example tool implementation
- Individual tools live in separate files for better organization
- Tool naming: `mcp__<server>__<tool>` (e.g., `mcp__utilities__calculate`)
- `get_mcp_servers()` and `get_allowed_tools()`: Convenience functions for accessing registry

**`agent_sandbox/prompts/prompts.py`** - Agent prompts

- `SYSTEM_PROMPT`: Configures agent behavior and tone
- `DEFAULT_QUESTION`: Fallback query when none provided

**`agent_sandbox/jobs.py`** - Async job processing

- `enqueue_job()`: Submit job to Modal Queue
- `get_job_status()`: Check job status from Modal Dict
- `cancel_job()`: Cancel a queued job
- `process_job_queue()`: Worker function that consumes jobs
- Uses Modal Queue and Dict for distributed state

### Background Sandbox Lifecycle

The persistent sandbox pattern in `get_or_start_background_sandbox()` (agent_sandbox/app.py):

1. Checks for existing sandbox in global `SANDBOX` and `SERVICE_URL` variables
2. Creates sandbox with `modal.Sandbox.create()` running uvicorn command
3. Polls `sandbox.tunnels()` for up to 30 seconds to get encrypted port URL
4. Calls `_wait_for_service()` to poll `/health_check` until 200 OK (60s timeout)
5. Returns sandbox handle and service URL for proxying

The sandbox persists across multiple requests within the same Modal worker, avoiding cold starts.

### Permission Modes

- `agent_sandbox/agents/loop.py`: Uses default permission mode (requires user approval for tools)
- `agent_sandbox/controllers/controller.py`: Uses `permission_mode="acceptEdits"` with `can_use_tool` handler for controlled tool access

When adding new endpoints or execution patterns, choose permission mode based on trust level and use case.

## Extending the Codebase

### Adding New Tools

1. Create a new file in `agent_sandbox/tools/` (e.g., `my_tool.py`):

```python
from claude_agent_sdk import tool
from typing import Any

@tool("my_tool_name", "Description", {"param": str})
async def my_tool(args: dict[str, Any]) -> dict[str, Any]:
    # Implementation
    return {"content": [{"type": "text", "text": "result"}]}
```

2. Register in `agent_sandbox/tools/registry.py`:

```python
from agent_sandbox.tools.my_tool import my_tool

# In ToolRegistry._initialize_defaults():
multi_tool_server = create_sdk_mcp_server(
    name="utilities",
    version="1.0.0",
    tools=[calculate, my_tool]  # Add your tool
)

# Add to allowed tools if needed
self._allowed_tools.append("mcp__utilities__my_tool_name")
```

### Modifying Agent Behavior

Edit `agent_sandbox/prompts/prompts.py` to change `SYSTEM_PROMPT`.

### Adjusting Runtime Configuration

Edit `agent_sandbox/config/settings.py`:

- Modify `Settings` class attributes for configuration values
- Update `get_modal_secrets()` to add new secrets

Edit `agent_sandbox/app.py`:

- Modify `_base_anthropic_sdk_image()` to add pip packages (`.pip_install()`)
- Add apt packages (`.apt_install()`)
- Adjust image build steps

### Adding HTTP Endpoints

Add new endpoints to `agent_sandbox/app.py`:

```python
@web_app.post("/your_endpoint")
async def your_endpoint(request: Request, body: QueryBody):
    # Implementation
    pass
```

Or add to the background service in `agent_sandbox/controllers/controller.py`:

```python
@app.post("/your_endpoint")
async def your_endpoint(body: QueryBody, request: Request):
    # Implementation
    pass
```

## Important Notes

- **Security**: `calculate` tool uses `eval()` - replace with safe parser for production (agent_sandbox/tools/calculate_tool.py)
- **Sandbox Timeouts**: Background sandbox runs for max 24 hours or 10 minutes idle (configurable in `agent_sandbox/config/settings.py`)
- **Autoscaling**: `min_containers=1` keeps containers warm by default; adjust `max_containers`, `scaledown_window` for cost/latency tradeoffs
- **Tool Wildcards**: `ALLOWED_TOOLS` supports wildcards like `"WebSearch(*)"` (agent_sandbox/tools/registry.py)
- **Node.js Dependency**: Agent SDK requires `@anthropic-ai/claude-agent-sdk` npm package (agent_sandbox/app.py)
- **Python Version**: Image uses Python 3.11 (agent_sandbox/app.py)
- **Module Mode**: All commands use `-m agent_sandbox.*` for proper package discovery
- **Agent Turn Limits**: Set `agent_max_turns` to limit conversation turns and prevent runaway loops

### Claude Code CLI in Modal Sandbox

The `/claude_cli` endpoint runs the Claude Code CLI via subprocess. Important considerations:

- **Non-root execution**: The CLI is executed as the `claude` user so `--dangerously-skip-permissions` can be used when requested. Use with care.
- **Default skip-permissions**: `run_claude_cli_remote` defaults to `--dangerously-skip-permissions` to avoid non-interactive approval prompts. Use `--allowedTools` to scope access when possible.

> **Security Note**: The `dangerously_skip_permissions=True` default is intentional for non-interactive
> task execution. This bypasses tool approval prompts, allowing the CLI to execute tools autonomously.
> Always scope tool access via `--allowedTools` when possible to limit what the CLI can do.
> For example, use `--allowedTools "Read"` for read-only operations.
- **Non-interactive mode**: The `-p` flag enables "print mode" which is non-interactive. Combined with `stdin=subprocess.DEVNULL`, this ensures the CLI won't hang waiting for user input.
- **Volume operations**: `reload()` and `commit()` on Modal Volumes can only be called from within a Modal function context, not from a sandbox subprocess. The code handles these errors gracefully.
- **Long-running CLI runs**: `run_claude_cli_remote` is configured with a 24-hour Modal function timeout. Set `--timeout-seconds` to control the CLI subprocess timeout.
- **Shared volume access**: The Claude CLI container mounts the shared `/data` Modal volume. Use `--job-id` to write into `/data/jobs/<job_id>/` for persistence.
- **CLI output capture**: `modal run` only writes return values when they are strings/bytes. Use `--return-stdout` with `--write-result` to capture output reliably (JSON fallback is returned if stdout/stderr is empty).
- **If output is empty**: Check the Modal run logs and confirm `anthropic-secret` is configured with `ANTHROPIC_API_KEY` so the Claude CLI can authenticate. A successful exit with empty stdout/stderr usually indicates missing auth or misconfigured CLI settings.

### Claude Code CLI Async Polling

For long-running runs, use async submission with polling:

```bash
# Start a run and get a call_id
curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/claude_cli/submit' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Create app.py and run it","allowed_tools":["Write","Bash","Read"],"job_id":"550e8400-e29b-41d4-a716-446655440000","timeout_seconds":300}'

# Poll for completion
curl -X GET 'https://<org>--test-sandbox-http-app-dev.modal.run/claude_cli/result/<call_id>'

# Cancel a running job
curl -X DELETE 'https://<org>--test-sandbox-http-app-dev.modal.run/claude_cli/<call_id>'
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
- `200` + `{"status":"cancelled"}` if the run was cancelled via DELETE
- `410` + `{"status":"expired"}` if the result TTL has passed
- `500` + `{"status":"failed","error":"..."}` on execution errors

### Running Code with Claude Code CLI

To create files and execute code, ensure you:

- **Pass `--job-id`** so the CLI runs in a volume-backed workspace at `/data/jobs/<job_id>/`.
- **Allow tools** needed for execution: `Write` for file creation and `Bash` for running code.
- **Set timeout** for longer runs (the CLI defaults to 120 seconds).

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
```

Notes:

- Without `--job-id`, files are written under `/home/claude/app` and are not persisted.
- Files written under `/data/jobs/<job_id>/` are persisted after the CLI finishes.

### Volume Persistence Behavior

When `volume_commit_interval` is configured in settings:

- The persistent volume is reloaded before each query to get the latest committed state
- The volume is committed after each query (respecting the configured interval)
- This ensures writes are persisted without requiring sandbox termination
- Note: Commits occur after all requests (including read-only) when the interval is reached
- Set `volume_commit_interval` to `None` (default) to disable automatic commits; writes persist only on sandbox termination

## Browser Automation

When the user asks to work with Chrome or perform browser automation tasks, use the `claude-in-chrome` MCP server.
