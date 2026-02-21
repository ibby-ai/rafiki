---
task_id: 07
plan_id: PLAN_modal-advanced-features
plan_file: ../PLAN_modal-advanced-features.md
title: Class-based lifecycle hooks
phase: Phase 4 - Resilience & Startup Cost
---

## Objective

Use Modal class lifecycle hooks to reduce cold-start overhead and manage resources deterministically.

## Scope

- Convert the agent runner to `@app.cls`.
- Enable `enable_memory_snapshot=True` and move heavy initialization into `@modal.enter(snap=True)`.
- Add a lightweight `@modal.enter(snap=False)` for post-restore setup.
- Move cleanup into `@exit` and validate behavior with the existing request flow.

## Files

- `modal_backend/main.py`
- `modal_backend/agent_runtime/loop.py` (if needed)

## Acceptance Criteria

- Cold-start time is reduced or stable (memory snapshots enabled).
- Resource initialization and cleanup are centralized.
