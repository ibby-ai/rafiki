---
task_id: 03
plan_id: PLAN_runtime-readiness-hardening
plan_file: ../PLAN_runtime-readiness-hardening.md
title: Sync docs and rerun full validation matrix
phase: Phase 3 - Operations and Governance
---

## Summary
Update runbooks/references/design/governance docs for readiness behavior and execute required validation commands including Cloudflare <-> Modal E2E.

## Scope
- `docs/references/runbooks/cloudflare-modal-e2e.md`
- `docs/references/runtime-docs-overview.md`
- `docs/references/configuration.md`
- `docs/references/troubleshooting.md`
- `docs/design-docs/controllers-background-service.md`
- `docs/design-docs/cloudflare-hybrid-architecture.md`
- `docs/QUALITY_SCORE.md`
- `docs/RELIABILITY.md`
- `docs/SECURITY.md`

## Steps
1. Add readiness-timeout/retry diagnostics expectations to runbook and triage matrix.
2. Ensure docs keep strict scoped-token-only auth wording and remove unsafe internal direct-call examples.
3. Update design/governance docs with this readiness wave and residual risks.
4. Run full validation matrix and E2E with `.venv` activation; record pass/fail and mitigation.

## Done When
- Runbook includes deterministic readiness-timeout triage and retry expectations.
- References use current strict-auth call paths.
- Governance docs contain dated evidence from this wave.
- Validation matrix and E2E outcomes are recorded in-repo (`PLAN_runtime-readiness-hardening.md` + governance docs).

## Rollback
- Revert docs to prior state if runtime patch is rolled back.
- Keep validation evidence attached to indicate rollback rationale.
