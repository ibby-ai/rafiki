---
task_id: 01
plan_id: PLAN_openai_agents_migration
plan_file: ../PLAN_openai_agents_migration.md
title: Cut over runtime dependencies and agent execution core
phase: Phase 1 - Runtime Migration
---

## Objective

Replace Claude SDK runtime dependencies and execution primitives with OpenAI Agents SDK primitives.

## Scope

- Update dependency declarations and typing overrides.
- Replace Claude settings/secrets with OpenAI equivalents.
- Migrate base executor and handoff construction.
- Ensure session creation/forking uses `SQLiteSession` correctly.

## Deliverables

- Updated `pyproject.toml` for OpenAI SDK + LangSmith integration.
- Updated `modal_backend/settings/settings.py` and `modal_backend/tracing.py`.
- Updated `modal_backend/agent_runtime/base.py` and related exports.
