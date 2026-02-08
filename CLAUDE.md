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
modal run -m modal_backend.main

# Run agent as remote function
modal run -m modal_backend.main::run_agent_remote --question "Your question here"

# Start dev server with hot reload (enables HTTP endpoints)
modal serve -m modal_backend.main

# Deploy to production
modal deploy -m modal_backend.deploy
```

### Testing HTTP Endpoints

When `modal serve -m modal_backend.main` or `modal deploy -m modal_backend.deploy` is running:

```bash
# Test the query endpoint
curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/query' \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the capital of Canada?"}'

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
- Executes `modal_backend.agent_runtime.loop` as a module
- Captures stdout/stderr
- Terminates sandbox after completion
- Use for: batch jobs, scheduled tasks, one-off queries

**2. Long-lived Background Service Pattern**

The system uses a persistent sandbox for the Agent SDK:

| Sandbox | Name | Port | Volume | Purpose |
|---------|------|------|--------|---------|
| Agent SDK | `svc-runner-8001` | 8001 | `/data` | Conversational queries via Claude Agent SDK |

- Sandbox maintains persistent FastAPI service via uvicorn
- HTTP gateway (`http_app`) routes requests to the sandbox
- Agent SDK sandbox: 24h timeout, 10min idle
- Use for: low-latency serving, repeated queries, production APIs

### Key Components

**`modal_backend/main.py`** - Modal app definition and entry points

- Defines `modal.App("test-sandbox")`
- `get_or_start_background_sandbox()`: Manages Agent SDK sandbox lifecycle
- `http_app`: ASGI app exposing HTTP endpoints that proxy to the sandbox
- `run_agent_remote`: Short-lived function for single queries
- `main`: Local CLI entry point for `modal run`

**`modal_backend/agent_runtime/loop.py`** - Agent execution

- Builds `ClaudeAgentOptions` from MCP servers and prompts
- `run_agent()`: Single query execution with streaming output
- Used by short-lived sandboxes via `sb.exec("python", "-m", "modal_backend.agent_runtime.loop", ...)`

**`modal_backend/api/controller.py`** - Agent SDK microservice (port 8001)

- `GET /health_check`: Liveness probe
- `POST /query`: Agent query endpoint that returns responses
- `POST /query_stream`: Agent query endpoint that streams responses as SSE
- Uses `permission_mode="acceptEdits"` with `can_use_tool` handler
- Supports session resumption via `session_id`, `session_key`, `fork_session`

**`modal_backend/main.py`** - HTTP Gateway endpoints (in addition to above)

- `POST /submit`: Enqueue async job to `JOB_QUEUE`
- `GET /jobs/{job_id}`: Check job status from `JOB_RESULTS` dict
- `DELETE /jobs/{job_id}`: Cancel a queued job

**`modal_backend/settings/settings.py`** - Configuration management

- `Settings`: Pydantic Settings class for environment variables and configuration
- `get_modal_secrets()`: Returns Modal Secret objects
- Centralized configuration for sandbox settings, timeouts, resources, etc.

**`modal_backend/main.py`** - Image building

- `_base_anthropic_sdk_image()`: Builds container with Python 3.11, FastAPI, uvicorn, httpx, claude-agent-sdk, Node.js 20, and @anthropic-ai/claude-agent-sdk
- Working directory: `/root/app`
- Copies local project and installs dependencies

**`modal_backend/mcp_tools/`** - MCP tool system

- `registry.py`: `ToolRegistry` class managing MCP servers and allowed tools
- `calculate_tool.py`: Example tool implementation
- Individual tools live in separate files for better organization
- Tool naming: `mcp__<server>__<tool>` (e.g., `mcp__utilities__calculate`)
- `get_mcp_servers()` and `get_allowed_tools()`: Convenience functions for accessing registry

**`modal_backend/instructions/prompts.py`** - Agent prompts

- `SYSTEM_PROMPT`: Configures agent behavior and tone
- `DEFAULT_QUESTION`: Fallback query when none provided

**`modal_backend/agent_runtime/base.py`** - Core multi-agent abstractions

- `AgentConfig`: Dataclass defining agent behavior (prompts, tools, subagents)
- `AgentExecutor`: Abstract base class for agent execution
- `ClaudeAgentExecutor`: Default implementation using Claude Agent SDK
- `build_agent_options()`: Central function for building ClaudeAgentOptions

**`modal_backend/agent_runtime/registry.py`** - Agent type management

- `AgentRegistry`: Singleton managing agent configurations
- `get_agent_config(name)`: Get configuration by agent type name
- `get_agent_executor(name)`: Get executor for agent type
- `list_agent_types()`: List all registered agent types
- `register_agent(config)`: Register custom agent types

**`modal_backend/agent_runtime/types/`** - Built-in agent definitions

- `default.py`: General-purpose agent (backward compatible)
- `marketing.py`: Marketing specialist with web search
- `research.py`: Research coordinator with SDK native subagents

**`modal_backend/instructions/`** - Agent-specific prompts

- `marketing.py`: Marketing agent system prompt
- `research.py`: Research agent system prompt
- `subagents/`: Prompts for SDK native subagents (researcher, data-analyst, report-writer)

**`modal_backend/jobs.py`** - Async job processing

- `enqueue_job()`: Submit job to Modal Queue
- `get_job_status()`: Check job status from Modal Dict
- `cancel_job()`: Cancel a queued job
- `process_job_queue()`: Worker function that consumes jobs
- Uses Modal Queue and Dict for distributed state

### Background Sandbox Lifecycle

The Agent SDK sandbox follows this lifecycle pattern:

**Agent SDK Sandbox** (`get_or_start_background_sandbox()`):
1. Checks for existing sandbox in global `SANDBOX` and `SERVICE_URL` variables
2. Creates sandbox with `modal.Sandbox.create()` running uvicorn on port 8001
3. Mounts `svc-runner-8001-vol` at `/data`
4. Polls `sandbox.tunnels()` for up to 30 seconds to get encrypted port URL
5. Calls `_wait_for_service()` to poll `/health_check` until 200 OK

The sandbox persists across multiple requests within the same Modal worker, avoiding cold starts.

### Permission Modes

- `modal_backend/agent_runtime/loop.py`: Uses default permission mode (requires user approval for tools)
- `modal_backend/api/controller.py`: Uses `permission_mode="acceptEdits"` with `can_use_tool` handler for controlled tool access

When adding new endpoints or execution patterns, choose permission mode based on trust level and use case.

## Extending the Codebase

### Adding New Tools

1. Create a new file in `modal_backend/mcp_tools/` (e.g., `my_tool.py`):

```python
from claude_agent_sdk import tool
from typing import Any

@tool("my_tool_name", "Description", {"param": str})
async def my_tool(args: dict[str, Any]) -> dict[str, Any]:
    # Implementation
    return {"content": [{"type": "text", "text": "result"}]}
```

2. Register in `modal_backend/mcp_tools/registry.py`:

```python
from modal_backend.mcp_tools.my_tool import my_tool

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

Edit `modal_backend/instructions/prompts.py` to change `SYSTEM_PROMPT`.

### Adding New Agent Types

1. Create config in `modal_backend/agent_runtime/types/my_agent.py`:

```python
from modal_backend.agent_runtime.base import AgentConfig

def my_agent_config() -> AgentConfig:
    return AgentConfig(
        name="my-agent",
        display_name="My Agent",
        description="What this agent does",
        system_prompt="Your system prompt here",
        allowed_tools=["Read", "Write", "WebSearch(*)"],
        max_turns=30,
        can_spawn_subagents=False,
    )
```

2. Register in `modal_backend/agent_runtime/registry.py`:

```python
from modal_backend.agent_runtime.types.my_agent import my_agent_config

# In AgentRegistry._initialize_defaults():
self.register(my_agent_config())
```

3. For SDK native subagents, add `AgentDefinition` objects to `subagents` field:

```python
from claude_agent_sdk import AgentDefinition

subagents = {
    "helper": AgentDefinition(
        description="What this subagent does",
        tools=["Read", "Write"],
        prompt="Subagent system prompt",
        model="haiku",
    ),
}

return AgentConfig(
    name="my-agent",
    # ... other fields ...
    subagents=subagents,
)
```

See `docs/multi-agent.md` for comprehensive documentation on the multi-agent architecture.

### Adjusting Runtime Configuration

Edit `modal_backend/settings/settings.py`:

- Modify `Settings` class attributes for configuration values
- Update `get_modal_secrets()` to add new secrets

Edit `modal_backend/main.py`:

- Modify `_base_anthropic_sdk_image()` to add pip packages (`.pip_install()`)
- Add apt packages (`.apt_install()`)
- Adjust image build steps

### Adding HTTP Endpoints

Add new endpoints to `modal_backend/main.py`:

```python
@web_app.post("/your_endpoint")
async def your_endpoint(request: Request, body: QueryBody):
    # Implementation
    pass
```

Or add to the background service in `modal_backend/api/controller.py`:

```python
@app.post("/your_endpoint")
async def your_endpoint(body: QueryBody, request: Request):
    # Implementation
    pass
```

## Important Notes

- **Security**: `calculate` tool uses `eval()` - replace with safe parser for production (modal_backend/mcp_tools/calculate_tool.py)
- **Sandbox Timeouts**: Background sandbox runs for max 24 hours or 10 minutes idle (configurable in `modal_backend/settings/settings.py`)
- **Autoscaling**: `min_containers=1` keeps containers warm by default; adjust `max_containers`, `scaledown_window` for cost/latency tradeoffs
- **Tool Wildcards**: `ALLOWED_TOOLS` supports wildcards like `"WebSearch(*)"` (modal_backend/mcp_tools/registry.py)
- **Node.js Dependency**: Agent SDK requires `@anthropic-ai/claude-agent-sdk` npm package (modal_backend/main.py)
- **Python Version**: Image uses Python 3.11 (modal_backend/main.py)
- **Module Mode**: All commands use `-m modal_backend.*` for proper package discovery
- **Agent Turn Limits**: Set `agent_max_turns` to limit conversation turns and prevent runaway loops

### Volume Persistence Behavior

When `volume_commit_interval` is configured in settings:

- The persistent volume is reloaded before each query to get the latest committed state
- The volume is committed after each query (respecting the configured interval)
- This ensures writes are persisted without requiring sandbox termination
- Note: Commits occur after all requests (including read-only) when the interval is reached
- Set `volume_commit_interval` to `None` (default) to disable automatic commits; writes persist only on sandbox termination

## Browser Automation

When the user asks to work with Chrome or perform browser automation tasks, use the `claude-in-chrome` MCP server.
