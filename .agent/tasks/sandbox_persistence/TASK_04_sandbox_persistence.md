---
task_id: 04
plan_id: PLAN_sandbox_persistence
plan_file: ../../plans/sandbox_persistence/PLAN_sandbox_persistence.md
title: Tune Sandbox.create and resource limits
phase: Phase 3 - Lifecycle & Observability
---

### Changes
- Set `timeout` (≤ 24h), `idle_timeout` (e.g., 10m), `cpu`, `memory`, `verbose=True`.
- Keep `encrypted_ports=[SERVICE_PORT]` and resolve tunnels with retry.
- Persist final status to Dict.

### Acceptance Criteria
- Resource limits visible in Modal UI.
- Tunnel URL discovered reliably within 30s and health check passes.
