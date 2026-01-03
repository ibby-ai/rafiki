# Configuration Guide

This document describes how to configure the agent sandbox application, with practical guidance on when and why to adjust each setting.

## Quick Setup

If you just want to get started quickly:

```bash
# 1. Install and configure Modal
pip install modal
modal setup

# 2. Create the required API secret
modal secret create anthropic-secret ANTHROPIC_API_KEY=your-api-key-here

# 3. Run the agent
modal run -m agent_sandbox.app
```

That's it! The defaults work well for development. Read on for customization options.

---

## Environment Variables

The application uses [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) for configuration management. Settings can be provided via:

1. **Environment variables** - Set in your shell or `.env` file in the project root
2. **Modal Secrets** - Managed via `modal secret create` (required for API keys)

### Using a `.env` File

Create a `.env` file in the project root for local development:

```bash
# .env (optional - for local overrides)
SANDBOX_CPU=0.5
SANDBOX_MEMORY=1024
SANDBOX_IDLE_TIMEOUT=120
```

> **Note**: API keys should always use Modal Secrets, not `.env` files, to avoid accidental exposure.

---

## Required Configuration

### Anthropic API Key

The application requires an Anthropic API key. This **must** be configured as a Modal Secret:

```bash
# Create the Modal secret (one-time setup)
modal secret create anthropic-secret ANTHROPIC_API_KEY=sk-ant-...
```

To update an existing secret:

```bash
# Delete and recreate
modal secret delete anthropic-secret
modal secret create anthropic-secret ANTHROPIC_API_KEY=sk-ant-new-key...
```

To verify your secret exists:

```bash
modal secret list
```

---

## Configuration Options Reference

All configuration options are defined in `agent_sandbox/config/settings.py`. This repo ships a `.env` with
recommended defaults for autoscaling, concurrency, and volume commits; the defaults below reflect that file.
Override or remove `.env` values to return to Modal defaults.

### Sandbox Identity

| Setting | Default | Description |
|---------|---------|-------------|
| `sandbox_name` | `"svc-runner-8001"` | Unique identifier for the sandbox instance |
| `service_port` | `8001` | Internal port for the FastAPI controller |
| `persist_vol_name` | `"svc-runner-8001-vol"` | Name of the Modal Volume for persistent storage |

**When to change**: Only if running multiple independent sandbox instances. Keep defaults for single-instance deployments.

### Resource Allocation

| Setting | Default | When to Increase | When to Decrease |
|---------|---------|------------------|------------------|
| `sandbox_cpu` | `1.0` | Agent does heavy computation (complex tools, data processing) | Tight budget; simple query-response workloads |
| `sandbox_memory` | `2048` MB | Large context windows; many tool results; file processing | Simple agents with minimal tool use |

**Resource guidance**:
- **0.5 CPU / 1024 MB**: Suitable for simple Q&A agents with basic tools
- **1.0 CPU / 2048 MB**: Good default for most agent workloads
- **2.0 CPU / 4096 MB**: Heavy computation, large file processing, or high concurrency

### Resource Limits (Optional)

| Setting | Default | Description |
|---------|---------|-------------|
| `sandbox_cpu_limit` | `null` | Hard CPU limit when set (tuple with `sandbox_cpu`) |
| `sandbox_memory_limit` | `null` | Hard memory limit in MB (tuple with `sandbox_memory`) |
| `sandbox_ephemeral_disk` | `null` | Ephemeral disk in MB for function-based workloads |

**Notes**:
- CPU/memory limits apply to Modal functions and sandboxes where configured.
- Ephemeral disk currently applies to Modal functions (not the long-lived sandbox).

### Autoscaling & Concurrency (Optional)

| Setting | Default | Description |
|---------|---------|-------------|
| `min_containers` | `1` | Minimum warm containers for Modal functions |
| `max_containers` | `4` | Maximum containers for Modal functions |
| `buffer_containers` | `1` | Extra warm containers to absorb bursts |
| `scaledown_window` | `null` | Seconds to wait before scaling down |
| `concurrent_max_inputs` | `10` | Hard cap on concurrent inputs per container |
| `concurrent_target_inputs` | `5` | Target concurrency per container |

**When to enable**: Defaults keep a single warm container with a small buffer and modest concurrency on the public
`http_app` endpoint. Set any value to `null` to disable that control and rely on Modal defaults. Tune higher for
bursty traffic or lower to reduce cost.

### Retry Policy (Optional)

| Setting | Default | Description |
|---------|---------|-------------|
| `retry_max_attempts` | `null` | Max retries for transient failures |
| `retry_initial_delay` | `null` | Initial backoff delay in seconds |
| `retry_backoff_coefficient` | `null` | Exponential backoff factor |
| `retry_max_delay` | `null` | Max delay between retries |

**When to enable**: Production deployments where transient Modal/network errors cause flaky invocations.

### Timeouts

| Setting | Default | Effect | Billing Impact |
|---------|---------|--------|----------------|
| `sandbox_timeout` | 12 hours | Maximum sandbox lifetime before forced restart | Longer = more potential cost if unused |
| `sandbox_idle_timeout` | 10 minutes | How long sandbox stays alive with no requests | Shorter = saves cost; longer = faster response |
| `service_timeout` | 60 seconds | Health check and internal request timeout | Increase if agent responses are slow |

> **Note**: These are project-configured defaults. Modal's actual default sandbox timeout is 5 minutes (max 24 hours). This project uses longer timeouts to support persistent service patterns.

**Understanding timeouts**:

```
Request arrives
    ↓
Is sandbox running? ──No──→ Create new sandbox (cold start: ~10-30 seconds)
    │                              ↓
    Yes                     Reset idle timer
    ↓                              ↓
Reset idle timer ←─────────────────┘
    ↓
Process request
    ↓
Idle timer starts counting
    ↓
No requests for `sandbox_idle_timeout`? → Sandbox terminates
    ↓
Request arrives → Cold start again
```

### Security

| Setting | Default | Description |
|---------|---------|-------------|
| `enforce_connect_token` | `false` | Require Modal Connect token for sandbox access |
| `require_proxy_auth` | `false` | Require Modal Proxy Auth headers on public HTTP endpoints |

**When to enable**: Production deployments where you want additional authentication between the HTTP gateway and the background sandbox. Use `require_proxy_auth` to protect the public endpoint itself, and `enforce_connect_token` for per-request sandbox access.

### Queue Processing

| Setting | Default | Description |
|---------|---------|-------------|
| `job_queue_name` | `"agent-job-queue"` | Modal Queue name for async jobs |
| `job_results_dict` | `"agent-job-results"` | Modal Dict name for job results |
| `job_queue_cron` | `null` | Optional cron schedule for `process_job_queue` |

**When to enable**: Set `job_queue_cron` when you want the queue worker to run automatically on a schedule (for example, `*/1 * * * *` for every minute). Leave unset to run the worker manually.

### Persistence (Optional)

| Setting | Default | Description |
|---------|---------|-------------|
| `persist_vol_version` | `null` | Modal Volume version (set to 2 to opt into v2 volumes) |
| `volume_commit_interval` | `60` | Seconds between `Volume.commit()` calls in the sandbox |

**When to enable**: Use `volume_commit_interval` to persist `/data` without terminating the sandbox. Remove
`VOLUME_COMMIT_INTERVAL` to commit only on sandbox termination.

**Verify commits**: Run `examples/03_file_persistence/run.sh` with `SKIP_TERMINATE=true` and wait for the
commit interval (defaults to 60s). Then `modal volume ls svc-runner-8001-vol` should show the new file without
terminating the sandbox.

### Snapshots (Optional)

| Setting | Default | Description |
|---------|---------|-------------|
| `enable_memory_snapshot` | `false` | Enable Modal memory snapshots for class-based runners |

### Agent Filesystem

| Setting | Default | Description |
|---------|---------|-------------|
| `agent_fs_root` | `"/data"` | Root directory for agent file operations |

**Important**: Files written to `agent_fs_root` persist across sandbox restarts. Files written elsewhere (e.g., `/tmp`) are lost on restart.

---

## Common Configuration Scenarios

### Scenario 1: Low-Cost Development

Minimize costs while developing and testing:

```bash
# Set via environment variables
export SANDBOX_CPU=0.5
export SANDBOX_MEMORY=1024
export SANDBOX_IDLE_TIMEOUT=120  # 2 minutes - shut down quickly when idle

# Or in .env file
SANDBOX_CPU=0.5
SANDBOX_MEMORY=1024
SANDBOX_IDLE_TIMEOUT=120
```

**Trade-offs**: Lower resources may slow tool execution; short idle timeout means more cold starts.

### Scenario 2: High-Performance Production

Optimize for low latency and reliability:

```bash
export SANDBOX_CPU=2.0
export SANDBOX_MEMORY=4096
export SANDBOX_IDLE_TIMEOUT=1800  # 30 minutes - stay warm longer
export SANDBOX_TIMEOUT=86400      # 24 hours max lifetime
```

**Trade-offs**: Higher costs; sandbox stays running even without traffic.

### Scenario 3: Batch Processing / CI

For one-off executions where cold start doesn't matter:

```bash
# Use the short-lived pattern instead of the service pattern
modal run -m agent_sandbox.app::run_agent_remote --question "Process this data"
```

No configuration needed - the sandbox terminates after each run.

### Scenario 4: Multiple Isolated Environments

Running separate dev/staging/prod sandboxes:

```bash
# Development
export SANDBOX_NAME="agent-sandbox-dev"
export PERSIST_VOL_NAME="agent-sandbox-dev-vol"

# Staging
export SANDBOX_NAME="agent-sandbox-staging"
export PERSIST_VOL_NAME="agent-sandbox-staging-vol"

# Production
export SANDBOX_NAME="agent-sandbox-prod"
export PERSIST_VOL_NAME="agent-sandbox-prod-vol"
```

---

## Image Configuration

The Modal container image is built in `agent_sandbox/app.py` via `_base_anthropic_sdk_image()`. The default image includes:

| Component | Version | Purpose |
|-----------|---------|---------|
| Python | 3.11 | Runtime (Debian slim base) |
| Claude Agent SDK | Latest | AI agent framework |
| FastAPI | Latest | HTTP framework |
| uvicorn | Latest | ASGI server |
| Node.js | 20.x | Required for `@anthropic-ai/claude-agent-sdk` |
| uv | Latest | Fast Python package installer |

### Adding Dependencies

To add Python packages, modify `_base_anthropic_sdk_image()` in `agent_sandbox/app.py`:

```python
def _base_anthropic_sdk_image() -> modal.Image:
    return (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install("your-package-here")  # Add your package
        # ... rest of the image definition
    )
```

To add system packages:

```python
.apt_install("your-system-package")
```

---

## Modal Secrets Management

### Creating Secrets

```bash
# Create a secret with one key
modal secret create anthropic-secret ANTHROPIC_API_KEY=sk-ant-...

# Create a secret with multiple keys
modal secret create my-secret KEY1=value1 KEY2=value2
```

### Listing Secrets

```bash
modal secret list
```

### Using Additional Secrets

To add more secrets to the application, modify `get_modal_secrets()` in `agent_sandbox/config/settings.py`:

```python
def get_modal_secrets() -> List[modal.Secret]:
    return [
        modal.Secret.from_name(
            "anthropic-secret",
            required_keys=["ANTHROPIC_API_KEY"]
        ),
        # Add more secrets here
        modal.Secret.from_name(
            "my-other-secret",
            required_keys=["API_KEY", "API_SECRET"]
        ),
    ]
```

---

## Troubleshooting Configuration Issues

### "Secret not found" Error

```
modal.exception.NotFoundError: Secret 'anthropic-secret' not found
```

**Solution**: Create the secret:
```bash
modal secret create anthropic-secret ANTHROPIC_API_KEY=your-key
```

### Sandbox Runs Out of Memory

Symptoms: Sandbox crashes; "OOMKilled" in logs.

**Solution**: Increase memory allocation:
```bash
export SANDBOX_MEMORY=4096
```

### Slow Response Times

Symptoms: First request after idle takes 10-30 seconds.

**Cause**: Cold start - sandbox was terminated due to idle timeout.

**Solutions**:
1. Increase idle timeout: `export SANDBOX_IDLE_TIMEOUT=1800`
2. Send periodic health checks to keep sandbox warm
3. Accept cold starts (appropriate for low-traffic use cases)

### Configuration Not Taking Effect

**Check order of precedence**:
1. Environment variables override `.env` file
2. `.env` file overrides defaults in `settings.py`
3. Modal Secrets are loaded at runtime, not build time

**Verify current configuration**:
```python
from agent_sandbox.config.settings import Settings
settings = Settings()
print(f"CPU: {settings.sandbox_cpu}")
print(f"Memory: {settings.sandbox_memory}")
print(f"Idle timeout: {settings.sandbox_idle_timeout}")
```

---

## Related Documentation

- [Architecture Overview](./architecture.md) - How components work together
- [Modal Secrets Documentation](https://modal.com/docs/guide/secrets) - Official Modal secrets guide
- [Modal Resource Configuration](https://modal.com/docs/guide/resources) - CPU, memory, and GPU allocation
