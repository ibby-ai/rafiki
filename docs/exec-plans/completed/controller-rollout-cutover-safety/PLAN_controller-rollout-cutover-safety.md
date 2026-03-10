# PLAN Controller Rollout Cutover Safety

## Purpose / Big Picture
Guarantee production-safe A->B controller cutover where public Worker traffic is never used to create or verify B, and where first public `/query` after controlled cutover succeeds on first try.

Practical meaning:

- The next real user message after a deploy or recycle must not become the readiness probe for B.
- If A is still finishing a streamed response, A should drain that work while fresh traffic moves to B.
- If a queued follow-up or background job fires during cutover, it must route to the authoritative active generation instead of landing on a controller that is already shutting down.
- In operator terms: safe cutover means users should not be able to tell which request crossed the A -> B handoff.

## Surprises & Discoveries
- Observation: invoking `uv run modal run -m modal_backend.main::terminate_service_sandbox` during local `modal serve` validation can steal/stop the dev webhook app and produce Worker `500` (`modal-http: app for invoked web endpoint is stopped`).
- Evidence: live cutover run on 2026-03-09 produced first-post-cutover Worker `/query` `500` with that exact error.
- Observation: stale recovered controllers are not retried in place during pointer recovery; if `attach_active_pointer` readiness fails, the registry entry is marked `failed` and bootstrap starts a clean controller.
- Evidence: live replay against terminated sandbox `sb-CM9UmFjHr7LMoi5kpCijL3` on 2026-03-09 produced `_SandboxReadinessTimeoutError` in `attach_active_pointer`, then promoted clean controller `sb-DnzRPHPm3OSRmSs2vYptOZ`.
- Observation: `modal serve` still cannot hydrate `drain_controller_sandbox.spawn()` for direct spawned-drain proof.
- Evidence: 2026-03-09 cutover replay again returned `modal.exception.ExecutionError: Function has not been hydrated...`; deterministic parity harness is the strongest automated spawned-drain evidence in local dev.
- Observation: the deployed Modal app path does hydrate spawned drain, but proving it required explicit drain call-id instrumentation because the prior rollout result only exposed `mode=spawned` without execution correlation.
- Evidence: 2026-03-10 deployed cutovers `4 -> 5` and `5 -> 6` persisted `drain_call_id`, `drain_execution_call_id`, and matching `controller_drain.scheduled/start/complete` log lines for `fc-01KKARB3BD5WP2F0F3Y1CNW5RS` and `fc-01KKARBXQ65FQXXSSDKBAB1NQM`.
- Observation: the public Worker blocker was an environment parity gap, not a rollout-semantics bug.
- Evidence: before repair, `wrangler secret list` returned `[]`; after uploading `INTERNAL_AUTH_SECRET` and `SESSION_SIGNING_SECRET` and redeploying with `MODAL_API_BASE_URL:https://saidiibrahim--modal-backend-http-app.modal.run` + `ENVIRONMENT:production`, deploy output returned `https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev` and `/health` returned `200` on 2026-03-10.
- Observation: the deployed proof artifact was captured from a dirty worktree, so it is not a clean-commit reproducibility proof.
- Evidence: `docs/generated/controller-rollout-cutover-safety-proof-2026-03-10T13-48-41-1030.json` records `dirty_worktree=true`.

## Decision Log
- Decision: make shared active pointer + generation authoritative for normal request routing.
- Rationale: fixed-name and stale module-global reuse can reattach workers to old controller after promotion.
- Date/Author: 2026-03-09 / Codex

- Decision: gate promotion on private `/health_check` + scoped-secret presence + synthetic direct `/query`.
- Rationale: health-only readiness is insufficient to prove real query serving behavior.
- Date/Author: 2026-03-09 / Codex

- Decision: track in-flight requests using per-request leases keyed by `request_id`.
- Rationale: aggregate counters are vulnerable to drift on retries/crashes; leases provide safer drain accounting.
- Date/Author: 2026-03-09 / Codex

- Decision: treat pointer promotion as a guarded generation transition from `G` to `G+1`.
- Rationale: stale writers must fail closed if rollout-lock ownership is lost or the active generation has already advanced.
- Date/Author: 2026-03-09 / Codex

- Decision: admit fresh requests only at lease start, not at prewarm claim time.
- Rationale: prewarm admissibility checks alone leave a TOCTOU gap where promotion can flip between check and forward.
- Date/Author: 2026-03-09 / Codex

- Decision: treat recovered-pointer attach failure as an explicit fail-closed boundary rather than attempting in-place recovery.
- Rationale: once pointer authority is missing, a recovered controller that cannot satisfy attach readiness is ambiguous; demoting it and bootstrapping cleanly preserves routing safety and auditability.
- Date/Author: 2026-03-09 / Codex

## Outcomes & Retrospective
- Code-level rollout model now includes guarded generation-transition commits, lease-start admission checks, fail-closed registry recovery, promotion gates, draining lifecycle, rollback-target tracking, and async-safe image-version reads on the bootstrap path.
- Required local bootstrap + cutover replay passed on 2026-03-09:
  - stale recovered-service replay marked terminated sandbox `sb-CM9UmFjHr7LMoi5kpCijL3` `failed` after `attach_active_pointer`, then promoted clean controller `sb-DnzRPHPm3OSRmSs2vYptOZ`
  - empty-pointer replay promoted generation `1` controller `sb-SSgJAg9fZqBTFQFAjosr6t` from `before_active: null`
  - `terminate_service_sandbox.local()` promoted generation `2` on `sb-IiLZoEm7isfh1XPElHMnX2`
  - post-cutover `/service_info` showed `last_verified_readiness_at=1773047392` on generation `2`, with generation `1` terminated and `drain_timeout_reached=false`
  - first public Worker `/query` after cutover returned `HTTP 200` with `e2e-ok`
  - Worker `/query_stream`, queue enqueue, and state checks passed after cutover
  - concrete overlap replay allowed exactly one commit (`sb-two`) and rejected the overlapping contender at the promotion commit slot
  - deterministic spawned-vs-inline drain parity harness matched terminal service state and rollback-target clearing
- Required deployed spawned-drain proof passed on 2026-03-10 for the Modal side:
  - deployed app `modal-backend` `v3` (`ap-NLl3xzI88msREbDi5ocPnR`) served two consecutive cutovers through the deployed function invocation path
  - cutover `4 -> 5` scheduled spawned drain `fc-01KKARB3BD5WP2F0F3Y1CNW5RS`; cutover `5 -> 6` scheduled spawned drain `fc-01KKARBXQ65FQXXSSDKBAB1NQM`
  - immediate `/service_info` snapshots showed old A `draining`; final snapshots showed old A `terminated`
  - both terminated services recorded `drain_timeout_reached=false` and `inflight_at_termination.total=0`
  - terminated-service metadata persisted matching `drain_execution_call_id`, and Modal app logs emitted matching `controller_drain.scheduled/start/complete` lines
  - rollback target metadata was present immediately after cutover and cleared after drain completion
- Public-ingress closure now passes on the canonical deployed Worker:
  - public Worker repair configured the missing secrets, redeployed `rafiki-control-plane`, and produced canonical URL `https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev`
  - deployed cutover `1 -> 2` returned first public post-cutover `/query` `HTTP 200` on the first try, then `/query_stream`, queue, and state checks passed
  - deployed cutover `2 -> 3` returned first public post-cutover `/query` `HTTP 200` on the first try, then `/query_stream`, queue, and state checks passed
  - both public cutovers kept the old controller on `draining -> terminated` with `drain_timeout_reached=false` and `inflight_at_termination.total=0`
- Evidence quality remains partial for release attribution:
  - the deployed proof packet captures strong runtime behavior evidence, but it was produced from a dirty worktree and should be rerun from a clean commit before release signoff if commit-level reproducibility is required

## Context and Orientation
- Runtime lifecycle and routing: `modal_backend/main.py`.
- Shared rollout store and inflight/session routing: `modal_backend/controller_rollout.py`.
- Warm-pool registration metadata: `modal_backend/jobs.py`.
- Rollout coverage: `tests/test_controller_rollout.py`.
- Canonical operations and configuration docs: `docs/references/runbooks/cloudflare-modal-e2e.md`, `docs/references/configuration.md`.

## Plan of Work
1. Stabilize routing and drain accounting correctness:
   - ensure every request path records/release inflight leases with deterministic `request_id`.
   - ensure async request entry only uses async-safe shared-state reads.
   - move fresh-request admission to lease start so stale prewarm claims cannot land on draining controllers.
2. Close promotion race findings:
   - make pointer promotion a fail-closed generation-transition commit.
   - reject stale writers that lose rollout-lock ownership or see the active generation advance.
   - make bootstrap pointer registration fail closed under the same ownership/generation rules.
3. Finalize docs/spec/governance alignment:
   - publish rollout spec and active plan.
   - update architecture/runbook/config/reliability/security/quality surfaces with guarded-commit and admission-at-lease semantics.
4. Complete live proof:
   - fresh Modal + Worker startup, establish A, private B rollout, first public `/query` after cutover must return `200` on first try, then verify `/query_stream`, queue/state, and drain completion evidence.

## Concrete Steps
- `docs/exec-plans/completed/controller-rollout-cutover-safety/tasks/TASK_01_controller-rollout-cutover-safety.md`

## Progress
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-09 16:30 ACDT) Implemented shared rollout state, pointer-first routing, readiness-gated promotion, and draining lifecycle with tests.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-09 17:05 ACDT) Fixed live regression where inflight lease API required `request_id` but call sites omitted it.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-09 post-review) Closed stale-writer and prewarm-admission race findings with guarded generation-transition commit, lease-start admission, and fail-closed registry recovery.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-09 19:43 ACDT) `uv run python -m pytest tests/test_controller_rollout.py` -> PASS (`33 passed`) after adding stale recovered-service sync/async bootstrap coverage, concrete two-writer overlap coverage, and spawned-vs-inline drain parity coverage.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-09 19:43 ACDT) `uv run python -m pytest tests/test_sandbox_auth_header.py -k 'prewarm or stop_session or get_or_start_background_sandbox'` -> PASS (`12 passed`) and `uv run python -m pytest tests/test_internal_auth_middleware.py tests/test_settings_openai.py` -> PASS (`27 passed`).
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-09 19:43 ACDT) `npm --prefix edge-control-plane run check`, `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit`, and `npm --prefix edge-control-plane run test:integration` -> PASS.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-09 19:43 ACDT) Live bootstrap recovery replay passed: clearing `controller-rollout-store`, seeding terminated sandbox `sb-CM9UmFjHr7LMoi5kpCijL3`, and calling `get_or_start_background_sandbox()` marked the stale service `failed` and promoted `sb-DnzRPHPm3OSRmSs2vYptOZ`.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-09 19:43 ACDT) Live empty-pointer replay passed: clearing `controller-rollout-store` and calling `get_or_start_background_sandbox()` from `before_active: null` promoted `sb-SSgJAg9fZqBTFQFAjosr6t`.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-09 19:43 ACDT) Re-ran complete live cutover proof on a fresh `modal serve` + `wrangler dev` stack: private promotion passed to `sb-IiLZoEm7isfh1XPElHMnX2`, first public Worker `/query` returned `200` on first try, `/query_stream` + queue/state passed, and replaced controller terminated without drain timeout.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-09 19:43 ACDT) Concrete local proof harnesses passed: threaded `_persist_active_controller_pointer` overlap replay committed exactly one writer, and spawned-vs-inline drain parity replay produced matching terminal controller state.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-09 19:43 ACDT) Persisted immutable proof packet at `docs/generated/controller-rollout-cutover-safety-proof-2026-03-09.json` and linked the active plan/docs to that artifact.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-10 12:42 ACDT) Added deployed drain audit instrumentation in `modal_backend/main.py`: rollout now persists `drain_call_id`, drain execution persists `drain_execution_call_id`, and app logs emit `controller_drain.scheduled/start/complete`.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-10 12:42 ACDT) `uv run python -m pytest tests/test_controller_rollout.py` -> PASS (`37 passed`) after adding spawned-drain metadata and execution call-id coverage.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-10 12:43 ACDT) Re-ran deployed cutover proof on the deployed Modal app path: generation `4 -> 5` and `5 -> 6` both returned `drain_status.mode=spawned`, correlated drain FunctionCall evidence, and terminated the replaced controller cleanly.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-10 12:43 ACDT) Persisted immutable deployed proof packet at `docs/generated/controller-rollout-cutover-safety-proof-2026-03-10T13-02-46-1030.json`, including command log, deployed cutover evidence, Modal log excerpts, and blocker classification for the missing public Worker deployment.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-10 12:43 ACDT) Re-ran worker-facing checks against `wrangler dev` wired to the deployed Modal base: `/query`, `/query_stream`, queue, and state passed; public Worker step remained blocked because Cloudflare reported missing script `rafiki-control-plane` (`code: 10007`).
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-10 13:35 ACDT) Repaired the canonical public Worker environment: uploaded `INTERNAL_AUTH_SECRET` and `SESSION_SIGNING_SECRET`, redeployed `rafiki-control-plane` against `https://saidiibrahim--modal-backend-http-app.modal.run`, and verified `/health` `200` at `https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev`.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-10 13:35 ACDT) Re-ran the full public-worker-first deployed proof wave: primed active A when `/service_info` reported `active: null`, then completed cutovers `1 -> 2` and `2 -> 3` with first public post-cutover `/query` `HTTP 200`, successful `/query_stream`, queue, and state checks, and clean spawned-drain termination evidence.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-10 13:48 ACDT) Persisted immutable public-worker-first proof packet at `docs/generated/controller-rollout-cutover-safety-proof-2026-03-10T13-48-41-1030.json`, superseding the 13:39 draft with the final exact command log, Worker-first evidence, git cleanliness, current Modal log excerpts, full PASS matrix, and reviewer applied/deferred summary.
- [x] (TASK_01_controller-rollout-cutover-safety.md) (2026-03-10 15:07 ACDT) Closed the production Worker deploy footgun: top-level `edge-control-plane/wrangler.jsonc` is now production-safe, local Worker dev moved to `wrangler dev --env development`, docs/scripts were updated to remove manual production `--var` overrides, and validation passed for default + `development` dry-run deploys, `npm --prefix edge-control-plane run check`, `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit`, and `npm --prefix edge-control-plane run test:integration`.

## Testing Approach
- Unit/integration:
  - `uv run python -m pytest tests/test_controller_rollout.py`
  - `uv run python -m pytest tests/test_sandbox_auth_header.py -k 'prewarm or stop_session or get_or_start_background_sandbox'`
  - `uv run python -m pytest tests/test_settings_openai.py tests/test_internal_auth_middleware.py`
  - `uv run python -m pytest tests/test_runtime_hardening.py tests/test_jobs_security.py tests/test_artifact_access.py`
- Edge quality gates:
  - `npm --prefix edge-control-plane run check`
  - `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit`
  - `npm --prefix edge-control-plane run test:integration`
- Live validation:
  - canonical Cloudflare <-> Modal E2E runbook
  - explicit cutover proof packet for A->B promotion and first public post-cutover `/query`.

## Proof Matrix

| Invariant | Evidence |
|---|---|
| Stale recovered active controller cannot stay authoritative after attach-readiness failure | live stale bootstrap replay (`sb-CM9UmFjHr7LMoi5kpCijL3` -> `failed`) + `tests/test_controller_rollout.py` stale bootstrap sync/async coverage |
| Empty-pointer first bootstrap promotes a clean controller | live empty-pointer replay (`before_active: null` -> `sb-SSgJAg9fZqBTFQFAjosr6t`) |
| Stale writer cannot overwrite newer promotion after lock expiry/overlap | `tests/test_controller_rollout.py` guarded-pointer tests + live threaded `_persist_active_controller_pointer` overlap replay |
| No fresh request lands on draining A through stale prewarm | `tests/test_sandbox_auth_header.py -k 'prewarm or stop_session or get_or_start_background_sandbox'` |
| Spawned drain path executes end-to-end through the deployed function path | canonical public-worker proof cutovers `1 -> 2` and `2 -> 3`, `drain_status.mode=spawned`, `drain_call_id`, `drain_execution_call_id`, Modal FunctionCall results/call graphs, and `controller_drain.scheduled/start/complete` logs |
| Rollback-target metadata clears after drain | `tests/test_controller_rollout.py::test_drain_controller_sandbox_clears_rollback_target_metadata` |
| Mixed-generation stop/session routing remains valid | `tests/test_controller_rollout.py::test_resolve_controller_route_for_session_prefers_draining_session_mapping` and `::test_resolve_controller_route_for_session_skips_terminated_route` |
| Worker/query behavior still holds against the deployed Modal base | canonical public Worker `https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev`, `/query`, `/query_stream`, queue, state all passed after cutovers `1 -> 2` and `2 -> 3` |
| Public Worker first-query acceptance | first public post-cutover `/query` returned `HTTP 200` on the first try for both public cutovers |

## Constraints & Considerations
- Sub-agent review loop is mandatory; findings must be captured as applied/deferred in finalization.
- Scoped sandbox auth contract must remain strict; no fallback reintroduction.
- `modal serve` vs `modal run` webhook behavior can affect local cutover tests and must be handled explicitly in runbook evidence.
- `modal serve` still cannot directly hydrate `drain_controller_sandbox.spawn()`; deployed proof must therefore use the deployed function path and not claim success from local-only evidence.
- Canonical public-worker deploys are now production-safe at the top level of `edge-control-plane/wrangler.jsonc`; local Worker work must use `npm run dev` / `wrangler dev --env development`, which keeps dev Durable Object state isolated via `rafiki-control-plane-development`.
- Deployed proof currently records `dirty_worktree=true`; clean-commit replay remains a separate signoff task if release attribution matters.
