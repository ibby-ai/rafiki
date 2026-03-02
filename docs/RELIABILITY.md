# RELIABILITY

## Reliability Expectations
- Core API and execution flow docs must match runtime behavior.
- Active incidents or recurring failures should result in plan updates under `docs/exec-plans/active/`.

## Reliability Review Inputs
- `docs/references/troubleshooting.md`
- `docs/references/api-usage.md`
- Active reliability-related plans in `docs/exec-plans/active/`

## 2026-03-02 Reliability Update - Agent Sandbox Infra Hardening

### Delivered Reliability Controls
- Deterministic session-budget preflight denials in SessionAgent DO (non-stream, stream, and queue preflight).
- Strict scoped gateway->sandbox auth enforcement with deterministic `401` (missing/invalid scoped token) and `503` (missing scoped sandbox secret metadata) failure behavior.
- Runtime startup hardening report endpoint (`/runtime_hardening`) and environment scrubbing support.
- Removal of unsafe `eval` path from calculate tool with deterministic denial behavior.
- Artifact token verification with expiry/signature/session/job/path checks and revocation support.

### Validation Evidence
- `uv run python -m pytest tests/test_controller_runtime_openai.py tests/test_controller_tools.py tests/test_schemas_sandbox.py tests/test_internal_auth_middleware.py tests/test_settings_openai.py tests/test_tools_calculate.py tests/test_runtime_hardening.py tests/test_jobs_security.py tests/test_artifact_access.py tests/test_sandbox_auth_header.py` -> `86 passed`
- `npm --prefix edge-control-plane run check` -> pass
- `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> pass
- `npm --prefix edge-control-plane run test:integration` -> pass (`3 passed`)
- Budget denial runbook smoke:
  - `/query` -> deterministic `429` denial payload
  - `/query_stream` -> deterministic `query_error` denial payload
  - `/session/{id}/queue` -> deterministic `429` denial payload

### Known Reliability Gaps
- Background sandbox `/query` may still return `500` when upstream execution fails; budget rails continue to apply preflight before Modal forwarding.

## 2026-03-02 Reliability Update - Runtime Readiness Hardening Follow-up

### Delivered Reliability Controls
- Lock-guarded `SANDBOX` / `SERVICE_URL` lifecycle updates to prevent stale state clobbering.
- Structured readiness timeout diagnostics (phase/attempt/sandbox/tunnel/poll context).
- One-time recycle+retry for startup readiness timeout in sync and async startup paths.
- Deterministic second-failure cutoff (`Background sandbox startup failed after 2 attempts`).
- Prewarm readiness failures are marked and recycled instead of silently reused.
- Tunnel-discovery startup failures now enter the same bounded retry path as readiness-timeout failures.

### Validation Evidence
- `uv run python -m pytest tests/test_sandbox_auth_header.py tests/test_settings_openai.py` -> pass (`22 passed`)
- Required full matrix command -> `96 passed, 2 warnings`
- Cloudflare <-> Modal E2E rerun -> failed with Worker `/query` `500 {"ok":false,"error":"Unknown error"}` while Modal/Worker `/health` were healthy.

### Residual Reliability Risk
- Live query failures can still occur after startup (observed `Unknown error` path) and require deeper controller-sandbox observability beyond gateway-level readiness diagnostics.

## 2026-03-02 Reliability Update - TD-003 `/query` Live E2E Closure

### Delivered Reliability Controls
- `/query` gateway now normalizes upstream non-2xx responses into deterministic `{"ok":false,"error":"..."}` payloads instead of opaque nested `detail` strings.
- Sandbox runtime now receives Modal API credentials on sandbox surface (when enabled), removing `AuthError: Token missing` failures in live query execution.
- Controller startup now enforces writable OpenAI session DB path and falls back to `/tmp/openai_agents_sessions.sqlite3` when privilege-drop makes configured path unwritable.
- Worker-side `/query` now surfaces concrete upstream errors during incident triage; no `Unknown error` collapse observed in the fixed flow.

### Validation Evidence
- `npm --prefix edge-control-plane run check` -> pass
- `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> pass
- `npm --prefix edge-control-plane run test:integration` -> pass (`3 passed`)
- `uv run python -m pytest tests/test_controller_runtime_openai.py tests/test_controller_tools.py tests/test_schemas_sandbox.py tests/test_internal_auth_middleware.py tests/test_settings_openai.py tests/test_tools_calculate.py tests/test_runtime_hardening.py tests/test_jobs_security.py tests/test_artifact_access.py tests/test_sandbox_auth_header.py tests/test_query_proxy_error_normalization.py tests/test_agent_runtime_session_fallback.py` -> pass (`106 passed`)
- Live Cloudflare <-> Modal E2E rerun (`source .venv/bin/activate`) -> Worker `/query` returned `200` with expected response shape.

### Residual Reliability Risk
- Hot-reload churn during `modal serve` can repeatedly rebuild/restart the app while files change; runbook now explicitly recommends recycling named sandboxes after secret/runtime-surface changes before final E2E validation.
