# ExecPlan: Phase 3 Cloudflare-First Cutover

## Purpose / Big Picture
Make Cloudflare Workers + Durable Objects the only public entry point. Modal remains an internal execution backend behind `X-Internal-Auth`. Implement all remaining Cloudflare TODOs (auth, rate limiting, KV session cache, presence events, queue migration, job events) and update docs/configs to reflect Phase 3 reality.

## Suprises & Discoveries
- Observation: By the 2026-03-08 repo audit, the Cloudflare-first cutover had already landed in code and canonical docs, but this plan was never moved out of `active/`.
- Evidence: `edge-control-plane/README.md`, `CHANGELOG.md`, `docs/references/api-usage.md`, `edge-control-plane/src/auth/session-auth.ts`, `edge-control-plane/src/index.ts`, `edge-control-plane/src/durable-objects/session-agent.ts`, `edge-control-plane/src/durable-objects/event-bus.ts`, `modal_backend/main.py`.

## Decision Log
- Decision: Keep Modal HTTP gateway internal-only (Cloudflare-only) and retain internal auth middleware.
- Rationale: Minimizes refactor, preserves Modal execution endpoints while removing public exposure.
- Date/Author: 2026-02-04 / Codex

- Decision: Enforce session-token client auth only in Cloudflare Worker.
- Rationale: Simplest, fully controlled token flow with shared secret.
- Date/Author: 2026-02-04 / Codex

- Decision: Use Cloudflare Rate Limiter binding for rate limiting.
- Rationale: Platform-supported limits with low overhead.
- Date/Author: 2026-02-04 / Codex

- Decision: Migrate prompt queue to SessionAgent DO and remove Modal prompt queue state.
- Rationale: Phase 3 deprecates Modal Dict queue and centralizes session state in DO.
- Date/Author: 2026-02-04 / Codex

## Outcomes & Retrospective
- Outcome: Completed. Cloudflare Workers + Durable Objects are the only public API surface, Modal gateway access is internal-only, and the repo code/docs reflect the cutover.
- Gaps: Residual post-cutover cleanup remains in non-canonical migration docs that still describe rollout or Modal fallback phases; this is tracked separately as `TD-001`.
- Lessons: Move cutover plans to `completed/` in the same wave that updates changelog and canonical references, otherwise active-plan taxonomy drifts after implementation is already done.

## Context and Orientation
- Cloudflare control plane lives in `edge-control-plane/` with Worker entry at `edge-control-plane/src/index.ts` and DOs in `edge-control-plane/src/durable-objects/`.
- Modal backend HTTP gateway is defined in `modal_backend/main.py` with internal auth middleware in `modal_backend/security/cloudflare_auth.py`.
- Modal controller runs in sandbox: `modal_backend/api/controller.py`.
- Modal prompt queue and session mapping settings remain only as deprecated placeholders in `modal_backend/settings/settings.py`; queueing and `session_key` resolution are owned by Cloudflare.
- Canonical docs now describe the Cloudflare-first cutover as current state; residual migration-language cleanup in non-canonical edge-control-plane docs is tracked under `TD-001`.

## Plan of Work
1. Remove Modal prompt queue and session_key mapping, mark gateway internal-only, and clean related schemas/endpoints.
2. Implement Cloudflare auth enforcement, session_key KV cache, rate limiting, presence events, queue endpoints, and job events across Worker/DOs.
3. Update Cloudflare configuration and documentation for Phase 3, add breaking change notes.
4. Run ruff lint/format and tests, plus Modal run smoke checks.

## Concrete Steps
- Tasks live in `docs/exec-plans/completed/phase-3-cloudflare-first/tasks/`.

## Progress
[x] (TASK_01_modal_phase3.md) Modal prompt queue/session-key mapping removed from active runtime ownership; Modal-side settings remain deprecated and unused while Cloudflare owns queue/session resolution.
[x] (TASK_02_cf_control_plane.md) Worker auth, KV cache, rate limiting, presence, queue endpoints, and job events are implemented in the Cloudflare control plane.
[x] (TASK_03_docs_config.md) Cloudflare-first docs/config/changelog updates are present, including internal-only Modal access and queue migration notes.
[x] (TASK_04_tests_checks.md) 2026-03-08 validation confirmed edge integration tests pass and edge type-check is clean; Python runtime test collection was blocked locally by missing `agents` dependency in the current environment.

## Testing Approach
- `uv run ruff check --fix .`
- `uv run ruff format .`
- `uv run pytest`
- `modal run -m modal_backend.main`
- `modal run -m modal_backend.main::run_agent_remote --question "health check"`
- Manual: `wrangler dev` checks for `/health`, `/query`, `/query_stream`, `/ws`, `/session/{id}/queue`, `/submit`, `/jobs/{id}`.

## Constraints & Considerations
- Avoid editing generated files (e.g., `edge-control-plane/.wrangler/tmp`).
- Enforce `X-Internal-Auth` for all non-health Modal endpoints.
- Cloudflare Worker must be the only public entry point.
- Use session tokens only for client auth; no API key/JWT support in Phase 3.
- Ensure docs reflect new defaults and breaking changes.
