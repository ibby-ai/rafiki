---
task_id: 02
plan_id: PLAN_nextjs-agent-scheduling
plan_file: ../PLAN_nextjs-agent-scheduling.md
title: Extend job persistence and scheduling semantics
phase: Phase 1 - Job State
---

## Objective

Extend job persistence to store new metadata, timestamps, and artifact manifests, and implement scheduling semantics (immediate vs scheduled) with cancellation and retries.

## Scope

- Update `modal_backend/jobs.py` to store tenant/user metadata, schedule time, webhook settings, metrics, and artifact manifest fields.
- Add helpers for scheduled jobs (e.g., defer processing until `schedule_at`).
- Ensure cancellation and retry attempt counts are preserved across runs.
- Add settings for scheduling defaults or TTL if needed.

## Files

- `modal_backend/jobs.py`
- `modal_backend/settings/settings.py`

## Acceptance Criteria

- Job records include new metadata fields and are backward compatible for existing jobs.
- Scheduled jobs are not executed before their scheduled time.
- Cancellation and attempts are tracked in job metadata.
