---
task_id: 05
plan_id: PLAN_nextjs-agent-scheduling
plan_file: ../PLAN_nextjs-agent-scheduling.md
title: Add webhook notifications and retries
phase: Phase 3 - Notifications
---

## Objective

Add webhook callbacks for job completion and failure, with signing, retries, and delivery tracking for Next.js server-side updates.

## Scope

- Introduce a webhook sender utility with retry/backoff behavior.
- Add settings for webhook signing secret and default callback policy.
- Record webhook delivery attempts and status in job metadata.
- Ensure webhook calls happen after job completion/failure without blocking queue processing.

## Files

- `modal_backend/platform_services/` (new webhook helper)
- `modal_backend/settings/settings.py`
- `modal_backend/main.py`
- `modal_backend/jobs.py`

## Acceptance Criteria

- Jobs with a webhook url trigger signed callbacks on completion/failure.
- Delivery attempts and failures are recorded in job metadata.
- Webhook failures do not crash the queue processor.
