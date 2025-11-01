---
task_id: 10
plan_id: PLAN_sandbox_persistence
plan_file: ../../plans/sandbox_persistence/PLAN_sandbox_persistence.md
title: Testing & verification steps
phase: Phase 9 - Testing & Docs
---

### Steps
- `modal run main.py` and `modal run main.py::sandbox_controller --question "health check"` for smoke.
- `modal serve main.py`; POST to endpoint with curl; observe logs.
- Verify `/workspace` persistence by writing and re-creating sandbox.
- Enable connect-token; confirm 401 without token and success with token.
- Trigger `snapshot_service` and record image id; (optional) spin a new sandbox from snapshot in follow-up.

### Acceptance Criteria
- All critical paths pass manually; notes/screenshots attached to PR.
