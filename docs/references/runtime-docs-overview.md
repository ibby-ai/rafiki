# Runtime Docs Overview

This page is the guided map for Cloudflare + Modal runtime operations.
For the full references catalog (including non-runtime docs), use `docs/references/index.md`.

Phase 3 boundary:
- Cloudflare Worker + Durable Objects are the only supported client-facing ingress.
- Modal `http_app` is the internal gateway used by Worker forwarding and by local/operator diagnostics.
- The controller sandbox executes agent runs behind that gateway.

## First-Time Setup

Use the canonical Cloudflare <-> Modal runbook first:

`docs/references/runbooks/cloudflare-modal-e2e.md`

For local E2E execution, activate the repo virtualenv first:

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
cp .env.example .env
# edit .env and set INTERNAL_AUTH_SECRET=<shared-secret>
uv sync --extra dev
source .venv/bin/activate
```

Worker environment contract:

- `edge-control-plane/wrangler.jsonc` top-level vars are production-safe for the canonical public Worker.
- Local Worker development uses `cd edge-control-plane && npm run dev`, which expands to `wrangler dev --env development`.
- The checked-in `development` environment uses explicit Durable Object script names (`rafiki-control-plane-development`) so local/dev object state stays isolated from the canonical public Worker.

Version contract:

- The repo dependency floor is `modal>=1.3.5`.
- Async runtime handlers rely on Modal 1.3.x async interfaces (`.aio`) for sandbox/app lookups and function spawns.
- For Python checks, prefer `uv run python -m pytest ...` to ensure the repo interpreter is used.

If you only need a quick internal Modal runtime smoke check:

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
cp .env.example .env
# edit .env and set INTERNAL_AUTH_SECRET=<shared-secret>
uv sync --extra dev
source .venv/bin/activate
uv run modal setup

# Create required secrets
uv run modal secret create openai-secret OPENAI_API_KEY=<your-key>
uv run modal secret create internal-auth-secret INTERNAL_AUTH_SECRET=<same-as-.env>
uv run modal secret create modal-auth-secret \
  SANDBOX_MODAL_TOKEN_ID=<token-id> \
  SANDBOX_MODAL_TOKEN_SECRET=<token-secret>

# Optional: only if ENABLE_LANGSMITH_TRACING=true
uv run modal secret create langsmith-secret \
  LANGSMITH_API_KEY=<your-langsmith-key> \
  LANGSMITH_PROJECT=<your-project>
```

Run a smoke check:

```bash
uv run modal run -m modal_backend.main
```

If startup fails, verify:
- `OPENAI_API_KEY` is valid and present in `openai-secret`
- `.env` exists and `INTERNAL_AUTH_SECRET` is set locally
- `modal setup` completed successfully
- `modal-auth-secret` exists because `ENABLE_MODAL_AUTH_SECRET=true` by default
- if tracing is enabled, `langsmith-secret` exists or `ENABLE_LANGSMITH_TRACING=false`
- `.venv` is synced (`uv sync --extra dev`) and `uv run python - <<'PY' ... importlib.metadata.version("modal") ... PY` reports `1.3.5` or newer

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

- **Worker + Durable Objects**: The public control plane for client auth, session authority, queueing, and streaming.
- **Modal `http_app`**: The internal gateway for Worker-forwarded traffic and local/operator diagnostics.
- **Controller**: The long-lived FastAPI service in `modal_backend/api/controller.py` that executes OpenAI Agents runs.
- **Backend Architecture**:
  - `http_app` receives internal Worker traffic plus local/operator diagnostic traffic.
  - Controller sandbox executes agent runs and streams SSE events.
- **Session Memory**: OpenAI `SQLiteSession` with persisted session IDs and optional fork behavior.
- **LangSmith tracing**: optional OpenAI Agents trace correlation for debugging, operations, and incident triage.
- **Readiness Hardening**: gateway startup waits on controller `/health_check`, logs bounded diagnostics on timeout, performs one recycle+retry, and fails deterministically after a second timeout.
- **Active-Pointer Rollout Authority**:
  - requests consult shared rollout state before reusing per-worker controller caches.
  - replacement controller `B` is created privately, must pass `/health_check` plus scoped-secret metadata plus a synthetic direct `/query`, and is promoted only after those gates pass.
  - previous controller `A` becomes `draining` after promotion, serves only in-flight work, and terminates after leases reach zero or the drain timeout expires.
- **TD-003 `/query` Live E2E Closure**:
  - sandbox runtime now receives Modal auth secret on sandbox surface when enabled.
  - gateway `/query` error propagation now preserves concrete upstream errors.
  - controller session DB path falls back to `/tmp/openai_agents_sessions.sqlite3` when configured path is not writable under dropped privileges.

## Related Resources

- [Main README](../../README.md)
- [Modal Documentation](https://modal.com/docs)
- [OpenAI Agents Python Documentation](https://openai.github.io/openai-agents-python/)
