---
task_id: 02
plan_id: PLAN_agent-sandbox-infra-hardening
plan_file: ../PLAN_agent-sandbox-infra-hardening.md
title: Minimize secret blast radius across control and execution planes
phase: Phase 2 - Credential boundary hardening
---

- Audit where secrets are injected in `modal_backend/settings/settings.py` and runtime function/sandbox creation paths in `modal_backend/main.py`.
- Split secrets by surface (control-plane auth, provider auth, optional tracing) and remove unnecessary secret exposure from sandbox paths.
- Enforce a sandbox secret allowlist (`OPENAI_API_KEY` and optional tracing keys only) and explicitly exclude `INTERNAL_AUTH_SECRET` and `MODAL_TOKEN_*` from sandbox runtime env.
- Implement scoped or ephemeral sandbox-to-control-plane credentials using session-scoped short-lived tokens (not shared signing secrets) with explicit TTL/rotation behavior.
- Update secret rotation/rollback instructions in `docs/references/configuration.md` and `docs/references/runbooks/cloudflare-modal-e2e.md`.

## Done When
- Before/after secret-surface evidence is recorded for sandbox runtime env.
- Negative auth regression checks confirm expected `401` behavior for invalid/missing internal auth.
- Rotation and rollback instructions are updated and tested in the runbook flow.
- Rollback notes explicitly document how to restore previous secret wiring without exposing additional secrets.
