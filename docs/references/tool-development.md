# Tool Development Guide

This guide explains how to create tools for the OpenAI Agents runtime.

## Overview

Tools are exposed to agents via OpenAI `function_tool` or hosted tools. Local tools in this project live in `modal_backend/mcp_tools/` and are mapped from allowlists by `modal_backend/mcp_tools/registry.py`.

## Create a Local Function Tool

```python
# modal_backend/mcp_tools/my_tool.py
from agents import function_tool

@function_tool(name_override="mcp__utilities__my_tool")
def my_tool(param1: str, count: int = 1) -> str:
    return f"Processed {param1} x{count}"
```

## Register Tool Mapping

Update `modal_backend/mcp_tools/registry.py`:

1. Import your tool.
2. Add it to `_tool_map`.
3. Add its allowlist name to `_allowed_tools` if desired by default.

Canonical naming is preserved for compatibility (for example `mcp__utilities__calculate`).

## Allowlist Behavior

`build_tools_for_allowed(allowed_tools)` resolves:

- exact names (for example `Read`, `mcp__utilities__calculate`)
- wildcard `WebSearch(*)` -> hosted `WebSearchTool()`
- wildcard `WebFetch(*)` -> local `WebFetch`

## Testing

Add unit tests in `tests/`.

Example invocation style for function tools:

```python
import json

result = await my_tool.on_invoke_tool(None, json.dumps({"param1": "hello", "count": 2}))
assert result == "Processed hello x2"
```

### Eval-Style Runtime Coverage

Tool and multi-agent changes should include regression-style expectations in runtime tests:

- handoff correctness (agent model updates and handoff tool routing)
- tool-call sequence fidelity (`tool_use` then `tool_result`, with stable `tool_use_id`)
- cancellation-adjacent behavior with trace metadata present (`trace_id`, optional `openai_trace_id`)

Primary test files:

- `tests/test_agents_loop.py`
- `tests/test_controller_runtime_openai.py`

## Best Practices

- Keep tools single-purpose.
- Return deterministic, parseable text when possible.
- Validate inputs and return helpful error strings.
- Avoid side effects outside `/data` unless required.
- Keep canonical tool names stable once published.

## Runtime Policy Guardrails

Current built-in policy checks in `modal_backend/mcp_tools/registry.py`:

- `Calculate` uses AST-only arithmetic evaluation (no `eval`, no names, no function calls, no attribute access).
- `Bash` rejects blocked destructive/network patterns, backticks, newline payloads, and overlong commands.
- `Bash` executes with constrained env (`PATH`, `HOME`, `LANG`) and confined workdir (`AGENT_FS_ROOT` or `/tmp` fallback).
- `WebFetch` only allows `http/https`, blocks embedded credentials, blocks non-standard ports, and blocks private/loopback hosts.
- Timeout and output-size parameters are clamped to safe ranges.

When adding new tools, follow the same pattern: validate inputs before side effects, and fail closed with explicit error messages.

For denied calls, return deterministic, user-visible error text that can be asserted in controller runtime tests.

## Trace Correlation Expectations

Controller summaries and SSE terminal payloads always include `trace_id` and may include `openai_trace_id` when available from provider metadata.
Tool authors should preserve call IDs and explicit error text so correlation across logs, SSE payloads, and LangSmith traces remains actionable.

## Runtime Image Dependencies

If your tool needs extra packages, update `_base_openai_agents_image()` in `modal_backend/main.py`.

## References

- [OpenAI Agents Tools Docs](https://openai.github.io/openai-agents-python/tools/)
