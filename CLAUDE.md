# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Modal-based agent sandbox starter that runs Claude Agent SDK in isolated sandboxed environments. The architecture enables running autonomous AI agents with secure execution, persistent background services, and HTTP API endpoints.

**Key Technologies:**

- Modal (serverless infrastructure and sandboxing)
- Claude Agent SDK (`claude-agent-sdk`)
- FastAPI (HTTP endpoints and internal service)
- MCP (Model Context Protocol) for tool integration

## Prerequisites

Before working with this codebase:

1. Modal CLI must be installed: `pip install modal`
2. Modal must be configured: `modal setup`
3. Anthropic API key must be stored in Modal Secret named `anthropic-secret` with key `ANTHROPIC_API_KEY`

## Common Commands

### Development & Testing

```bash
# Run agent locally (spawns sandbox and executes runner.py)
modal run main.py

# Ask custom question via sandbox controller
modal run main.py::sandbox_controller --question "Your question here"

# Run agent as remote function
modal run main.py::run_agent_remote --question "Your question here"

# Start dev server with hot reload (enables HTTP endpoints)
modal serve main.py

# Deploy to production
modal deploy main.py
```

### Testing HTTP Endpoints

When `modal serve main.py` or `modal deploy main.py` is running:

```bash
# Test the main endpoint (note: body is a JSON string, not object)
curl -X POST 'https://<org>--test-sandbox-test-endpoint-dev.modal.run' \
  -H 'Content-Type: application/json' \
  -d '"What is the capital of Canada?"'
```

## Architecture

### Execution Patterns

The codebase demonstrates two distinct patterns for running agents:

**1. Short-lived Sandbox Pattern** (`sandbox_controller`, `main`)

- Creates ephemeral sandbox
- Executes `runner.py` as a subprocess
- Captures stdout/stderr
- Terminates sandbox after completion
- Use for: batch jobs, scheduled tasks, one-off queries

**2. Long-lived Background Service Pattern** (`test_endpoint` + `runner_service.py`)

- Maintains persistent sandbox running FastAPI service via uvicorn
- Service runs on encrypted port 8001 with tunnel URL
- HTTP endpoint proxies requests to service
- Sandbox persists for 6 hours (timeout) or 10 minutes idle
- Use for: low-latency serving, repeated queries, production APIs

### Key Components

**`main.py`** - Modal app definition and entry points

- Defines `modal.App("test-sandbox")`
- `get_or_start_background_sandbox()`: Manages persistent sandbox lifecycle with health checks
- `test_endpoint`: HTTP endpoint that proxies to background service (main.py:185)
- `run_agent_remote`: Short-lived function for single queries (main.py:164)
- `sandbox_controller`: Scheduled job pattern (cron-based) (main.py:247)
- `main`: Local CLI entry point for `modal run` (main.py:279)

**`runner.py`** - CLI-based agent execution

- Builds `ClaudeAgentOptions` from MCP servers and prompts
- `run_agent()`: Single query execution with streaming output
- Used by short-lived sandboxes via `sb.exec("python", "runner.py", ...)`

**`runner_service.py`** - FastAPI microservice for background sandbox

- `GET /health_check`: Liveness probe used by controller (runner_service.py:59)
- `POST /query`: Agent query endpoint that streams responses (runner_service.py:74)
- Runs via `uvicorn runner_service:app --host 0.0.0.0 --port 8001`
- Uses `permission_mode="bypassPermissions"` for autonomous operation (runner_service.py:55)

**`utils/env_templates.py`** - Modal environment configuration

- `AgentEnvTemplate`: Bundles Modal Image, workdir, and secrets
- `_base_anthropic_sdk_image()`: Builds container with Python 3.11, FastAPI, uvicorn, httpx, claude-agent-sdk, Node.js 20, and @anthropic-ai/claude-code
- Working directory: `/root/app`
- All environment templates centralized in `ENV_TEMPLATES` registry

**`utils/tools.py`** - MCP tool definitions

- Custom tools: `calculate`, `translate`, `search_web` (stubs for extension)
- Tools bundled in MCP server named "utilities"
- Tool naming: `mcp__<server>__<tool>` (e.g., `mcp__utilities__calculate`)
- `ALLOWED_TOOLS`: Whitelist controlling agent tool access

**`utils/prompts.py`** - Agent prompts

- `SYSTEM_PROMPT`: Configures agent behavior and tone
- `DEFAULT_QUESTION`: Fallback query when none provided

### Background Sandbox Lifecycle

The persistent sandbox pattern in `get_or_start_background_sandbox()` (main.py:81):

1. Checks for existing sandbox in global `SANDBOX` and `SERVICE_URL` variables
2. Creates sandbox with `modal.Sandbox.create()` running uvicorn command
3. Polls `sandbox.tunnels()` for up to 30 seconds to get encrypted port URL
4. Calls `_wait_for_service()` to poll `/health_check` until 200 OK (60s timeout)
5. Returns sandbox handle and service URL for proxying

The sandbox persists across multiple requests within the same Modal worker, avoiding cold starts.

### Permission Modes

- `runner.py`: Uses default permission mode (requires user approval for tools)
- `runner_service.py`: Uses `permission_mode="bypassPermissions"` for fully autonomous operation (runner_service.py:55)

When adding new endpoints or execution patterns, choose permission mode based on trust level and use case.

## Extending the Codebase

### Adding New Tools

Edit `utils/tools.py`:

```python
@tool("your_tool_name", "Description", {"param": type})
async def your_tool(args: dict[str, Any]) -> dict[str, Any]:
    # Implementation
    return {"content": [{"type": "text", "text": "result"}]}

# Add to server
multi_tool_server = create_sdk_mcp_server(
    name="utilities",
    version="1.0.0",
    tools=[calculate, translate, search_web, your_tool]
)

# Add to allowed list
ALLOWED_TOOLS = [
    "mcp__utilities__your_tool_name",
    # ... existing tools
]
```

### Modifying Agent Behavior

Edit `utils/prompts.py` to change `SYSTEM_PROMPT`.

### Adjusting Runtime Environment

Edit `utils/env_templates.py`:

- Add pip packages to `.pip_install()`
- Add apt packages to `.apt_install()`
- Add new secrets to `secrets` list
- Create new environment templates in `ENV_TEMPLATES` registry

### Adding HTTP Endpoints

Add new functions to `main.py`:

```python
@app.function(
    image=agent_sdk_env.image,
    secrets=agent_sdk_env.secrets,
)
@modal.fastapi_endpoint(method="POST")
async def your_endpoint(request: Request) -> Response:
    # Implementation
    pass
```

## Important Notes

- **Security**: `calculate` tool uses `eval()` - replace with safe parser for production (utils/tools.py:28)
- **Sandbox Timeouts**: Background sandbox runs for max 6 hours or 10 minutes idle (main.py:134-135)
- **Tool Wildcards**: `ALLOWED_TOOLS` supports wildcards like `"WebSearch(*)"` (utils/tools.py:63)
- **Request Format**: `test_endpoint` expects JSON string body, not object (main.py:211)
- **Node.js Dependency**: Agent SDK requires `@anthropic-ai/claude-code` npm package (utils/env_templates.py:43)
- **Python Version**: Image uses Python 3.11 (utils/env_templates.py:38)

## ExecPlans

When writing complex features or refactoring, you should create an ExecPlan as described in the .agent/plans/PLANS.md file. This plan should be stored in the `.agent/plans/{feature_name}/` directory and it should be accompanied by a task list in the `.agent/tasks/{feature_name}/` directory. Place any temporary research, clones, etc., in the .gitignored subdirectory of the .agent/ directory.
