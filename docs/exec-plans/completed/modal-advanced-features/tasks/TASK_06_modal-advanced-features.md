---
task_id: 06
plan_id: PLAN_modal-advanced-features
plan_file: ../PLAN_modal-advanced-features.md
title: Retry policies for key operations
phase: Phase 4 - Resilience
---

## Objective

Add Modal retry policies for transient failures.

## Scope

- Apply `modal.Retries` to sandbox creation, snapshot, cleanup, termination, and queue consumer functions.
- Ensure idempotence or safe guards where retries can repeat work.

## Files

- `modal_backend/main.py`
- `modal_backend/api/controller.py`

## Acceptance Criteria

- Transient errors are retried with controlled backoff.
- No duplicate side effects for retried operations.
