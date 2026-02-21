---
task_id: 01
plan_id: PLAN_phase-3-cloudflare-first
plan_file: ../PLAN_phase-3-cloudflare-first.md
title: Remove Modal queue state and session_key mapping; mark gateway internal-only
phase: Phase 1 - Modal Backend Cleanup
---

## Steps
- Update `modal_backend/api/controller.py` to remove session_key mapping and prompt queue drain logic.
- Remove Modal prompt queue state and helpers from `modal_backend/jobs.py`.
- Update `modal_backend/main.py` to remove/410 queue endpoints and adjust internal-only gateway docs.
- Mark prompt queue settings as deprecated in `modal_backend/settings/settings.py` or remove if unused.
- Ensure internal auth middleware remains enforced on all non-health endpoints.
