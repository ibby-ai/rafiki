# Examples

Runnable examples demonstrating the key capabilities of the agent-sandbox-starter project.

## Prerequisites

Before running these examples:

1. Install dependencies:
   ```bash
   uv sync
   source .venv/bin/activate
   ```

2. Configure Modal:
   ```bash
   modal setup
   ```

3. Create the OpenAI secret:
   ```bash
   modal secret create openai-secret OPENAI_API_KEY=<your-key>
   ```

## Quick Start

```bash
# Run the simplest example (from project root)
./examples/01_basic_query/run.sh

# Or using uv directly
uv run modal run -m modal_backend.main::run_agent_remote --question "Hello"
```

## Examples Overview

| Example | Description | Pattern |
|---------|-------------|---------|
| [01_basic_query](./01_basic_query/) | Simple agent queries | Short-lived sandbox |
| [02_streaming_responses](./02_streaming_responses/) | Real-time SSE streaming | Long-lived service |
| [03_file_persistence](./03_file_persistence/) | Volume persistence demo | Both patterns |
| [04_custom_tools](./04_custom_tools/) | Creating MCP tools | Development guide |
| [05_http_endpoints](./05_http_endpoints/) | Full API testing | Long-lived service |
| [06_batch_processing](./06_batch_processing/) | Multiple queries | Short-lived sandbox |
| [07_service_management](./07_service_management/) | Lifecycle management | Service operations |

## Execution Patterns

### Short-lived Sandbox (Pattern 1)

```bash
uv run modal run -m modal_backend.main::run_agent_remote --question "Your question"
```

Best for: One-off queries, CI/CD, batch processing

### Long-lived Service (Pattern 2)

```bash
# Start the service (in one terminal)
uv run modal serve -m modal_backend.main

# Query via HTTP (in another terminal)
curl -X POST 'https://<your-url>/query' \
  -H 'Content-Type: application/json' \
  -d '{"question":"Your question"}'
```

Best for: Low-latency APIs, interactive applications

## Environment Variables

Set these before running HTTP examples:

```bash
export DEV_URL="https://your-org--test-sandbox-http-app-dev.modal.run"
```

## Related Documentation

- [API Usage Guide](../docs/api-usage.md)
- [Tool Development Guide](../docs/tool-development.md)
- [Configuration Guide](../docs/configuration.md)
