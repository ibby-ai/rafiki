# Configuration Guide

This document covers runtime configuration for the Modal + OpenAI Agents deployment.

## Quick Setup

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
uv sync --extra dev
source .venv/bin/activate
uv run modal setup
uv run modal secret create openai-secret OPENAI_API_KEY=your-api-key-here
uv run modal run -m modal_backend.main
```

All Modal CLI commands in this doc are expected to run from the activated repo `.venv` (or prefixed with `uv run`).
This repo now expects the `.venv` to resolve `modal>=1.3.5`.
For Python validation, prefer `uv run python -m pytest ...` so the repo interpreter is used even when a global `pytest` is on `PATH`.

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

This secret remains required for:

- Cloudflare Worker -> Modal gateway internal authentication.
- Modal function runtime surfaces that mint/verify internal tokens.

### Modal Auth Secret

When `ENABLE_MODAL_AUTH_SECRET=true` (default), controller calls into Modal sandboxes
require `modal-auth-secret` containing `SANDBOX_MODAL_TOKEN_ID` and
`SANDBOX_MODAL_TOKEN_SECRET`.

```bash
modal secret create modal-auth-secret \
  SANDBOX_MODAL_TOKEN_ID=<token-id> \
  SANDBOX_MODAL_TOKEN_SECRET=<token-secret>
```

## Secret Surface Contract (Task 02)

Rafiki now uses split secret injection by execution surface:

- Function/runtime surface (`get_modal_secrets(surface="function")`):
  - `openai-secret` (`OPENAI_API_KEY`)
  - `internal-auth-secret` (`INTERNAL_AUTH_SECRET`)
  - `modal-auth-secret` (`SANDBOX_MODAL_TOKEN_ID`, `SANDBOX_MODAL_TOKEN_SECRET`) when `ENABLE_MODAL_AUTH_SECRET=true` (default)
  - optional tracing secret (`LANGSMITH_API_KEY`)
- Sandbox/controller surface (`get_modal_secrets(surface="sandbox")`):
  - `openai-secret` (`OPENAI_API_KEY`)
  - `modal-auth-secret` (`SANDBOX_MODAL_TOKEN_ID`, `SANDBOX_MODAL_TOKEN_SECRET`) when `ENABLE_MODAL_AUTH_SECRET=true` (default)
  - optional tracing secret (`LANGSMITH_API_KEY`)
  - explicitly excludes `INTERNAL_AUTH_SECRET`

Scoped sandbox auth is now session/sandbox/path bound via `X-Sandbox-Session-Auth` and
`X-Sandbox-Id` headers. Tokens are short-lived and validated in
`modal_backend/security/cloudflare_auth.py`.

## Runtime/Auth Hardening Environment Controls

Relevant settings/env keys introduced for hardening:

- `REQUIRE_INTERNAL_AUTH_SECRET` (default `true`; set `false` inside scoped sandbox runtime)
- `SANDBOX_SESSION_SECRET` (per-sandbox scoped auth verification key)
- `SANDBOX_SESSION_TOKEN_TTL_SECONDS` (default `120`)
- `REQUIRE_ARTIFACT_ACCESS_TOKEN` (default `true`)
- `ARTIFACT_ACCESS_TOKEN_MAX_TTL_SECONDS` (default `300`)
- `ARTIFACT_ACCESS_REVOCATION_STORE_NAME` (default `artifact-access-revocations`)
- `SANDBOX_DROP_PRIVILEGES`, `SANDBOX_RUNTIME_UID`, `SANDBOX_RUNTIME_GID`, `SANDBOX_WRITABLE_ROOTS`
- `SERVICE_TIMEOUT` (default `60`, minimum `1`) for sandbox readiness probes (`/health_check`)
- `CONTROLLER_ROLLOUT_STORE_NAME` (default `controller-rollout-store`) authoritative active-pointer + rollout lifecycle store
- `CONTROLLER_ROLLOUT_LOCK_MAX_AGE_SECONDS` (default `900`) stale-rollout lock recovery threshold and stale generation-transition claim reclamation window
- `CONTROLLER_DRAIN_TIMEOUT_SECONDS` (default `300`) max drain wait before forced termination fallback
- Legacy sandbox fallback controls are removed; scoped sandbox auth is mandatory.

## Rotation and Remediation (Scoped Sandbox/Auth + Artifact)

### Scoped sandbox auth hard-cut (strict scoped-token-only)

1. Deploy code with scoped sandbox token support enabled and legacy fallback branches removed.
2. Recycle warm pool/service sandboxes so all active entries have `sandbox_session_secret`.
3. Verify transition health:
   - `curl -sS "$DEV_URL/pool/status" -H "X-Internal-Auth: <signed-token>" | jq '{missing_scoped_secret_count,scoped_secret_transition_stable}'`
   - Require `missing_scoped_secret_count == 0` and `scoped_secret_transition_stable == true`.
4. Verify `/query` and `/query_stream` pass through Cloudflare runbook flow.

Failure behavior:

- Missing/invalid scoped token -> deterministic `401`.
- Missing scoped sandbox secret metadata -> deterministic `503` at gateway->sandbox forwarding.

Remediation steps:

1. Recycle/recreate affected sandboxes until scoped secrets are present.
2. Re-run `uv run python -m pytest tests/test_internal_auth_middleware.py tests/test_sandbox_auth_header.py`.
3. Re-run Cloudflare/Modal `/query` + `/query_stream` smoke from runbook.

### Controlled rollout trigger notes

- `terminate_service_sandbox` now defaults to safe A->B rollout behavior (create/verify/promote/drain) rather than immediate teardown.
- Emergency hard terminate remains available via `terminate_service_sandbox(immediate=True)` and should be reserved for break-glass scenarios.
- `GET /service_info` is rollout/status observability only; it does not create, warm, or verify a controller.
- Promotion commit is fail-closed:
  - the promoting writer must still own the rollout lock
  - the active pointer must still report the expected previous generation before the pointer flips
  - overlapping or stale writers that miss either condition abort and cannot overwrite the active pointer
- Fresh request admission is fail-closed:
  - request leases are created only if the target sandbox still matches the active pointer generation
  - stale prewarm claims are marked failed and rerouted before forwarding
- If the active pointer is missing and the rollout registry contains multiple `active` services, routing/bootstrap recovery fails closed until the ambiguity is resolved.
- If the active pointer is missing and registry recovery finds one `active` service that then fails `attach_active_pointer` readiness, the recovered service is marked `failed`, the pointer is cleared, and a clean bootstrap begins.
- For local `modal serve` validation, trigger safe rollout with:
  - `uv run python -c "from modal_backend.main import terminate_service_sandbox; print(terminate_service_sandbox.local())"`
  Use `modal run -m modal_backend.main::terminate_service_sandbox` only against deployed/webhook-backed app runs.
- In local `modal serve` validation, `drain_controller_sandbox.spawn()` can be unavailable; `drain_status.mode=inline` is acceptable when the result still reports `status=terminated` and `drain_timeout_reached=false`.
- Local `modal serve` cannot directly prove hydrated spawned-drain behavior because `drain_controller_sandbox.spawn()` is not hydrated there; require deterministic parity-harness evidence alongside the inline live fallback.
- Required regression gates for rollout changes:
  - `uv run python -m pytest tests/test_controller_rollout.py`
  - `uv run python -m pytest tests/test_sandbox_auth_header.py -k 'prewarm or stop_session or get_or_start_background_sandbox'`
  - `uv run python -m pytest tests/test_internal_auth_middleware.py tests/test_settings_openai.py`

### Artifact token rollback

Rollback trigger:

- Artifact list/download regressions caused by scoped token enforcement.

Rollback steps:

1. Set `REQUIRE_ARTIFACT_ACCESS_TOKEN=false` (temporary).
2. Keep path traversal and actor-scope checks enabled.
3. Re-run `uv run python -m pytest tests/test_jobs_security.py tests/test_artifact_access.py`.

## Cloudflare <-> Modal E2E Environment Baseline

Canonical runbook: `docs/references/runbooks/cloudflare-modal-e2e.md`

### Required Worker-side configuration

- `MODAL_API_BASE_URL` (`edge-control-plane/wrangler.jsonc`)
- `SESSION_CACHE` KV binding
- `SESSION_SIGNING_SECRET` secret
- `INTERNAL_AUTH_SECRET` secret

Notes:

- The standard Cloudflare <-> Modal `/health`, `/query`, `/query_stream`, queue, and state
  path currently signs Worker -> Modal requests with `INTERNAL_AUTH_SECRET`; the Worker source
  does not consume `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` for that canonical E2E request path.
- If future Worker routes adopt explicit Modal workspace auth, document those separately instead
  of treating them as part of the baseline E2E contract.
- `edge-control-plane/wrangler.jsonc` keeps the canonical public Worker production-safe at the top
  level and isolates local/operator values under `env.development`.
- `npm run dev` expands to `wrangler dev --env development`; that env keeps explicit Durable Object
  `script_name` values (`rafiki-control-plane-development`) so local/dev object state stays
  isolated from the canonical public Worker.
- Keep shared local secrets in `edge-control-plane/.dev.vars` unless you intentionally want an
  env-specific file. Adding `.dev.vars.development` causes Wrangler to stop loading the generic
  `.dev.vars` for that environment.
- Canonical public-worker proof or production ingress repair now uses the checked-in production
  defaults directly:

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane
npm run deploy
```

### Required Modal-side configuration

- `internal-auth-secret` Modal secret containing `INTERNAL_AUTH_SECRET`
- value must exactly match the Worker `INTERNAL_AUTH_SECRET`
- `modal-auth-secret` Modal secret containing (required when `ENABLE_MODAL_AUTH_SECRET=true`, default):
  - `SANDBOX_MODAL_TOKEN_ID`
  - `SANDBOX_MODAL_TOKEN_SECRET`

### Standard local exports

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki

export DEV_URL="https://saidiibrahim--modal-backend-http-app-dev.modal.run"
export MODAL_API_BASE_URL="$DEV_URL"
export WORKER_URL="http://localhost:8787"
```

### Edge Control Plane Quality Tooling

`edge-control-plane` uses Ultracite with Biome for lint/format checks.
These checks are required release gates.

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane
npm run check
```

Auto-fix pass (mutates files):

```bash
npm run fix
```

Worker integration proxy suite:

```bash
npm run test:integration
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
- `openai_session_db_path` (default: `/data/openai_agents_sessions.sqlite3`, with runtime fallback to `/tmp/openai_agents_sessions.sqlite3` when the configured path is not writable under dropped privileges)
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

- `service_timeout` (readiness probe timeout)
- `sandbox_cpu`, `sandbox_memory`
- `sandbox_timeout`, `sandbox_idle_timeout`
- `min_containers`, `max_containers`, `buffer_containers`
- `concurrent_max_inputs`, `concurrent_target_inputs`
- `volume_commit_interval`

These are documented inline in `modal_backend/settings/settings.py` and can be overridden via environment variables.

Readiness timeout semantics:

- Gateway startup probes controller `/health_check` using `service_timeout`.
- On timeout, runtime emits diagnostics and retries once.
- A second startup failure fails deterministically (`Background sandbox startup failed after 2 attempts`).

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
