---
task_id: 01
plan_id: PLAN_sandbox_persistence
plan_file: ../../plans/sandbox_persistence/PLAN_sandbox_persistence.md
title: Audit current implementation and define input mapping
phase: Phase 1 - Orientation & Audit
---

### Goal
Document the current sandbox flow, images, secrets, endpoints, and identify insertion points for persistence and security.

### Steps
- Read `main.py` and list functions that create/use `modal.Sandbox`.
- Map controller → sandbox service flow (`test_endpoint` → `/query`).
- Capture current image/secrets/workdir from `utils/env_templates.py`.
- Identify where to inject: Dict registry, sandbox name/tags, volume mount, connect tokens, snapshot.
- Record any constraints (networking, secrets, 24h limits).

### Acceptance Criteria
- Short notes committed under this task file or plan Context confirming insertion points and constraints.
