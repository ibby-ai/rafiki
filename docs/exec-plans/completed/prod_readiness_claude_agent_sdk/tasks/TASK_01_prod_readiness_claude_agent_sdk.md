---
task_id: 01
plan_id: PLAN_prod_readiness_claude_agent_sdk
plan_file: ../PLAN_prod_readiness_claude_agent_sdk.md
title: Add max_turns configuration for agent runs
phase: Phase 1 - Runtime Safety
---

## Summary

Add a configurable `max_turns` limit for all Claude Agent SDK runs to prevent runaway loops, and thread it through both CLI and HTTP execution paths.

## Scope

- `modal_backend/settings/settings.py`
- `modal_backend/agent_runtime/loop.py`
- `modal_backend/api/controller.py`
- Optional: `docs/references/configuration.md` and README updates

## Steps

1. Introduce a new settings field (e.g., `agent_max_turns`) with a default of 50 and env override.
2. Update `build_agent_options` in `modal_backend/agent_runtime/loop.py` to pass `max_turns` into `ClaudeAgentOptions`.
3. Update `_options()` in `modal_backend/api/controller.py` to pass the same value.
4. Document the setting and note how it prevents runaway loops.

## Acceptance Criteria

- All entry points (one-off and service) construct `ClaudeAgentOptions` with a `max_turns` value.
- The setting is configurable via environment variables.
- Docs mention the new setting and its purpose.
