---
task_id: 04
plan_id: PLAN_claude_cli_sandbox
plan_file: ../PLAN_claude_cli_sandbox.md
title: Lint/format pass
phase: Phase 4 - Validation
---

## Scope
- Run ruff lint and formatter after changes.

## Deliverables
- `uv run ruff check --fix .`
- `uv run ruff format .`

## Acceptance
- No remaining ruff lint issues.
- Code formatted consistently.
