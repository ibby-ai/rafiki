# PLAN_modal-advanced-features

## Purpose / Big Picture

Introduce advanced Modal platform features to improve persistence, security, scaling, resiliency, and runtime efficiency of the agent sandbox. The user-visible result is faster warm responses, safer public endpoints, smoother burst handling, and more durable artifact storage without needing to terminate the background sandbox. This plan also adds memory snapshots and resource tuning for improved cold starts and throughput. Custom domains are noted as a future enhancement (deferred).

## Suprises & Discoveries

- Observation: None yet (plan initialization).
- Evidence: N/A.

## Decision Log

- Decision: Stage advanced Modal features as incremental, testable changes with opt-in defaults.
- Rationale: Reduces risk of regressions in a long-lived sandbox service.
- Date/Author: 2026-01-02 / Codex

- Decision: Fold memory snapshots, v2 volumes, and resource limit tuning into the implementation plan.
- Rationale: These are low-effort/high-impact improvements called out in the peer plan.
- Date/Author: 2026-01-02 / Codex

## Outcomes & Retrospective

- Pending implementation.

## Context and Orientation

- The project uses `modal_backend/main.py` to define the Modal app, HTTP ASGI entrypoint, and a long-lived `modal.Sandbox` running `modal_backend.api.controller:app`.
- Persistence currently relies on `modal.Volume` mounted at `/data`, but volume writes are flushed only when the sandbox terminates.
- Public HTTP endpoints are exposed via `@modal.asgi_app()` without workspace proxy auth; sandbox-to-service access can use connect tokens.
- The background service pattern is designed to keep latency low while using a short-lived frontdoor function to proxy requests.

Key files:
- `modal_backend/main.py` — Modal app configuration, sandbox lifecycle, HTTP endpoints.
- `modal_backend/api/controller.py` — FastAPI service inside the sandbox.
- `modal_backend/settings/settings.py` — Configuration and Modal secrets.
- `README.md` and `docs/` — Developer guidance and operational notes.

## Plan of Work

1. Baseline audit and settings alignment
   - Review `modal_backend/main.py` and `modal_backend/settings/settings.py` for existing lifecycle, timeouts, and resource settings.
   - Identify safe defaults and add config flags for new features (autoscaling, concurrency, retries, proxy auth, memory snapshots, queue settings, resource limits).

2. Volume commit/reload persistence
   - Update `modal_backend/main.py` and `modal_backend/api/controller.py` to commit the Modal Volume after agent runs and reload volume in reader paths.
   - Consider `sb.reload_volumes()` or `Volume.reload()` for fresh reads and periodic background commits.
   - Upgrade persistent volume to v2 where safe for better concurrency and file scaling.
   - Ensure `terminate_service_sandbox()` no longer has to be the only persistence trigger.

3. Proxy auth for public HTTP endpoints
   - Apply `requires_proxy_auth=True` to `@modal.asgi_app()` in `modal_backend/main.py`.
   - Add documentation in `README.md` for obtaining and using Proxy Auth tokens.

4. Autoscaling and concurrency controls
   - Set `min_containers`, `buffer_containers`, `max_containers`, and `scaledown_window` for the HTTP app and worker functions in `modal_backend/main.py`.
   - Add `@modal.concurrent` to increase per-container throughput for I/O-heavy endpoints.
   - Add resource limit tuning (CPU/memory limit tuples and ephemeral disk) via settings.

5. Queue-based ingestion path
   - Introduce a `modal.Queue` for decoupled request ingestion and agent execution.
   - Add a queue consumer function to process jobs and persist results (e.g., `modal.Dict`).
   - Add `/submit`, `/jobs/{job_id}`, and `/jobs/{job_id}` delete endpoints for enqueue/status/cancel.
   - Update HTTP endpoints to optionally enqueue jobs instead of synchronous execution.

6. Retry policies
   - Add `modal.Retries` to sandbox creation, snapshot, and queue processing to handle transient failures.

7. Class-based lifecycle hooks and memory snapshots
   - Convert the agent runner to `@app.cls` with `@enter` for heavy initialization and `@exit` cleanup.
   - Enable memory snapshots and move heavy imports/tool registry initialization into `@modal.enter(snap=True)`.
   - Add a lightweight `@modal.enter(snap=False)` method for post-restore setup.

8. Documentation, tests, and future notes
   - Update `README.md` and `docs/` with new operational behavior and configuration.
   - Add or adjust tests in `tests/` for queue handling and auth headers if applicable.
   - Document custom domains as a future enhancement (no implementation in this plan).

## Concrete Steps

Each step is tracked in `docs/exec-plans/completed/modal-advanced-features/tasks/` and linked in the Progress section below.

## Progress

[x] (TASK_01_modal-advanced-features.md) (2026-01-02 18:23) Baseline audit and settings alignment.

[x] (TASK_02_modal-advanced-features.md) (2026-01-02 18:27) Volume commit/reload persistence updates.

[x] (TASK_03_modal-advanced-features.md) (2026-01-02 18:33) Proxy auth enablement and docs.

[x] (TASK_04_modal-advanced-features.md) (2026-01-02 18:38) Autoscaling and concurrency controls.

[x] (TASK_05_modal-advanced-features.md) (2026-01-02 18:47) Queue ingestion + worker path.

[x] (TASK_06_modal-advanced-features.md) (2026-01-02 18:58) Retry policies for key operations.

[x] (TASK_07_modal-advanced-features.md) (2026-01-02 18:58) Class-based lifecycle hooks.

[x] (TASK_08_modal-advanced-features.md) (2026-01-02 18:58) Docs/tests updates and custom-domain future notes.

## Testing Approach

- `uv run pytest` for unit coverage changes.
- `modal run -m modal_backend.main::run_agent_remote --question "health check"` to validate agent execution path.
- `modal serve -m modal_backend.main` and hit `/health`, `/query`, `/query_stream` with and without proxy auth.
- For queue mode, enqueue a task and verify consumer processing and persistence.
- Cold start benchmarking before/after memory snapshots (if feasible in dev).

## Constraints & Considerations

- Keep Modal API usage compatible with Modal runtime images and Python 3.11.
- Avoid breaking the existing synchronous query path while adding queue-based ingestion.
- Custom domains require workspace-level configuration and DNS ownership. This is recorded as a future enhancement only and will not be implemented in this plan.
