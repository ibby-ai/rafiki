---
task_id: 04
plan_id: PLAN_nextjs-agent-scheduling
plan_file: ../PLAN_nextjs-agent-scheduling.md
title: Update execution pipeline and artifact capture
phase: Phase 2 - Execution
---

## Objective

Update the job execution pipeline to create per-job workspaces, run the agent in the sandbox, capture created files, commit the Volume, and persist structured results.

## Scope

- Create a per-job workspace path under the agent filesystem root (e.g., `/data/jobs/{job_id}`).
- Ensure the agent runs with that workspace as its working directory.
- Collect an artifact manifest (paths, sizes, timestamps) after completion.
- Commit/reload the Volume so artifacts are visible to the HTTP API.
- Update job status with results, artifacts, and execution timestamps.

## Files

- `modal_backend/main.py`
- `modal_backend/api/controller.py`
- `modal_backend/instructions/prompts.py` (if guidance is needed for artifact paths)
- `modal_backend/sandbox_runtime/` (if utilities are needed for listing files)

## Acceptance Criteria

- Completed jobs include an artifact manifest in their status payload.
- Artifacts are persisted on the Volume and visible via the HTTP API.
- Volume commits occur without blocking synchronous endpoints.
