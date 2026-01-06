# Custom Tools

Learn how to create and register custom MCP tools for the agent.

## Overview

Tools extend the agent's capabilities by providing callable functions. This example shows how to create string manipulation tools.

## Files

- `string_utils_tool.py` - Example tool implementations
- `INTEGRATION.md` - Step-by-step guide to register tools

## Tool Anatomy

```python
from claude_agent_sdk import tool

@tool(
    "tool_name",                    # Unique identifier
    "Description of what it does",  # Shown to agent
    {"param": str}                  # Parameter schema
)
async def my_tool(args: dict[str, Any]) -> dict[str, Any]:
    result = process(args["param"])
    return {"content": [{"type": "text", "text": result}]}
```

## Key Concepts

### Tool Naming Convention

Tools are referenced as `mcp__<server>__<tool>`:
- `mcp__utilities__calculate` - Built-in calculator
- `mcp__utilities__reverse_string` - Custom string tool

### Return Format

Tools must return a dict with a `content` list:

```python
return {
    "content": [
        {"type": "text", "text": "Result text"}
    ]
}
```

### Allowed Tools

Tools must be added to `_allowed_tools` in `registry.py` to be usable.

## Quick Start

1. Review `string_utils_tool.py` for implementation patterns
2. Follow `INTEGRATION.md` to add tools to your project
3. Test with a query that would use your tool
