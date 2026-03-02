# Runtime Docs Overview

This page is the guided map for Cloudflare + Modal runtime operations.
For the full references catalog (including non-runtime docs), use `docs/references/index.md`.

## First-Time Setup

Use the canonical Cloudflare <-> Modal runbook first:

`docs/references/runbooks/cloudflare-modal-e2e.md`

For local E2E execution, activate the repo virtualenv first:

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
source .venv/bin/activate
```

If you only need a quick Modal runtime smoke check:

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
uv sync --extra dev
source .venv/bin/activate
uv run modal setup

# Create required secrets
uv run modal secret create openai-secret OPENAI_API_KEY=<your-key>
uv run modal secret create internal-auth-secret INTERNAL_AUTH_SECRET=<same-as-cloudflare>
uv run modal secret create modal-auth-secret \
  SANDBOX_MODAL_TOKEN_ID=<token-id> \
  SANDBOX_MODAL_TOKEN_SECRET=<token-secret>
```

Run a smoke check:

```bash
uv run modal run -m modal_backend.main
```

If startup fails, verify:
- `OPENAI_API_KEY` is valid and present in `openai-secret`
- `modal setup` completed successfully

## Start Here

1. [Canonical E2E Runbook (Cloudflare <-> Modal)](./runbooks/cloudflare-modal-e2e.md)
2. [Cloudflare + Modal Hybrid Architecture](../design-docs/cloudflare-hybrid-architecture.md)
3. [Architecture Overview](../design-docs/architecture-overview.md) (Modal runtime internals)
4. [Next.js + Supabase BFF Integration](./nextjs-supabase-bff-integration.md)
5. [Controllers](../design-docs/controllers-background-service.md)
6. [Configuration](./configuration.md)
7. [API Usage](./api-usage.md)

## Core Docs

- [Canonical E2E Runbook](./runbooks/cloudflare-modal-e2e.md)
- [Cloudflare + Modal Hybrid Architecture](../design-docs/cloudflare-hybrid-architecture.md)
- [Architecture Overview](../design-docs/architecture-overview.md)
- [Next.js + Supabase BFF Integration](./nextjs-supabase-bff-integration.md)
- [Controllers: Background Service](../design-docs/controllers-background-service.md)
- [Multi-Agent Architecture](../design-docs/multi-agent-architecture.md)
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
- **Readiness Hardening**: gateway startup waits on controller `/health_check`, logs bounded diagnostics on timeout, performs one recycle+retry, and fails deterministically after a second timeout.
- **TD-003 `/query` Live E2E Closure**:
  - sandbox runtime now receives Modal auth secret on sandbox surface when enabled.
  - gateway `/query` error propagation now preserves concrete upstream errors.
  - controller session DB path falls back to `/tmp/openai_agents_sessions.sqlite3` when configured path is not writable under dropped privileges.

## Related Resources

- [Main README](../../README.md)
- [Modal Documentation](https://modal.com/docs)
- [OpenAI Agents Python Documentation](https://openai.github.io/openai-agents-python/)
