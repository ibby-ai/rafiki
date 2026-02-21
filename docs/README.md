# Documentation Index

This directory contains implementation and operations documentation for the Modal + OpenAI Agents runtime.

## First-Time Setup

```bash
# Install and authenticate Modal
pip install modal
modal setup

# Create required secrets
modal secret create openai-secret OPENAI_API_KEY=<your-key>
modal secret create internal-auth-secret INTERNAL_AUTH_SECRET=<same-as-cloudflare>
```

Run a smoke check:

```bash
modal run -m modal_backend.main
```

If startup fails, verify:
- `OPENAI_API_KEY` is valid and present in `openai-secret`
- `modal setup` completed successfully

## Start Here

1. [Architecture Overview](./architecture.md)
2. [Controllers](./controllers.md)
3. [Configuration](./configuration.md)
4. [API Usage](./api-usage.md)

## Core Docs

- [Architecture Overview](./architecture.md)
- [Controllers: Background Service](./controllers.md)
- [Multi-Agent Architecture](./multi-agent.md)
- [Configuration Guide](./configuration.md)
- [API Usage Guide](./api-usage.md)
- [Tool Development Guide](./tool-development.md)
- [Troubleshooting Guide](./troubleshooting.md)

## Key Concepts

- **Controller**: The long-lived FastAPI service in `modal_backend/api/controller.py` that executes OpenAI Agents runs.
- **Two-Tier Architecture**:
  - `http_app` receives public/internal requests.
  - Controller sandbox executes agent runs and streams SSE events.
- **Session Memory**: OpenAI `SQLiteSession` with persisted session IDs and optional fork behavior.

## Related Resources

- [Main README](../README.md)
- [Modal Documentation](https://modal.com/docs)
- [OpenAI Agents Python Documentation](https://openai.github.io/openai-agents-python/)
