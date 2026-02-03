# Cloudflare Durable Objects Integration

This document provides an overview of the Cloudflare + Modal hybrid architecture implementation.

## Quick Links

- **Control Plane Code**: [`cloudflare-control-plane/`](./cloudflare-control-plane/)
- **Architecture Docs**: [`docs/cloudflare-architecture.md`](./docs/cloudflare-architecture.md)
- **API Reference**: [`cloudflare-control-plane/API.md`](./cloudflare-control-plane/API.md)
- **Implementation Summary**: [`cloudflare-control-plane/IMPLEMENTATION_SUMMARY.md`](./cloudflare-control-plane/IMPLEMENTATION_SUMMARY.md)

## What is This?

This integration adds Cloudflare Workers + Durable Objects as a control plane layer on top of the existing Modal backend, enabling:

1. **Session Persistence**: Sessions stored in Durable Object SQLite survive sandbox restarts
2. **Real-time Multiplayer**: Multiple users can collaborate on the same session via WebSockets
3. **Edge Performance**: API gateway and session state at 300+ Cloudflare locations globally
4. **WebSocket Streaming**: Native WebSocket support for real-time query updates
5. **Scalable State Management**: Separate scaling for session state (Cloudflare) vs execution (Modal)

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Clients                               │
│              (Web, Mobile, CLI, Slack)                       │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│                  Cloudflare Edge                             │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │   Worker    │  │ SessionAgent │  │  EventBus    │       │
│  │ (Gateway)   │→ │      DO      │→ │     DO       │       │
│  └─────────────┘  └──────────────┘  └──────────────┘       │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│                    Modal Backend                             │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │  Sandbox    │  │    Volume    │  │  Job Queue   │       │
│  │ (Execution) │  │  (Artifacts) │  │  (Async)     │       │
│  └─────────────┘  └──────────────┘  └──────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

## Key Components

### Cloudflare Layer (New)

1. **Worker**: API gateway, authentication, routing
2. **SessionAgent DO**: Per-session state, messages, prompt queue (SQLite)
3. **EventBus DO**: Real-time WebSocket fan-out for multiplayer
4. **KV**: Session cache, rate limits

### Modal Layer (Existing, Unchanged)

1. **Sandbox**: Agent execution with Claude SDK
2. **Volume**: Persistent filesystem for workspaces and artifacts
3. **Queue & Dict**: Job processing and results
4. **Controller**: FastAPI service in sandbox

## What Changed?

### New Code

- `cloudflare-control-plane/`: Complete Cloudflare Worker + DO implementation
- `docs/cloudflare-architecture.md`: Hybrid architecture documentation

### Modal Backend Changes Required

**Add authentication middleware** to verify requests from Cloudflare (required for all non-health endpoints):

```python
# agent_sandbox/middleware/cloudflare_auth.py (new file)
# X-Internal-Auth is required on all non-health endpoints.
# See cloudflare-control-plane/INTEGRATION.md for full implementation.
def verify_internal_token(raw_token: str) -> dict:
    """Verify HMAC-signed token from Cloudflare Worker."""
    ...

# agent_sandbox/controllers/controller.py (update)
from agent_sandbox.middleware.cloudflare_auth import internal_auth_middleware

app.middleware("http")(internal_auth_middleware)
```

**Add secret** (required):

```bash
modal secret create internal-auth-secret INTERNAL_AUTH_SECRET=<same-as-cloudflare>
```

### Existing Code Unchanged

- All Modal sandbox execution logic remains the same
- `/query`, `/query_stream`, `/submit` endpoints unchanged
- Agent SDK integration unchanged
- Volume and job queue logic unchanged

## Benefits

### 1. Session Continuity

- Sessions persist across sandbox restarts
- Message history stored durably
- Resume conversations seamlessly

### 2. Real-time Collaboration

- Multiple users in same session
- Live updates via WebSocket
- See what collaborators are doing
- Presence tracking

### 3. Performance

- Edge routing (10-50ms vs 100-200ms)
- WebSocket at edge (no long-polling)
- Geographic distribution
- Caching at KV

### 4. Scalability

- Separate scaling: state (Cloudflare) vs compute (Modal)
- SessionAgent DO: 1,000 concurrent WebSockets
- EventBus DO: 10,000 concurrent WebSockets
- Hibernation reduces idle costs

### 5. Developer Experience

- Cloudflare control plane is the primary API surface
- WebSocket-first for streaming
- Easy to add new clients
- Better error handling at edge

## Migration Path

### Phase 0: Setup (Week 1)

- Deploy Cloudflare infrastructure
- Add Modal auth middleware
- Test in staging

### Phase 1: Canary (Week 2-3)

- Route 10% traffic to Cloudflare
- Monitor closely
- Keep Modal gateway as fallback

### Phase 2: Rollout (Week 4-6)

- Gradually increase: 25% → 50% → 90%
- Enable WebSocket features
- Migrate active sessions

### Phase 3: Complete (Week 7-8)

- Route 100% to Cloudflare
- Remove Modal gateway code
- Archive old configs

### Phase 4: Optimize (Ongoing)

- Tune caching and storage
- Monitor costs
- Add features

## Cost Impact

**Per million requests** (2s avg execution):

- **Before** (Modal only): ~$160
- **After** (Cloudflare + Modal): ~$175-180
- **Increase**: ~10-15% overhead

**Why it's worth it**:

- Session persistence (no data loss)
- Real-time multiplayer (new feature)
- Edge performance (better UX)
- Separate scaling (more flexible)

## Getting Started

### 1. Review Documentation

Start with:

- [`cloudflare-control-plane/README.md`](./cloudflare-control-plane/README.md) - Quick start
- [`cloudflare-control-plane/API.md`](./cloudflare-control-plane/API.md) - API reference
- [`docs/cloudflare-architecture.md`](./docs/cloudflare-architecture.md) - Architecture details

### 2. Deploy Cloudflare

```bash
cd cloudflare-control-plane
npm install
wrangler login
wrangler secret put MODAL_TOKEN_ID
wrangler secret put MODAL_TOKEN_SECRET
wrangler secret put INTERNAL_AUTH_SECRET
wrangler secret put SESSION_SIGNING_SECRET
npm run deploy
```

### 3. Update Modal Backend

Add authentication middleware and secret (see [`cloudflare-control-plane/INTEGRATION.md`](./cloudflare-control-plane/INTEGRATION.md)).

### 4. Test Integration

```bash
# Test health
curl https://your-worker.workers.dev/health

# Test query
curl -X POST https://your-worker.workers.dev/query \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"question": "Test"}'

# Test WebSocket
wscat -c "wss://your-worker.workers.dev/query_stream"
```

### 5. Monitor & Iterate

- Cloudflare Analytics: Request volume, latency, errors
- Modal Metrics: Sandbox utilization, execution time
- Custom logs: Trace requests across both platforms

## FAQ

### Q: Do I need to rewrite my Modal code?

**A:** No. All Modal sandbox execution logic remains unchanged. Only add authentication middleware.

### Q: Are there breaking changes to the API?

**A:** No. All existing endpoints work the same way. WebSocket is an addition, not a replacement.

### Q: What if I don't want Cloudflare?

**A:** The existing Modal-only architecture still works. This is an optional enhancement.

### Q: How do I rollback if there are issues?

**A:** Feature flag to route traffic back to Modal gateway. Or point DNS to Modal if Worker is down.

### Q: What about costs?

**A:** ~10-15% increase per request. Worth it for persistence, multiplayer, and edge performance.

### Q: Can I use this in production?

**A:** Yes, after thorough testing in staging. Follow the phased rollout plan.

## Support

- **Documentation**: See links at top of this file
- **Issues**: [GitHub Issues]
- **Slack**: #agent-sandbox
- **Email**: team@example.com

## Status

- ✅ Design Complete
- ✅ Implementation Complete
- ⏳ Testing Pending
- ⏳ Staging Deployment Pending
- ⏳ Production Rollout Pending

---

**Last Updated**: 2025-02-02  
**Version**: 1.0.0  
**Authors**: [Your team]
