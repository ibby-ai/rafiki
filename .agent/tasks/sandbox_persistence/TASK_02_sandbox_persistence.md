---
task_id: 02
plan_id: PLAN_sandbox_persistence
plan_file: ../../plans/sandbox_persistence/PLAN_sandbox_persistence.md
title: Add Dict registry and sandbox naming/tags
phase: Phase 2 - Persistence & Discovery
---

### Edit Targets
- `main.py`: below CORS block, add constants and `modal.Dict.from_name("sandbox-sessions", create_if_missing=True)`.
- Add `SANDBOX_NAME`, `SERVICE_PORT`, `PERSIST_VOL_NAME`, `ENFORCE_CONNECT_TOKEN`.

### Changes
- In `get_or_start_background_sandbox`, first attempt `modal.Sandbox.from_name(app.name, SANDBOX_NAME)` and reuse tunnels.
- On creation, set `name=SANDBOX_NAME` and `tags={"role":"service","app":app.name,"port": str(SERVICE_PORT)}`.
- Persist metadata in the Dict when ready: id, url, volume, timestamps, tags, status.

### Acceptance Criteria
- Repeated calls across workers reuse the same sandbox by name.
- Registry contains entry keyed by `SANDBOX_NAME` with URL and object id.
