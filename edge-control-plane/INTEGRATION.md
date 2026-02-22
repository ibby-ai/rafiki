# Integration Mapping: Cloudflare Worker/DOs <-> Modal Backend

This document describes the current integration behavior in the Phase 3 Cloudflare-first architecture.

## Scope and Source of Truth

- Worker routing: `edge-control-plane/src/index.ts`
- Session state + queue behavior: `edge-control-plane/src/durable-objects/SessionAgent.ts`
- Event fan-out behavior: `edge-control-plane/src/durable-objects/EventBus.ts`
- Public API contracts: `edge-control-plane/API.md`
- Architecture context: `docs/design-docs/cloudflare-hybrid-architecture.md`

## Responsibility Matrix (Current)

| Capability | Cloudflare Layer | Modal Layer |
| --- | --- | --- |
| Public HTTP/WebSocket entry | Worker | No |
| Client auth (`Authorization` / `token`) | Worker (`sessionAuth.ts`) | No |
| Session key caching (`SESSION_CACHE`) | Worker | No |
| Session state + messages + prompt queue | SessionAgent DO (SQLite) | No |
| Event fan-out / presence | EventBus DO | No |
| Edge rate limiting | Worker (`RATE_LIMITER` binding) | No |
| Query execution (`/query`, `/query_stream`) | Worker/DO proxy | Modal executes agent runs |
| Job queue + status persistence | Worker proxy | Modal (`/submit`, `/jobs/*`) |
| Internal service auth (`X-Internal-Auth`) | Worker/DO generate token | Modal verifies token |

## Endpoint Coverage (Current)

### Worker public endpoints

- `GET /health`
- `POST /query`
- `GET /query_stream` (WebSocket upgrade)
- `POST /submit`
- `GET/DELETE /jobs/{job_id}`
- `GET /jobs/{job_id}/artifacts`
- `GET /jobs/{job_id}/artifacts/{path}`
- `POST/GET/PATCH/DELETE /schedules...`
- `GET/POST/DELETE /session/{session_id}/queue`
- `DELETE /session/{session_id}/queue/{prompt_id}`
- `GET /session/{session_id}/state`
- `GET /session/{session_id}/messages`
- `POST /session/{session_id}/stop`
- `GET /ws` and `GET /events` (WebSocket upgrade)

### Modal backend endpoints used by Worker/DOs

- `POST /query` (via SessionAgent DO)
- `POST /query_stream` (via SessionAgent DO, SSE)
- `POST /session/{session_id}/stop` (via SessionAgent DO)
- `POST /submit` (via Worker)
- `GET/DELETE /jobs/{job_id}` (via Worker)
- `GET /jobs/{job_id}/artifacts...` (via Worker)
- `GET/POST/PATCH/DELETE /schedules...` (via Worker)

## Auth and Identity Flow

### 1) Client -> Worker

- Public endpoints require session tokens (except `GET /health`).
- Worker accepts token via:
  - `Authorization: Bearer <token>`
  - `token=<token>` for WebSocket query params
- Token validation enforces:
  - valid payload/signature
  - valid `issued_at`/`expires_at`
  - authorized `session_id` (if token has `session_ids`)

### 2) Session resolution and KV behavior

- Worker resolves session in this order:
  1. explicit `session_id`
  2. `session_key` -> `SESSION_CACHE` lookup
  3. create new UUID session
- KV key format:
  - `session_key:<scope>:<session_key>`
  - scope precedence: `tenant_id` -> `user_id` -> `anonymous`
- TTL controlled by `SESSION_KEY_TTL_SECONDS` (default 30 days).

### 3) Worker/DO -> Modal

- Worker and SessionAgent DO sign `X-Internal-Auth` tokens using `INTERNAL_AUTH_SECRET`.
- Modal verifies `X-Internal-Auth` on non-health endpoints.
- Secret value must match between:
  - Worker secret `INTERNAL_AUTH_SECRET`
  - Modal secret `internal-auth-secret` (`INTERNAL_AUTH_SECRET`)

## Query Flow

### Edge rate limiting

- Worker enforces rate limiting for `/query`, `/query_stream`, `/submit`, and event bus routes.
- Binding: `RATE_LIMITER` in `edge-control-plane/wrangler.jsonc`.
- Behavior: Worker returns HTTP `429` with `{\"ok\":false,\"error\":\"Rate limit exceeded\"}`.

### `/query` (sync)

```text
Client -> Worker /query -> SessionAgent DO /query -> Modal /query -> Worker response
```

Current behaviors:

- SessionAgent marks session status `executing` then `idle`.
- SessionAgent stores user/assistant messages in DO SQLite.
- SessionAgent emits `session_update`, `query_start`, and `query_complete`/`query_error` events.

### `/query_stream` (WebSocket)

```text
Client WS -> Worker /query_stream -> SessionAgent DO WS -> Modal /query_stream (SSE)
```

Current behaviors:

- SessionAgent bridges SSE events to WebSocket events.
- SessionAgent forwards uncategorized events as `execution_state`.
- SessionAgent publishes events to EventBus DO for fan-out.

## Queue Flow (SessionAgent DO)

Queue endpoints are exposed via Worker paths under `/session/{session_id}/queue`.

### Enqueue

```text
Client -> Worker POST /session/{id}/queue -> SessionAgent DO -> SQLite prompt_queue insert
```

- Enforced queue max (`MAX_QUEUED_PROMPTS_PER_SESSION`, default 10).
- Expiry pruning based on `PROMPT_QUEUE_ENTRY_EXPIRY_SECONDS` (default 3600).
- Broadcasts `prompt_queued` event.

### Inspect/Clear/Remove

- `GET /session/{id}/queue`: list queued prompts with positions and `expires_at`
- `DELETE /session/{id}/queue`: clear all queued prompts
- `DELETE /session/{id}/queue/{prompt_id}`: remove one queued prompt

### Drain behavior

- After a successful non-streaming `/query`, SessionAgent asynchronously drains the queue.
- Each queued prompt is executed in FIFO order (priority, then queued time).
- Drain stops on first execution error.

## Session State and Messages

- `GET /session/{id}/state` returns DO-managed session metadata.
- `GET /session/{id}/messages` returns persisted user/assistant messages.
- `POST /session/{id}/stop` forwards stop request to Modal and updates DO state to `idle`.

## Job/Event Integration

### Jobs

- Worker `POST /submit` creates `job_id`, forwards to Modal `/submit`, returns `{ok, job_id}`.
- Worker `GET/DELETE /jobs/{job_id}` proxies to Modal.
- Worker `GET /jobs/{job_id}` publishes `job_status` to EventBus on successful reads.

### EventBus

- Worker routes `/ws` and `/events` to EventBus DO.
- EventBus supports filtered fan-out by `session_ids`, `user_ids`, `tenant_ids`.
- Presence updates are broadcast on connect/disconnect/subscription changes.

## Operational Notes

For executable setup, token generation, and E2E verification commands, use:

- `docs/references/runbooks/cloudflare-modal-e2e.md` (canonical)
