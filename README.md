# Agent Sandbox Starter (Modal + Claude Agent SDK)

![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Modal](https://img.shields.io/badge/Modal-1.2.1+-8B5CF6.svg)
![Claude Agent SDK](https://img.shields.io/badge/Claude%20Agent%20SDK-0.1.4+-FF6B35.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

A starter template for running a Claude agent in a persistent Modal Sandbox with FastAPI service endpoints and volume persistence.

## Requirements

- **Modal CLI**: `pip install modal` and `modal setup`
- **Anthropic API key**: store in a Modal Secret named `anthropic-secret` with key `ANTHROPIC_API_KEY`

## Project Structure

```
agent_sandbox/

├── __init__.py              # Package initialization & module registration
├── app.py                   # Main Modal app definition
├── deploy.py                # Deployment composition
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
├── sandbox/                 # Sandbox utilities
│   └── helpers.py          # Volume operations (get_session_volume, upload_paths_to_volume)
│
├── services/                # Cross-cutting services
│   └── logging.py          # Logging configuration utilities
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

- **Terminate the background sandbox** (flushes writes to persistent volume)

```bash
modal run -m agent_sandbox.app::terminate_service_sandbox
```

- **Create a filesystem snapshot** (captures current state)

```bash
modal run -m agent_sandbox.app::snapshot_service
```

## Architecture

This project uses a **persistent sandbox service pattern**:

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
- **Sandbox timeout**: The background service has a 12-hour timeout and 10-minute idle timeout (configurable in `agent_sandbox/config/settings.py`)
- **Service URL discovery**: The endpoint waits up to 30 seconds for the encrypted tunnel URL to be available

## Documentation

Comprehensive documentation is available in the [`docs/`](./docs/) directory:

- **[Architecture Overview](./docs/architecture.md)** - System architecture, component responsibilities, and request flow
- **[Controllers: Background Service](./docs/controllers.md)** - Deep dive into the controller service that runs the agent
- **[Modal Ingress](./docs/modal-ingress.md)** - How Modal handles HTTP ingress and routes requests
- **[API Usage Guide](./docs/api-usage.md)** - Complete guide for end users: endpoints, examples, authentication, error handling
- **[Configuration Guide](./docs/configuration.md)** - Configuration options and environment setup
- **[Documentation Index](./docs/README.md)** - Complete documentation index

## Additional Resources

- [Modal Documentation](https://modal.com/docs)
- [Claude Agent SDK Documentation](https://docs.claude.com/en/api/agent-sdk/python)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
