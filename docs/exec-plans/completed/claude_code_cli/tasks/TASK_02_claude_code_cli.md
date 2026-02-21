---
task_id: 02
plan_id: PLAN_claude_code_cli
plan_file: ../PLAN_claude_code_cli.md
title: Add Claude CLI schemas and controller endpoint
phase: Phase 2 - Service API
---

- Add request/response schemas for CLI usage.
- Implement `/claude_cli` endpoint in `modal_backend/api/controller.py` with subprocess execution and timeout handling.
- Wire error handling to existing `ErrorResponse` structure.
