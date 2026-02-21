# API Usage Guide

This guide documents the external HTTP contract and streaming behavior.

## Base URLs

### Public API (Cloudflare Worker)

`https://<your-worker>.workers.dev`

### Internal Modal Gateway

`https://<org>--<app>-http-app.modal.run`

Internal endpoints require `X-Internal-Auth` (except health).

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

### `POST /query_stream`

Streaming query execution.

Public Cloudflare route uses WebSocket transport; internal controller uses SSE.

SSE event names preserved:

- `assistant`
- `tool_use`
- `tool_result`
- `result`
- `done`
- `error`

Internal SSE example:

```text
event: assistant
data: {"type":"assistant","content":[{"type":"text","text":"..."}],"model":"gpt-4.1"}

event: tool_use
data: {"type":"assistant","content":[{"type":"tool_use","id":"...","name":"mcp__utilities__calculate","input":{"expression":"2+2"}}],"model":"gpt-4.1"}

event: tool_result
data: {"type":"assistant","content":[{"type":"tool_result","tool_use_id":"...","content":"Result: 4","is_error":false}],"model":"gpt-4.1"}

event: result
data: {"type":"result","subtype":"success","session_id":"..."}

event: done
data: {"text":"...","is_complete":true,"session_id":"..."}
```

### `POST /session/{session_id}/stop`

Stops an active run.

Request body:

```json
{
  "mode": "graceful",
  "reason": "optional",
  "requested_by": "optional"
}
```

Modes:

- `graceful`: after-turn cancellation
- `immediate`: immediate cancellation

### `GET /session/{session_id}/stop`

Returns cancellation flag state.

## Session Semantics

- New run returns a stable `session_id`.
- Reuse `session_id` to continue memory.
- Set `fork_session=true` to branch into a new `session_id` with inherited history.

## Background Jobs

### `POST /submit`

Queues a background job.

### `GET /jobs/{job_id}`

Returns status + result/summary/metrics when complete.

Model list examples now use OpenAI IDs such as `gpt-4.1`.

## Auth

- Public worker endpoints: `Authorization: Bearer <session_token>`.
- Internal gateway/controller calls: `X-Internal-Auth`.

## References

- [Controllers](../design-docs/controllers-background-service.md)
- [Configuration](./configuration.md)
- [OpenAI Agents Sessions Docs](https://openai.github.io/openai-agents-python/sessions/)
