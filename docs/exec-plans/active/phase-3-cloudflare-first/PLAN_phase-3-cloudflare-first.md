# ExecPlan: Phase 3 Cloudflare-First Cutover

## Purpose / Big Picture
Make Cloudflare Workers + Durable Objects the only public entry point. Modal remains an internal execution backend behind `X-Internal-Auth`. Implement all remaining Cloudflare TODOs (auth, rate limiting, KV session cache, presence events, queue migration, job events) and update docs/configs to reflect Phase 3 reality.

## Suprises & Discoveries
- Observation: (placeholder)
- Evidence: (placeholder)

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
- Outcome: (placeholder)
- Gaps: (placeholder)
- Lessons: (placeholder)

## Context and Orientation
- Cloudflare control plane lives in `edge-control-plane/` with Worker entry at `edge-control-plane/src/index.ts` and DOs in `edge-control-plane/src/durable-objects/`.
- Modal backend HTTP gateway is defined in `modal_backend/main.py` with internal auth middleware in `modal_backend/security/cloudflare_auth.py`.
- Modal controller runs in sandbox: `modal_backend/api/controller.py`.
- Modal prompt queue and session mappings live in `modal_backend/jobs.py` and are being deprecated.
- Docs across `docs/` and `edge-control-plane/` reference Phase 2/3 TODOs that will be updated.

## Plan of Work
1. Remove Modal prompt queue and session_key mapping, mark gateway internal-only, and clean related schemas/endpoints.
2. Implement Cloudflare auth enforcement, session_key KV cache, rate limiting, presence events, queue endpoints, and job events across Worker/DOs.
3. Update Cloudflare configuration and documentation for Phase 3, add breaking change notes.
4. Run ruff lint/format and tests, plus Modal run smoke checks.

## Concrete Steps
- Create tasks in `docs/exec-plans/active/phase-3-cloudflare-first/tasks/` with YAML frontmatter linking this plan.

## Progress
[ ] (TASK_01_modal_phase3.md) Remove Modal queue state and session_key mapping; mark gateway internal-only.
[ ] (TASK_02_cf_control_plane.md) Implement Cloudflare auth, KV cache, rate limiting, presence, queue endpoints, job events.
[ ] (TASK_03_docs_config.md) Update config and docs to Phase 3 Cloudflare-first; add changelog entry.
[ ] (TASK_04_tests_checks.md) Run lint/format/tests and smoke checks; record results.

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
