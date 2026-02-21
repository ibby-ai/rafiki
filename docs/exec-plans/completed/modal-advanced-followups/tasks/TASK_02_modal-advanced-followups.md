---
task_id: 02
plan_id: PLAN_modal-advanced-followups
plan_file: ../PLAN_modal-advanced-followups.md
title: Enable and verify volume commit interval
phase: Phase 1 - Persistence
---

## Objective

Enable `volume_commit_interval` by default and document how to verify persistence without terminating the sandbox.

## Scope

- Set a conservative default for `VOLUME_COMMIT_INTERVAL` (e.g., in `.env` and/or settings).
- Update `README.md` and `docs/references/configuration.md` with the chosen default and a short verification recipe.
- Add or update any operational notes in `docs/` about commit behavior and trade-offs.

## Files

- `.env`
- `modal_backend/settings/settings.py`
- `README.md`
- `docs/references/configuration.md`
- `docs/references/troubleshooting.md` (if needed)

## Acceptance Criteria

- `volume_commit_interval` is enabled by default for local/dev use or has a documented recommended value.
- Documentation explains how to verify commits without calling `terminate_service_sandbox`.
