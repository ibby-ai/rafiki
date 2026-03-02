---
task_id: 06
plan_id: PLAN_agent-sandbox-infra-hardening
plan_file: ../PLAN_agent-sandbox-infra-hardening.md
title: Add scoped artifact transfer and close docs governance gaps
phase: Phase 6 - Data path and documentation closure
---

- Design and implement scoped/presigned artifact transfer flow so execution runtime does not require broad storage credentials.
- Specify signer authority, signed claims (`session_id`, `artifact_id`, expiry), max TTL, and revocation behavior for artifact access tokens/URLs.
- Validate artifact upload/download behavior and failure handling for expiry, tampering, and cross-session access attempts.
- Update canonical architecture/runbook docs to reflect implemented behavior and rollout status in the same change wave as runtime changes.
- Re-score and update `docs/QUALITY_SCORE.md`, `docs/RELIABILITY.md`, and `docs/SECURITY.md` with dated evidence.

## Done When
- Artifact transfer access is scoped, time-bounded, and revocable by documented policy.
- Abuse-case tests pass (expired token, tampered signature, cross-session attempt).
- Architecture/runbook/governance docs include dated command evidence in the same implementation wave.
- Rollback notes explicitly document how to revert artifact-transfer changes while preserving access controls.

## Evidence Capture (Required)
- Commands:
  - `uv run python -m pytest tests/test_jobs_security.py`
  - `uv run python -m pytest tests/test_artifact_access.py`
  - `npm --prefix edge-control-plane run check`
- Expected outcomes:
  - Signed artifact access checks are deterministic and enforce claims/expiry/session binding.
  - Governance docs include dated command evidence from this change wave.
- Artifact path:
  - Plan `Progress` entry for TASK_06 in `../PLAN_agent-sandbox-infra-hardening.md`.

## Rollback Notes (Required)
- Trigger:
  - Artifact download/upload regressions caused by signed-access enforcement.
- Rollback steps:
  - Re-enable prior artifact proxy path while maintaining path traversal protections.
  - Disable signed-artifact enforcement flag pending fix.
- Verification:
  - Re-run artifact list/download checks plus abuse-case tests.
- Record location:
  - Plan `Progress` entry + runbook/configuration docs.

## Required Doc Sync
- `docs/references/configuration.md`
- `docs/references/runbooks/cloudflare-modal-e2e.md`
- `docs/design-docs/cloudflare-hybrid-architecture.md`
- `docs/references/api-usage.md`
- `docs/QUALITY_SCORE.md`
- `docs/RELIABILITY.md`
- `docs/SECURITY.md`
