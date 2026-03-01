# Rafiki (Modal + OpenAI Agents SDK)
<!-- ⚠️ WARNING: This project is experimental. Features and behavior may change without warning. -->

![CI](https://github.com/Saidiibrahim/rafiki/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Modal](https://img.shields.io/badge/Modal-1.2.1+-8B5CF6.svg)
![OpenAI Agents SDK](https://img.shields.io/badge/OpenAI%20Agents%20SDK-0.9.2+-0A66C2.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

![Rafiki](docs/images/readme-image.png)

A multiplayer open-source background agent, powered by Modal, Cloudflare, and the OpenAI Agents Python SDK.
This project was directly inspired by Ramp's blog post, [Why we built our background agent](https://builders.ramp.com/post/why-we-built-our-background-agent).

## Setup

```bash
source .venv/bin/activate
uv sync
uv run pre-commit install

# Modal auth + required API secret
pip install modal
modal setup
modal secret create openai-secret OPENAI_API_KEY=<your-key>
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

![Query execution flow](docs/images/rafiki-diagram.png)

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

- `docs/design-docs/architecture-overview.md` - architecture overview
- `docs/design-docs/multi-agent-architecture.md` - agent types + orchestration
- `docs/design-docs/controllers-background-service.md` - background service
- `docs/references/api-usage.md` - endpoints and auth
- `docs/references/configuration.md` - settings
- `docs/references/tool-development.md` - tool development
- `docs/references/troubleshooting.md` - common issues
- `docs/references/runtime-docs-overview.md` - doc index

## Links

- [Modal Documentation](https://modal.com/docs)
- [OpenAI Agents Python Documentation](https://openai.github.io/openai-agents-python/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
