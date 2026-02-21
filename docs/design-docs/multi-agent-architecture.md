# Multi-Agent Architecture

This guide covers agent configuration and delegation in the OpenAI Agents runtime.

## Core Concepts

### `AgentConfig`

`AgentConfig` defines one agent type:

- `name`, `display_name`, `description`
- `system_prompt`
- `allowed_tools`
- `max_turns`
- `subagents` (optional)

### `SubAgentConfig`

Subagents are represented as handoff targets:

- `description`
- `prompt`
- `tools`
- `model` (default delegated model is `gpt-4.1-mini`)

### `OpenAIAgentExecutor`

The default executor builds an OpenAI `Agent` and executes with `Runner.run(...)`.

## Built-in Agent Types

- `default`: general-purpose runtime
- `marketing`: marketing/copy workflows
- `research`: coordinator with handoffs to:
  - `researcher`
  - `data-analyst`
  - `report-writer`

## Handoffs

`build_agent_options(...)` creates handoffs using `handoff(...)` with deterministic tool names:

- `transfer_to_researcher`
- `transfer_to_data_analyst`
- `transfer_to_report_writer`

Lead model default: `gpt-4.1`.
Delegated model default: `gpt-4.1-mini`.

## Tool Access

Tool allowlists are translated by `modal_backend/mcp_tools/registry.py` into concrete OpenAI tools, including:

- Local parity tools: `Read`, `Write`, `Glob`, `Bash`, `WebFetch`
- Hosted web search when `WebSearch(*)` is present
- Session tools:
  - `mcp__sessions__spawn_session`
  - `mcp__sessions__check_session_status`
  - `mcp__sessions__get_session_result`
  - `mcp__sessions__list_child_sessions`

## Session Behavior

- Resume by `session_id` through `SQLiteSession`
- Branch by `fork_session=true` (new session ID, inherited history)

## Adding a Custom Agent Type

1. Add a config in `modal_backend/agent_runtime/types/`.
2. Register it in `modal_backend/agent_runtime/registry.py`.
3. Use via `agent_type` in `/query`, `/query_stream`, or `run_agent_remote`.

## CLI Examples

```bash
modal run -m modal_backend.main::run_agent_remote --question "Explain decorators"
modal run -m modal_backend.main::run_agent_remote --question "Write tagline" --agent-type marketing
modal run -m modal_backend.main::run_agent_remote --question "Research AI trends" --agent-type research
```
