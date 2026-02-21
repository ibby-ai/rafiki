---
task_id: 03
plan_id: PLAN_multi_agent_support
plan_file: ../PLAN_multi_agent_support.md
title: Refactor controller + agent loop to provider API
phase: Phase 3 - Runtime Integration
---

## Scope
- Replace direct Claude SDK usage in `controller.py` and `agents/loop.py`.
- Route option building, client creation, and message serialization through provider.
- Preserve existing request/response behavior and error handling.

## Deliverables
- Provider-backed execution path for sync + streaming flows.

## Acceptance
- Claude default behavior unchanged (output structure, fields, and streaming).
