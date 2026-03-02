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
