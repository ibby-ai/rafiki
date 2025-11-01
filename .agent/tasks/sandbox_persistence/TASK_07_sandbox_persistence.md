---
task_id: 07
plan_id: PLAN_sandbox_persistence
plan_file: ../../plans/sandbox_persistence/PLAN_sandbox_persistence.md
title: Add async sandbox creation/exec variants
phase: Phase 6 - Concurrency & Async
---

### Changes
- `main.py`: add `get_or_start_background_sandbox_aio` using `.create.aio()` and `tunnels.aio()`; keep parity with sync version.

### Acceptance Criteria
- Async path can be swapped into the endpoint without behavior change (optional).
