---
task_id: 04
plan_id: PLAN_modal-advanced-features
plan_file: ../PLAN_modal-advanced-features.md
title: Autoscaling and concurrency controls
phase: Phase 2 - Performance & Scale
---

## Objective

Tune autoscaling and per-container concurrency to reduce cold starts and handle bursts.

## Scope

- Add `min_containers`, `buffer_containers`, `max_containers`, and `scaledown_window` to key functions.
- Add `@modal.concurrent(...)` for I/O-heavy endpoints where safe.
- Add resource limit tuning (CPU/memory limit tuples and ephemeral disk) and expose via `Settings`.
- Ensure new settings are configurable via `Settings`.

## Files

- `modal_backend/main.py`
- `modal_backend/settings/settings.py`

## Acceptance Criteria

- Scaling knobs are configurable and documented.
- Endpoints remain stable under concurrent requests in dev tests.
