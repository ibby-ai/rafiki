# Agent Sandbox Starter (Modal + Claude Agent SDK)

![CI](https://github.com/Saidiibrahim/agent-sandbox-starter/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Modal](https://img.shields.io/badge/Modal-1.2.1+-8B5CF6.svg)
![Claude Agent SDK](https://img.shields.io/badge/Claude%20Agent%20SDK-0.1.4+-FF6B35.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

![Agent Sandbox Starter](docs/images/readme-image.png)

A multiplayer open-source background agent inspired by [Ramp Inspect](https://builders.ramp.com/post/why-we-built-our-background-agent), powered by Modal, Cloudflare and Claude Agent SDK

## Setup

```bash
source .venv/bin/activate
uv sync
uv run pre-commit install
```

## Quickstart

### Development (one-off)

```bash
modal run -m modal_backend.main
modal run -m modal_backend.main::run_agent_remote --question "Explain REST vs gRPC"
```

### Service mode (hot-reload)

```bash
modal serve -m modal_backend.main
# or
make serve
```

### Production

```bash
modal deploy -m modal_backend.deploy
```

## Cloudflare Control Plane (Recommended for Public APIs)

```bash
cd edge-control-plane
npm install
wrangler login
wrangler secret put INTERNAL_AUTH_SECRET
wrangler secret put SESSION_SIGNING_SECRET
wrangler kv:namespace create SESSION_CACHE
npm run deploy
```

See `CLOUDFLARE_INTEGRATION.md` and `edge-control-plane/README.md`.

## Query Execution Flow

The diagram below shows how a query moves from the public API entrypoint through the Modal gateway and backend runtime:

![Query execution flow](docs/images/sandbox-starter-diagram.png)

## Project Structure

```
modal_backend/
  app.py                 # Modal app + HTTP gateway
  agents/                # Agent execution logic
  controllers/           # FastAPI background service
  prompts/               # System prompts
  tools/                 # MCP tool registry + implementations
  config/                # Settings + secrets
  schemas/               # Pydantic models
```

## Common Ops

```bash
# Terminate background sandbox
modal run -m modal_backend.main::terminate_service_sandbox

# Snapshot service filesystem
modal run -m modal_backend.main::snapshot_service

# Run tests
uv run pytest
```

## Docs

- `docs/architecture.md` - architecture overview
- `docs/multi-agent.md` - agent types + orchestration
- `docs/controllers.md` - background service
- `docs/api-usage.md` - endpoints and auth
- `docs/configuration.md` - settings
- `docs/tool-development.md` - MCP tools
- `docs/troubleshooting.md` - common issues
- `docs/README.md` - doc index

## Links

- [Modal Documentation](https://modal.com/docs)
- [Claude Agent SDK Documentation](https://docs.claude.com/en/api/agent-sdk/python)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
