# Generated DB Schema

- Last generated: 2026-02-21
- Source of truth: `edge-control-plane/src/durable-objects/SessionAgent.ts` (`initializeSchema()`)
- Runtime: Cloudflare Durable Object SQLite (`this.ctx.storage.sql`)

## Schema Snapshot

### `session_metadata`
```sql
CREATE TABLE IF NOT EXISTS session_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
)
```

### `messages`
```sql
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at INTEGER NOT NULL
)
```

### `prompt_queue`
```sql
CREATE TABLE IF NOT EXISTS prompt_queue (
  id TEXT PRIMARY KEY,
  question TEXT NOT NULL,
  agent_type TEXT NOT NULL,
  user_id TEXT,
  queued_at INTEGER NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0
)
```

### `execution_state`
```sql
CREATE TABLE IF NOT EXISTS execution_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL
)
```

## Regeneration Method

1. Inspect the current schema block in source:
   ```bash
   source .venv/bin/activate
   sed -n '50,110p' edge-control-plane/src/durable-objects/SessionAgent.ts
   ```
2. Replace the SQL snapshot in this file with the latest schema.
3. Update the `Last generated` date.
