---
task_id: 06
plan_id: PLAN_nextjs-agent-scheduling
plan_file: ../PLAN_nextjs-agent-scheduling.md
title: Add metering and observability fields
phase: Phase 3 - Metering
---

## Objective

Add metering and observability fields to job records and logs, including durations, attempts, and model/tool usage when available.

## Scope

- Extend job metadata to include start/end timestamps and duration.
- Capture attempt counts, sandbox id, and optional token/tool usage if returned by the agent.
- Ensure logs include job id and request id for tracing.

## Files

- `modal_backend/jobs.py`
- `modal_backend/api/serialization.py`
- `modal_backend/platform_services/logging.py` (if needed)

## Acceptance Criteria

- Job status includes timing and attempt metrics.
- Logs correlate job id, request id, and execution outcomes.
