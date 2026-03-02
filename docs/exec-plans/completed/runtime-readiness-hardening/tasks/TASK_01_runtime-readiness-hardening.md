---
task_id: 01
plan_id: PLAN_runtime-readiness-hardening
plan_file: ../PLAN_runtime-readiness-hardening.md
title: Harden runtime startup readiness lifecycle
phase: Phase 1 - Runtime Lifecycle
---

## Summary
Implement guarded sandbox state transitions and structured readiness timeout handling with a single recycle+retry.

## Scope
- `modal_backend/main.py`
- `modal_backend/settings/settings.py` (timeout contract reuse)

## Steps
1. Add lock-protected helpers for `SANDBOX` and `SERVICE_URL`.
2. Add structured readiness timeout context + diagnostics helpers.
3. Update sync/async startup paths to retry once on timeout and fail deterministically on second timeout.
4. Ensure prewarm readiness failures are marked and recycled instead of silently reused.
5. Ensure termination helper clears both sandbox and URL state.

## Done When
- Startup readiness timeout path logs bounded diagnostics and retries once.
- Second timeout fails deterministically with stable error message.
- No auth fallback behavior is introduced.

## Rollback
- Revert startup retry helpers and return to prior startup flow in `modal_backend/main.py`.
- Re-run runtime and auth tests to confirm baseline behavior.
