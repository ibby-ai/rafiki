---
task_id: 01
plan_id: PLAN_openai-agents-hardening
plan_file: ../PLAN_openai-agents-hardening.md
title: Add trace and request validation guards
phase: Phase 1 - Request validation and correlation
---

- Add `trace_id` and question validation in `modal_backend/models/sandbox.py`.
- Resolve stable per-request trace IDs in `modal_backend/api/controller.py`.
- Ensure `trace_id` is included in query/query_stream runtime paths.
