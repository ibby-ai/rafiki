# PLAN_nextjs-agent-scheduling

## Purpose / Big Picture

Enable a Next.js app to schedule background agent jobs that run asynchronously in Modal sandboxes, return immediately with a job id, and later report completion details plus any created files. The user-visible result is a reliable "schedule, poll, and retrieve artifacts" workflow that keeps the app responsive while agents run in the background.

## Suprises & Discoveries

- Observation: None yet (plan initialization).
- Evidence: N/A.

## Decision Log

- Decision: Use a single Modal App deployed in a production environment and model end users as app-level tenants; use sandbox-per-job or per-session for isolation.
- Rationale: Matches SaaS usage for a single Next.js product without per-tenant app sprawl.
- Date/Author: 2026-01-06 / Codex

- Decision: Keep Modal Queue + Dict for v1 scheduling and persistence, with hooks to swap in external storage later.
- Rationale: The primitives already exist in the codebase and are sufficient for initial background scheduling.
- Date/Author: 2026-01-06 / Codex

- Decision: Persist job artifacts in a Modal Volume under per-job directories with an explicit manifest.
- Rationale: Provides durable file outputs that can be listed and downloaded.
- Date/Author: 2026-01-06 / Codex

- Decision: Support polling plus optional webhook callbacks for job completion.
- Rationale: Polling is simple for Next.js clients, while webhooks enable server-driven updates.
- Date/Author: 2026-01-06 / Codex

## Outcomes & Retrospective

- Pending implementation.

## Context and Orientation

- The project uses `modal_backend/main.py` to define the Modal app, HTTP endpoints, background sandbox lifecycle, and queue processing.
- `modal_backend/jobs.py` implements async job scheduling using Modal Queue + Dict, but currently tracks only basic fields.
- `modal_backend/api/controller.py` runs the Claude Agent SDK in a long-lived sandbox and supports volume commit/reload.
- `modal_backend/models/jobs.py` provides minimal submit/status schemas without tenant metadata or artifact manifests.

Key files:
- `modal_backend/main.py` — Modal app, HTTP endpoints, sandbox lifecycle, job processor.
- `modal_backend/jobs.py` — queue + dict state for async jobs.
- `modal_backend/api/controller.py` — sandbox FastAPI, agent execution, volume commit/reload.
- `modal_backend/models/jobs.py` — job request/response schemas.
- `modal_backend/settings/settings.py` — queue, volume, and security settings.
- `docs/references/api-usage.md`, `docs/design-docs/architecture-overview.md`, `docs/design-docs/controllers-background-service.md` — API and architecture docs.

## Plan of Work

1. Define the scheduling API contract for Next.js clients, including tenant/user identifiers, optional schedule time, optional webhook callback, and artifact manifest fields; update schemas accordingly.
2. Extend job persistence to store new metadata, timestamps, and artifact manifests, and implement scheduling semantics (immediate vs scheduled) with cancellation and retries tracked in the job record.
3. Implement HTTP endpoints for job submission, status, cancellation, and artifact listing/downloading; return stable payloads for polling clients.
4. Update the execution pipeline to create per-job workspaces, run the agent in the sandbox, capture created files, commit the Volume, and persist structured results.
5. Add webhook notifications for job completion/failure with signed payloads and retries; store delivery attempts in job metadata.
6. Add metering and observability fields (duration, attempts, model/tool usage if available) and align logs with request/job IDs.
7. Update docs and tests to cover scheduling, polling, artifacts, and Next.js integration flows.

## Concrete Steps

Each step is tracked in `docs/exec-plans/completed/nextjs-agent-scheduling/tasks/` and linked in the Progress section below.

## Progress

[x] (TASK_01_nextjs-agent-scheduling.md) (2026-01-06 00:00) Define scheduling API contract and schemas.

[x] (TASK_02_nextjs-agent-scheduling.md) (2026-01-06 00:00) Extend job persistence and scheduling semantics.

[x] (TASK_03_nextjs-agent-scheduling.md) (2026-01-06 00:00) Implement HTTP endpoints for jobs and artifacts.

[x] (TASK_04_nextjs-agent-scheduling.md) (2026-01-06 00:00) Update execution pipeline and artifact capture.

[x] (TASK_05_nextjs-agent-scheduling.md) (2026-01-06 00:00) Add webhook notifications and retries.

[x] (TASK_06_nextjs-agent-scheduling.md) (2026-01-06 00:00) Add metering and observability fields.

[x] (TASK_07_nextjs-agent-scheduling.md) (2026-01-06 00:00) Docs and tests for Next.js integration.

## Testing Approach

- `uv run pytest` for schema and job lifecycle tests.
- `modal serve -m modal_backend.main` and exercise `/jobs` endpoints via curl or a small Next.js client.
- Submit a job, poll status, and verify artifacts are listed and downloadable.
- Verify webhook delivery using a local test endpoint and confirm retry behavior.

## Constraints & Considerations

- Modal web endpoints have a hard request timeout (150s), so scheduling must return quickly and use background processing.
- Modal Volumes are eventually consistent; commit/reload behavior must be handled to expose new artifacts.
- Queue processing should enforce per-run limits and timeouts to avoid runaway runtimes.
- Avoid breaking existing synchronous `/query` and `/query_stream` paths while adding scheduling features.
- All credentials must stay in Modal secrets; no API keys in code.
