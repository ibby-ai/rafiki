# Runtime Docs Overview

This page is the guided map for Cloudflare + Modal runtime operations.
For the full references catalog (including non-runtime docs), use `docs/references/index.md`.

## First-Time Setup

Use the canonical Cloudflare <-> Modal runbook first:

`docs/references/runbooks/cloudflare-modal-e2e.md`

If you only need a quick Modal runtime smoke check:

```bash
# Install and authenticate Modal
pip install modal
modal setup

# Create required secrets
modal secret create openai-secret OPENAI_API_KEY=<your-key>
modal secret create internal-auth-secret INTERNAL_AUTH_SECRET=<same-as-cloudflare>
modal secret create modal-auth-secret \
  SANDBOX_MODAL_TOKEN_ID=<token-id> \
  SANDBOX_MODAL_TOKEN_SECRET=<token-secret>
```

Run a smoke check:

```bash
modal run -m modal_backend.main
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

## Related Resources

- [Main README](../../README.md)
- [Modal Documentation](https://modal.com/docs)
- [OpenAI Agents Python Documentation](https://openai.github.io/openai-agents-python/)
