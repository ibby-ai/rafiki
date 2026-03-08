---
task_id: 05
plan_id: PLAN_openai-agents-hardening
plan_file: ../PLAN_openai-agents-hardening.md
title: Add session compaction controls, eval-style regressions, and runbook updates
phase: Phase 4 - P1/P2 continuation
---

- Add deterministic SQLite session history compaction controls:
  - `openai_session_max_items`
  - `openai_session_compaction_keep_items`
- Apply compaction during session acquisition for resume and fork target flows.
- Extend runtime coverage for:
  - handoff correctness across model updates
  - tool-call sequencing expectations
  - cancellation-adjacent behavior with trace metadata
- Surface optional `openai_trace_id` in result summaries/SSE done payload when available.
- Update operational docs and controller design reference with troubleshooting guidance:
  - missing traces
  - tool policy denials
  - memory growth/compaction tuning
