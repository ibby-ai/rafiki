# Controllers: Background Service Architecture

This document explains the long-lived controller service in `modal_backend/api/controller.py`.

## Overview

The controller is a FastAPI app running inside a Modal sandbox. It executes OpenAI Agents runs and exposes internal endpoints used by the HTTP gateway.

| Controller | Location | Port | Purpose |
|---|---|---|---|
| Agent Runtime Controller | `modal_backend/api/controller.py` | 8001 | Execute `/query` and `/query_stream` |

## Responsibilities

- Execute non-streaming runs via `Runner.run_streamed(...)` collection flow.
- Execute streaming runs and emit SSE events.
- Persist/resume memory via `SQLiteSession(session_id, db_path=...)`.
- Support session forking by cloning prior session items into a new session ID.
- Track active runs for stop/status behavior.

## Start Path

The controller is launched by Modal with `uvicorn modal_backend.api.controller:app` and reached through an internal tunnel from the gateway app.

Gateway readiness behavior (`modal_backend/main.py`):

- probes controller readiness via `/health_check` using `service_timeout` (default `60s`)
- emits bounded timeout diagnostics (phase/attempt/sandbox/tunnel/poll context)
- performs one recycle+retry on readiness timeout
- fails deterministically after the second startup failure (`Background sandbox startup failed after 2 attempts`)

## Endpoints

### `GET /health_check`

Liveness check.

### `POST /query`

Executes a run, returns the compatibility envelope:

- `ok`
- `messages`
- `summary`
- `session_id`

### `POST /query_stream`

Executes a run and streams SSE events. Contract-parity event names are preserved:

- `assistant`
- `tool_use`
- `tool_result`
- `result`
- `done`
- `error`

### `POST /session/{session_id}/stop`

Stop active session with mode:

- `graceful`: sets stop flag and cancels run `after_turn`
- `immediate`: immediate cancellation via `cancel(mode="immediate")`

### `GET /session/{session_id}/status`

Returns active/stop-requested status for an active in-memory run.

## Session Model

- New request without `session_id`: generates a new UUID session ID.
- Resume request with `session_id`: reuses that SQLite session history.
- Resume with `fork_session=true`: creates a new UUID session and copies prior items.

Configured by `openai_session_db_path` in `modal_backend/settings/settings.py`.
At controller startup, if the configured DB path is not writable under runtime privilege-drop, the controller falls back to `/tmp/openai_agents_sessions.sqlite3` to preserve query availability.

### Memory Trimming and Compaction

Session memory growth is bounded by settings in `modal_backend/settings/settings.py`:

- `openai_session_max_items`
- `openai_session_compaction_keep_items`

Compaction is deterministic and runs when a session is acquired:

- if history size exceeds `openai_session_max_items`, retain newest `openai_session_compaction_keep_items` (or max-items when keep-items is unset)
- applied for resumed sessions and fork targets
- fork source history is not mutated during copy

## Streaming Adapter

The controller maps OpenAI stream items into existing wire shapes:

- assistant text item -> assistant message block
- tool call item -> `tool_use`
- tool output item -> `tool_result`
- final run metadata -> `result` and `done`

Serialization lives in `modal_backend/api/serialization.py`.

### Correlation Metadata

The controller resolves a stable `trace_id` per request and propagates it through:

- controller logs
- streamed assistant/result payloads
- SSE terminal events (`error`, `done`)

When available from OpenAI run metadata, `openai_trace_id` is propagated through:

- result payload metadata
- `/query` summary
- SSE `done` summary

This allows deterministic correlation between logs, traces, and client-visible events for a single run.

## Cancellation Semantics

The controller keeps `ACTIVE_CLIENTS` as:

- `session_id -> (RunResultStreaming, stop_event)`

A watcher task polls cancellation signals and applies `cancel(mode="after_turn")` for graceful stop requests.
Cancellation terminal payloads retain `trace_id` correlation, and successful terminal summaries can include `openai_trace_id` when present.

## Security

- Internal auth middleware requires `X-Internal-Auth` for non-health endpoints.
- Gateway -> sandbox forwarding requires scoped headers:
  - `X-Sandbox-Session-Auth`
  - `X-Sandbox-Id`
  with strict scoped-token-only validation (no legacy internal-auth fallback path).
- Optional connect token validation can be enforced through settings.
- Sandbox runtime receives Modal auth credentials via `modal-auth-secret` (when enabled) so in-sandbox Modal Dict/Queue/Volume operations can authenticate without reintroducing legacy gateway auth fallback paths.
