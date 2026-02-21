---
task_id: 07
plan_id: PLAN_nextjs-agent-scheduling
plan_file: ../PLAN_nextjs-agent-scheduling.md
title: Docs and tests for Next.js integration
phase: Phase 4 - Docs & QA
---

## Objective

Update documentation and add tests to cover scheduling, polling, artifact retrieval, and webhook flows for Next.js integration.

## Scope

- Update `docs/references/api-usage.md`, `docs/design-docs/architecture-overview.md`, and `docs/design-docs/controllers-background-service.md` with scheduling and artifacts.
- Add an example Next.js integration flow (submit, poll, download).
- Add or update tests under `tests/` to cover job submission, status, and artifact endpoints.

## Files

- `docs/references/api-usage.md`
- `docs/design-docs/architecture-overview.md`
- `docs/design-docs/controllers-background-service.md`
- `README.md` (if the API surface changes)
- `tests/`

## Acceptance Criteria

- Docs describe the scheduling workflow end-to-end.
- Tests cover the new job status and artifact behaviors.
