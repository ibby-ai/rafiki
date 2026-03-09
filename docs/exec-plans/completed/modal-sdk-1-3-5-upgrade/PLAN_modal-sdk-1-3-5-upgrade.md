# PLAN_modal-sdk-1-3-5-upgrade

## Purpose / Big Picture
Upgrade Rafiki from `modal 1.3.0.post1` to the current upstream `1.3.5`, remove async-interface drift in the Modal-backed request paths, and adopt the new deterministic teardown support where it improves runtime behavior. The result should be a repo-locked Modal toolchain that matches the latest SDK, avoids sync Modal I/O in async startup/request flows, and preserves existing Cloudflare <-> Modal behavior.

## Suprises & Discoveries
- Observation: The repo dependency floor is `modal>=1.2.5`, but the lockfile and local environment were still on `1.3.0.post1`.
- Evidence: `pyproject.toml`, `uv.lock`, and `uv run python -c 'import importlib.metadata as md; print(md.version("modal"))'`

- Observation: Async Modal drift already exists in `modal_backend/main.py`; async handlers still call blocking `Sandbox.from_id`, `App.lookup`, and `Sandbox.from_name`.
- Evidence: `query_proxy`, `query_stream`, and `get_or_start_background_sandbox_aio` in `modal_backend/main.py`

- Observation: `Image.from_id` should not be blanket-converted to `.aio`; Modal 1.3.x treats it as a non-I/O path.
- Evidence: planning sub-agent code review `019cd0c2-5dd1-7832-87f5-927e9b5e61b3`

- Observation: On this macOS workspace, `uv run pytest ...` still resolved a non-repo pytest installation, while `uv run python -m pytest ...` used the synced repo interpreter and dependencies.
- Evidence: collection failed with `ModuleNotFoundError: No module named 'agents'` under `uv run pytest ...` but succeeded under `uv run python -m pytest ...`

## Decision Log
- Decision: Target `modal==1.3.5` as the upgrade floor for this wave.
- Rationale: PyPI reports `1.3.5` as the latest release published on 2026-03-03, ahead of the repo's locked `1.3.0.post1`.
- Date/Author: 2026-03-09 / Codex

- Decision: Fix async Modal call sites surgically instead of blanket-converting every Modal call to `.aio`.
- Rationale: Some 1.3.x APIs intentionally remain sync (`Image.from_id`), and broad conversion would add churn without removing the actual warning sources.
- Date/Author: 2026-03-09 / Codex

- Decision: Use `terminate(wait=True)` only in explicit teardown paths.
- Rationale: It improves determinism where code immediately assumes termination completed, but broad use in recycle/maintenance paths can increase latency and alter timing semantics.
- Date/Author: 2026-03-09 / Codex

- Decision: Do not adopt `Sandbox.detach()` in this wave.
- Rationale: Rafiki caches sandbox handles globally; detaching those handles would conflict with current ownership and reuse semantics.
- Date/Author: 2026-03-09 / Codex

- Decision: Standardize validation commands on `uv run python -m pytest`.
- Rationale: It forces the repo interpreter and synced Modal/OpenAI dependencies on this workspace, avoiding PATH drift during validation.
- Date/Author: 2026-03-09 / Codex

## Outcomes & Retrospective
- Rafiki now locks `modal 1.3.5` in both `pyproject.toml` and `uv.lock`, and the local `.venv` resolves that same version.
- Async ingress/startup paths now use Modal `.aio` interfaces for sandbox/app lookups and function spawns, while sync Modal-backed store helpers run off the event loop via `anyio.to_thread`.
- Explicit teardown now uses `terminate(wait=True)` when supported in `terminate_service_sandbox` and the local entrypoint, improving shutdown determinism without broadening wait semantics across recycle/maintenance paths.
- Runtime/operator docs now record the Modal 1.3.5 floor and the repo-local Python validation command shape.
- Targeted regression coverage passed, including warning-sensitive async checks; canonical live Cloudflare <-> Modal E2E was not rerun in this wave.

## Context and Orientation
- Dependency specification and lock state: `pyproject.toml`, `uv.lock`
- Modal runtime entrypoint and sandbox lifecycle: `modal_backend/main.py`
- Distributed job/sandbox state helpers: `modal_backend/jobs.py`
- Settings and secret surface helpers: `modal_backend/settings/settings.py`
- Primary regression coverage for sandbox lifecycle/auth: `tests/test_sandbox_auth_header.py`
- Query error normalization coverage: `tests/test_query_proxy_error_normalization.py`
- Canonical runtime docs: `docs/references/runbooks/cloudflare-modal-e2e.md`, `docs/references/configuration.md`, `docs/references/runtime-docs-overview.md`
- Governance evidence: `docs/QUALITY_SCORE.md`, `docs/RELIABILITY.md`

## Plan of Work
1. Add the active ExecPlan/task pack and register it in the exec-plan index.
2. Upgrade the Modal dependency floor in `pyproject.toml`, refresh `uv.lock`, and sync the local environment to the same version.
3. Update async request/startup paths in `modal_backend/main.py` to use `.aio` only where the SDK performs actual remote I/O (`App.lookup`, `Sandbox.from_id`, `Sandbox.from_name`), while keeping non-I/O paths such as `Image.from_id` sync.
4. Introduce a narrow helper for `terminate(wait=True)` support and apply it only to explicit teardown paths that immediately rely on completed termination.
5. Update tests to match async Modal method shapes and add warning-focused regression coverage.
6. Update runtime/reference/governance docs to reflect the new Modal floor, async-safe runtime behavior, and deterministic teardown expectations.
7. Run the declared validation matrix, record outcomes, and then move this plan to `completed/` if all work closes in this wave.

## Concrete Steps
- `tasks/TASK_01_modal-sdk-1-3-5-upgrade.md`
- `tasks/TASK_02_modal-sdk-1-3-5-upgrade.md`
- `tasks/TASK_03_modal-sdk-1-3-5-upgrade.md`

## Progress
[x] (TASK_01_modal-sdk-1-3-5-upgrade.md) Planning review completed; upgrade target and safe feature scope defined.

[x] (TASK_02_modal-sdk-1-3-5-upgrade.md) Dependency/code/test updates for Modal 1.3.5 compatibility, async-safe runtime access, and explicit teardown improvements.

[x] (TASK_03_modal-sdk-1-3-5-upgrade.md) Docs/governance sync plus validation matrix.

## Sub-Agent Collaboration Evidence
- Planning review (code risk): `019cd0c2-5dd1-7832-87f5-927e9b5e61b3`
  - Applied:
    - keep `Image.from_id` sync
    - avoid `Sandbox.detach()` with current cached-handle model
    - scope `terminate(wait=True)` to explicit teardown paths
    - add warning-sensitive async regression coverage
- Planning review (docs/evidence): `019cd0c2-633a-7871-9bfe-d5cc6acca952`
  - Applied:
    - create active ExecPlan/task pack for this migration wave
    - predeclare docs updates for runbook/runtime references
    - include explicit validation matrix and sub-agent evidence ledger
- Post-implementation review (code risk): `019cd0cf-dd81-7132-97f2-14b9dd64be63`
  - Applied:
    - added `/query_stream` prewarm-success regression coverage proving `Sandbox.from_id.aio(...)` is used without fallback allocation
  - Result:
    - no high-severity regressions found in changed runtime/dependency files after the test gap was closed
- Post-implementation review (docs/evidence): `019cd0cf-e388-7570-a1af-ea5babc41000`
  - Applied:
    - finalized plan closure requirements in this plan pack
    - recorded the remaining live canonical E2E gap as a deferred follow-up instead of leaving it implicit
- Deferred findings:
  - Canonical live Cloudflare <-> Modal E2E was not rerun in this wave. Code/test/docs evidence is sufficient for the SDK upgrade itself, but rollout-signoff should still run `docs/references/runbooks/cloudflare-modal-e2e.md` before any production-readiness claim that depends on end-to-end Cloudflare ingress proof.

## Testing Approach
- Dependency verification:
  - `uv run python - <<'PY' ... importlib.metadata.version("modal") ... PY` -> `modal 1.3.5`
- Targeted Python regressions:
  - `uv run python -m pytest tests/test_sandbox_auth_header.py tests/test_query_proxy_error_normalization.py -q` -> `29 passed`
- Warning-sensitive async regression:
  - `uv run python -W error -m pytest -o asyncio_default_fixture_loop_scope=function tests/test_sandbox_auth_header.py -k 'prewarm or get_or_start_background_sandbox_aio or terminate' -q` -> `9 passed, 16 deselected`
- Broader repo validation:
  - `npm --prefix edge-control-plane run check`
  - `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit`
  - `uv run python -m pytest tests/test_schedules.py tests/test_jobs_enqueue.py tests/test_jobs_cancellation.py tests/test_jobs_security.py -q` -> `21 passed`

## Constraints & Considerations
- Preserve existing Cloudflare-first ingress contract; do not reframe Modal as the public entrypoint.
- Avoid broad timing changes in warm-pool and recycle paths unless validation proves them safe.
- Keep worktree-safe behavior: do not disturb unrelated user changes if they appear during the upgrade.
- A full Cloudflare <-> Modal live E2E rerun remains optional follow-up evidence; it was not executed in this upgrade wave.
