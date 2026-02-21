---
task_id: 01
plan_id: PLAN_claude_code_cli
plan_file: ../PLAN_claude_code_cli.md
title: Install Claude Code CLI in Modal image
phase: Phase 1 - Image Build
---

- Update `modal_backend/main.py` `_base_anthropic_sdk_image()` to install Claude Code CLI via curl installer.
- Ensure `claude` is on PATH during runtime.
- Keep existing Agent SDK installs intact.
