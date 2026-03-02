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

## Evidence Capture (Required)
- Commands:
  - `uv run python -m pytest tests/test_internal_auth_middleware.py`
  - `uv run python -m pytest tests/test_settings_openai.py`
  - `rg -n "INTERNAL_AUTH_SECRET|MODAL_TOKEN_ID|MODAL_TOKEN_SECRET|SANDBOX_MODAL_TOKEN" modal_backend/main.py modal_backend/settings/settings.py`
- Expected outcomes:
  - Middleware negative-path tests pass with deterministic `401` contracts.
  - Sandbox secret allowlist and split-surface wiring are visible in code.
  - Session-scoped auth token path is present and covered by tests.
- Artifact path:
  - Plan `Progress` entry for TASK_02 in `../PLAN_agent-sandbox-infra-hardening.md`.

## Rollback Notes (Required)
- Trigger:
  - `/query` or `/query_stream` fails due to sandbox auth token verification errors.
- Rollback steps:
  - Re-enable legacy internal-auth forwarding path for gateway->sandbox calls.
  - Re-introduce previous sandbox secret wiring while investigating scoped-token failure.
- Verification:
  - Re-run `uv run python -m pytest tests/test_internal_auth_middleware.py`.
  - Re-run Cloudflare↔Modal `/query` smoke from runbook.
- Record location:
  - Plan `Progress` entry + runbook note in `docs/references/runbooks/cloudflare-modal-e2e.md`.

## Required Doc Sync
- `docs/references/configuration.md`
- `docs/references/runbooks/cloudflare-modal-e2e.md`
