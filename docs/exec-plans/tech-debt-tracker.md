# Tech Debt Tracker

## How To Use
- Add one row per debt item.
- Link each item to an active or completed ExecPlan.
- Update status in the same PR that changes the debt state.

## Debt Backlog
| ID | Debt Item | Owner | Status | Target Date | Linked Plan | Notes |
|---|---|---|---|---|---|---|
| TD-001 | Post-cutover cleanup: remove remaining Phase 2 assumptions and parity debt | Platform | Closed (2026-03-12) | 2026-03-31 | `docs/exec-plans/completed/phase-3-cloudflare-first/PLAN_phase-3-cloudflare-first.md` | Closed after removing stale non-canonical migration docs, tightening the public README and deploy checklist to the current Cloudflare-first contract, and demoting legacy Claude-era planning artifacts from the main docs surface. |
| TD-002 | Production-hardening drift between stale Claude planning docs and current OpenAI runtime | Platform | Closed (2026-03-08) | 2026-03-08 | `docs/exec-plans/completed/openai-agents-hardening/PLAN_openai-agents-hardening.md` | Closed after repo audit confirmed `agent_max_turns`, `sandbox_ephemeral_disk`, safe AST-based calculate evaluation, and `session_id`/`fork_session` support are already present in the current OpenAI runtime; the Claude-named plan is retained only as a historical artifact. |
| TD-003 | Close Cloudflare<->Modal live `/query` E2E `500 Unknown error` after startup hardening | Platform | Closed (2026-03-02) | 2026-03-09 | `docs/exec-plans/completed/runtime-readiness-hardening/PLAN_runtime-readiness-hardening.md` | Resolved with sandbox Modal-auth secret injection, writable session-DB fallback, and normalized upstream `/query` error propagation; live E2E `/query` now returns `200`. |
