---
task_id: 02
plan_id: PLAN_multi_agent_support
plan_file: ../PLAN_multi_agent_support.md
title: Implement provider registry and settings wiring
phase: Phase 2 - Provider Scaffold
---

## Scope
- Add provider base protocols and registry (`modal_backend/llm_providers/`).
- Extend `Settings` with `agent_provider` and image override fields.
- Update secrets resolution to be provider-aware.

## Deliverables
- New provider module scaffolding.
- Settings changes documented and wired.

## Acceptance
- Default provider resolves to Claude with no behavior changes.
