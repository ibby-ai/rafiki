# Agent Sandbox Starter (Modal + Claude Agent SDK)

![Python](https://img.shields.io/badge/python-3.13+-blue.svg)
![Modal](https://img.shields.io/badge/Modal-1.2.1+-8B5CF6.svg)
![Claude Agent SDK](https://img.shields.io/badge/Claude%20Agent%20SDK-0.1.4+-FF6B35.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

A starter template for running a Claude agent in a persistent Modal Sandbox with FastAPI service endpoints and volume persistence.

## Requirements

- **Modal CLI**: `pip install modal` and `modal setup`
- **Anthropic API key**: store in a Modal Secret named `anthropic-secret` with key `ANTHROPIC_API_KEY`

## Quickstart

### Development Mode

- **Run locally (spawns a short-lived Sandbox and executes `runner.py`)**

```bash
modal run main.py
```

- **Run the agent as a remote function (one-off execution)**

```bash
modal run main.py::run_agent_remote --question "Explain REST vs gRPC"
```

### Production Mode (Persistent Service)

- **Start dev server with hot-reload (recommended for development)**

```bash
modal serve main.py
```

Or use the Makefile:

```bash
make serve
```

Once the server is running, you'll get a dev endpoint URL like:

```text
https://<org>--test-sandbox-test-endpoint-dev.modal.run
```

- **Test the HTTP endpoint with curl**

```bash
curl -X POST 'https://<org>--test-sandbox-test-endpoint-dev.modal.run' \
  -H 'Content-Type: application/json' \
  -d '"What is the capital of Canada?"'
```

Or use the Makefile (set `DEV_URL` in Makefile first):

```bash
make curl Q="What is the capital of Canada?"
```

- **Deploy to production**

```bash
modal deploy main.py
```

### Service Management

- **Terminate the background sandbox** (flushes writes to persistent volume)

```bash
modal run main.py::terminate_service_sandbox
```

- **Create a filesystem snapshot** (captures current state)

```bash
modal run main.py::snapshot_service
```

## Architecture

This project uses a **persistent sandbox service pattern**:

1. **Background Service**: A long-lived `modal.Sandbox` runs a FastAPI microservice (`runner_service.py`) that handles agent queries
2. **HTTP Endpoint**: `test_endpoint` in `main.py` proxies requests to the background service
3. **Volume Persistence**: Files written to `/data` are persisted across sandbox restarts
4. **Low Latency**: The background service avoids cold-start delays while keeping the HTTP endpoint responsive

### Key Files

- `main.py`: Defines the Modal `App`, persistent sandbox management, and HTTP endpoints
- `runner_service.py`: FastAPI microservice running inside the sandbox with `/health_check` and `/query` endpoints
- `runner.py`: Standalone agent runner (used by `run_agent_remote` for one-off executions)
- `utils/env_templates.py`: Builds the Modal image, sets workdir, and attaches required secrets
- `utils/tools.py`: MCP tool servers and allowed tools list (WebSearch, WebFetch, Read, Write). Obtained from [Claude Agent SDK Documentation](https://docs.claude.com/en/api/agent-sdk/python)
- `utils/prompts.py`: System prompt and default question

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

- **Change the system prompt**: edit `utils/prompts.py`
- **Add or modify tools**: edit `utils/tools.py` (update tool list and allowed tools as needed)
- **Adjust runtime image/secrets**: edit `utils/env_templates.py`
- **Modify service behavior**: edit `runner_service.py`
- **Change sandbox settings**: edit constants in `main.py` (timeouts, memory, CPU, etc.)

## Troubleshooting

- **Ensure the Modal secret exists**: `modal secret create anthropic-secret ANTHROPIC_API_KEY=<your-key>`
- **Run `modal setup`**: Required if you haven't logged in or configured Modal locally
- **Check service health**: Once `modal serve` is running, verify with:

  ```bash
  curl "${DEV_URL}/health_check"
  ```

- **Volume persistence**: Remember to write files to `/data`, not `/tmp` or other ephemeral locations
- **Sandbox timeout**: The background service has a 12-hour timeout and 10-minute idle timeout (configurable in `main.py`)
- **Service URL discovery**: The endpoint waits up to 30 seconds for the encrypted tunnel URL to be available

## Additional Resources

- [Modal Documentation](https://modal.com/docs)
- [Claude Agent SDK Documentation](https://docs.claude.com/en/api/agent-sdk/python)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
