---
task_id: 08
plan_id: PLAN_sandbox_persistence
plan_file: ../../plans/sandbox_persistence/PLAN_sandbox_persistence.md
title: Improve health checks, retries, and exception handling
phase: Phase 7 - Robustness
---

### Changes
- Ensure `_wait_for_service` covers timeouts/HTTP errors with backoff.
- Add clear error responses in controller for upstream timeouts/HTTP errors (already present; validate behavior).
- Catch Modal exceptions (e.g., `FunctionTimeoutError`) in scheduled job paths.

### Acceptance Criteria
- Endpoint returns 5xx/4xx with actionable messages; logs include context.
