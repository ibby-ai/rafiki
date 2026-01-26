---
name: claude-agent-sdk
description: Explore Claude Agent SDK Python source code to understand implementation details, API patterns, and capabilities. Use when: (1) understanding SDK internals (query, client, sessions), (2) finding type definitions or exports, (3) learning how permissions, hooks, or MCP tools work, (4) troubleshooting SDK behavior.
---

# Claude Agent SDK Source

SDK location: `.venv/lib/python*/site-packages/claude_agent_sdk/`

## Key Files

| File | Contents |
|------|----------|
| `__init__.py` | Public exports: `query`, `ClaudeSDKClient`, `create_sdk_mcp_server`, `tool` |
| `client.py` | `ClaudeSDKClient` - bidirectional session management |
| `query.py` | `query()` - async one-shot interactions |
| `types.py` | `ClaudeAgentOptions`, `PermissionMode`, message types, hooks |
| `_errors.py` | Exception definitions |
| `_internal/` | Implementation details, transport layer |

## Search Patterns

```bash
# Find SDK files
Glob pattern=".venv/lib/python*/site-packages/claude_agent_sdk/**/*.py"

# Search SDK source
Grep pattern="<query>" path=".venv/lib/python*/site-packages/claude_agent_sdk"
```

## Common Queries

| Topic | Search Pattern |
|-------|---------------|
| Client creation | `class ClaudeSDKClient` |
| Permission modes | `PermissionMode` |
| Tool decorator | `def tool` |
| MCP server factory | `create_sdk_mcp_server` |
| Hook types | `HookCallback\|PreToolUse\|PostToolUse` |
| Message types | `class.*Message` |
| Agent options | `ClaudeAgentOptions` |
