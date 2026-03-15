# agent-browser Reference

Developer tooling reference for browser automation in this project.

## Auto-Connect to Running Chrome

The preferred way to use `agent-browser` is with auto-connect, which discovers and attaches to your already-running Chrome instance (with Remote Debugging enabled) instead of spawning a new browser.

### Using auto-connect

Set the environment variable before any `agent-browser` command:

```bash
AGENT_BROWSER_AUTO_CONNECT=1 agent-browser open <url>
```

Or use the flag inline:

```bash
agent-browser --auto-connect open <url>
```

### Make it permanent (recommended)

Add to `~/.claude/settings.json` under the `env` block so all Claude Code sessions inherit it:

```json
{
  "env": {
    "AGENT_BROWSER_AUTO_CONNECT": "1"
  }
}
```

With this set, all `agent-browser` commands automatically reuse your running Chrome session — including your logged-in cookies, storage, and existing tabs.

## Why not `--cdp 9222` directly?

Chrome's Remote Debugging UI enables the **Chrome DevTools MCP** server (Chrome 136+), which uses a WebSocket-only protocol with strict origin restrictions. Playwright's CDP client (used by `--cdp`) expects HTTP `/json/version` endpoints and gets rejected. `--auto-connect` handles the handshake correctly.

## Skill location

The `agent-browser` skill lives at `.agents/skills/agent-browser/SKILL.md` in this repo.
