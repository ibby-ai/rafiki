# Custom Tools

Learn how to create and register custom MCP tools for the agent.

## Overview

Tools extend the agent's capabilities by providing callable functions. This example shows how to create string manipulation tools.

## Files

- `string_utils_tool.py` - Example tool implementations
- `INTEGRATION.md` - Step-by-step guide to register tools

## Tool Anatomy

```python
from agents import function_tool

@function_tool(name_override="mcp__utilities__tool_name")
def my_tool(param: str) -> str:
    result = process(param)
    return result
```

## Key Concepts

### Tool Naming Convention

Tools are referenced as `mcp__<server>__<tool>`:
- `mcp__utilities__calculate` - Built-in calculator
- `mcp__utilities__reverse_string` - Custom string tool

### Return Format

Function tools return regular Python values (usually strings or dicts).

### Allowed Tools

Tools must be added to `_allowed_tools` in `registry.py` to be usable.

## Quick Start

1. Review `string_utils_tool.py` for implementation patterns
2. Follow `INTEGRATION.md` to add tools to your project
3. Test with a query that would use your tool
