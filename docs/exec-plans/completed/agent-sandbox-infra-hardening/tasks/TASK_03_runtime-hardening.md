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

## Evidence Capture (Required)
- Commands:
  - `uv run python -m pytest tests/test_controller_runtime_openai.py`
  - `uv run python -m pytest tests/test_internal_auth_middleware.py`
  - Runtime verification commands captured in runbook (`/query`, `/query_stream`, UID/writable-path checks).
- Expected outcomes:
  - Runtime hardening checks pass without breaking query/query_stream contracts.
  - UID and writable-path evidence is attached in plan progress.
- Artifact path:
  - Plan `Progress` entry for TASK_03 in `../PLAN_agent-sandbox-infra-hardening.md`.

## Rollback Notes (Required)
- Trigger:
  - Runtime startup/query regressions caused by non-root or environment scrubbing changes.
- Rollback steps:
  - Restore prior hardening gate defaults in controller bootstrap.
  - Restore prior sandbox startup user mode if required for service recovery.
- Verification:
  - Re-run `/query` + `/query_stream` runbook checks and targeted pytest suite.
- Record location:
  - Plan `Progress` entry + `docs/references/runbooks/cloudflare-modal-e2e.md`.

## Required Doc Sync
- `docs/references/runbooks/cloudflare-modal-e2e.md`
- `docs/design-docs/cloudflare-hybrid-architecture.md`
- `docs/references/configuration.md`
