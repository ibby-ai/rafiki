---
task_id: 04
plan_id: PLAN_phase-3-cloudflare-first
plan_file: ../PLAN_phase-3-cloudflare-first.md
title: Run lint/format/tests and Modal smoke checks
phase: Phase 4 - Validation
---

## Steps
- Run `uv run ruff check --fix .` and `uv run ruff format .`.
- Run `uv run pytest`.
- Run `modal run -m modal_backend.main` and `modal run -m modal_backend.main::run_agent_remote --question "health check"`.
- Capture results in plan progress.
