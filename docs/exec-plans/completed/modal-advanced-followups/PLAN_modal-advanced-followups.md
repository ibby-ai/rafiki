# PLAN_modal-advanced-followups

## Purpose / Big Picture

Finalize the remaining Modal advanced-feature followups (excluding custom domains): enable periodic volume commits, set autoscaling/concurrency defaults, and operationalize Proxy Auth by documenting token creation and updating client examples to send `Modal-Key`/`Modal-Secret`. The user-visible result is more durable persistence without manual sandbox termination, better warm capacity/concurrency defaults, and secured public endpoints with ready-to-use client samples.

## Suprises & Discoveries

- Observation: Autoscaling defaults would have applied to non-HTTP helper functions if left global.
- Evidence: `_function_runtime_kwargs()` was used by `AgentRunner`, `run_agent_remote`, and `process_job_queue` in `modal_backend/main.py`.

## Decision Log

- Decision: Defer custom domains; focus on volume commits, autoscaling defaults, and Proxy Auth client enablement.
- Rationale: Custom domains require workspace/DNS ownership steps outside this repo; remaining items are repo-scoped.
- Date/Author: 2026-01-03 / Codex

- Decision: Keep autoscaling/concurrency defaults in `.env` and apply autoscale only to `http_app`.
- Rationale: Preserve easy overrides while avoiding unintended scaling of background helper functions.
- Date/Author: 2026-01-03 / Codex

- Decision: Standardize Proxy Auth examples on `MODAL_PROXY_KEY` / `MODAL_PROXY_SECRET` env vars.
- Rationale: Keeps sample clients consistent and avoids hardcoding credentials.
- Date/Author: 2026-01-03 / Codex

## Outcomes & Retrospective

- Enabled periodic volume commits (default 60s) via `.env` and updated persistence docs/examples.
- Added autoscaling + concurrency defaults (min/buffer/max + concurrent inputs) and scoped autoscale to `http_app`.
- Documented Proxy Auth token creation and updated HTTP examples to send `Modal-Key` / `Modal-Secret`.

## Context and Orientation

- `modal_backend/settings/settings.py` defines `volume_commit_interval`, autoscaling knobs, and `require_proxy_auth` but defaults are unset.
- `modal_backend/api/controller.py` commits/reloads volumes only when `volume_commit_interval` is configured.
- `modal_backend/main.py` wires `@modal.asgi_app(requires_proxy_auth=...)` and autoscaling/concurrency helpers but no defaults are set.
- `docs/references/api-usage.md` documents Proxy Auth headers, but client examples under `examples/05_http_endpoints/` do not send `Modal-Key`/`Modal-Secret`.
- `docs/references/configuration.md` lists the settings but does not provide recommended defaults or verification steps.

## Plan of Work

1. Audit current config and examples
   - Review `modal_backend/settings/settings.py`, `modal_backend/main.py`, `modal_backend/api/controller.py`, and `examples/05_http_endpoints/*` for existing defaults and client usage.
   - Identify the minimal set of changes to enable volume commits, autoscaling/concurrency defaults, and Proxy Auth headers in examples.

2. Enable and verify volume commit interval
   - Set a safe default for `VOLUME_COMMIT_INTERVAL` (via `.env` and/or settings) and update docs to explain the behavior and verification steps.
   - Add a short verification recipe to confirm commits are happening without sandbox termination.

3. Set autoscaling + concurrency defaults
   - Choose conservative defaults for `min_containers`, `buffer_containers`, and `concurrent_*` suitable for dev/prod starter usage.
   - Update config docs and any relevant operational guidance to show how to tune or disable.

4. Proxy Auth token enablement + client updates
   - Document workspace steps to create a Proxy Auth token and store the credentials safely.
   - Update example clients/scripts to accept `Modal-Key`/`Modal-Secret` via env vars or CLI args, and include them on requests.
   - Ensure README/docs call out the headers in quickstart usage when `require_proxy_auth` is enabled.

## Concrete Steps

Each task is tracked in `docs/exec-plans/completed/modal-advanced-followups/tasks/`.

## Progress

[x] (TASK_01_modal-advanced-followups.md) (2026-01-03 14:25) Audit current defaults and client usage for followups.

[x] (TASK_02_modal-advanced-followups.md) (2026-01-03 14:25) Enable and verify volume commit interval defaults and docs.

[x] (TASK_03_modal-advanced-followups.md) (2026-01-03 14:25) Configure autoscaling/concurrency defaults and guidance.

[x] (TASK_04_modal-advanced-followups.md) (2026-01-03 14:25) Proxy Auth token docs + client header updates.

## Testing Approach

- Run `modal serve -m modal_backend.main` and hit `/health` + `/query` with and without Proxy Auth headers.
- Confirm volume commit interval by writing to `/data` in the sandbox and verifying persistence without calling `terminate_service_sandbox`.
- Exercise concurrency/autoscaling settings under light local load (basic concurrency test or repeated curl requests).

## Constraints & Considerations

- Proxy Auth token creation is a workspace action and cannot be automated in this repo.
- Enabling `volume_commit_interval` adds commit overhead; choose a conservative default and document trade-offs.
- Autoscaling defaults should be safe for development and cost-aware, with clear guidance to tune in production.
