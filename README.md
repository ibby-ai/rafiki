# Agent Sandbox Starter (Modal + Claude Agent SDK)

![CI](https://github.com/Saidiibrahim/agent-sandbox-starter/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Modal](https://img.shields.io/badge/Modal-1.2.1+-8B5CF6.svg)
![Claude Agent SDK](https://img.shields.io/badge/Claude%20Agent%20SDK-0.1.4+-FF6B35.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

![Agent Sandbox Starter](docs/images/readme-image.png)

Modal-based sandboxed runtime for the **Claude Agent SDK** with MCP tool integration.
Supports two execution patterns:

- Short-lived sandboxes (one-off jobs)
- Long-lived background service (low-latency API)

**Cloudflare-first:** Public API traffic route through the Cloudflare control plane.
Direct Modal gateway access is internal-only and requires `X-Internal-Auth`.

## Requirements

- Modal CLI: `pip install modal` and `modal setup`
- Anthropic API key in Modal Secret `anthropic-secret` with key `ANTHROPIC_API_KEY`

## Setup

```bash
source .venv/bin/activate
uv sync
uv run pre-commit install
```

## Quickstart

### Development (one-off)

```bash
modal run -m agent_sandbox.app
modal run -m agent_sandbox.app::run_agent_remote --question "Explain REST vs gRPC"
```

### Service mode (hot-reload)

```bash
modal serve -m agent_sandbox.app
# or
make serve
```

### Production

```bash
modal deploy -m agent_sandbox.deploy
```

## Cloudflare Control Plane (Recommended for Public APIs)

```bash
cd cloudflare-control-plane
npm install
wrangler login
wrangler secret put INTERNAL_AUTH_SECRET
wrangler secret put SESSION_SIGNING_SECRET
wrangler kv:namespace create SESSION_CACHE
npm run deploy
```

See `CLOUDFLARE_INTEGRATION.md` and `cloudflare-control-plane/README.md`.

## Project Structure

```
agent_sandbox/
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
modal run -m agent_sandbox.app::terminate_service_sandbox

# Snapshot service filesystem
modal run -m agent_sandbox.app::snapshot_service

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
