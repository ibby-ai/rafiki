---
task_id: 03
plan_id: PLAN_prod_readiness_claude_agent_sdk
plan_file: ../PLAN_prod_readiness_claude_agent_sdk.md
title: Replace eval() in calculate tool with safe parser
phase: Phase 2 - Tool Hardening
---

## Summary

Remove `eval()` from the calculate tool and replace it with a safe arithmetic parser that only allows numeric literals and basic operators.

## Scope

- `modal_backend/mcp_tools/calculate_tool.py`
- `tests/test_tools_calculate.py`

## Steps

1. Implement a safe evaluator (AST-based) that only permits numeric constants and operators (+, -, *, /, //, %, **, parentheses).
2. Reject any unsupported nodes (names, attribute access, calls, subscripts, etc.) with a clear error message.
3. Update tests to cover valid expressions, invalid tokens, and security cases.

## Acceptance Criteria

- `eval()` is removed from the codebase.
- Calculator only evaluates permitted arithmetic expressions.
- Tests cover both allowed and blocked inputs and pass.
