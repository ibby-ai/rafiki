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

## 2026-03-10 Security Update - Controller Rollout Cutover Safety

### Security-Relevant Outcomes
- Scoped sandbox auth remains strict during rollout: no internal-auth fallback reintroduced for sandbox endpoints.
- Rollout observability (`/service_info`, `/pool/status`) redacts scoped sandbox secret material.
- Function-vs-sandbox secret-surface split remains unchanged.
- Fresh-request admission now verifies `/query`, `/query_stream`, and queued job dispatch against the authoritative active-pointer generation at lease start, so stale prewarm claims cannot route fresh traffic onto draining controllers.
- Guarded generation-transition commit prevents stale writers from re-pointing active traffic after overlapping rollouts or stale-lock recovery.

### Verification Evidence
- `uv run python -m pytest tests/test_internal_auth_middleware.py tests/test_settings_openai.py` -> pass (`27 passed`)
- `uv run python -m pytest tests/test_sandbox_auth_header.py -k 'prewarm or stop_session or get_or_start_background_sandbox'` -> pass (`12 passed`)
- `uv run python -m pytest tests/test_controller_rollout.py` -> pass (`37 passed`)
- Generated proof artifact: `docs/generated/controller-rollout-cutover-safety-proof-2026-03-10T13-48-41-1030.json`
- Live rollout observability checks:
  - Cloudflare Worker secret repair configured `INTERNAL_AUTH_SECRET` and `SESSION_SIGNING_SECRET` before the public proof wave; deployed Modal `/service_info` accepted the same internal-auth value used on the Worker.
  - `/service_info` and `/pool/status` returned rollout status without `sandbox_session_secret` or synthetic-session secret material.
  - deployed cutover `1 -> 2` persisted `drain_call_id=fc-01KKAV7J9BHCF70NNHFZEFF2AQ` and terminated the replaced service without exposing scoped secret material.
  - deployed cutover `2 -> 3` persisted `drain_call_id=fc-01KKAV8YCS28RD8F8YQH464TT2` and terminated the replaced service without exposing scoped secret material.
  - both cutovers correlated schedule -> execution -> completion via `drain_execution_call_id` and `controller_drain.scheduled/start/complete` app-log lines without leaking scoped secret values.
  - both first public post-cutover Worker `/query` calls returned `HTTP 200` on the first try without reintroducing legacy fallback auth.

### Residual Security Risk
- `modal-auth-secret` remains a high-value secret on the sandbox surface when enabled; rotation and sandbox recycle still need to stay coupled operationally.

## 2026-03-13 Security Update - Code Quality Governance

### Security-Relevant Outcomes
- Public Worker request bodies and Worker-to-Modal JSON seams now use runtime validation, reducing silent contract drift at untrusted boundaries.
- Public `/jobs/**` reads now reject missing upstream ownership identity instead
  of silently trusting partial payloads, closing the Oracle fail-open actor-scope gap.
- Auth and contract modules in `edge-control-plane` are now covered by TypeDoc and dependency-cruiser gates, preserving boundary separation between shared contracts, auth helpers, and transport orchestration.
- Session stop audit identity is now derived from authenticated actor scope rather
  than a client-controlled `requested_by` field, closing a transport trust gap on
  persisted cancellation records.
- Python waiver handling is now explicit, time-bounded, and audited in CI; anonymous suppressions are not accepted as governance-approved exceptions.
- Waiver auditing now verifies that suppressions stay within the waiver's
  declared `scope` and correspond to the waiver's declared `rule`, and the live
  `.importlinter` / `dependency-cruiser` configs now have integrity coverage.
- The public session/event-bus surface is now explicit: documented DO-backed
  state/messages/queue routes plus `/ws` / `/events` remain public, while
  undocumented session alias/query passthrough paths are blocked at the Worker edge.

### Verification Evidence
- `uv run python scripts/quality/validate_code_quality_waivers.py` -> pass
- `uv run python scripts/quality/check_python_governance.py` -> pass
- `uv run python scripts/quality/check_python_boundary_config.py` -> pass
- `uv run python -m pytest tests/test_code_quality_waivers.py tests/test_python_boundary_config.py`
  -> pass (`4 passed`)
- `npm --prefix edge-control-plane run docs:api` -> pass
- `npm --prefix edge-control-plane run check:boundaries` -> pass
- `npm --prefix edge-control-plane run test:integration` -> pass (`15 passed`) with deterministic `400`/`502` checks on malformed Worker request and proxy-response payloads, including stop-route contract enforcement, fail-closed `/jobs/**` ownership, and CORS/session-route contract coverage
- `docs/generated/code-quality-governance-proof-2026-03-13T11-59-01+1030.json` records the current command matrix and classifies remaining baseline pytest failures as pre-existing unrelated

### Residual Security Risk
- `edge-control-plane/src/auth/session-auth.ts` still uses string equality
  rather than constant-time verification for session-token signature checks;
  this remains an explicit dated deferral from the Oracle review.
- Advisory modules such as `modal_backend/main.py`, `modal_backend/jobs.py`, and `edge-control-plane/src/index.ts` are reviewed but not yet blocking under the new governance contract.
- Full repo pytest still has unrelated failures in controller-rollout and sandbox-runtime suites; those are outside this rollout scope but still matter for release-level confidence.
