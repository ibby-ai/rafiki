# SECURITY

## Security Documentation Expectations
- Secrets and auth flows must be documented in `docs/references/configuration.md` and related architecture docs.
- Security-impacting changes must update the relevant design docs and linked ExecPlan.

## Security Review Inputs
- `docs/design-docs/cloudflare-hybrid-architecture.md`
- `docs/references/configuration.md`
- `docs/references/api-usage.md`

## 2026-03-02 Security Update - Agent Sandbox Infra Hardening

### Implemented Security Controls
- Secret surface split by runtime surface (`function` vs `sandbox`) with explicit sandbox allowlist.
- Scoped sandbox auth tokens (`X-Sandbox-Session-Auth`) bound to path/sandbox/session and short TTL.
- Required sandbox identity binding (`X-Sandbox-Id`) for scoped-token validation.
- Legacy internal-auth sandbox fallback path removed from runtime and config surface.
- Session authority guard (`X-Session-History-Authority: durable-object`) for query/query_stream runtime forwarding.
- Artifact access tokens with signed claims, expiry cap, revocation store, and actor-scope checks.
- Tool execution hardening:
  - `calculate` now AST-only (no runtime `eval`)
  - stricter Bash/WebFetch deny policies and execution constraints.
- Runtime startup hardening:
  - sensitive env scrubbing
  - privilege-drop attempt and report
  - writable-path probing metadata.

### Verification Evidence
- `uv run python -m pytest tests/test_internal_auth_middleware.py tests/test_settings_openai.py tests/test_tools_calculate.py tests/test_controller_tools.py tests/test_runtime_hardening.py tests/test_jobs_security.py tests/test_artifact_access.py tests/test_sandbox_auth_header.py` -> pass (included in 86-pass suite)
- `npm --prefix edge-control-plane run check` -> pass
- `npm --prefix edge-control-plane run test:integration` -> pass (`3 passed`)
- `rg -n "eval\\(" modal_backend/mcp_tools` -> no matches
- Runtime command smoke:
  - `uv run modal run -m modal_backend.main`
  - `uv run modal run -m modal_backend.main::run_agent_remote --question "sandbox hardening smoke check"`
- Budget denial smoke produced deterministic denial payloads for non-stream/stream/queue flows.

### Residual Security Risks
- Warm-pool/session metadata drift can still surface as deterministic `503` until affected sandboxes are recycled with scoped session secrets.

## 2026-03-02 Security Update - Runtime Readiness Hardening Follow-up

### Security-Relevant Outcomes
- Startup retry/recycle logic is lifecycle-scoped only and does not alter auth verification paths.
- Strict scoped gateway->sandbox auth remains mandatory (`X-Sandbox-Session-Auth` + `X-Sandbox-Id`).
- No rollback path to legacy internal-auth fallback was introduced while hardening readiness behavior.

### Verification Evidence
- `uv run python -m pytest tests/test_internal_auth_middleware.py tests/test_settings_openai.py tests/test_sandbox_auth_header.py` -> included in this wave's `96 passed` matrix run.
- Docs/runbook updates explicitly preserve strict scoped-token-only guidance and remove unsafe direct-call examples.
- Cloudflare <-> Modal E2E rerun captured a non-auth runtime `500 Unknown error`; no scoped-auth rollback/fallback was introduced during mitigation.

### Residual Security Risk
- If readiness incidents persist, operators may be tempted to bypass canonical Cloudflare paths; runbook now requires strict-auth preserving remediation only (recycle/restart/diagnose) with no fallback toggles.

## 2026-03-02 Security Update - TD-003 `/query` Live E2E Closure

### Security-Relevant Outcomes
- Strict scoped gateway->sandbox auth hard-cut is unchanged:
  - no reintroduction of legacy internal-auth fallback paths
  - scoped `X-Sandbox-Session-Auth` + `X-Sandbox-Id` remains mandatory.
- Sandbox secret surface now includes `modal-auth-secret` when enabled so in-sandbox Modal SDK operations can authenticate; `INTERNAL_AUTH_SECRET` remains excluded from sandbox surface.
- `/query` upstream error normalization now preserves actionable error strings for operators while retaining the existing public Worker error envelope (`ok` + `error`).
- Controller OpenAI session DB path fallback prevents privilege-drop induced runtime write failures without relaxing auth enforcement.

### Verification Evidence
- `uv run python -m pytest tests/test_internal_auth_middleware.py tests/test_settings_openai.py tests/test_sandbox_auth_header.py tests/test_query_proxy_error_normalization.py tests/test_agent_runtime_session_fallback.py` -> pass (included in 106-pass matrix).
- `npm --prefix edge-control-plane run check` -> pass
- `npm --prefix edge-control-plane run test:integration` -> pass (`3 passed`)
- Live `/query` E2E now succeeds (`200`) through canonical Cloudflare path after sandbox recycle.

### Residual Security Risk
- `modal-auth-secret` is now available inside sandbox runtime when enabled; credential scope/rotation hygiene remains important. Keep secret-rotation and sandbox-recycle steps coupled in operations runbooks.
