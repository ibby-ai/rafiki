---
task_id: 02
plan_id: PLAN_prod_readiness_claude_agent_sdk
plan_file: ../PLAN_prod_readiness_claude_agent_sdk.md
title: Add explicit ephemeral disk allocation
phase: Phase 1 - Runtime Safety
---

## Summary

Expose explicit ephemeral disk sizing (~5GiB) and apply it to Modal functions and, if supported, to Modal sandboxes to align with SDK hosting guidance.

## Scope

- `modal_backend/settings/settings.py`
- `modal_backend/main.py`
- Documentation updates (README or docs/references/configuration.md)

## Steps

1. Set `sandbox_ephemeral_disk` default to 5120 MiB (~5 GiB) and keep env override.
2. Apply the disk setting to function resource kwargs (used for `@app.function`/`@app.cls`).
3. Add missing `ephemeral_disk` to `_sandbox_resource_kwargs()` so sandboxes actually receive the allocation.
4. Document the default disk recommendation and how to override.

## Acceptance Criteria

- Ephemeral disk size is configurable via settings/env.
- Functions use the configured disk allocation.
- Sandbox behavior is updated or documented based on Modal SDK support.
- Documentation explains the default and recommended size.
