---
task_id: 03
plan_id: PLAN_sandbox_persistence
plan_file: ../../plans/sandbox_persistence/PLAN_sandbox_persistence.md
title: Mount persistent Volume and helper utilities
phase: Phase 2 - Persistence & Discovery
---

### Edit Targets
- `main.py`: create `modal.Volume.from_name(PERSIST_VOL_NAME, create_if_missing=True)` and mount at `/workspace` on create.
- `utils/sandbox_helpers.py`: add simple helpers for volume lookups and batch uploads.

### Acceptance Criteria
- Files written to `/workspace` survive sandbox termination and recreation.
- Helper usable from future features to upload local paths.
