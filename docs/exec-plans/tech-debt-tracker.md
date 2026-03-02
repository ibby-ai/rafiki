# Tech Debt Tracker

## How To Use
- Add one row per debt item.
- Link each item to an active or completed ExecPlan.
- Update status in the same PR that changes the debt state.

## Debt Backlog
| ID | Debt Item | Owner | Status | Target Date | Linked Plan | Notes |
|---|---|---|---|---|---|---|
| TD-001 | Post-cutover cleanup: remove remaining Phase 2 assumptions and parity debt | Platform | Open | 2026-03-31 | `docs/exec-plans/active/phase-3-cloudflare-first/PLAN_phase-3-cloudflare-first.md` | Public traffic is Cloudflare-first; track residual API/control-plane cleanup to retire Phase 2 assumptions. |
| TD-002 | Complete production-hardening tasks that are still unchecked | Platform | Open | 2026-03-31 | `docs/exec-plans/active/prod_readiness_claude_agent_sdk/PLAN_prod_readiness_claude_agent_sdk.md` | Includes safe eval replacement and hybrid session hardening. |
| TD-003 | Close Cloudflare<->Modal live `/query` E2E `500 Unknown error` after startup hardening | Platform | Closed (2026-03-02) | 2026-03-09 | `docs/exec-plans/completed/runtime-readiness-hardening/PLAN_runtime-readiness-hardening.md` | Resolved with sandbox Modal-auth secret injection, writable session-DB fallback, and normalized upstream `/query` error propagation; live E2E `/query` now returns `200`. |
