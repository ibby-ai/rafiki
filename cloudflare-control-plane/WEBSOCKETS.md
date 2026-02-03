# WebSocket Events & Hibernation API

This document describes the WebSocket implementation using Cloudflare's Durable Objects WebSocket Hibernation API, event types, and connection management.

## Overview

The control plane uses WebSockets for real-time, bidirectional communication with clients. We implement two types of WebSocket connections:

1. **SessionAgent WS**: Per-session streaming for query execution
2. **EventBus WS**: Multi-session event fan-out for multiplayer/notifications

## Cloudflare WebSocket Hibernation API

### Why Hibernation API?

The Durable Objects WebSocket Hibernation API provides:

- **Memory efficiency**: WebSocket connections don't consume memory when idle
- **Automatic reconnection**: Connections survive DO hibernation
- **Event-driven**: Messages trigger DO wake-up only when needed
- **Scalability**: Support thousands of concurrent connections per DO

### Key Differences from Standard WebSocket API

| Feature             | Standard API            | Hibernation API                                      |
| ------------------- | ----------------------- | ---------------------------------------------------- |
| Connection handling | `ws.addEventListener()` | `ctx.acceptWebSocket()` + handler methods            |
| Memory usage        | Always in memory        | Hibernates when idle                                 |
| Event handlers      | Event listeners         | Class methods (`webSocketMessage`, `webSocketClose`) |
| Accept pattern      | `ws.accept()`           | `this.ctx.acceptWebSocket(server)`                   |
| Max connections     | ~1000 per DO            | ~10,000+ per DO                                      |

### Implementation Pattern

```typescript
import { DurableObject } from "cloudflare:workers";

export class MyDurableObject extends DurableObject {
  private connections: Set<WebSocket> = new Set();

  async fetch(request: Request): Promise<Response> {
    // Check for WebSocket upgrade
    if (request.headers.get("Upgrade") === "websocket") {
      const pair = new WebSocketPair();
      const [client, server] = Object.values(pair);

      // Accept using Hibernation API
      this.ctx.acceptWebSocket(server);
      this.connections.add(server);

      // Send initial message
      server.send(JSON.stringify({ type: "connection_ack" }));

      return new Response(null, {
        status: 101,
        webSocket: client,
      });
    }

    // Handle HTTP requests
    // ...
  }

  // Hibernation API handlers
  async webSocketMessage(
    ws: WebSocket,
    message: string | ArrayBuffer
  ): Promise<void> {
    const data =
      typeof message === "string" ? message : new TextDecoder().decode(message);
    const msg = JSON.parse(data);

    // Handle message
    // ...
  }

  async webSocketClose(
    ws: WebSocket,
    code: number,
    reason: string,
    wasClean: boolean
  ): Promise<void> {
    this.connections.delete(ws);
    console.log(`Connection closed: ${code} ${reason}`);
  }

  async webSocketError(ws: WebSocket, error: unknown): Promise<void> {
    this.connections.delete(ws);
    console.error("WebSocket error:", error);
  }
}
```

## WebSocket Message Types

All WebSocket messages follow this base structure:

```typescript
interface WebSocketMessage {
  type: string; // Message type
  session_id: string; // Associated session (empty for EventBus)
  timestamp: number; // Unix timestamp in milliseconds
  data: unknown; // Type-specific payload
}
```

---

## SessionAgent WebSocket Events

### Server → Client Events

#### 1. Connection Acknowledgment

Sent immediately after WebSocket connection is established.

```json
{
  "type": "connection_ack",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "status": "idle"
  }
}
```

---

#### 2. Session Update

Sent when session state changes (status, prompt, queue).

```json
{
  "type": "session_update",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "status": "executing",
    "current_prompt": "What is the capital of Canada?",
    "queue_length": 2
  }
}
```

**Status values:**

- `idle`: No active execution
- `executing`: Currently processing a query
- `waiting_approval`: Waiting for user approval (tool use)
- `error`: Execution failed

---

#### 3. Query Start

Sent when query execution begins.

```json
{
  "type": "query_start",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "question": "What is the capital of Canada?",
    "agent_type": "default"
  }
}
```

---

#### 4. Assistant Message

Sent as the agent generates text responses.

```json
{
  "type": "assistant_message",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "content": "The capital of Canada is Ottawa.",
    "partial": false
  }
}
```

Note: `assistant_message.data.content` is plain text extracted from assistant content blocks.

**Fields:**

- `content`: Message text
- `partial`: always `false` in the current implementation

---

#### 5. Tool Use

Sent when the agent invokes a tool.

```json
{
  "type": "tool_use",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "tool_use_id": "toolu_abc123",
    "name": "Read",
    "input": {
      "path": "/path/to/file.txt"
    }
  }
}
```

---

#### 6. Tool Result

Sent when a tool execution completes.

```json
{
  "type": "tool_result",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "tool_use_id": "toolu_abc123",
    "content": "File contents here...",
    "is_error": false
  }
}
```

---

#### 7. Query Complete

Sent when query execution finishes.

```json
{
  "type": "query_complete",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "messages": [
      {
        "role": "user",
        "content": [{ "type": "text", "text": "..." }]
      },
      {
        "role": "assistant",
        "content": [{ "type": "text", "text": "..." }]
      }
    ],
    "duration_ms": 1234,
    "summary": {
      "text": "The capital of Canada is Ottawa.",
      "is_complete": true
    }
  }
}
```

---

#### 8. Query Error

Sent when query execution fails.

```json
{
  "type": "query_error",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "error": "Tool execution failed: File not found"
  }
}
```

---

#### 9. Prompt Queued

Sent when a follow-up prompt is queued.

```json
{
  "type": "prompt_queued",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "prompt_id": "prompt-uuid",
    "queue_length": 3
  }
}
```

---

#### 10. Execution State

Emitted for system/result/unknown SSE events or other execution signals. Payload is a passthrough.

```json
{
  "type": "execution_state",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "elapsed_ms": 5000,
    "tool_calls": 3,
    "tokens_used": 1234
  }
}
```

---

### Client → Server Events

#### 1. Ping

Keep-alive message to maintain connection.

```json
{
  "type": "ping",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {}
}
```

**Response:**

```json
{
  "type": "pong",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {}
}
```

---

#### Planned Client Events (TODO, Not Yet Implemented)

- `stop` (request execution stop)
- `approve_tool` (permission-gated tool approvals)

---

## EventBus WebSocket Events

### Connection

```
wss://worker.example.com/ws?user_id=user-123&session_ids=sess_abc,sess_def
```

**Query Parameters:**

- `user_id`: Filter events for this user
- `tenant_id`: Filter events for this tenant
- `session_ids`: Comma-separated list of sessions to subscribe to

---

### Server → Client Events

#### 1. Connection Acknowledgment

```json
{
  "type": "connection_ack",
  "session_id": "",
  "timestamp": 1234567890000,
  "data": {
    "connection_id": "conn-uuid",
    "session_ids": ["sess_abc", "sess_def"]
  }
}
```

---

#### 2. Broadcast Events

All events from SessionAgent DOs are broadcast to relevant EventBus connections.

```json
{
  "type": "session_update",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "status": "executing",
    "user_id": "user-456"
  }
}
```

**Filtering:**

- Events are filtered by `session_ids` in connection metadata
- Only subscribed sessions are forwarded to client

---

#### 3. Presence Update (Planned, TODO)

Sent when users join/leave sessions (not emitted yet).

**Planned (TODO, Not Yet Implemented):**

```json
{
  "type": "presence_update",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "users_online": ["user-123", "user-456"],
    "user_joined": "user-456"
  }
}
```

---

### Client → Server Events

#### 1. Subscribe to Session (Planned, TODO)

```json
{
  "type": "subscribe_session",
  "session_id": "",
  "timestamp": 1234567890000,
  "data": {
    "session_id": "sess_xyz"
  }
}
```

Response acknowledgments are not emitted in the current implementation.

---

#### 2. Unsubscribe from Session (Planned, TODO)

```json
{
  "type": "unsubscribe_session",
  "session_id": "",
  "timestamp": 1234567890000,
  "data": {
    "session_id": "sess_xyz"
  }
}
```

---

## Connection Management

### Connection Lifecycle

```
Client                 Worker                 SessionAgent DO
  │                      │                          │
  ├──WebSocket Upgrade─→│                          │
  │                      ├──Forward Upgrade────────→│
  │                      │                          │
  │                      │←─101 Switching Protocols─┤
  │←─101 Switching ─────┤                          │
  │   Protocols          │                          │
  │                      │                          │
  │←─connection_ack─────────────────────────────────┤
  │                      │                          │
  │──ping──────────────────────────────────────────→│
  │←─pong───────────────────────────────────────────┤
  │                      │                          │
  │←─session_update─────────────────────────────────┤
  │←─assistant_message──────────────────────────────┤
  │                      │                          │
  │──close─────────────────────────────────────────→│
  │←─close──────────────────────────────────────────┤
```

---

### Reconnection Strategy

**Client-side:**

```typescript
class AgentWebSocket {
  private ws: WebSocket | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;

  connect(sessionId: string): void {
    this.ws = new WebSocket(`wss://worker.example.com/query_stream`);

    this.ws.onopen = () => {
      console.log("Connected");
      this.reconnectAttempts = 0;

      // Send initial query or resume
      this.ws?.send(
        JSON.stringify({
          question: "...",
          session_id: sessionId,
        })
      );
    };

    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      this.handleMessage(msg);
    };

    this.ws.onclose = (event) => {
      console.log("Disconnected:", event.code, event.reason);

      if (this.reconnectAttempts < this.maxReconnectAttempts) {
        const delay = Math.min(
          1000 * Math.pow(2, this.reconnectAttempts),
          30000
        );
        console.log(`Reconnecting in ${delay}ms...`);

        setTimeout(() => {
          this.reconnectAttempts++;
          this.connect(sessionId);
        }, delay);
      }
    };

    this.ws.onerror = (error) => {
      console.error("WebSocket error:", error);
    };
  }

  // Send ping every 30 seconds
  startPingInterval(): void {
    setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(
          JSON.stringify({
            type: "ping",
            session_id: "",
            timestamp: Date.now(),
            data: {},
          })
        );
      }
    }, 30000);
  }
}
```

---

### Connection Limits

**Per DO:**

- SessionAgent DO: ~1,000 concurrent WebSocket connections
- EventBus DO: ~10,000 concurrent WebSocket connections

**Rate Limiting (Planned, TODO):**

```typescript
// Limit new connections per user
const connectionKey = `conn:${userId}:${Math.floor(Date.now() / 60000)}`;
const connectionCount = (await env.SESSION_CACHE.get(connectionKey)) || "0";

if (parseInt(connectionCount) >= 10) {
  return new Response("Too many connections", { status: 429 });
}

await env.SESSION_CACHE.put(
  connectionKey,
  (parseInt(connectionCount) + 1).toString(),
  { expirationTtl: 60 }
);
```

---

## Modal SSE → WebSocket Bridge

### Converting SSE to WebSocket

**Modal Backend (SSE):**

```
event: assistant
data: {"content": "Hello"}

event: tool_use
data: {"name": "Read", "input": {...}}

event: done
data: {"session_id": "sess_abc"}
```

**SessionAgent DO (Bridge):**

- SSE is internal only (Modal → SessionAgent).
- WebSocket is the external contract (Client → Worker → SessionAgent).
- Assistant content blocks are parsed into `assistant_message`, `tool_use`, or `tool_result`.
- System/result/unknown SSE events are surfaced as `execution_state`.

```typescript
async function bridgeSSEToWebSocket(
  sseUrl: string,
  websocket: WebSocket
): Promise<void> {
  const response = await fetch(sseUrl, {
    headers: {
      "Accept": "text/event-stream",
      "X-Internal-Auth": await this.generateInternalAuthToken()
    }
  });

  if (!response.body) {
    throw new Error("No response body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();

    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    let currentEvent: string | null = null;
    let currentData: string | null = null;

    for (const line of lines) {
      if (line.startsWith("event:")) {
        currentEvent = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        currentData = line.slice(5).trim();
      } else if (line === "" && currentEvent && currentData) {
        // Parse SSE event and convert to WebSocket message
        const sseData = JSON.parse(currentData);

        const wsMessage: WebSocketMessage = {
          type: this.mapSSEEventToWSType(currentEvent),
          session_id: this.sessionState?.session_id || "",
          timestamp: Date.now(),
          data: sseData
        };

        // Send to WebSocket
        websocket.send(JSON.stringify(wsMessage));

        // Also broadcast to EventBus
        await this.broadcastToEventBus(wsMessage);

        currentEvent = null;
        currentData = null;
      }
    }
  }
}

private mapSSEEventToWSType(sseEvent: string): string {
  const mapping: Record<string, string> = {
    "assistant": "assistant_message",
    "tool_use": "tool_use",
    "tool_result": "tool_result",
    "done": "query_complete",
    "error": "query_error"
  };

  return mapping[sseEvent] || "execution_state";
}
```

---

## Error Handling

### WebSocket Error Codes

| Code | Name             | Description                    |
| ---- | ---------------- | ------------------------------ |
| 1000 | Normal Closure   | Clean disconnect               |
| 1001 | Going Away       | Client/server shutting down    |
| 1003 | Unsupported Data | Invalid message format         |
| 1008 | Policy Violation | Auth failed or rate limit      |
| 1011 | Internal Error   | Server error during processing |

### Error Response Format

```json
{
  "type": "error",
  "session_id": "sess_abc123",
  "timestamp": 1234567890000,
  "data": {
    "code": "internal_error",
    "message": "Failed to execute query",
    "retry_after": 5000
  }
}
```

---

## Testing WebSockets

### Using `wscat`

```bash
# Install wscat
npm install -g wscat

# Connect to SessionAgent
wscat -c "wss://worker.example.com/query_stream" \
  -H "Authorization: Bearer <token>"  # accepted but not enforced yet

# Send query
> {"question": "What is the capital of Canada?", "session_id": "sess_abc"}

# Receive events
< {"type":"connection_ack","session_id":"sess_abc",...}
< {"type":"query_start","session_id":"sess_abc",...}
< {"type":"assistant_message","session_id":"sess_abc",...}
< {"type":"query_complete","session_id":"sess_abc",...}

# Send ping
> {"type":"ping","session_id":"sess_abc","timestamp":1234567890000,"data":{}}
< {"type":"pong","session_id":"sess_abc",...}

# Connect to EventBus
wscat -c "wss://worker.example.com/ws?user_id=user-123&session_ids=sess_abc"

# Receive broadcasts
< {"type":"session_update","session_id":"sess_abc",...}
```

### JavaScript Client

```typescript
const ws = new WebSocket("wss://worker.example.com/query_stream");

ws.onopen = () => {
  ws.send(
    JSON.stringify({
      question: "What is the capital of Canada?",
      session_id: "sess_abc123",
    })
  );
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  console.log("Received:", msg.type, msg.data);

  if (msg.type === "query_complete") {
    console.log("Query finished:", msg.data.messages);
  }
};

ws.onerror = (error) => {
  console.error("WebSocket error:", error);
};

ws.onclose = (event) => {
  console.log("Connection closed:", event.code, event.reason);
};
```

---

## Performance Considerations

### Message Batching

For high-throughput scenarios, batch messages to reduce overhead:

```typescript
private messageBatch: WebSocketMessage[] = [];
private batchTimer: number | null = null;

private queueMessage(msg: WebSocketMessage): void {
  this.messageBatch.push(msg);

  if (!this.batchTimer) {
    this.batchTimer = setTimeout(() => {
      this.flushBatch();
    }, 100) as unknown as number; // Batch every 100ms
  }
}

private flushBatch(): void {
  if (this.messageBatch.length === 0) return;

  const batch = {
    type: "batch",
    session_id: this.sessionState?.session_id || "",
    timestamp: Date.now(),
    data: { messages: this.messageBatch }
  };

  for (const ws of this.webSockets) {
    ws.send(JSON.stringify(batch));
  }

  this.messageBatch = [];
  this.batchTimer = null;
}
```

### Compression

Enable WebSocket compression in Cloudflare:

```typescript
// In wrangler.jsonc
{
  "websocket": {
    "compression": true
  }
}
```

### Memory Management

Clean up stale connections periodically:

```typescript
async alarm(): Promise<void> {
  const now = Date.now();
  const staleThreshold = 5 * 60 * 1000; // 5 minutes

  for (const [connId, info] of this.connectionInfo) {
    if (now - info.last_ping_at > staleThreshold) {
      const ws = this.connections.get(connId);
      if (ws) {
        ws.close(1000, "Connection timeout");
      }
      this.connections.delete(connId);
      this.connectionInfo.delete(connId);
    }
  }

  // Schedule next cleanup
  await this.ctx.storage.setAlarm(Date.now() + 60000); // 1 minute
}
```
