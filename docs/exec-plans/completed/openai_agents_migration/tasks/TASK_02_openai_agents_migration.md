---
task_id: 02
plan_id: PLAN_openai_agents_migration
plan_file: ../PLAN_openai_agents_migration.md
title: Preserve HTTP/SSE compatibility with OpenAI run mapping
phase: Phase 2 - Controller and Serialization
---

## Objective

Implement OpenAI `Runner.run_streamed` execution while preserving existing response and SSE contracts.

## Scope

- Update controller execution loop and active run tracking.
- Implement graceful/immediate cancellation with OpenAI cancel modes.
- Ensure message/event mapping emits unchanged external event names.
- Preserve final summary/result envelope fields.

## Deliverables

- Updated `modal_backend/api/controller.py`.
- Updated `modal_backend/api/serialization.py`.
- Compatibility helper coverage for tool allowlist and message adaptation.
