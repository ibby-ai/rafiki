# PLAN_runtime-readiness-hardening

## Purpose / Big Picture
Stabilize Cloudflare <-> Modal runtime startup when sandbox controller readiness is slow. Startup should emit actionable diagnostics, perform one bounded recycle+retry, and fail deterministically on repeated timeout without altering auth hard-cut behavior.

## Suprises & Discoveries
- Observation: `/query` E2E failures reproduced as timeout waiting for `.../health_check` in async startup path.
- Evidence: `TimeoutError` surfaced from `_wait_for_service_aio` during `get_or_start_background_sandbox_aio`.

## Decision Log
- Decision: Reuse `service_timeout` for readiness probing and validate it is positive.
- Rationale: Avoid parallel timeout knobs and keep config contract explicit.
- Date/Author: 2026-03-02 / Codex

- Decision: Implement one-time retry (`max_attempts=2`) with deterministic second failure.
- Rationale: Improve recovery from transient startup stalls while preserving predictable failure semantics.
- Date/Author: 2026-03-02 / Codex

- Decision: Do not auto-terminate discovered `from_name` sandbox on readiness timeout.
- Rationale: Avoid killing potentially shared healthy sandboxes; prefer guarded reset + retry.
- Date/Author: 2026-03-02 / Codex

## Outcomes & Retrospective
- Runtime startup hardening shipped with guarded state reset, retryable startup failure handling, scoped-secret preservation checks, and one bounded retry.
- Required validation matrix passed after implementation updates.
- Cloudflare <-> Modal live E2E `/query` now passes with `HTTP 200` and expected response envelope.
- TD-003 root causes were closed in this wave:
  - upstream `/query` errors were double-encoded and collapsed to `Unknown error`
  - sandbox runtime lacked Modal auth secret on sandbox surface for metrics path
  - runtime privilege-drop caused readonly SQLite path for OpenAI session storage
- Added deterministic error normalization passthrough in gateway `/query` proxy, strict sandbox secret-surface alignment, and writable session-DB fallback behavior.
- Follow-up tracker updated: `docs/exec-plans/tech-debt-tracker.md` marks `TD-003` closed on 2026-03-02.

## Context and Orientation
- Runtime startup lifecycle: `modal_backend/main.py`
- Settings contract: `modal_backend/settings/settings.py`
- Regression tests: `tests/test_sandbox_auth_header.py`, `tests/test_settings_openai.py`
- Canonical runtime operations docs: `docs/references/runbooks/cloudflare-modal-e2e.md`, `docs/references/troubleshooting.md`, `docs/references/runtime-docs-overview.md`

## Plan of Work
1. Add guarded lifecycle helpers and structured readiness-timeout handling in `modal_backend/main.py`.
2. Add sync+async startup retry tests and state-reset tests.
3. Update runbook/reference/design/governance docs with readiness-timeout triage and validation evidence.
4. Run full validation matrix plus Cloudflare <-> Modal E2E with `.venv` activation.

## Concrete Steps
- `tasks/TASK_01_runtime-readiness-hardening.md`
- `tasks/TASK_02_runtime-readiness-hardening.md`
- `tasks/TASK_03_runtime-readiness-hardening.md`

## Progress
[x] (TASK_01_runtime-readiness-hardening.md) Runtime readiness timeout hardening in `modal_backend/main.py`.

[x] (TASK_02_runtime-readiness-hardening.md) Tests for guarded state reset + one-retry behavior.

[x] (TASK_03_runtime-readiness-hardening.md) Docs/governance updates and validation reruns (including live E2E `/query` success and TD-003 closure evidence).

## Sub-Agent Collaboration Evidence
- Planning review (code risk): `019cacf2-db89-7283-ab32-58354d9758d0`
  - Applied:
    - id-guarded global state reset/set
    - one bounded retry and deterministic second-failure path
    - startup diagnostics with phase/attempt metadata
- Planning review (docs/evidence): `019cacf2-dbbb-78a3-90f2-f0f8a1f9808e`
  - Applied:
    - added product spec + active exec-plan/task artifacts
    - updated runbook/reference/design/governance docs in same wave
- Post-implementation review (code risk): `019cac6e-be57-7c42-937d-bf5cebd369fa`
  - Applied:
    - preserved/scoped-secret resolution on reuse/attach/warm-pool paths
    - async warm-pool polling switched to `await pool_sb.poll.aio()`
    - tunnel-discovery failures made retryable under bounded startup retry
    - added regression tests for these paths
- Post-implementation review (docs/evidence): `019cac6e-be7f-7273-a217-08f1a5c4f370`
  - Applied:
    - aligned plan/gov docs completion state and pass/fail wording
    - added explicit sub-agent evidence ledger in plan artifact
    - aligned troubleshooting/runtime docs timeout and `.venv` guidance
- Planning review (code risk): `019cad23-697d-7b02-9371-267022cf76b1`
  - Applied:
    - normalized `/query` upstream error passthrough to preserve status + structured body
    - added regression tests for FastAPI `detail` shapes and non-JSON upstream failures
    - kept strict scoped auth hard-cut behavior (no legacy fallback reintroduction)
- Planning review (docs/evidence): `019cad23-69b2-7be2-9906-4e7b53c912cf`
  - Applied:
    - captured per-hop E2E failure evidence and hypothesis-elimination proof
    - expanded docs sync to include configuration, runtime overview, architecture, and governance scorecards
    - updated tracker with explicit TD-003 close criteria and closure note
- Post-implementation review (code risk): `019cad43-a121-7783-a179-5b526d821bda`
  - Applied:
    - fixed writable-path probe for nested creatable SQLite paths (avoid false `/tmp` fallback)
    - added sandbox secret-cache eviction on state clear/replace to prevent stale growth
    - added regression tests for creatable-path behavior and secret-cache eviction
- Post-implementation review (docs/evidence): `019cad43-a152-7d22-b9a7-6164901c245f`
  - Applied:
    - synchronized Task 03 + outcomes with TD-003 closed state
    - corrected architecture traceability label from `app.py` to `modal_backend/main.py`
    - aligned debt wording with Cloudflare-first cutover completion status
- Post-implementation recheck (code risk): `019cad3d-9d7d-7fb3-9cc5-c6d47077a26a`
  - Applied:
    - bounded sandbox secret cache growth for prewarm-only IDs
    - confirmed no remaining medium/high code findings after final patch set
- Post-implementation recheck (docs/evidence): `019cad3d-9dd2-7033-9206-666bb79efe81`
  - Applied:
    - moved runtime-readiness plan to completed index path and synced tracker linkage
    - added architecture note for `/query` error-envelope preservation
    - confirmed no remaining medium/high docs/evidence findings
- Deferred findings:
  - None (high/medium findings were applied in this wave).

## Testing Approach
- `uv run python -m pytest tests/test_sandbox_auth_header.py tests/test_settings_openai.py`
- Required matrix:
  - `npm --prefix edge-control-plane run check`
  - `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit`
  - `npm --prefix edge-control-plane run test:integration`
  - `uv run python -m pytest tests/test_controller_runtime_openai.py tests/test_controller_tools.py tests/test_schemas_sandbox.py tests/test_internal_auth_middleware.py tests/test_settings_openai.py tests/test_tools_calculate.py tests/test_runtime_hardening.py tests/test_jobs_security.py tests/test_artifact_access.py tests/test_sandbox_auth_header.py tests/test_query_proxy_error_normalization.py tests/test_agent_runtime_session_fallback.py`
- E2E gate:
  - `source .venv/bin/activate`
  - canonical Cloudflare <-> Modal runbook sequence in `docs/references/runbooks/cloudflare-modal-e2e.md`

## Constraints & Considerations
- Preserve strict scoped sandbox auth behavior; do not add rollback/fallback branches.
- Worktree is dirty; avoid touching unrelated changes.
- Keep retry bounded to one extra attempt; no unbounded loops.
