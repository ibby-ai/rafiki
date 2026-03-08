---
task_id: 02
plan_id: PLAN_openai-agents-hardening
plan_file: ../PLAN_openai-agents-hardening.md
title: Harden Bash and WebFetch tool inputs
phase: Phase 1 - Tool policy hardening
---

- Add policy checks for blocked bash patterns and command length.
- Add URL validation for `WebFetch` (protocol + blocked private hosts).
- Clamp timeout/output controls.
