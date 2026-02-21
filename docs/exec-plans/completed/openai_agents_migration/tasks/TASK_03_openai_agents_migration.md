---
task_id: 03
plan_id: PLAN_openai_agents_migration
plan_file: ../PLAN_openai_agents_migration.md
title: Migrate and extend tests for OpenAI parity
phase: Phase 3 - Verification
---

## Objective

Replace Claude-coupled tests with OpenAI/provider-neutral coverage and add parity-focused tests.

## Scope

- Rewrite serialization tests to dict/provider-neutral fixtures.
- Update agent loop tests for async `SQLiteSession` behavior.
- Update schema model ID expectations.
- Add tests for cancellation modes and allowlist-to-toolset mapping.

## Deliverables

- Updated `tests/test_controllers_serialization.py`.
- Updated `tests/test_agents_loop.py`.
- Updated `tests/test_schemas_jobs.py`.
- Added `tests/test_controller_runtime_openai.py` and updated tool tests.
