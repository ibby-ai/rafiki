---
task_id: 04
plan_id: PLAN_agent-sandbox-infra-hardening
plan_file: ../PLAN_agent-sandbox-infra-hardening.md
title: Isolate high-risk tools and remove unsafe evaluation paths
phase: Phase 4 - Tool execution safety
---

- Replace `eval()` usage in `modal_backend/mcp_tools/calculate_tool.py` with an AST-only arithmetic evaluator (no names, function calls, or attribute access).
- Define stricter policy enforcement and explicit isolation strategy for high-risk tools (`Bash`, network fetch flows) in `modal_backend/mcp_tools/registry.py`.
- Add regression tests for blocked payloads, allowed payloads, and error contract stability.
- Add dedicated malicious-input tests for calculate tool safety and contract-stable error payloads.
- Update tool-development guidance to reflect new guardrails and security rationale.

## Done When
- `eval()` is fully removed from tool runtime execution paths.
- Malicious input payloads are denied with deterministic, tested errors.
- High-risk tool isolation boundaries are documented and covered by policy tests.
- Rollback notes explicitly document how to restore the previous tool execution path if production regressions appear.
