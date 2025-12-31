# Configuration Guide

This document describes how to configure the agent sandbox application.

## Environment Variables

The application uses Pydantic Settings for configuration management. Settings can be provided via:

1. **Environment variables** - Set in your shell or `.env` file
2. **Modal Secrets** - Managed via `modal secret create anthropic-secret ANTHROPIC_API_KEY=...`

## Required Configuration

### Anthropic API Key

The application requires an Anthropic API key to be configured:

```bash
# Create Modal secret
modal secret create anthropic-secret ANTHROPIC_API_KEY=your-api-key-here
```

## Configuration Options

All configuration options are defined in `agent_sandbox.config.settings.Settings`. Key settings include:

- `sandbox_name`: Name identifier for the service sandbox (default: "svc-runner-8001")
- `service_port`: Port number for the sandbox service (default: 8001)
- `sandbox_timeout`: Maximum lifetime of a sandbox in seconds (default: 12 hours)
- `sandbox_idle_timeout`: Idle timeout before sandbox shutdown (default: 10 minutes)
- `sandbox_cpu`: CPU allocation for sandbox (default: 1.0)
- `sandbox_memory`: Memory allocation in MB (default: 2048)
- `agent_fs_root`: Root directory for agent filesystem (default: "/data")

## Image Configuration

The Modal image is built in `agent_sandbox.app._base_anthropic_sdk_image()` and includes:

- Python 3.11 (Debian slim base)
- Claude Agent SDK
- FastAPI and uvicorn
- Node.js and @anthropic-ai/claude-agent-sdk
- Project dependencies installed via `uv`

## Secrets

Modal secrets are managed via the `modal secret` CLI. The application expects:

- `anthropic-secret` with key `ANTHROPIC_API_KEY`

See [Modal Secrets documentation](https://modal.com/docs/guide/secrets) for more details.
