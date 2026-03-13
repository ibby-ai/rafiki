# Rafiki

![CI](https://github.com/Saidiibrahim/rafiki/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Modal](https://img.shields.io/badge/Modal-1.3.5+-8B5CF6.svg)
![Cloudflare Workers](https://img.shields.io/badge/Cloudflare-Workers-F38020.svg?logo=cloudflare&logoColor=white)
![Cloudflare Durable Objects](https://img.shields.io/badge/Cloudflare-Durable%20Objects-F38020.svg?logo=cloudflare&logoColor=white)
![Cloudflare KV](https://img.shields.io/badge/Cloudflare-KV-F38020.svg?logo=cloudflare&logoColor=white)
![Cloudflare Rate Limiting](https://img.shields.io/badge/Cloudflare-Rate%20Limiting-F38020.svg?logo=cloudflare&logoColor=white)
![OpenAI Agents SDK](https://img.shields.io/badge/OpenAI%20Agents%20SDK-0.9.2+-0A66C2.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

![Rafiki](docs/images/readme-image.png)

Rafiki is a Cloudflare-first agent harness for running stateful OpenAI Agents workflows with secure Modal execution.
Cloudflare Workers + Durable Objects provide the public control plane, while Modal runs the internal execution backend for the current runtime.
The harness remains the stable system boundary even if the underlying agent loop changes.
This project was inspired by Ramp's blog post, [Why we built our background agent](https://builders.ramp.com/post/why-we-built-our-background-agent).

## System Boundary

- Public client ingress: Cloudflare Worker + Durable Objects.
- Internal gateway: Modal `http_app`, called by the Worker or by local/operator diagnostics.
- Execution runtime: the long-lived controller sandbox in `modal_backend/api/controller.py`.
- Direct Modal gateway access is not the supported client path; non-health Modal endpoints require internal auth.

## Setup

```bash
uv sync --extra dev
source .venv/bin/activate

# Modal auth + required API secret
uv run modal setup
uv run modal secret create openai-secret OPENAI_API_KEY=<your-key>
```

## Quickstart

### Local Modal smoke check

```bash
uv run modal run -m modal_backend.main
uv run modal run -m modal_backend.main::run_agent_remote --question "Explain REST vs gRPC"
```

### Local service mode

```bash
uv run modal serve -m modal_backend.main
# or
make serve
```

### Deploy Internal Modal Backend

```bash
uv run modal deploy -m modal_backend.deploy
```

## Cloudflare Control Plane (Required for Client Traffic)

```bash
cd edge-control-plane
npm install
wrangler login
wrangler secret put INTERNAL_AUTH_SECRET
wrangler secret put SESSION_SIGNING_SECRET
wrangler kv:namespace create SESSION_CACHE
npm run deploy
```

The Worker also depends on matching Modal-side auth secrets:

```bash
uv run modal secret create internal-auth-secret INTERNAL_AUTH_SECRET=<same-as-cloudflare>
uv run modal secret create modal-auth-secret \
  SANDBOX_MODAL_TOKEN_ID=<token-id> \
  SANDBOX_MODAL_TOKEN_SECRET=<token-secret>
```

`MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` are not part of the canonical public Worker request path. If you need a non-canonical route that depends on them, follow the edge control plane docs explicitly.

For the canonical setup and verification flow, start with `docs/references/runbooks/cloudflare-modal-e2e.md` and `edge-control-plane/README.md`.

## Query Execution Flow

![Query execution flow](docs/images/rafiki-diagram.png)

## Common Ops

```bash
# Terminate background sandbox
uv run modal run -m modal_backend.main::terminate_service_sandbox

# Snapshot service filesystem
uv run modal run -m modal_backend.main::snapshot_service

# Run tests
uv run python -m pytest
```

## Docs

- `docs/design-docs/cloudflare-hybrid-architecture.md` - public control plane and system boundary
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
