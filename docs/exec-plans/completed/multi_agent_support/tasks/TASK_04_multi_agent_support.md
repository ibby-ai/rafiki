---
task_id: 04
plan_id: PLAN_multi_agent_support
plan_file: ../PLAN_multi_agent_support.md
title: Tool adapter and response normalization
phase: Phase 4 - Tools + Serialization
---

## Scope
- Move Claude-specific serialization into provider implementation.
- Introduce provider-agnostic tool definitions and adapters.
- Update response schema to include `provider` + `provider_payload`.

## Deliverables
- Provider-based serialization path.
- Stable response schema with optional provider metadata.

## Acceptance
- Existing clients still parse responses without changes.
- Non-Claude providers can return additional metadata safely.
