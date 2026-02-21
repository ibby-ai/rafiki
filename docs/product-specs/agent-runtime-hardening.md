# Agent Runtime Hardening

## Problem
The agent runtime lacked consistent request correlation metadata in streamed responses and had permissive tool inputs for high-risk tools (`Bash`, `WebFetch`). This made debugging and incident response harder, and increased risk of unsafe tool usage.

## User Outcome
Users and operators can reliably correlate a run across logs and SSE output using `trace_id`, and unsafe tool invocations are rejected before execution.

## Scope
- Request validation for `question` and optional `trace_id`.
- `trace_id` propagation in controller logs and stream payloads.
- Policy hardening for `Bash` and `WebFetch` tool wrappers.
- Regression tests for new validation and metadata behavior.
- Deterministic OpenAI session memory compaction controls (bounded history).
- Eval-style runtime regressions for handoffs, tool-call sequencing, and cancellation with traces.
- Optional `openai_trace_id` surfacing in API/SSE summaries when provider metadata includes it.

## Non-Goals
- Token-based memory compaction.
- New external tracing providers.
- Changes to public endpoint names or major response envelope schema.

## Success Metrics
- Streamed `error` and `done` events include a stable `trace_id`.
- Unsafe bash commands and private-host fetches are blocked with deterministic errors.
- Targeted runtime and tool tests pass.

## Rollout / Risks
- Risk: stricter validation can reject previously accepted invalid inputs.
- Mitigation: explicit validation errors and updated docs.

## Linked ExecPlan
- `docs/exec-plans/active/openai-agents-hardening/PLAN_openai-agents-hardening.md`
