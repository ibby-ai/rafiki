---
task_id: 05
plan_id: PLAN_modal-advanced-features
plan_file: ../PLAN_modal-advanced-features.md
title: Queue ingestion and worker path
phase: Phase 3 - Throughput
---

## Objective

Decouple request ingestion from agent execution using `modal.Queue`.

## Scope

- Create a named `modal.Queue` for agent jobs.
- Add a consumer function that pulls tasks and runs the agent.
- Add `/submit` to enqueue jobs and `/jobs/{job_id}` GET/DELETE for status and cancellation.
- Add an optional API mode to enqueue instead of synchronous execution.
- Persist job status/results where appropriate (e.g., `modal.Dict`).

## Files

- `modal_backend/main.py`
- `modal_backend/api/controller.py`
- `modal_backend/jobs.py` (new)
- `modal_backend/models/` (new job schemas)
- `README.md`

## Acceptance Criteria

- HTTP endpoints can enqueue work and return a job id.
- Worker consumes jobs and records results in a durable store.
- Job status endpoint returns completed/failed states and cancellation results.
