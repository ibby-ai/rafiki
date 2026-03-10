---
task_id: 01
plan_id: PLAN_controller-rollout-cutover-safety
plan_file: ../PLAN_controller-rollout-cutover-safety.md
title: Implement authoritative pointer-based rollout and verify cutover behavior
phase: Phase 1 - Rollout Control Plane + Validation
---

## Objective
Implement and validate production-safe A->B controller rollout with private warmup gates, generation-aware routing refresh, explicit draining, and rollback-safe promotion order.

## Checklist
- [x] Add dedicated controller rollout shared state model (active pointer, service registry, inflight/session route tracking).
- [x] Route request entry via shared active pointer generation rather than fixed-name reuse.
- [x] Add private candidate preparation and readiness gates (`/health_check`, scoped secret, synthetic direct `/query`).
- [x] Promote only after readiness checks; mark previous active controller as `draining`.
- [x] Track inflight controller work and terminate draining controller after quiesce/timeout.
- [x] Redact scoped secret material from rollout observability endpoints.
- [x] Guard pointer promotion with generation-transition ownership so stale writers fail closed.
- [x] Admit fresh requests only at lease start so stale prewarm claims reroute/fail closed.
- [x] Record fresh live proof that first public Worker `/query` after controlled cutover returns `200` on first try.
- [x] Prove stale recovered-service and empty-pointer bootstrap behavior on the real `get_or_start_background_sandbox()` path.
- [x] Add concrete overlap replay and deterministic spawned-vs-inline drain parity evidence.

## Evidence
- `uv run python -m pytest tests/test_controller_rollout.py` (`33 passed`)
- `uv run python -m pytest tests/test_sandbox_auth_header.py -k 'prewarm or stop_session or get_or_start_background_sandbox'` (`12 passed`)
- `uv run python -m pytest tests/test_internal_auth_middleware.py tests/test_settings_openai.py` (`27 passed`)
- `npm --prefix edge-control-plane run check`
- `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit`
- `npm --prefix edge-control-plane run test:integration` (`3 passed`)
- Generated proof artifact: `docs/generated/controller-rollout-cutover-safety-proof-2026-03-09.json`
- Live bootstrap proofs:
  - stale recovered-service replay marked terminated sandbox `sb-CM9UmFjHr7LMoi5kpCijL3` failed at `attach_active_pointer` and bootstrapped clean controller `sb-DnzRPHPm3OSRmSs2vYptOZ`
  - empty-pointer replay promoted generation `1` controller `sb-SSgJAg9fZqBTFQFAjosr6t` from `before_active: null`
- Live cutover proof:
  - generation `1` active before rollout on `sb-SSgJAg9fZqBTFQFAjosr6t`
  - generation `2` active after rollout on `sb-IiLZoEm7isfh1XPElHMnX2` with `last_verified_readiness_at=1773047392`
  - generation `1` terminated with `drain_timeout_reached=false`
  - first public Worker `/query` after cutover returned `HTTP 200` with `e2e-ok`
  - `/query_stream`, queue enqueue, and state checks passed after cutover
- Concrete local harnesses:
  - threaded `_persist_active_controller_pointer` overlap replay committed `sb-two` and rejected `sb-one` at the promotion commit slot
  - spawned-vs-inline drain parity replay matched terminal service state and rollback-target clearing

## Rollback
- Keep active-pointer promotion atomic and retain rollback target metadata until old controller drain ends.
- Promotion commit must fail closed if rollout-lock ownership is lost or the active generation already advanced.
- Fresh-request admission must fail closed if prewarm or cached route state no longer matches the active pointer generation.
- If candidate verification fails, keep pointer on current active and terminate failed candidate.

## Proof Limits
- `modal serve` still cannot hydrate `drain_controller_sandbox.spawn()` directly, so local live proof remains bounded to acceptable inline drain fallback plus deterministic parity harness evidence.
- Bootstrap still fails closed after two readiness timeouts; a deliberately tightened timeout can reproduce that boundary without indicating a regression when the default-timeout replay succeeds.
