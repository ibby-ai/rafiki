# Runtime Readiness Hardening

## Problem
Cloudflare -> Modal E2E can intermittently fail on cold/slow startup with `/health_check` timeout in `get_or_start_background_sandbox_aio`, which surfaces as a request-time failure even when auth configuration is correct.

## User Outcome
Gateway runtime startup behaves deterministically under slow readiness: bounded diagnostics are emitted, stale startup attempts are recycled safely, one retry is attempted, and a second timeout fails predictably.

## Scope
- Add guarded global sandbox state reset/set helpers for `SANDBOX` and `SERVICE_URL`.
- Add structured readiness timeout handling for sync/async sandbox startup paths.
- Add one-time recycle+retry logic for readiness timeout in:
  - `get_or_start_background_sandbox`
  - `get_or_start_background_sandbox_aio`
- Reuse validated `service_timeout` setting for readiness probes.
- Mark/recycle failed prewarm claims when readiness probe fails.
- Add regression tests for guarded reset and retry semantics.
- Update runbook/reference/design/governance docs in the same wave.

## Non-Goals
- Any auth contract change for Cloudflare session tokens, `X-Internal-Auth`, or scoped sandbox headers.
- Any rollback path reintroducing legacy internal-auth fallback behavior.
- Major architecture changes outside readiness lifecycle behavior.

## Success Metrics
- Readiness timeout paths emit deterministic diagnostics with attempt/phase/sandbox context.
- Startup performs at most one retry after timeout; second timeout fails deterministically.
- Tests enforce:
  - guarded state reset behavior
  - one-retry semantics (sync + async)
  - deterministic second-timeout failure
- Cloudflare <-> Modal canonical E2E gate passes with `.venv` activation and updated runbook steps.

## Rollout / Risks
- Risk: terminating shared discovered sandboxes could disrupt in-flight traffic.
  - Mitigation: `reuse_by_name` timeout path does not force terminate; it clears guarded state and retries.
- Risk: stale globals can clobber newer runtime state under concurrent startup attempts.
  - Mitigation: id-guarded state clearing and lock-protected state transitions.
- Risk: retry masks deeper startup regressions.
  - Mitigation: bounded diagnostics + deterministic second-failure path and explicit runbook triage.

## Linked ExecPlan
- `docs/exec-plans/completed/runtime-readiness-hardening/PLAN_runtime-readiness-hardening.md`
