# Configuration Guide

This document covers runtime configuration for the Modal + OpenAI Agents deployment.

## Quick Setup

```bash
pip install modal
modal setup
modal secret create openai-secret OPENAI_API_KEY=your-api-key-here
modal run -m modal_backend.main
```

## Required Secrets

### OpenAI API Secret

The runtime requires a Modal secret named `openai-secret` containing `OPENAI_API_KEY`.

```bash
# create
modal secret create openai-secret OPENAI_API_KEY=sk-...

# rotate
modal secret delete openai-secret
modal secret create openai-secret OPENAI_API_KEY=sk-new...

# verify
modal secret list
```

### Internal Auth Secret

Control-plane traffic requires `internal-auth-secret` with `INTERNAL_AUTH_SECRET`.

```bash
modal secret create internal-auth-secret INTERNAL_AUTH_SECRET=<shared-secret>
```

## Core OpenAI Agent Settings

Defined in `modal_backend/settings/settings.py`:

- `openai_api_key`
- `openai_model_default` (default: `gpt-4.1`)
- `openai_model_subagent` (default: `gpt-4.1-mini`)
- `openai_session_db_path` (default: `/data/openai_agents_sessions.sqlite3`)
- `agent_max_turns`

## Runtime and Resource Settings

Common controls:

- `sandbox_cpu`, `sandbox_memory`
- `sandbox_timeout`, `sandbox_idle_timeout`
- `min_containers`, `max_containers`, `buffer_containers`
- `concurrent_max_inputs`, `concurrent_target_inputs`
- `volume_commit_interval`

These are documented inline in `modal_backend/settings/settings.py` and can be overridden via environment variables.

## Image Configuration

The Modal image is built in `modal_backend/main.py` by `_base_openai_agents_image()`.

Default image includes:

- Python 3.11
- `openai-agents==0.9.2`
- `langsmith[openai-agents]>=0.3.15`
- `fastapi`, `uvicorn`, `httpx`, `uv`

To add dependencies, update `_base_openai_agents_image()`.

## LangSmith Tracing

Optional tracing is controlled by:

- `enable_langsmith_tracing`
- `langsmith_secret_name`

When enabled, tracing is configured via `modal_backend/tracing.py` using LangSmith's OpenAI Agents tracing processor.

## Troubleshooting

### Secret not found

```
modal.exception.NotFoundError: Secret 'openai-secret' not found
```

Fix:

```bash
modal secret create openai-secret OPENAI_API_KEY=your-key
```

### Slow first request

Likely a cold start. Increase `sandbox_idle_timeout` or keep warm pool enabled.

### Session memory not persisted

Verify `openai_session_db_path` points to mounted persistent storage (default `/data/...`).
