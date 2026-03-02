---
task_id: 03
plan_id: PLAN_agent-sandbox-infra-hardening
plan_file: ../PLAN_agent-sandbox-infra-hardening.md
title: Harden runtime startup and execution posture
phase: Phase 3 - Runtime hardening
---

- Implement non-root execution defaults for sandbox runtime processes; if a constraint blocks this, document the exact blocker and compensating controls.
- Add environment scrubbing for sensitive runtime variables after bootstrap in controller startup paths.
- Tighten filesystem and process-level defaults (least privilege, explicit writable paths).
- Add explicit verification gates: sandbox UID check, writable-path confinement check, and `/query` + `/query_stream` regressions via the Cloudflare↔Modal runbook.

## Done When
- Runtime verification proves expected UID and writable-path boundaries.
- Query and stream endpoints pass regression checks after hardening.
- Any non-root exceptions are documented with compensating controls and review sign-off.
- Rollback notes explicitly document how to revert hardening changes if runtime stability regresses.
