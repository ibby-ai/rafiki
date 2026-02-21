# OpenAI Agents Hardening Plan

## Purpose / Big Picture
Improve production safety and observability for the OpenAI Agents runtime by adding request/run trace correlation, stricter tool guardrails, and payload validation in the controller stream adapter.

## Suprises & Discoveries
- Observation: Controller stream events lacked a stable cross-surface correlation id.
- Evidence: `modal_backend/api/controller.py` emitted events without `trace_id`.
- Observation: `Bash` and `WebFetch` tools accepted broad inputs without policy checks.
- Evidence: `modal_backend/mcp_tools/registry.py` executed commands and fetches directly.

## Decision Log
- Decision: Implement P0 changes first (tool policies + trace propagation + request validation) before memory/eval work.
- Rationale: P1/P2 reliability depends on trusted event payloads and identifiable traces.
- Date/Author: 2026-02-21 / Codex
- Decision: Deliver deterministic item-count compaction for SQLite session memory (not token-based trimming) using configurable max/keep thresholds.
- Rationale: Predictable bounded memory with low implementation risk and no API contract change.
- Date/Author: 2026-02-21 / Codex
- Decision: Surface optional `openai_trace_id` in result/summary payloads only when provider metadata exposes it.
- Rationale: Improve LangSmith/OpenAI drill-down while preserving backwards compatibility.
- Date/Author: 2026-02-21 / Codex

## Outcomes & Retrospective
- P0 complete and validated.
- P1 delivered:
  - session memory compaction controls
  - eval-style coverage for handoffs/tool ordering/cancellation traces
  - optional `openai_trace_id` surfacing
- P2 delivered:
  - configuration/tool-development/controller runbook updates
- Deferred:
  - token-based session trimming (follow-up if required)

## Context and Orientation
Key files:
- `modal_backend/api/controller.py`: run execution + SSE bridge
- `modal_backend/api/serialization.py`: message/content serialization
- `modal_backend/mcp_tools/registry.py`: local tool implementations
- `modal_backend/models/sandbox.py`: query request schema
- `tests/test_controller_runtime_openai.py`: controller runtime coverage
- `tests/test_controller_tools.py`: tool allowlist/policy tests
- `tests/test_schemas_sandbox.py`: schema validation tests

## Plan of Work
1. Add strict input guards for `QueryBody` (`question` + `trace_id`) and resolve a stable trace id per request.
2. Add tool policy enforcement for `Bash` and `WebFetch`.
3. Propagate `trace_id` + `session_id` through run messages, serialized payloads, and SSE `done/error` envelopes.
4. Update/extend tests to cover new behavior and run the targeted suites.
5. Update references/design docs for operational clarity.

## Concrete Steps
- `tasks/TASK_01_trace-and-request-validation.md`
- `tasks/TASK_02_tool-policy-hardening.md`
- `tasks/TASK_03_stream-payload-metadata.md`
- `tasks/TASK_04_tests-and-docs.md`
- `tasks/TASK_05_memory-compaction-and-evals.md`

## Progress
[x] (TASK_01_trace-and-request-validation.md) (2026-02-21 00:00) QueryBody + trace resolution implemented.
[x] (TASK_02_tool-policy-hardening.md) (2026-02-21 00:00) Bash/WebFetch policy checks implemented.
[x] (TASK_03_stream-payload-metadata.md) (2026-02-21 00:00) Controller messages and SSE now include trace metadata.
[x] (TASK_04_tests-and-docs.md) (2026-02-21 00:00) Tests updated and docs refreshed; targeted suite passed via `uv run pytest`.
[x] (TASK_05_memory-compaction-and-evals.md) (2026-02-21 00:00) Session compaction controls, eval-style regressions, optional openai trace metadata, and runbook docs delivered.

## Testing Approach
Run targeted tests first:
- `pytest tests/test_controller_runtime_openai.py tests/test_controller_tools.py tests/test_schemas_sandbox.py tests/test_controllers_serialization.py`
Then run broader runtime tests if needed.

## Constraints & Considerations
- Maintain wire compatibility where possible for existing clients.
- Preserve canonical tool names.
- Avoid introducing tracing dependencies beyond current LangSmith integration path.
