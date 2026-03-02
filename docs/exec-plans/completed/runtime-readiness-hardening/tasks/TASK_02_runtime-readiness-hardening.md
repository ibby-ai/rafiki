---
task_id: 02
plan_id: PLAN_runtime-readiness-hardening
plan_file: ../PLAN_runtime-readiness-hardening.md
title: Add retry and state-reset regression tests
phase: Phase 2 - Verification
---

## Summary
Add targeted tests for guarded state clearing and one-retry readiness behavior.

## Scope
- `tests/test_sandbox_auth_header.py`
- `tests/test_settings_openai.py`

## Steps
1. Add tests for expected-id guarded global sandbox state clearing.
2. Add sync startup test for first-timeout retry and second-timeout deterministic failure.
3. Add async startup test for first-timeout retry success.
4. Add settings test validating positive `service_timeout` contract.

## Done When
- New tests fail against non-retrying/non-guarded behavior.
- Updated tests pass with runtime hardening patch.
- Existing strict scoped-auth tests remain green.

## Rollback
- Remove newly added readiness tests.
- Revert to previous settings validation contract if necessary.
