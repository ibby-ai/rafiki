# File Persistence

Demonstrates how files written to `/data/` persist to Modal Volume across sandbox restarts.

## How It Works

1. Agent writes files to `/data/` inside the sandbox
2. `/data/` is mounted to Modal Volume `svc-runner-8001-vol`
3. When sandbox terminates, writes are flushed to the volume
4. Files survive across sandbox restarts

## Usage

```bash
./run.sh
```

## Key Concepts

### Persistent vs Ephemeral Storage

| Path | Persistence | Use Case |
|------|-------------|----------|
| `/data/` | Persistent (Modal Volume) | Generated code, outputs, state |
| `/tmp/` | Ephemeral | Temporary files, caches |
| `/root/app/` | Read-only | Application code |

### Volume Commands

```bash
# List files in volume
uv run modal volume ls svc-runner-8001-vol

# Download a file
uv run modal volume get svc-runner-8001-vol /hello.py ./hello.py

# Remove a file
uv run modal volume rm svc-runner-8001-vol /hello.py
```

## Important Notes

- Files are only synced to volume when the sandbox terminates
- Use `terminate_service_sandbox` to flush writes immediately
- Volume name is configured in `agent_sandbox/config/settings.py`
