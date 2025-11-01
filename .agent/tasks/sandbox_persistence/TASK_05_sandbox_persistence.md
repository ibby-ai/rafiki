---
task_id: 05
plan_id: PLAN_sandbox_persistence
plan_file: ../../plans/sandbox_persistence/PLAN_sandbox_persistence.md
title: Implement connect-token auth (controller + service)
phase: Phase 4 - Security & Auth
---

### Changes
- `main.py`: if `ENFORCE_CONNECT_TOKEN`, call `sb.create_connect_token(user_metadata=...)` and forward `Authorization: Bearer <token>`.
- `runner_service.py`: when enabled, require `X-Verified-User-Data` header; return 401 if missing.

### Acceptance Criteria
- Requests without token are rejected when enforcement is on; accepted otherwise.
