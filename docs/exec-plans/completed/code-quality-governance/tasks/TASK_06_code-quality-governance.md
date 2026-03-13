---
task_id: 06
plan_id: PLAN_code-quality-governance
plan_file: ../PLAN_code-quality-governance.md
title: Resolve or explicitly defer Oracle findings
phase: Phase 6 - Oracle Follow-up
---

## Goal
Remediate the must-fix Oracle findings for the code-quality-governance rollout
and document any intentional deferrals with owner and rationale before plan
closure.

## Status
Implementation complete as of 2026-03-13 14:32 ACDT: the Oracle must-fix set is
now implemented. `/jobs/**` ownership fails closed on missing identity,
`docs/references/api-usage.md` matches the intended Worker public surface,
Worker CORS preflight allows `PATCH`, waiver validation binds suppressions to
waiver `scope` + `rule`, and live boundary-config integrity coverage now exists
for both `.importlinter` and `edge-control-plane/dependency-cruiser.cjs`.

Post-Oracle deployed verification on 2026-03-13 found and resolved three
operator/runtime issues before the public proof could complete:

1. The canonical public Worker was stale, so this wave includes a repair deploy
   via `npm --prefix edge-control-plane run deploy` (Worker version
   `0cfeea3e-e192-4397-896b-3734c84a9b9c`).
2. The production Modal HTTP app had stopped and required
   `uv run modal deploy -m modal_backend.deploy`.
3. The deployed Worker `SESSION_SIGNING_SECRET` had drifted from the canonical
   local helper source, so the public secret was realigned with
   `wrangler secret put SESSION_SIGNING_SECRET`.

The authenticated public Worker matrix is now complete as of 2026-03-13
15:14 ACDT. Public `/query`, `/query_stream`, queue/state/messages, `GET` +
`POST /session/{id}/stop`, schedule create/list/get/patch, `/submit`,
`/jobs/{id}`, `/jobs/{id}/artifacts`, and `/events` all passed through the
deployed Worker after the repairs above.

This proof also caught one real Oracle follow-up regression: the Modal
`submit_job()` path dropped `session_id` during enqueue, which caused Worker
runtime validation on `/jobs/**` to fail with deterministic `502` responses.
That regression is fixed in `modal_backend/main.py` and `modal_backend/jobs.py`,
covered by new tests in `tests/test_jobs_enqueue.py`, and revalidated live on
the public Worker after redeploying Modal.

Current closure note: the rollout is now ready for closure. The explicit
session-token constant-time and proof git-SHA deferrals remain open, the
jobs-proxy non-JSON error content-type issue remains a non-blocking residual
risk, and artifact download was not exercised in the public proof because the
live artifact manifest for the proof job contained zero files.
