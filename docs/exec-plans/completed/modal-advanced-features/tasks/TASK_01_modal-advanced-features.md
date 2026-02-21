---
task_id: 01
plan_id: PLAN_modal-advanced-features
plan_file: ../PLAN_modal-advanced-features.md
title: Baseline audit and settings alignment
phase: Phase 0 - Baseline
---

## Objective

Establish a safe baseline for new Modal features and add configuration toggles where needed.

## Scope

- Review `modal_backend/main.py` for current Modal settings (timeouts, resources, lifecycle).
- Identify insertion points for autoscaling, concurrency, retries, proxy auth, memory snapshots, queue config, volume version, and resource limits.
- Extend `modal_backend/settings/settings.py` for new settings (with safe defaults).

## Files

- `modal_backend/main.py`
- `modal_backend/settings/settings.py`
- `README.md` (if config flags are user-facing)

## Acceptance Criteria

- New settings are defined and wired to existing code without behavior change by default.
- No runtime-only changes are introduced yet.
