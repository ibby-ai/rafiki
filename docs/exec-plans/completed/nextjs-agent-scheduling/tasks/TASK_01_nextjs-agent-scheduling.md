---
task_id: 01
plan_id: PLAN_nextjs-agent-scheduling
plan_file: ../PLAN_nextjs-agent-scheduling.md
title: Define scheduling API contract and schemas
phase: Phase 1 - API Contract
---

## Objective

Define the request/response schema for Next.js scheduling, including tenant/user metadata, optional schedule time, webhook callbacks, and artifact manifest fields.

## Scope

- Update `JobSubmitRequest`/`JobSubmitResponse` and `JobStatusResponse` in `modal_backend/models/jobs.py`.
- Add artifact manifest schema (new file or extend `modal_backend/models/responses.py`).
- Add optional webhook metadata fields (url, signature, headers/secret reference).
- Update `docs/references/api-usage.md` with new payloads and examples.

## Files

- `modal_backend/models/jobs.py`
- `modal_backend/models/responses.py` (or a new schema module)
- `docs/references/api-usage.md`

## Acceptance Criteria

- Schemas include tenant/user identifiers, `schedule_at`, webhook metadata, and artifact manifest fields.
- API docs show the new submit and status payloads, including sample artifacts.
