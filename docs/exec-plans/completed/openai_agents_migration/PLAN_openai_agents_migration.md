# OpenAI Agents SDK Direct Cutover Plan (Strict Contract Parity)

## Purpose / Big Picture

Replace the Claude Agent SDK runtime with OpenAI Agents Python SDK while preserving the public HTTP/SSE contract used by the edge control plane and job system. After this migration, `/query`, `/query_stream`, session stop/status behavior, and result envelope shape remain stable for clients.

## Suprises & Discoveries

- Observation: `SQLiteSession` history APIs (`get_items`, `add_items`) are async and require `await` for fork-copy behavior.
- Evidence: Runtime/session forking path initially called these methods synchronously and required correction.

- Observation: Existing serialization tests were tightly coupled to Claude message classes.
- Evidence: Tests imported `claude_agent_sdk.types` and failed under OpenAI-only dependency set.

## Decision Log

- Decision: Pin `openai-agents==0.9.2` and `langsmith[openai-agents]>=0.3.15`.
- Rationale: Deterministic cutover and documented tracing compatibility.
- Date/Author: 2026-02-20 / Codex

- Decision: Keep strict external event/response contract parity and adapt internally via mapping layer.
- Rationale: Avoid Cloudflare SessionAgent and client breakage.
- Date/Author: 2026-02-20 / Codex

- Decision: Use `SQLiteSession` for resume/fork behavior and clone history for forks.
- Rationale: Preserve existing session semantics on OpenAI runtime.
- Date/Author: 2026-02-20 / Codex

## Outcomes & Retrospective

In progress. Runtime has been migrated to OpenAI primitives, tests are being updated, and docs are aligned to OpenAI setup. Final verification outcomes will be appended after lint/test/modal smoke checks.

## Context and Orientation

Key files:

- `modal_backend/agent_runtime/base.py`: agent construction, handoffs, session creation/fork.
- `modal_backend/api/controller.py`: `/query`, `/query_stream`, cancellation, SSE mapping.
- `modal_backend/api/serialization.py`: provider-neutral message serialization + summary.
- `modal_backend/mcp_tools/registry.py`: allowlist -> OpenAI tool objects.
- `modal_backend/main.py`: Modal image build + runtime entrypoints.
- `modal_backend/tracing.py`: LangSmith tracing processor integration.
- `tests/`: runtime, serialization, schema, and compatibility coverage.

Non-obvious terms:

- Strict parity: preserve external response/event contracts while changing internals.
- Fork session: create new `session_id` with inherited prior history.
- Active run registry: in-memory mapping for stop/status endpoints.

## Plan of Work

1. Complete runtime refactor from Claude client loop to OpenAI `Runner` + `SQLiteSession`.
2. Ensure deterministic handoffs and tool mapping preserve names relied upon by prompts/allowlists.
3. Preserve SSE event names and message/result shapes via compatibility adapters.
4. Migrate tests from Claude types to provider-neutral/OpenAI behavior.
5. Update docs and contributor guidance to OpenAI secret/model setup.
6. Run formatting/lint/tests and document residual risks.

## Concrete Steps

Implementation tracked via task files:

- `../../tasks/openai_agents_migration/TASK_01_openai_agents_migration.md`
- `../../tasks/openai_agents_migration/TASK_02_openai_agents_migration.md`
- `../../tasks/openai_agents_migration/TASK_03_openai_agents_migration.md`
- `../../tasks/openai_agents_migration/TASK_04_openai_agents_migration.md`

## Progress

[x] (TASK_01_openai_agents_migration.md) (2026-02-20) Runtime, sessions, and tool/tracing migration completed.
[x] (TASK_02_openai_agents_migration.md) (2026-02-20) Serialization and controller compatibility mapping completed.
[x] (TASK_03_openai_agents_migration.md) (2026-02-20) Test suite migration in progress with new targeted OpenAI compatibility coverage.
[x] (TASK_04_openai_agents_migration.md) (2026-02-20) Documentation and contributor guidance updated to OpenAI setup.

## Testing Approach

- Unit tests (`uv run pytest`) for serialization, agent runtime, tool mapping, and cancellation behavior.
- Lint/format gates (`uv run ruff check --fix .`, `uv run ruff format .`).
- Modal smoke checks for `run_agent_remote` and service endpoints when environment permits.

## Constraints & Considerations

- Public API/SSE contracts must remain stable for edge consumers.
- Session persistence path must be on mounted volume (`/data`).
- Avoid introducing dual-provider runtime complexity; this is direct cutover.
