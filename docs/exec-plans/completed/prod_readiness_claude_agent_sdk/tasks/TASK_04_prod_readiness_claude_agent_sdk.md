---
task_id: 04
plan_id: PLAN_prod_readiness_claude_agent_sdk
plan_file: ../PLAN_prod_readiness_claude_agent_sdk.md
title: Add optional hybrid session resumption
phase: Phase 3 - Session Management
---

## Summary

Wire up Claude Agent SDK session resumption for optional hybrid sessions, persisting session IDs in Modal storage so containers can be hydrated with prior context.

## Scope

- `modal_backend/api/controller.py`
- `modal_backend/models/*`
- `modal_backend/main.py` (if request routing or headers need changes)
- `modal_backend/settings/settings.py`
- Docs updates

## Steps

1. Extend request schemas to accept `session_id` and `fork_session` fields (with safe defaults).
2. Capture session IDs returned from the SDK init message and persist them in Modal Dict or Volume keyed by user/session.
3. Use `ClaudeAgentOptions(resume=<session_id>, fork_session=<bool>)` for resumed sessions.
4. Add safe defaults: if no session ID is provided, start a new session.
5. Return `session_id` in API responses so clients can store it.
6. Update docs and endpoint examples to show hybrid session usage.

## Acceptance Criteria

- Session IDs can be persisted and reused across requests.
- API supports optional session resumption without breaking existing clients.
- The implementation uses SDK-supported session primitives (`resume`, optional `fork_session`).
