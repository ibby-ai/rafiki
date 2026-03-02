# Configuration Guide

This document covers runtime configuration for the Modal + OpenAI Agents deployment.

## Quick Setup

```bash
pip install modal
modal setup
modal secret create openai-secret OPENAI_API_KEY=your-api-key-here
modal run -m modal_backend.main
```

## Required Secrets

### OpenAI API Secret

The runtime requires a Modal secret named `openai-secret` containing `OPENAI_API_KEY`.

```bash
# create
modal secret create openai-secret OPENAI_API_KEY=sk-...

# rotate
modal secret delete openai-secret
modal secret create openai-secret OPENAI_API_KEY=sk-new...

# verify
modal secret list
```

### Internal Auth Secret

Control-plane traffic requires `internal-auth-secret` with `INTERNAL_AUTH_SECRET`.

```bash
modal secret create internal-auth-secret INTERNAL_AUTH_SECRET=<shared-secret>
```

### Modal Auth Secret

Controller calls into Modal sandboxes require `modal-auth-secret` containing
`SANDBOX_MODAL_TOKEN_ID` and `SANDBOX_MODAL_TOKEN_SECRET`.

```bash
modal secret create modal-auth-secret \
  SANDBOX_MODAL_TOKEN_ID=<token-id> \
  SANDBOX_MODAL_TOKEN_SECRET=<token-secret>
```

## Cloudflare <-> Modal E2E Environment Baseline

Canonical runbook: `docs/references/runbooks/cloudflare-modal-e2e.md`

### Required Worker-side configuration

- `MODAL_API_BASE_URL` (`edge-control-plane/wrangler.jsonc`)
- `SESSION_CACHE` KV binding
- `SESSION_SIGNING_SECRET` secret
- `INTERNAL_AUTH_SECRET` secret
- `MODAL_TOKEN_ID` secret
- `MODAL_TOKEN_SECRET` secret

### Required Modal-side configuration

- `internal-auth-secret` Modal secret containing `INTERNAL_AUTH_SECRET`
- value must exactly match the Worker `INTERNAL_AUTH_SECRET`
- `modal-auth-secret` Modal secret containing:
  - `SANDBOX_MODAL_TOKEN_ID`
  - `SANDBOX_MODAL_TOKEN_SECRET`

### Standard local exports

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki

export MODAL_API_BASE_URL="$(rg -o '\"MODAL_API_BASE_URL\": \"[^\"]+\"' edge-control-plane/wrangler.jsonc | sed -E 's/.*: \"([^\"]+)\"/\1/')"
export DEV_URL="$MODAL_API_BASE_URL"
export WORKER_URL="http://localhost:8787"
```

### Edge Control Plane Quality Tooling

`edge-control-plane` uses Ultracite with Biome for lint/format checks.
This is currently an optional audit step while legacy diagnostics are remediated.

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane
npm run check
```

Auto-fix pass (mutates files):

```bash
npm run fix
```

### Generate a test session token

```bash
TOKEN="$(node edge-control-plane/scripts/generate-session-token.js \
  --user-id e2e-user \
  --tenant-id e2e-tenant \
  --session-id sess-e2e-001 \
  --ttl-seconds 3600 \
  --secret "$SESSION_SIGNING_SECRET")"
```

## Core OpenAI Agent Settings

Defined in `modal_backend/settings/settings.py`:

- `openai_api_key`
- `openai_model_default` (default: `gpt-4.1`)
- `openai_model_subagent` (default: `gpt-4.1-mini`)
- `openai_session_db_path` (default: `/data/openai_agents_sessions.sqlite3`)
- `openai_session_max_items` (default: `400`, set `None` to disable item-based compaction)
- `openai_session_compaction_keep_items` (default: `300`, set `None` to keep exactly `openai_session_max_items`)
- `agent_max_turns`

### Session Memory Compaction Controls

The OpenAI session store uses deterministic item-count compaction at session load time:

- Trigger: current item count is greater than `openai_session_max_items`.
- Action: retain newest `openai_session_compaction_keep_items` items (or `openai_session_max_items` when keep-items is `None`).
- Scope: applied for resume and fork targets; fork does not mutate source history.

Validation constraints:

- `openai_session_max_items` must be greater than `0` when set.
- `openai_session_compaction_keep_items` must be greater than `0` when set.
- `openai_session_compaction_keep_items` cannot exceed `openai_session_max_items`.

## Request Guardrails

`QueryBody` validation in `modal_backend/models/sandbox.py` now enforces:

- non-empty `question`
- max `question` length of 20,000 characters
- optional `trace_id` format: `[A-Za-z0-9._:-]{1,128}`

If `trace_id` is omitted, the controller generates one from request context.

## Trace Correlation

`modal_backend/api/controller.py` now propagates a stable `trace_id` through:

- runtime logs (`agent.query.start`, `agent.query_stream.start`)
- serialized assistant/result messages
- SSE `error` and `done` events

When available from OpenAI run metadata, `openai_trace_id` is also surfaced in:

- result payload metadata
- `/query` summary payload
- SSE `done` payload

## Runtime and Resource Settings

Common controls:

- `sandbox_cpu`, `sandbox_memory`
- `sandbox_timeout`, `sandbox_idle_timeout`
- `min_containers`, `max_containers`, `buffer_containers`
- `concurrent_max_inputs`, `concurrent_target_inputs`
- `volume_commit_interval`

These are documented inline in `modal_backend/settings/settings.py` and can be overridden via environment variables.

## Image Configuration

The Modal image is built in `modal_backend/main.py` by `_base_openai_agents_image()`.

Default image includes:

- Python 3.11
- `openai-agents==0.9.2`
- `langsmith[openai-agents]>=0.3.15`
- `fastapi`, `uvicorn`, `httpx`, `uv`

To add dependencies, update `_base_openai_agents_image()`.

## LangSmith Tracing

Optional tracing is controlled by:

- `enable_langsmith_tracing`
- `langsmith_secret_name`

When enabled, tracing is configured via `modal_backend/tracing.py` using LangSmith's `OpenAIAgentsTracingProcessor`.
Runs are wrapped with `langsmith_run_context(...)` so per-run metadata (for example `trace_id`, `session_id`, `request_id`) is attached to emitted traces.

## Troubleshooting

### Secret not found

```
modal.exception.NotFoundError: Secret 'openai-secret' not found
```

Fix:

```bash
modal secret create openai-secret OPENAI_API_KEY=your-key
```

### Slow first request

Likely a cold start. Increase `sandbox_idle_timeout` or keep warm pool enabled.

### Session memory not persisted

Verify `openai_session_db_path` points to mounted persistent storage (default `/data/...`).

### Memory growth and compaction

- Confirm compaction settings are valid and enabled (`openai_session_max_items` not `None`).
- Check logs for `openai.session.compacted` events to verify trimming activity during resume/fork session acquisition.
- Inspect `items_before`, `items_after`, `max_items`, and `keep_items` fields on that log event when tuning thresholds.
- If compaction is too aggressive, increase `openai_session_compaction_keep_items` while keeping it at or below `openai_session_max_items`.
- If run context is insufficient after compaction, fork a session before risky long runs to preserve a separate lineage.

### Missing LangSmith/OpenAI trace correlation

- Verify `enable_langsmith_tracing=true` and `LANGSMITH_API_KEY` is present in runtime env.
- Confirm request/summary contains `trace_id`; this is required baseline correlation even when `openai_trace_id` is absent.
- Treat missing `openai_trace_id` as expected when provider metadata does not expose it for a run.

### Tool policy denials

- `Bash` and `WebFetch` policy denials return explicit error text and keep the run alive.
- Confirm the tool name is in the agent allowlist and input adheres to policy limits in `modal_backend/mcp_tools/registry.py`.
