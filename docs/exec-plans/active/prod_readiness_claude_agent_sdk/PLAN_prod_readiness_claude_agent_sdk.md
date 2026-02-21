# PLAN_prod_readiness_claude_agent_sdk

## Purpose / Big Picture

Improve production readiness for the Claude Agent SDK deployment by (1) bounding agent turns, (2) sizing ephemeral disk explicitly, (3) removing unsafe evaluation in the calculate tool, and (4) enabling optional hybrid sessions via SDK session resumption with Modal persistence. This should make the service safer, more predictable, and easier to scale while preserving existing execution patterns.

## Suprises & Discoveries

- Observation: None yet.
- Evidence: Not started.

## Decision Log

- Decision: Use `ClaudeAgentOptions.max_turns` to cap agent loops and expose it as a configurable setting with a default of 50.
- Rationale: The SDK supports `max_turns` to limit runaway interactions; a sane default prevents infinite loops while preserving configurability.
- Date/Author: 2026-01-03 / Codex

- Decision: Implement hybrid session resumption via `ClaudeAgentOptions.resume` (and optional `fork_session`) with persisted session IDs in Modal storage.
- Rationale: The SDK provides explicit session resumption primitives; storing session IDs in Modal Dict or Volume makes them recoverable across sandbox restarts.
- Date/Author: 2026-01-03 / Codex

- Decision: Fix sandbox disk allocation by adding `ephemeral_disk` to `_sandbox_resource_kwargs()` and set default to 5120 MiB (~5 GiB).
- Rationale: Current sandbox creation ignores disk settings; explicit 5 GiB aligns with hosting guidance and reduces runtime surprises.
- Date/Author: 2026-01-03 / Codex

## Outcomes & Retrospective

Not started.

## Context and Orientation

- The agent runtime options are built in `modal_backend/agent_runtime/loop.py` (`build_agent_options`) and `modal_backend/api/controller.py` (`_options`). These are the primary insertion points for `max_turns` and session resumption parameters.
- The background service sandbox is created in `modal_backend/main.py` via `modal.Sandbox.create(...)`, while function-level resource settings are also configured there via `_function_resource_kwargs()`.
- Resource settings (CPU, memory, optional disk, timeouts) live in `modal_backend/settings/settings.py`.
- The calculate tool that currently uses `eval()` is in `modal_backend/mcp_tools/calculate_tool.py`.
- Request/response payloads are defined in `modal_backend/models/`, and the HTTP API entry points are in `modal_backend/api/controller.py` and `modal_backend/main.py`.

## Plan of Work

1. Add a `max_turns` setting (default 50) to configuration and thread it into both `build_agent_options` and `_options` so that all entry points enforce a turn limit.
2. Fix sandbox disk allocation by adding `ephemeral_disk` to `_sandbox_resource_kwargs()` and set a 5120 MiB (~5 GiB) default. Apply disk sizing to Modal functions and sandboxes, and document the default/override.
3. Replace `eval()` in `modal_backend/mcp_tools/calculate_tool.py` with a safe AST-based arithmetic parser that only allows numeric literals and basic operators. Add tests for valid expressions and rejections.
4. If hybrid sessions are required, extend request/response schemas to carry `session_id` and `fork_session`, persist session IDs using Modal Dict/Volume, and use SDK session resumption (`resume` and optional `fork_session`) in `ClaudeAgentOptions`. Return the session_id from responses by capturing it from the SDK init message.

## Concrete Steps

- Task files live in `docs/exec-plans/active/prod_readiness_claude_agent_sdk/tasks/`:
  - TASK_01_prod_readiness_claude_agent_sdk.md
  - TASK_02_prod_readiness_claude_agent_sdk.md
  - TASK_03_prod_readiness_claude_agent_sdk.md
  - TASK_04_prod_readiness_claude_agent_sdk.md

## Progress

[ ] (TASK_01_prod_readiness_claude_agent_sdk.md) Add max_turns to agent options and settings.

[ ] (TASK_02_prod_readiness_claude_agent_sdk.md) Add explicit disk allocation settings and document usage.

[ ] (TASK_03_prod_readiness_claude_agent_sdk.md) Replace eval() with safe arithmetic parser and tests.

[ ] (TASK_04_prod_readiness_claude_agent_sdk.md) Add optional hybrid session resumption and persistence.

## Testing Approach

- Unit tests for the calculate tool covering allowed expressions and blocked inputs.
- Unit/contract tests for new request schema fields and option construction.
- Validate disk sizing by verifying Modal reports the configured ephemeral disk for sandboxes and functions.
- Smoke test `modal run -m modal_backend.main` and `modal serve -m modal_backend.main` once changes land.

## Constraints & Considerations

- Maintain backward compatibility for existing endpoints unless hybrid sessions are explicitly enabled.
- Validate Modal SDK support for `ephemeral_disk` on sandboxes before wiring it into `modal.Sandbox.create(...)`.
- Session resumption must ensure per-user isolation to avoid cross-tenant leakage.
- Avoid breaking the current permission model (`permission_mode="acceptEdits"` with `can_use_tool`).
