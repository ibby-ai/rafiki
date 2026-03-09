---
task_id: 02
plan_id: PLAN_modal-sdk-1-3-5-upgrade
plan_file: ../PLAN_modal-sdk-1-3-5-upgrade.md
title: Remove async Modal drift and adopt deterministic teardown support
phase: Phase 2 - Runtime and Test Updates
---

## Summary
Patch async Modal usage in the runtime and update tests to match the new call shapes, while adopting `terminate(wait=True)` only where deterministic teardown materially helps.

## Scope
- `modal_backend/main.py`
- `tests/test_sandbox_auth_header.py`
- `tests/test_query_proxy_error_normalization.py`

## Steps
1. Convert async request/startup Modal I/O to `.aio` where the SDK performs remote work.
2. Keep `Image.from_id` sync and avoid `detach()`.
3. Add a narrow compatibility helper for `terminate(wait=True)` and use it only on explicit teardown paths.
4. Update or add tests so async fakes mirror production Modal method shapes.
5. Add a warnings-focused regression that fails if async interface drift remains.

## Done When
- Async request/startup paths no longer use blocking Modal I/O.
- Explicit teardown paths use deterministic termination when supported.
- Tests cover the new async method shapes and warning-sensitive behavior.

## Rollback
- Revert the runtime/test edits in this task.
- Re-run targeted pytest to confirm the old behavior is restored.
