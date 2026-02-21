# Tech Debt Tracker

## How To Use
- Add one row per debt item.
- Link each item to an active or completed ExecPlan.
- Update status in the same PR that changes the debt state.

## Debt Backlog
| ID | Debt Item | Owner | Status | Target Date | Linked Plan | Notes |
|---|---|---|---|---|---|---|
| TD-001 | Finish Cloudflare-first cutover and remove remaining Phase 2 assumptions | Platform | Open | 2026-03-31 | `docs/exec-plans/active/phase-3-cloudflare-first/PLAN_phase-3-cloudflare-first.md` | Track API and control-plane parity completion. |
| TD-002 | Complete production-hardening tasks that are still unchecked | Platform | Open | 2026-03-31 | `docs/exec-plans/active/prod_readiness_claude_agent_sdk/PLAN_prod_readiness_claude_agent_sdk.md` | Includes safe eval replacement and hybrid session hardening. |
