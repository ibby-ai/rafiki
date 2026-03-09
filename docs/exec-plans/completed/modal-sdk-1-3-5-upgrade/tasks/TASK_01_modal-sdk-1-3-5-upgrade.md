---
task_id: 01
plan_id: PLAN_modal-sdk-1-3-5-upgrade
plan_file: ../PLAN_modal-sdk-1-3-5-upgrade.md
title: Lock the repo to Modal 1.3.5 and audit affected runtime call sites
phase: Phase 1 - Dependency and Runtime Audit
---

## Summary
Upgrade the project dependency/lock state to Modal 1.3.5 and align the implementation plan with the actual async/runtime call sites in Rafiki.

## Scope
- `pyproject.toml`
- `uv.lock`
- `modal_backend/main.py`

## Steps
1. Raise the Modal dependency floor to `>=1.3.5`.
2. Refresh the lockfile and local environment to the same version.
3. Confirm the exact async Modal call sites that must move to `.aio`.
4. Confirm which new SDK feature to adopt and which to defer.

## Done When
- `pyproject.toml` and `uv.lock` both resolve to Modal 1.3.5.
- The upgrade scope is limited to proven runtime-impacting call sites.
- The chosen feature adoption is documented in the plan.

## Rollback
- Restore the previous Modal dependency floor and lockfile entry.
- Re-run version verification before resuming work.
