---
task_id: 02
plan_id: PLAN_modal-advanced-features
plan_file: ../PLAN_modal-advanced-features.md
title: Volume commit/reload persistence updates
phase: Phase 1 - Persistence
---

## Objective

Persist sandbox writes without terminating the background sandbox.

## Scope

- Add explicit `Volume.commit()` after agent runs that write artifacts.
- Add `Volume.reload()` or `sb.reload_volumes()` in read paths to ensure fresh state.
- Consider optional periodic background commits for long-running sessions.
- Upgrade persistent volume to v2 where safe for improved concurrency.
- Update `terminate_service_sandbox()` messaging to clarify it is no longer the only flush mechanism.

## Files

- `modal_backend/main.py`
- `modal_backend/api/controller.py`
- `modal_backend/sandbox_runtime/` (if helper utilities are added)
- `README.md`

## Acceptance Criteria

- Writes in `/data` persist without forcing sandbox termination.
- Documentation reflects the new persistence path.
