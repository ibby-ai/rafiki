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

## 2026-03-09 Reliability Update - Modal SDK 1.3.5 Upgrade

### Delivered Reliability Controls
- Repo dependency/lock state now targets `modal 1.3.5`, removing drift from the previously locked `1.3.0.post1`.
- Async request/startup flows now use Modal `.aio` interfaces for sandbox/app lookups and function spawns, eliminating blocking Modal calls in the validated ingress/runtime paths.
- Async HTTP endpoints that still rely on sync Modal-backed helpers (job/schedule/prewarm/session metadata stores) now run those helpers off the event loop via `anyio.to_thread`.
- Explicit teardown paths now use `terminate(wait=True)` when supported. `terminate_service_sandbox(immediate=True)` remains the hard-stop path; the default `terminate_service_sandbox()` behavior is now safe rollout/promotion rather than immediate teardown.

### Validation Evidence
- `uv run python -m pytest tests/test_sandbox_auth_header.py tests/test_query_proxy_error_normalization.py` -> pass (`28 passed`)
- `uv run python -m pytest tests/test_schedules.py tests/test_jobs_enqueue.py tests/test_jobs_cancellation.py tests/test_jobs_security.py` -> pass (`21 passed`)
- `uv run python -W error -m pytest -o asyncio_default_fixture_loop_scope=function tests/test_sandbox_auth_header.py -k 'prewarm or get_or_start_background_sandbox_aio or terminate'` -> pass (`8 passed`)
- `npm --prefix edge-control-plane run check` -> pass
- `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> pass

### Residual Reliability Risk
- The canonical Cloudflare <-> Modal live E2E runbook was not rerun in this upgrade wave, so real network/runtime behavior beyond the targeted local validation matrix remains an explicit follow-up if release confidence requires a full end-to-end replay.

## 2026-03-10 Reliability Update - Controller Rollout Cutover Safety

### Delivered Reliability Controls
- Request routing now uses shared active-pointer generation state as authority, with worker-local cache treated as non-authoritative.
- Promotion path creates/verifies replacement controller privately before pointer flip.
- Pointer promotion is now a guarded generation transition:
  - stale writers fail closed if rollout-lock ownership is lost
  - stale writers fail closed if the active generation already advanced
- Promotion lifecycle records active/promoting/draining/terminated states and tracks rollback target metadata.
- Fresh request admission for `/query`, `/query_stream`, and queued job dispatch now happens at lease start, so stale prewarm claims cannot land on draining controllers after promotion.
- Pointer recovery fails closed when the active pointer is missing and the rollout registry contains multiple `active` services.
- Pointer recovery also fails closed when a recovered single `active` controller cannot satisfy `attach_active_pointer` readiness: that service is marked `failed`, the pointer is cleared, and bootstrap starts a clean replacement controller.
- Draining controller termination waits for in-flight lease quiescence or bounded timeout.

### Validation Evidence
- `uv run python -m pytest tests/test_controller_rollout.py` -> pass (`37 passed`)
- `uv run python -m pytest tests/test_sandbox_auth_header.py -k 'prewarm or stop_session or get_or_start_background_sandbox'` -> pass (`12 passed`)
- `uv run python -m pytest tests/test_internal_auth_middleware.py tests/test_settings_openai.py` -> pass (`27 passed`)
- `npm --prefix edge-control-plane run check` -> pass
- `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> pass
- `npm --prefix edge-control-plane run test:integration` -> pass (`3 passed`)
- Generated proof artifact: `docs/generated/controller-rollout-cutover-safety-proof-2026-03-10T13-48-41-1030.json`
- Fresh local bootstrap replays still passed:
  - stale recovered-service replay seeded terminated sandbox `sb-CM9UmFjHr7LMoi5kpCijL3`; recovery failed closed at `attach_active_pointer`, marked that service `failed`, and bootstrapped clean controller `sb-DnzRPHPm3OSRmSs2vYptOZ`.
  - empty-pointer replay bootstrapped generation `1` controller `sb-SSgJAg9fZqBTFQFAjosr6t` from `before_active: null` under the default timeout.
- Deployed spawned-drain proof now passes through the canonical public Worker:
  - the Worker repair path configured `INTERNAL_AUTH_SECRET` and `SESSION_SIGNING_SECRET`, redeployed `rafiki-control-plane` against `https://saidiibrahim--modal-backend-http-app.modal.run`, and returned `/health` `200`.
  - deployed cutover `1 -> 2` returned `drain_status.mode=spawned`, `drain_call_id=fc-01KKAV7J9BHCF70NNHFZEFF2AQ`, immediate old-service `status="draining"`, final old-service `status="terminated"`, and first public post-cutover `/query` `HTTP 200` on the first try.
  - deployed cutover `2 -> 3` returned `drain_status.mode=spawned`, `drain_call_id=fc-01KKAV8YCS28RD8F8YQH464TT2`, immediate old-service `status="draining"`, final old-service `status="terminated"`, and first public post-cutover `/query` `HTTP 200` on the first try.
  - both cutovers persisted matching `drain_execution_call_id`, recorded `drain_timeout_reached=false`, `inflight_at_termination.total=0`, and emitted matching `controller_drain.scheduled/start/complete` log lines.
  - public Worker `/query_stream`, queue, and state checks passed after both cutovers.
  - the generated proof artifact still records `dirty_worktree=true`; runtime behavior is proven, but clean-commit reproducibility remains a separate signoff limitation.

### Residual Reliability Risk
- Local `modal serve` validation can be disrupted if cutover is triggered via `modal run` (webhook app stop/label steal), so serve-safe trigger path is now required in runbook.
- Direct live proof of hydrated `drain_controller_sandbox.spawn()` remains unavailable under `modal serve`; deployed spawned-drain proof now exists and must continue to use the deployed public Worker plus deployed Modal function path.
- The deployed proof packet was captured from a dirty worktree (`dirty_worktree=true`), so commit-level reproducibility remains an explicit follow-up even though the runtime behavior evidence is strong.
- The bootstrap retry boundary remains explicit and fail-closed: if readiness still times out twice, bootstrap returns `Background sandbox startup failed after 2 attempts` rather than trusting an unverified controller.

## 2026-03-10 Reliability Follow-up - Cloudflare Deploy Target Hardening

### Delivered Reliability Controls
- Plain `wrangler deploy` for `edge-control-plane` is now production-safe by default because top-level `wrangler.jsonc` vars point at the production Modal gateway.
- Local Worker development moved to `wrangler dev --env development`, preventing local/operator sessions from mutating the canonical public Worker deploy target by accident.
- `env.development` now duplicates the non-inheritable Worker bindings needed for local dev and uses explicit Durable Object script names (`rafiki-control-plane-development`) so dev DO state stays isolated from the canonical public Worker.
- Reference docs now describe the new default deploy contract and the local secret-loading caveat for `.dev.vars.development`.

### Validation Evidence
- `cd edge-control-plane && ./node_modules/.bin/wrangler deploy --dry-run` -> pass
- `cd edge-control-plane && ./node_modules/.bin/wrangler deploy --dry-run --env development` -> pass
- `npm --prefix edge-control-plane run check` -> pass
- `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> pass
- `npm --prefix edge-control-plane run test:integration` -> pass (`3 passed`)

### Residual Reliability Risk
- `env.development` currently reuses the checked-in KV and rate-limit binding IDs; local `wrangler dev` remains safe, but a remote deploy of the development environment should get dedicated non-production bindings before it is treated as an isolated shared environment.

## 2026-03-13 Reliability Update - Code Quality Governance

### Delivered Reliability Controls
- Worker transport boundaries now reject malformed request bodies deterministically via Zod before forwarding to Modal.
- Worker proxy seams now reject malformed upstream schedule/job JSON with deterministic `502` responses instead of accepting drifted payloads.
- Worker `/jobs/**` ownership checks now fail closed when upstream job payloads
  omit `session_id`, `user_id`, or `tenant_id` fields required for actor-scope
  enforcement.
- Session stop routes now preserve `GET` read-only status semantics, reject invalid
  stop bodies with deterministic `400`, and reject malformed upstream stop payloads
  with deterministic `502`.
- Worker public session ingress is now explicit: documented DO-backed
  `/state`, `/messages`, queue, and stop routes remain public, while
  undocumented `/session/{id}` aliases and `/session/{id}/query` passthroughs
  are blocked at the edge.
- Blocking Python governance now covers leaf-like models/security/serialization/webhook modules with doc, type, and import-boundary checks.
- CI and local task runners now include dedicated governance commands plus a proof-packet generator.

### Validation Evidence
- `uv run python scripts/quality/check_docs_governance.py` -> pass
- `uv run python scripts/quality/check_python_governance.py` -> pass
- `uv run python scripts/quality/check_python_boundary_config.py` -> pass
- `uv run python -m pytest tests/test_code_quality_waivers.py tests/test_python_boundary_config.py`
  -> pass (`4 passed`)
- `npm --prefix edge-control-plane run check:contracts` -> pass (`12 passed`)
- `npm --prefix edge-control-plane run test:integration` -> pass (`15 passed`)
- `npm --prefix edge-control-plane run check:boundaries` -> pass
- `docs/generated/code-quality-governance-proof-2026-03-13T11-59-01+1030.json` captures the full command matrix, marks `rollout_checks_passed=true`, and classifies the remaining pytest baseline as pre-existing unrelated

### Residual Reliability Risk
- Full `uv run pytest` still has unrelated baseline failures in rollout and sandbox-runtime suites. Those failures are outside this rollout's changed files but still block a fully green repo-wide release bundle.
- Wave-1 governance keeps orchestration hubs advisory, so transport and boundary regressions in those modules still rely on review plus existing tests until later ratchet waves.
- jobs-proxy passthrough errors can still mislabel some non-JSON upstream
  errors as JSON; this remains an explicit non-blocking residual risk from the
  Oracle review.
