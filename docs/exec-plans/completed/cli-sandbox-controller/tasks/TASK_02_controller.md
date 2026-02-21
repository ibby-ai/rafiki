---
task_id: 02
plan_id: PLAN_cli-sandbox-controller
plan_file: ../PLAN_cli-sandbox-controller.md
title: Implement CLI controller service endpoints
phase: Phase 2 - CLI Controller
---

## Scope
- Create `modal_backend/api/cli_controller.py` with `/health_check`, `/execute`, `/ralph/execute`.
- Ensure non-root CLI execution and CLI volume commit after runs.
- Use Claude CLI job workspace conventions under `/data-cli/jobs/<job_id>/`.
