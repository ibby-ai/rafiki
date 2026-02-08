# Integrating Custom Tools

Step-by-step guide to add the example tools to your agent.

## Step 1: Copy the Tool File

```bash
cp examples/04_custom_tools/string_utils_tool.py modal_backend/mcp_tools/
```

## Step 2: Update the Registry

Edit `modal_backend/mcp_tools/registry.py`:

```python
# Add import at the top
from modal_backend.mcp_tools.string_utils_tool import (
    reverse_string,
    count_words,
    to_uppercase,
)

# In _initialize_defaults(), update the multi_tool_server:
multi_tool_server = create_sdk_mcp_server(
    name="utilities",
    version="1.0.0",
    tools=[calculate, reverse_string, count_words, to_uppercase]  # Add new tools
)

# Add to _allowed_tools list:
self._allowed_tools = [
    # ... existing tools ...
    "mcp__utilities__reverse_string",
    "mcp__utilities__count_words",
    "mcp__utilities__to_uppercase",
]
```

## Step 3: Update the `__init__.py`

Edit `modal_backend/mcp_tools/__init__.py`:

```python
from modal_backend.mcp_tools.string_utils_tool import (
    reverse_string,
    count_words,
    to_uppercase,
)
```

## Step 4: Test the Tools

```bash
modal run -m modal_backend.main::run_agent_remote \
    --question "Reverse the string 'hello world' using the reverse_string tool"
```

## Troubleshooting

### Tool not found

Ensure the tool name in `_allowed_tools` matches the pattern `mcp__<server>__<tool_name>`.

### Tool not called

The agent decides when to use tools. Make your prompt explicit:
- "Use the reverse_string tool to..."
- "Count the words using count_words..."

### Import errors

Run `uv sync` after adding new files to ensure the package is reinstalled.
