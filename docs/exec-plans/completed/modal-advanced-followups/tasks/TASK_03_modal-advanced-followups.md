---
task_id: 03
plan_id: PLAN_modal-advanced-followups
plan_file: ../PLAN_modal-advanced-followups.md
title: Configure autoscaling and concurrency defaults
phase: Phase 2 - Performance & Scale
---

## Objective

Set conservative default values for autoscaling and per-container concurrency, plus guidance for tuning.

## Scope

- Choose baseline defaults for `min_containers`, `buffer_containers`, and `concurrent_*` suited for dev/prod starter usage.
- Update `modal_backend/settings/settings.py` or `.env` with defaults.
- Update `docs/references/configuration.md` and `README.md` with tuning guidance and cost notes.

## Files

- `.env`
- `modal_backend/settings/settings.py`
- `README.md`
- `docs/references/configuration.md`

## Acceptance Criteria

- Autoscaling and concurrency defaults are set and documented.
- Docs explain how to disable or tune defaults for cost/latency trade-offs.
