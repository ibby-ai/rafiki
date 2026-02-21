---
task_id: 03
plan_id: PLAN_cli-sandbox-controller
plan_file: ../PLAN_cli-sandbox-controller.md
title: Wire CLI sandbox lifecycle + route CLI/Ralph runs
phase: Phase 3 - App Integration
---

## Scope
- Add CLI sandbox lifecycle helpers in `modal_backend/main.py`.
- Route `run_claude_cli_remote` and `run_ralph_remote` through the CLI controller endpoints.
- Ensure CLI volume commits are triggered after runs.
