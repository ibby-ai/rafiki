---
task_id: 03
plan_id: PLAN_modal-advanced-features
plan_file: ../PLAN_modal-advanced-features.md
title: Proxy auth enablement and docs
phase: Phase 1 - Security
---

## Objective

Require Modal Proxy Auth on public HTTP endpoints to reduce unauthorized access.

## Scope

- Apply `requires_proxy_auth=True` to `@modal.asgi_app()`.
- Add config flag to toggle proxy auth if needed for local/dev.
- Update `README.md` with instructions for creating and using Proxy Auth tokens.

## Files

- `modal_backend/main.py`
- `modal_backend/settings/settings.py`
- `README.md`

## Acceptance Criteria

- Public HTTP endpoints reject unauthenticated requests when enabled.
- Documentation explains how to supply Proxy Auth headers.
