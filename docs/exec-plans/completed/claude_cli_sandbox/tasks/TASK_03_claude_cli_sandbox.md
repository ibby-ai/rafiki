---
task_id: 03
plan_id: PLAN_claude_cli_sandbox
plan_file: ../PLAN_claude_cli_sandbox.md
title: Wire Ralph + endpoints to CLI sandbox + docs
phase: Phase 3 - Integration
---

## Scope
- Add Ralph sandbox runner and invoke it from `run_ralph_remote`.
- Update Ralph status polling to use CLI volume path.
- Update documentation with new CLI configuration defaults.

## Deliverables
- `modal_backend/ralph/runner.py` module.
- `run_ralph_remote` uses sandbox execution.
- Docs updated for CLI settings + workspace path.

## Acceptance
- Ralph runs inside the CLI sandbox and writes to the CLI volume.
- Status polling reads from CLI volume.
