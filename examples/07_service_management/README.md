# Service Management

Demonstrates sandbox lifecycle management operations.

## Operations

| Function | Description |
|----------|-------------|
| `terminate_service_sandbox` | Stop sandbox, flush writes to volume |
| `snapshot_service` | Capture filesystem state |
| `tail_logs` | View recent sandbox logs |

## Usage

```bash
./run.sh
```

## When to Use

### Terminate Sandbox

Use when you need to:
- Ensure files are persisted to volume
- Force a fresh sandbox on next request
- Clean up resources

```bash
uv run modal run -m agent_sandbox.app::terminate_service_sandbox
```

### Snapshot Service

Use when you need to:
- Capture current state for debugging
- Create a checkpoint before changes

```bash
uv run modal run -m agent_sandbox.app::snapshot_service
```

## Sandbox Lifecycle

```
Request arrives
    ↓
Sandbox exists? ─── No ──→ Create new sandbox
    ↓ Yes                      ↓
    ↓                    Start uvicorn service
    ↓                          ↓
    └─────────────────→ Process request
                               ↓
                    Idle timeout (10 min)
                        or explicit terminate
                               ↓
                    Sandbox terminates
                               ↓
                    Writes flushed to volume
```

## Configuration

Timeouts are configured in `agent_sandbox/config/settings.py`:

- `sandbox_timeout`: Max lifetime (default: 12 hours)
- `sandbox_idle_timeout`: Shutdown after idle (default: 10 minutes)
