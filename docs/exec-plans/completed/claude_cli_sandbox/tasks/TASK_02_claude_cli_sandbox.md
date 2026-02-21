---
task_id: 02
plan_id: PLAN_claude_cli_sandbox
plan_file: ../PLAN_claude_cli_sandbox.md
title: Implement CLI sandbox runner + volume helpers
phase: Phase 2 - CLI Sandbox Execution
---

## Scope
- Add CLI-specific settings and volume helpers.
- Create a CLI sandbox runner module to execute `claude` with demoted user.
- Add sandbox creation helper for CLI runs.

## Deliverables
- New CLI settings in `modal_backend/settings/settings.py`.
- `modal_backend/sandbox_runtime/cli_runner.py` module.
- Sandbox helper wired in `modal_backend/main.py`.

## Acceptance
- CLI can be executed via sandbox runner with proper auth/env.
- CLI artifacts are written to the dedicated CLI volume mount.
