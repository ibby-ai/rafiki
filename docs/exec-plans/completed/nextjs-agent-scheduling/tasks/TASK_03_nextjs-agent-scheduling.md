---
task_id: 03
plan_id: PLAN_nextjs-agent-scheduling
plan_file: ../PLAN_nextjs-agent-scheduling.md
title: Implement HTTP endpoints for jobs and artifacts
phase: Phase 2 - HTTP API
---

## Objective

Implement the Next.js-friendly HTTP endpoints for job submission, status, cancellation, and artifact listing/downloading.

## Scope

- Update `modal_backend/main.py` endpoints (`/jobs`, `/jobs/{id}`, `/jobs/{id}` delete) to accept new fields and return expanded responses.
- Add artifact listing and download endpoints (e.g., `/jobs/{id}/artifacts` and `/jobs/{id}/artifacts/{path}`).
- Validate job ids and return structured 404s for missing jobs.
- Wire endpoints to the updated schemas.

## Files

- `modal_backend/main.py`
- `modal_backend/models/jobs.py`
- `modal_backend/models/responses.py` (if artifact responses live here)

## Acceptance Criteria

- Job submission returns a job id immediately without running the agent inline.
- Status and artifact endpoints return structured payloads usable by a Next.js UI.
- Invalid or missing job ids return consistent error responses.
