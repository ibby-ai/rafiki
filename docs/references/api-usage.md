# API Usage Guide

This guide documents the client-facing HTTP contract and streaming behavior.
Cloudflare Worker URLs are the only supported public/client entrypoint; Modal gateway URLs are internal/operator-only surfaces behind internal auth.

## Base URLs

### Public API (Cloudflare Worker)

`https://<your-worker>.workers.dev`

### Internal Modal Gateway

`https://<org>--<app>-http-app.modal.run`

Internal endpoints require `X-Internal-Auth` (except health) and should not be used as direct client traffic.

## Core Endpoints

### `GET /health`

Health probe.

### `POST /query`

Non-streaming query execution.

Request body:

```json
{
  "question": "Your prompt",
  "agent_type": "default",
  "session_id": null,
  "session_key": null,
  "fork_session": false
}
```

Error responses:

- `400` when the request body is not valid JSON or does not include a string `question`.
- `409` when a session is already executing.
- `429` when session budget rails deny pre-flight execution (`request_budget_exceeded` or `cost_budget_exceeded`).
- `500` when execution fails after request validation.

Response envelope (unchanged):

```json
{
  "ok": true,
  "messages": [
    {
      "type": "assistant",
      "content": [{ "type": "text", "text": "..." }],
      "model": "gpt-4.1"
    },
    {
      "type": "result",
      "subtype": "success",
      "duration_ms": 1234,
      "session_id": "...",
      "usage": {
        "requests": 1,
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150
      },
      "result": "..."
    }
  ],
  "summary": {
    "text": "...",
    "is_complete": true,
    "subtype": "success",
    "duration_ms": 1234,
    "session_id": "..."
  },
  "session_id": "..."
}
```

### `GET /query_stream`

Streaming query execution via WebSocket upgrade.

Public Cloudflare route uses WebSocket transport; internal controller uses SSE.

Expected public WebSocket message types include:

- `connection_ack`
- `query_start`
- `assistant_message`
- `execution_state`
- `query_complete`
- `query_error`

Actor scope for streaming queries is derived from the authenticated connection
context. WebSocket payloads must not include `session_id`, `session_key`,
`user_id`, or `tenant_id`.

### `GET /ws` or `GET /events`

WebSocket event-bus subscriptions for multi-session updates.

Authentication:

- `Authorization: Bearer <session_token>` header, or
- `token=<session_token>` query parameter

Supported subscription query parameters:

- `session_id`
- `session_ids` (comma-separated)
- `user_id`
- `tenant_id`

Expected public event-bus message types include:

- `connection_ack`
- `presence_update`
- `session_update`
- `job_submitted`
- `job_status`

## Session Resources

Only the documented `/state`, `/messages`, `/queue`, `/queue/{prompt_id}`, and
`/stop` session routes are public Worker contract surfaces. `GET /session/{session_id}`
and `/session/{session_id}/query` are blocked at the Worker edge and return `404`.
Methods outside the documented set return `405`.

### `GET /session/{session_id}/state`

Returns current DO-backed session metadata.

Response payload:

```json
{
  "ok": true,
  "state": {
    "session_id": "...",
    "session_key": "...",
    "user_id": "...",
    "tenant_id": "...",
    "created_at": 1234,
    "last_active_at": 1234,
    "status": "idle"
  }
}
```

### `GET /session/{session_id}/messages`

Returns persisted user/assistant message history for the session.

Response payload:

```json
{
  "ok": true,
  "messages": [
    {
      "id": "...",
      "session_id": "...",
      "role": "user",
      "content": [{ "type": "text", "text": "..." }],
      "created_at": 1234
    }
  ]
}
```

### `POST /session/{session_id}/queue`

Queue a prompt for sequential execution in the target session.

Actor scope is derived from the authenticated session context, not from client
request-body identity fields.

Request body:

```json
{
  "question": "Your prompt",
  "agent_type": "default"
}
```

Error responses:

- `400` when the request body is not valid JSON or does not include a string `question`.
- `429` when the session queue has reached its configured max size.
- `429` when session budget rails deny queue preflight (`request_budget_exceeded` or `cost_budget_exceeded`).

### `GET /session/{session_id}/queue`

Returns queue state, prompt positions, and expiry timestamps.

Response payload:

```json
{
  "ok": true,
  "session_id": "...",
  "is_executing": false,
  "queue_size": 1,
  "max_queue_size": 10,
  "prompts": [
    {
      "prompt_id": "...",
      "question": "...",
      "user_id": "...",
      "queued_at": 1234,
      "expires_at": 5678,
      "position": 1
    }
  ]
}
```

### `DELETE /session/{session_id}/queue`

Clears the session queue.

Response payload includes `ok`, `session_id`, `cleared_count`, and a human-readable `message`.

### `DELETE /session/{session_id}/queue/{prompt_id}`

Removes a single queued prompt.

Error responses:

- `404` when the `prompt_id` is not present in the queue.

### `POST /session/{session_id}/stop`

Stops an active run.

Request body:

```json
{
  "mode": "graceful",
  "reason": "optional"
}
```

Modes:

- `graceful`: after-turn cancellation
- `immediate`: immediate cancellation

Response payload mirrors the Modal stop contract, including `status`,
`requested_at`, `expires_at`, `reason`, and `requested_by`.

Additional behavior:

- `requested_by` is derived from the authenticated actor scope and is not
  accepted as a client-controlled request field.
- `400` when the request body is not valid JSON or fails the public stop schema.
- `502` when the Modal stop response fails the Worker runtime contract.

### `GET /session/{session_id}/stop`

Returns cancellation flag state.

Error responses:

- `502` when the Modal stop-status response fails the Worker runtime contract.

## Session Semantics

- New run returns a stable `session_id`.
- Reuse `session_id` to continue memory.
- Set `fork_session=true` to branch into a new `session_id` with inherited history.

## Background Jobs

### `POST /submit`

Queues a background job.

Error responses:

- `400` when the request body is not valid JSON or fails the public job schema.

### `GET /jobs/{job_id}`

Returns status + result/summary/metrics when complete.

Actor scope (session/user/tenant) is enforced for Worker-proxied reads.
If ownership precheck fails, Worker returns deterministic `403` with mismatch reason.

Additional Worker validation:

- `502` when the Modal job-status payload is malformed.
- `502` when the Modal payload omits `session_id`, or omits `user_id` / `tenant_id`
  required by the authenticated actor scope.

### `GET /jobs/{job_id}/artifacts`

Returns artifact manifest for a completed job.

Worker behavior:

- Performs ownership precheck (`session_id`, `user_id`, `tenant_id`) before proxying.
- Returns `403` on ownership mismatch.

### `GET /jobs/{job_id}/artifacts/{artifact_path}`

Artifact download requires Worker-minted scoped token (`X-Artifact-Access-Token`) with:

- `session_id`
- `job_id`
- `artifact_path`
- expiry and revocation controls

Additional behavior:

- Worker ownership precheck runs before token minting.
- malformed URL-encoded artifact paths return deterministic `400` with `Invalid artifact path encoding` (no downstream artifact fetch).

## Schedules

### `POST /schedules`

Creates a schedule owned by the authenticated session and actor scope.

Error responses:

- `400` when the request body is not valid JSON or fails the public schedule-create schema.

### `GET /schedules`

Lists schedules visible to the authenticated actor scope.

Additional Worker validation:

- `502` when the Modal backend returns a payload that does not match the public schedule-list contract.

### `GET /schedules/{schedule_id}`

Returns a single schedule resource.

Additional Worker validation:

- `502` when the Modal backend returns a payload that does not match the public schedule contract.

### `PATCH /schedules/{schedule_id}`

Updates a schedule resource.

Error responses:

- `400` when the request body is not valid JSON or fails the public schedule-update schema.

Browser preflight for this route explicitly allows `PATCH`.

Model list examples now use OpenAI IDs such as `gpt-4.1`.

## Auth

- Public worker endpoints: `Authorization: Bearer <session_token>`.
- Session-scoped routes that do not already carry `session_id` in the path or
  request body must provide `session_id` as a query parameter so the Worker can
  bind the request to an authorized session. Canonical examples in the current
  public contract are `/schedules?...` and `/jobs/{job_id}...` reads.
- Internal gateway/controller calls: `X-Internal-Auth`.
- Modal gateway -> sandbox controller calls: `X-Sandbox-Session-Auth` + `X-Sandbox-Id` (strict scoped-token-only, no legacy internal-auth fallback).
- Session authority contract for query forwarding: `X-Session-History-Authority: durable-object`.

Readiness behavior:

- Gateway startup probes controller `/health_check` and performs one bounded retry on timeout.
- Repeated startup failure fails deterministically (`Background sandbox startup failed after 2 attempts`).

## References

- [Controllers](../design-docs/controllers-background-service.md)
- [Configuration](./configuration.md)
- [OpenAI Agents Sessions Docs](https://openai.github.io/openai-agents-python/sessions/)
