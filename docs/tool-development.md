# Tool Development Guide

This guide explains how to create custom tools for the Claude agent using the MCP (Model Context Protocol) system.

## Overview

Tools extend the agent's capabilities by giving it the ability to perform actions like calculations, web searches, file operations, or custom business logic. When the agent decides it needs to use a tool, it sends a request to the tool and receives a response.

### How Tools Work

```
User Question → Agent Reasoning → Tool Call → Tool Execution → Response → Agent Continues
                     ↓
           "I need to calculate 2+2"
                     ↓
           Tool: calculate(expression="2+2")
                     ↓
           Result: "4"
                     ↓
           "The answer is 4"
```

---

## Quick Start: Creating Your First Tool

### Step 1: Create the Tool File

Create a new file in `modal_backend/mcp_tools/`:

```python
# modal_backend/mcp_tools/my_tool.py
"""My custom tool for doing something useful."""

from claude_agent_sdk import tool
from typing import Any


@tool("my_tool_name", "Description of what this tool does", {"param1": str, "param2": int})
async def my_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Execute the tool logic.

    Args:
        args: Dictionary containing the tool parameters.

    Returns:
        A content dict with the result.
    """
    param1 = args["param1"]
    param2 = args["param2"]

    # Your tool logic here
    result = f"Processed {param1} with value {param2}"

    return {"content": [{"type": "text", "text": result}]}
```

### Step 2: Register the Tool

Edit `modal_backend/mcp_tools/registry.py`:

```python
# Add import at the top
from modal_backend.mcp_tools.my_tool import my_tool

# In _initialize_defaults(), add to the tools list:
multi_tool_server = create_sdk_mcp_server(
    name="utilities",
    version="1.0.0",
    tools=[calculate, my_tool]  # Add your tool here
)

# Add to allowed tools list:
self._allowed_tools = [
    # ... existing tools ...
    "mcp__utilities__my_tool_name",  # Add this line
]
```

### Step 3: Test Your Tool

```bash
# Run the agent and ask it to use your tool
modal run -m modal_backend.main::run_agent_remote --question "Use my_tool with param1='hello' and param2=42"
```

---

## Anatomy of a Tool

### The @tool Decorator

```python
@tool(name, description, parameters)
async def tool_function(args: dict[str, Any]) -> dict[str, Any]:
    ...
```

| Argument | Type | Description |
|----------|------|-------------|
| `name` | `str` | Unique identifier for the tool (used in `mcp__server__name`) |
| `description` | `str` | Human-readable description the agent sees when deciding to use the tool |
| `parameters` | `dict` | Schema defining expected parameters: `{"param_name": type}` |

### Parameter Types

Supported parameter types:

```python
# Basic types
{"text": str}          # String input
{"count": int}         # Integer input
{"ratio": float}       # Float input
{"enabled": bool}      # Boolean input

# Optional parameters (with defaults)
{"query": str, "limit": int}  # Both required

# Complex example
@tool(
    "search",
    "Search for information",
    {
        "query": str,      # Required search query
        "max_results": int  # Required result limit
    }
)
```

### Return Format

Tools must return a dictionary with a `content` key containing a list of content blocks:

```python
# Text response (most common)
return {"content": [{"type": "text", "text": "Your result here"}]}

# Multiple content blocks
return {
    "content": [
        {"type": "text", "text": "First part of result"},
        {"type": "text", "text": "Second part of result"}
    ]
}

# Error response
return {
    "content": [{"type": "text", "text": "Error: Invalid input"}],
    "isError": True
}
```

---

## Tool Naming Convention

Tools are identified using the pattern: `mcp__<server>__<tool>`

| Component | Description | Example |
|-----------|-------------|---------|
| `mcp__` | Prefix indicating MCP tool | `mcp__` |
| `<server>` | Server name from `create_sdk_mcp_server()` | `utilities` |
| `<tool>` | Tool name from `@tool()` decorator | `calculate` |

**Full example**: `mcp__utilities__calculate`

---

## Complete Example: Weather Tool

Here's a more realistic example of a tool that fetches weather data:

### 1. Create the Tool

```python
# modal_backend/mcp_tools/weather_tool.py
"""Weather lookup tool using a weather API."""

from claude_agent_sdk import tool
from typing import Any
import httpx


@tool(
    "get_weather",
    "Get current weather for a city. Returns temperature, conditions, and humidity.",
    {"city": str, "units": str}
)
async def get_weather(args: dict[str, Any]) -> dict[str, Any]:
    """Fetch current weather for a city.

    Args:
        args: Dict with 'city' (city name) and 'units' ('celsius' or 'fahrenheit').

    Returns:
        Weather information as text.
    """
    city = args["city"]
    units = args.get("units", "celsius")

    # In production, use a real weather API
    # This is a mock implementation for demonstration
    try:
        # Example: Call a weather API
        # async with httpx.AsyncClient() as client:
        #     response = await client.get(f"https://api.weather.com/{city}")
        #     data = response.json()

        # Mock response for demonstration
        weather_data = {
            "temperature": 22 if units == "celsius" else 72,
            "unit": "°C" if units == "celsius" else "°F",
            "conditions": "Partly cloudy",
            "humidity": 65
        }

        result = (
            f"Weather in {city}:\n"
            f"Temperature: {weather_data['temperature']}{weather_data['unit']}\n"
            f"Conditions: {weather_data['conditions']}\n"
            f"Humidity: {weather_data['humidity']}%"
        )

        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error fetching weather: {str(e)}"}],
            "isError": True
        }
```

### 2. Register It

```python
# modal_backend/mcp_tools/registry.py
from modal_backend.mcp_tools.weather_tool import get_weather

# In _initialize_defaults():
multi_tool_server = create_sdk_mcp_server(
    name="utilities",
    version="1.0.0",
    tools=[calculate, get_weather]
)

self._allowed_tools = [
    # ... existing tools ...
    "mcp__utilities__get_weather",
]
```

### 3. Test It

```bash
modal run -m modal_backend.main::run_agent_remote \
  --question "What's the weather like in Tokyo?"
```

---

## Adding External Dependencies

If your tool needs additional Python packages:

### 1. Add to pyproject.toml

```toml
[project]
dependencies = [
    # ... existing deps ...
    "your-package>=1.0.0",
]
```

### 2. Update the Modal Image

The image is rebuilt automatically when dependencies change. If you need system packages, edit `modal_backend/main.py`:

```python
def _base_anthropic_sdk_image() -> modal.Image:
    return (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("your-system-package")  # Add system packages
        .pip_install("claude-agent-sdk", "fastapi", "uvicorn", "httpx")
        .pip_install("your-python-package")  # Add Python packages
        # ... rest of image definition
    )
```

---

## Tool Permissions

Tools must be explicitly allowed to be used. This is a security feature.

### Adding to Allowed Tools

Edit `modal_backend/mcp_tools/registry.py`:

```python
self._allowed_tools = [
    # Exact match
    "mcp__utilities__calculate",

    # Wildcard - allows all tools from a server
    "mcp__utilities__*",

    # Built-in tools
    "Read",
    "Write",
    "WebSearch(*)",
    "WebFetch(*)",
]
```

### Wildcard Patterns

| Pattern | Matches |
|---------|---------|
| `"mcp__utilities__calculate"` | Only the calculate tool |
| `"mcp__utilities__*"` | All tools from utilities server |
| `"WebSearch(*)"` | WebSearch with any parameters |

---

## Creating Multiple Tools in One Server

You can group related tools in a single MCP server:

```python
# modal_backend/mcp_tools/math_tools.py
from claude_agent_sdk import tool
from typing import Any


@tool("add", "Add two numbers", {"a": float, "b": float})
async def add(args: dict[str, Any]) -> dict[str, Any]:
    result = args["a"] + args["b"]
    return {"content": [{"type": "text", "text": f"Result: {result}"}]}


@tool("multiply", "Multiply two numbers", {"a": float, "b": float})
async def multiply(args: dict[str, Any]) -> dict[str, Any]:
    result = args["a"] * args["b"]
    return {"content": [{"type": "text", "text": f"Result: {result}"}]}


@tool("power", "Raise a to the power of b", {"a": float, "b": float})
async def power(args: dict[str, Any]) -> dict[str, Any]:
    result = args["a"] ** args["b"]
    return {"content": [{"type": "text", "text": f"Result: {result}"}]}
```

Register all of them:

```python
# registry.py
from modal_backend.mcp_tools.math_tools import add, multiply, power

multi_tool_server = create_sdk_mcp_server(
    name="math",
    version="1.0.0",
    tools=[add, multiply, power]
)

self._allowed_tools = [
    "mcp__math__add",
    "mcp__math__multiply",
    "mcp__math__power",
    # Or use wildcard: "mcp__math__*"
]
```

---

## Best Practices

### 1. Write Clear Descriptions

The agent uses descriptions to decide when to use a tool:

```python
# ❌ Bad - vague description
@tool("process", "Process data", {"data": str})

# ✅ Good - specific description
@tool(
    "format_json",
    "Format a JSON string with proper indentation. Use when the user asks to prettify or format JSON data.",
    {"json_string": str}
)
```

### 2. Handle Errors Gracefully

```python
@tool("divide", "Divide a by b", {"a": float, "b": float})
async def divide(args: dict[str, Any]) -> dict[str, Any]:
    try:
        if args["b"] == 0:
            return {
                "content": [{"type": "text", "text": "Error: Cannot divide by zero"}],
                "isError": True
            }
        result = args["a"] / args["b"]
        return {"content": [{"type": "text", "text": f"Result: {result}"}]}
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {str(e)}"}],
            "isError": True
        }
```

### 3. Validate Input

```python
@tool("send_email", "Send an email", {"to": str, "subject": str, "body": str})
async def send_email(args: dict[str, Any]) -> dict[str, Any]:
    to = args.get("to", "")
    if not to or "@" not in to:
        return {
            "content": [{"type": "text", "text": "Error: Invalid email address"}],
            "isError": True
        }
    # ... rest of implementation
```

### 4. Keep Tools Focused

Each tool should do one thing well:

```python
# ❌ Bad - too many responsibilities
@tool("file_operations", "Read, write, or delete files", {...})

# ✅ Good - single responsibility
@tool("read_file", "Read contents of a file", {"path": str})
@tool("write_file", "Write content to a file", {"path": str, "content": str})
@tool("delete_file", "Delete a file", {"path": str})
```

### 5. Use Async for I/O Operations

Tools are async by default. Use `await` for any I/O:

```python
@tool("fetch_url", "Fetch content from a URL", {"url": str})
async def fetch_url(args: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        response = await client.get(args["url"])  # Use await!
        return {"content": [{"type": "text", "text": response.text}]}
```

---

## Testing Tools

### Unit Testing

```python
# tests/test_tools.py
import pytest
from modal_backend.mcp_tools.calculate_tool import calculate


@pytest.mark.asyncio
async def test_calculate_simple():
    result = await calculate({"expression": "2 + 2"})
    assert result["content"][0]["text"] == "Result: 4"


@pytest.mark.asyncio
async def test_calculate_complex():
    result = await calculate({"expression": "(10 * 5) + 3"})
    assert result["content"][0]["text"] == "Result: 53"
```

### Integration Testing

```bash
# Test via the agent
modal run -m modal_backend.main::run_agent_remote \
  --question "Calculate 15 * 7 + 3"
```

---

## Troubleshooting

### Tool Not Being Called

1. **Check the description** - Is it clear enough for the agent to understand when to use it?
2. **Check allowed tools** - Is it in the `_allowed_tools` list?
3. **Check naming** - Does the name in `_allowed_tools` match `mcp__<server>__<tool>`?

### Tool Returns Error

1. **Check logs** - Run with `modal serve` and watch for exceptions
2. **Test directly** - Call the tool function directly in a test
3. **Check parameters** - Are all required parameters being passed?

### Tool Not Found

```
Error: Tool 'mcp__utilities__my_tool' not found
```

1. Verify the tool is imported in `registry.py`
2. Verify the tool is added to `tools=[...]` in `create_sdk_mcp_server()`
3. Verify the name matches exactly (case-sensitive)

---

## Related Documentation

- [Configuration Guide](./configuration.md) - Environment and settings
- [Architecture Overview](./architecture.md) - How components interact
- [Troubleshooting](./troubleshooting.md) - Common issues and solutions
- [Claude Agent SDK Documentation](https://docs.claude.com/en/api/agent-sdk/python) - Official SDK docs
