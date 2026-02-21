---
task_id: 03
plan_id: PLAN_openai-agents-hardening
plan_file: ../PLAN_openai-agents-hardening.md
title: Propagate metadata in stream payloads
phase: Phase 2 - Stream adapter and serialization
---

- Include `trace_id`/`session_id` on controller-generated messages.
- Include `trace_id` in SSE `error` and `done` events.
- Ensure serialization preserves and normalizes metadata fields.
