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

## Best Practices

- Keep tools single-purpose.
- Return deterministic, parseable text when possible.
- Validate inputs and return helpful error strings.
- Avoid side effects outside `/data` unless required.
- Keep canonical tool names stable once published.

## Runtime Image Dependencies

If your tool needs extra packages, update `_base_openai_agents_image()` in `modal_backend/main.py`.

## References

- [OpenAI Agents Tools Docs](https://openai.github.io/openai-agents-python/tools/)
