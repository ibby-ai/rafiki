---
task_id: 06
plan_id: PLAN_sandbox_persistence
plan_file: ../../plans/sandbox_persistence/PLAN_sandbox_persistence.md
title: Add snapshot function and persist snapshot metadata
phase: Phase 5 - Snapshots & Recovery
---

### Changes
- `main.py`: add `snapshot_service` Modal function: `sb.snapshot_filesystem()`; store image id and timestamp in the dict under a `-snapshot` key.

### Acceptance Criteria
- Snapshot function returns an image id and records metadata in the registry.
