# PLAN_sandbox_persistence

## Purpose / Big Picture

Introduce robust, persistent Modal Sandbox orchestration to: 1) reuse a named long‑running service sandbox across workers; 2) persist user/session data via `modal.Volume.from_name`; 3) store sandbox metadata in a durable `modal.Dict`; 4) harden security (egress controls, connect tokens) and lifecycle (timeouts, idle) with clear observability. Outcome: lower cold‑starts, safer execution of untrusted code, and easier recovery via snapshots.

## Suprises & Discoveries

- Observation: Current app already runs a background sandbox but doesn’t persist metadata/volume or reuse by name across workers.
- Evidence: `get_or_start_background_sandbox` caches process-local globals only.

## Decision Log

- Decision: Reuse sandbox by name via `modal.Sandbox.from_name` and tag for discovery.
- Rationale: Enable cross-worker reuse; simplify lifecycle management.
- Date/Author: 2025-11-01 / AI

- Decision: Persist state with `modal.Volume.from_name` mounted at `/workspace`.
- Rationale: Preserve user/session artifacts across restarts and >1 sandbox.
- Date/Author: 2025-11-01 / AI

- Decision: Store metadata in `modal.Dict` named `sandbox-sessions`.
- Rationale: Durable registry for URLs, ids, timestamps, and snapshots.
- Date/Author: 2025-11-01 / AI

## Outcomes & Retrospective

Target outcomes:
- Named service sandbox with encrypted tunnel URL discoverable across workers
- Persistent volume for `/workspace` and helpers to upload content
- Optional connect‑token auth from controller to service
- Snapshot flow for >24h use and warm restarts
- Tuned resources + idle cleanup to manage costs

## Context and Orientation

Key files:
- `main.py`: Modal `App`, HTTP endpoint, background sandbox starter.
- `runner_service.py`: FastAPI app hosted inside the sandbox.
- `utils/env_templates.py`: Modal `Image` and secrets/workdir.
- `utils/tools.py`, `utils/prompts.py`: Agent SDK tools and prompts.

Current state:
- Background sandbox started via `modal.Sandbox.create` with encrypted port 8001 and cached in module globals only.
- No `modal.Dict`/tags to discover an existing sandbox across workers, and no persistent `modal.Volume`.

## Plan of Work

1) Add `modal.Dict` registry and constants (sandbox name, port, volume name) in `main.py`.
2) Reuse by name in `get_or_start_background_sandbox` via `Sandbox.from_name`; add tags.
3) Create and mount `modal.Volume.from_name` at `/workspace`.
4) Tune `Sandbox.create` (timeouts, idle_timeout, cpu/memory, verbose); persist metadata in the dict.
5) Add optional connect‑token auth: controller sends token; service validates `X-Verified-User-Data`.
6) Add `snapshot_service` function to capture filesystem diffs and store snapshot metadata.
7) Provide async variant of sandbox creation to improve concurrency.
8) Strengthen health‑checks/retries and error handling.
9) Add security toggles for egress restrictions and resource caps.
10) Document and verify with manual tests (`modal run`, `modal serve`, curl examples).

## Concrete Steps

- (Task 01) Audit current implementation and capture context.
- (Task 02) Insert `modal.Dict` registry and sandbox naming/tags into `main.py`.
- (Task 03) Mount persistent `Volume` and add simple volume helper utilities.
- (Task 04) Harden `Sandbox.create` with resources, timeouts, and verbose logs.
- (Task 05) Implement connect‑token flow in controller and service.
- (Task 06) Add snapshot function and metadata persistence logic.
- (Task 07) Add async sandbox creation/exec APIs where beneficial.
- (Task 08) Improve health checks, retries, and exception handling.
- (Task 09) Add security toggles for egress (block/allowlist) and limits.
- (Task 10) Define testing and operational verification steps, plus docs.

## Progress

- [ ] (TASK_01_sandbox_persistence.md) Audit current implementation and context mapping
- [ ] (TASK_02_sandbox_persistence.md) Add Dict registry and sandbox naming/tags
- [ ] (TASK_03_sandbox_persistence.md) Mount persistent Volume and helpers
- [ ] (TASK_04_sandbox_persistence.md) Tune Sandbox.create and resources
- [ ] (TASK_05_sandbox_persistence.md) Connect‑token controller/service flow
- [ ] (TASK_06_sandbox_persistence.md) Snapshot function and metadata
- [ ] (TASK_07_sandbox_persistence.md) Async creation variant
- [ ] (TASK_08_sandbox_persistence.md) Health checks and retries
- [ ] (TASK_09_sandbox_persistence.md) Security toggles and limits
- [ ] (TASK_10_sandbox_persistence.md) Testing & verification steps

## Testing Approach

- Dev loop: `modal serve main.py`, verify `/test_endpoint` forwards to sandboxed `/query`.
- Direct sandbox: curl `${SERVICE_URL}/health_check` and `/query`.
- Persistence: write file to `/workspace` inside service; terminate/recreate; verify persistence.
- Snapshot: call `snapshot_service`, recreate sandbox from image (follow‑up), verify state.
- Auth: enable connect‑token; validate 401 without token and success with token.

## Constraints & Considerations

- External API access needed by the agent means `block_network=True` may be unsuitable by default; expose toggle.
- 24h max sandbox lifetime requires snapshot/restore to extend beyond a day.
- Secrets must remain in Modal Secrets (`anthropic-secret`).


