# Cloudflare Control Plane for Rafiki

This directory contains the Cloudflare Worker + Durable Objects implementation that serves as the control plane for the Rafiki system.

**Phase 3 status:** Cloudflare is the primary public API surface. Direct Modal gateway access is internal-only and requires `X-Internal-Auth`.

## Architecture

The control plane uses Cloudflare's edge infrastructure to provide:

- **Session Management**: Per-session Durable Objects with SQLite storage
- **Real-time Updates**: WebSocket-based event fan-out via EventBus DO
- **Edge Authentication**: Enforced token validation and rate limiting at the edge
- **Request Routing**: Intelligent routing to Modal backend sandboxes

## Project Structure

```
edge-control-plane/
├── src/
│   ├── index.ts                      # Worker entry point
│   ├── types.ts                      # TypeScript type definitions
│   └── durable-objects/
│       ├── session-agent.ts          # Per-session DO
│       └── event-bus.ts              # Real-time event bus DO
├── scripts/
│   └── generate-session-token.js     # Session token helper for E2E tests
├── wrangler.jsonc                    # Cloudflare configuration
├── package.json                      # Dependencies
├── tsconfig.json                     # TypeScript config
├── API.md                            # API documentation
├── AUTH.md                           # Authentication design
├── INTEGRATION.md                    # Modal integration mapping
└── WEBSOCKETS.md                     # WebSocket event specs
```

## Quick Start

Canonical E2E setup and verification sequence:

- [Canonical E2E Runbook](../docs/references/runbooks/cloudflare-modal-e2e.md)

### Prerequisites

1. **Cloudflare Account**: Sign up at https://cloudflare.com
2. **Wrangler CLI**: Install via `npm install -g wrangler`
3. **Node.js**: Version 20+ required (Ultracite/Biome tooling dependency)
4. **Modal Backend**: Must be deployed and accessible

### Installation

```bash
# Install dependencies
npm install

# Login to Cloudflare
wrangler login

# Generate TypeScript types
npm run types

# Optional: lint + format audit (current repo has baseline diagnostics)
npm run check
```

Optional fix pass (mutates files):

```bash
npm run fix
```

### Configuration

#### 1. Set Environment Variables

Keep the top-level Worker config production-safe and put local/dev values under `env.development`:

```jsonc
{
  "vars": {
    "MODAL_API_BASE_URL": "https://your-org--modal-backend-http-app.modal.run",
    "ENVIRONMENT": "production"
  },
  "env": {
    "development": {
      "vars": {
        "MODAL_API_BASE_URL": "https://your-org--modal-backend-http-app-dev.modal.run",
        "ENVIRONMENT": "development"
      }
    }
  }
}
```

Notes:
- `npm run dev` expands to `wrangler dev --env development`.
- Plain `npm run deploy` publishes the canonical public Worker with the top-level
  production Modal target.
- Named Wrangler environments do not inherit bindings automatically; keep the
  `development` environment's Durable Object, KV, and rate-limit bindings in
  sync with the checked-in `wrangler.jsonc` contract.
- The checked-in `development` environment uses explicit Durable Object
  `script_name` values (`rafiki-control-plane-development`) so local/dev state
  does not reuse the canonical public Worker objects.

#### 2. Create Secrets

```bash
# Internal authentication for the standard local E2E path
wrangler secret put INTERNAL_AUTH_SECRET
wrangler secret put SESSION_SIGNING_SECRET
```

Notes:

- The standard local `/health`, `/query`, `/query_stream`, queue, and state flow signs
  Worker -> Modal requests with `INTERNAL_AUTH_SECRET`.
- `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` are not consumed by the current canonical
  `edge-control-plane/src` E2E request path.

Generate secrets:

```bash
# Generate random secrets
openssl rand -hex 32  # For INTERNAL_AUTH_SECRET
openssl rand -hex 32  # For SESSION_SIGNING_SECRET
```

#### 3. Create KV Namespace

```bash
# Create KV namespace for session cache
wrangler kv:namespace create SESSION_CACHE

# Copy the ID to wrangler.jsonc
```

#### 4. Configure Rate Limiting Binding

Optional but recommended: add a `RATE_LIMITER` binding under
`unsafe.bindings` in `wrangler.jsonc` with the desired `limit` and `period`
values.

### Development

```bash
# Start local development server
npm run dev

# Test endpoints
curl http://localhost:8787/health

# Test WebSocket (use wscat)
npm install -g wscat
wscat -c "ws://localhost:8787/ws?user_id=test-user&token=<session_token>"
```

### Deployment

```bash
# Canonical public Worker deploy for proof / production ingress repair
npm run deploy

# Monitor logs
npm run tail

# View dashboard
wrangler dashboard
```

## API Endpoints

### Health Check

```bash
curl https://your-worker.workers.dev/health
```

### Query (Synchronous)

```bash
curl -X POST https://your-worker.workers.dev/query \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is the capital of Canada?",
    "session_id": "sess_abc123"
  }'
```

### Query (Streaming via WebSocket)

```javascript
const ws = new WebSocket(
  "wss://your-worker.workers.dev/query_stream?session_id=sess_abc123&token=<session_token>"
);

ws.onopen = () => {
  ws.send(
    JSON.stringify({
      question: "Explain quantum computing",
      session_id: "sess_abc123",
    })
  );
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  console.log(msg.type, msg.data);
};
```

### Job Submission

```bash
curl -X POST https://your-worker.workers.dev/submit \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Analyze this large dataset",
    "webhook": {
      "url": "https://example.com/webhook"
    }
  }'
```

### Event Bus (Multiplayer)

```javascript
const ws = new WebSocket(
  "wss://your-worker.workers.dev/ws?user_id=user-123&session_ids=sess_abc,sess_def&token=<session_token>"
);

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  // Receive updates from multiple sessions
  console.log(`Update from ${msg.session_id}:`, msg.data);
};
```

See [API.md](./API.md) for complete API documentation.

## Authentication

All public endpoints require `Authorization: Bearer <token>` (or `token=<token>`
for WebSocket connections). Phase 3 supports **session tokens only**.

### Session Tokens

```bash
TOKEN="$(node ./scripts/generate-session-token.js \
  --user-id user-123 \
  --tenant-id tenant-456 \
  --session-id sess_abc \
  --ttl-seconds 3600 \
  --secret "$SESSION_SIGNING_SECRET")"
```

Use it in requests:

```bash
curl -X POST http://localhost:8787/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is 2+2?","session_id":"sess_abc"}'
```

See [AUTH.md](./AUTH.md) for token format details and
[Canonical E2E Runbook](../docs/references/runbooks/cloudflare-modal-e2e.md)
for the full flow.

## WebSocket Events

### SessionAgent Events

- `connection_ack`: Connection established
- `session_update`: Status changed
- `query_start`: Query execution began
- `assistant_message`: Agent response (streaming)
- `tool_use`: Tool invoked
- `tool_result`: Tool result
- `query_complete`: Query finished
- `query_error`: Execution failed
- `prompt_queued`: Prompt queued for later execution
- `execution_state`: SSE events that don't map to explicit types

### EventBus Events

- `connection_ack`: Connected to event bus
- `presence_update`: User joined/left
- `job_submitted`: Job was queued via `/submit`
- `job_status`: Job status update emitted on `/jobs/{id}`
- Broadcasts all SessionAgent events to subscribed clients

See [WEBSOCKETS.md](./WEBSOCKETS.md) for complete event specifications.

## Durable Objects

### SessionAgent DO

**Purpose**: Per-session state management

**Storage**: SQLite database with tables:

- `session_metadata`: Session info (user, status, timestamps)
- `messages`: Message history
- `prompt_queue`: Queued prompts
- `execution_state`: Current execution state

**Endpoints**:

- `POST /query`: Execute query
- `POST /queue`: Queue prompt
- `GET /state`: Get session state
- `GET /messages`: Get message history
- `POST /stop`: Stop execution

**Lifecycle**: One DO per `session_id`, persists indefinitely

---

### EventBus DO

**Purpose**: Real-time event broadcasting

**Storage**: Connection metadata (user ID, session subscriptions, timestamps)

**Endpoints**:

- WebSocket upgrade: Connect for events
- `POST /broadcast`: Broadcast message (internal)
- `GET /connections`: List active connections
- `GET /presence`: Get online users

**Lifecycle**: One DO per `tenant_id` or `user_id`, auto-cleanup via alarms

---

## Integration with Modal

The control plane integrates with Modal backend via:

### 1. Internal Authentication

DO generates HMAC-signed tokens for Modal requests:

```typescript
const token = await generateInternalAuthToken(env);

fetch(modalUrl, {
  headers: { "X-Internal-Auth": token },
});
```

Modal validates tokens using the shared secret in `X-Internal-Auth`.

### 2. Request Proxying

- Query requests: DO → Modal `/query`
- Job submissions: DO → Modal `/submit`
- Artifacts: Worker → Modal `/jobs/{id}/artifacts`
- Internal rollout/status checks: trusted operator calls Modal `/service_info` (signed `X-Internal-Auth`) to inspect active generation, draining services, and rollout lock state.

### 3. SSE → WebSocket Bridge

Modal streams via SSE, DO converts to WebSocket:

```typescript
// Poll Modal SSE endpoint
const sseStream = await fetch(modalUrl + "/query_stream");

// Convert events to WebSocket messages
for await (const event of parseSSE(sseStream)) {
  const wsMessage = convertSSEToWS(event);
  websocket.send(JSON.stringify(wsMessage));
}
```

See [INTEGRATION.md](./INTEGRATION.md) for detailed integration mapping.

## Monitoring

### Cloudflare Analytics

View in Wrangler dashboard:

- Request volume and latency
- Error rate by endpoint
- DO invocation count
- WebSocket connection count

### Logs

```bash
# Tail logs in real-time
wrangler tail

# Filter by event type
wrangler tail --format json | jq 'select(.event == "query_start")'
```

### Custom Metrics

```typescript
// In your code
console.log(
  JSON.stringify({
    timestamp: Date.now(),
    event: "query_complete",
    duration_ms: 1234,
    session_id: "sess_abc",
  })
);
```

## Testing

### Unit Tests

```bash
# Run tests (when added)
npm test
```

### Integration Tests

```bash
# Test against local dev server
npm run dev

# In another terminal
curl http://localhost:8787/health
curl -X POST http://localhost:8787/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Test"}'
```

### Load Testing

```bash
# Use k6 or similar
k6 run load-test.js
```

## Troubleshooting

### Common Issues

#### 1. "Durable Object not found"

**Cause**: DO namespace not migrated

**Fix**:

```bash
npm run deploy
```

#### 2. "WebSocket connection failed"

**Cause**: Missing `Upgrade: websocket` header or CORS issue

**Fix**: Ensure client sends correct headers:

```javascript
new WebSocket("wss://...", {
  headers: { Upgrade: "websocket" },
});
```

#### 3. "Internal auth token invalid"

**Cause**: Secret mismatch between Cloudflare and Modal

**Fix**: Ensure same secret in both:

```bash
wrangler secret put INTERNAL_AUTH_SECRET
modal secret create internal-auth-secret INTERNAL_AUTH_SECRET=<same-value>
```

#### 4. "Rate limit exceeded"

**Cause**: Too many requests from same user/IP

**Fix**: Implement exponential backoff in client:

```javascript
async function retryWithBackoff(fn, maxAttempts = 5) {
  for (let i = 0; i < maxAttempts; i++) {
    try {
      return await fn();
    } catch (error) {
      if (error.status === 429 && i < maxAttempts - 1) {
        const delay = Math.min(1000 * Math.pow(2, i), 30000);
        await sleep(delay);
      } else {
        throw error;
      }
    }
  }
}
```

### Debug Mode

Enable verbose logging:

```typescript
// In src/index.ts
const DEBUG = true;

if (DEBUG) {
  console.log("Request:", request.url, request.method);
  console.log("Headers:", Object.fromEntries(request.headers));
}
```

### Contact

For issues or questions:

- GitHub Issues: [link]
- Slack: #rafiki
- Email: support@example.com

## Performance

### Benchmarks

| Metric                    | Value  |
| ------------------------- | ------ |
| Worker cold start         | ~50ms  |
| DO cold start             | ~100ms |
| Query latency (P50)       | ~2s    |
| Query latency (P99)       | ~10s   |
| WebSocket message latency | ~50ms  |
| Session state read        | ~10ms  |

### Optimization Tips

1. **Use KV for session key caching** (24h TTL)
2. **Batch WebSocket messages** when throughput is high
3. **Implement connection pooling** for Modal requests
4. **Enable DO hibernation** for idle sessions
5. **Use warm pools** for Modal sandboxes (optional)

## Security

### Best Practices

1. **Rotate secrets regularly** (monthly)
2. **Use short token expiry** (1 hour for session tokens)
3. **Implement rate limiting** (per user/IP)
4. **Log authentication failures** for monitoring
5. **Use HTTPS only** (Cloudflare enforces this)
6. **Validate all inputs** (use Zod or similar)
7. **Implement CORS carefully** (whitelist origins)

### Secrets Management

```bash
# List secrets
wrangler secret list

# Update secret
wrangler secret put SECRET_NAME

# Delete secret
wrangler secret delete SECRET_NAME
```

Never commit secrets to version control.

## Cost Optimization

### Cloudflare Pricing

- **Worker requests**: $0.50 per million
- **DO requests**: $0.15 per million
- **DO duration**: $12.50/GB-hour (hibernation reduces this)
- **KV reads**: $0.50 per million
- **KV writes**: $5.00 per million

### Tips

1. Use KV caching to reduce DO invocations
2. Enable DO hibernation for idle sessions
3. Batch operations when possible
4. Monitor and alert on cost anomalies
5. Archive old sessions to reduce storage costs

## License

[Your license here]

## Contributing

[Contributing guidelines here]

## Changelog

### v1.0.0 (2025-02-02)

- Initial release
- SessionAgent DO with SQLite storage
- EventBus DO for real-time updates
- WebSocket streaming support
- Internal auth integration with Modal
- Complete API documentation
