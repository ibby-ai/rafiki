---
task_id: 02
plan_id: PLAN_phase-3-cloudflare-first
plan_file: ../PLAN_phase-3-cloudflare-first.md
title: Implement Cloudflare auth, KV cache, rate limiting, presence, queues, job events
phase: Phase 2 - Cloudflare Control Plane
---

## Steps
- Add session token auth verification (`sessionAuth.ts`) and new types in `edge-control-plane/src/types.ts`.
- Enforce auth + rate limiting in `edge-control-plane/src/index.ts` for non-health endpoints.
- Implement session_key KV mapping and routing logic.
- Add queue endpoints that forward to SessionAgent DO.
- Add job event notifications in EventBus.
- Update SessionAgent DO with queue CRUD, queue draining after non-stream queries, stop via WS, and duration tracking.
- Update EventBus DO with presence update broadcasts and alarm scheduling.
