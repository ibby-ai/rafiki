---
task_id: 09
plan_id: PLAN_sandbox_persistence
plan_file: ../../plans/sandbox_persistence/PLAN_sandbox_persistence.md
title: Add security toggles for egress and resource caps
phase: Phase 8 - Security & Limits
---

### Changes
- Expose config flags for `block_network` and `cidr_allowlist` in `Sandbox.create` (default off to keep agent usable).
- Document CPU/memory defaults in constants; allow tuning per environment.

### Acceptance Criteria
- Clear toggles exist; turning them on/off reflects expected behavior.
