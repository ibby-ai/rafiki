# Deployment Checklist

Use this checklist to deploy the Cloudflare control plane.

## Pre-Deployment

### Cloudflare Account Setup

- [ ] Sign up for Cloudflare account at https://dash.cloudflare.com
- [ ] Enable Workers subscription (includes Durable Objects)
- [ ] Note your account ID from dashboard

### Local Setup

- [ ] Install Node.js 18+ (`node --version`)
- [ ] Install Wrangler CLI: `npm install -g wrangler`
- [ ] Clone repository
- [ ] Navigate to `cloudflare-control-plane/`
- [ ] Run `npm install`

### Wrangler Configuration

- [ ] Run `wrangler login` and authenticate
- [ ] Update `wrangler.jsonc`:
  - [ ] Update `MODAL_API_BASE_URL` to your Modal backend URL
  - [ ] Set `ENVIRONMENT` to "development" or "production"

### KV Namespace Setup

- [ ] Create KV namespace: `wrangler kv:namespace create SESSION_CACHE`
- [ ] Copy the ID from output (looks like: `abc123...`)
- [ ] Update `kv_namespaces[0].id` in `wrangler.jsonc` with this ID

### Secrets Setup

- [ ] Generate internal auth secret: `openssl rand -hex 32`
- [ ] Generate session signing secret: `openssl rand -hex 32`
- [ ] Set Cloudflare secrets:

  ```bash
  wrangler secret put MODAL_TOKEN_ID
  # Enter your Modal token ID

  wrangler secret put MODAL_TOKEN_SECRET
  # Enter your Modal token secret

  wrangler secret put INTERNAL_AUTH_SECRET
  # Enter the internal auth secret (generated above)

  wrangler secret put SESSION_SIGNING_SECRET
  # Enter the session signing secret (generated above)
  ```

- [ ] Save secrets securely (password manager)

## Modal Backend Updates

### Add Authentication Middleware

- [ ] Create `agent_sandbox/middleware/cloudflare_auth.py` (requires `X-Internal-Auth` on all non-health endpoints)
- [ ] Copy implementation from `cloudflare-control-plane/INTEGRATION.md`
- [ ] Update `agent_sandbox/controllers/controller.py` to use middleware
- [ ] Add middleware import and app.middleware("http") call

### Create Modal Secret

- [ ] Create secret with same value as Cloudflare:
  ```bash
  modal secret create internal-auth-secret \
    INTERNAL_AUTH_SECRET="<same-value-as-cloudflare>"
  ```

### Update Settings

- [ ] Edit `agent_sandbox/config/settings.py`
- [ ] Add `internal_auth_secret: str | None` field and require it at startup
- [ ] Update `get_modal_secrets()` to always include "internal-auth-secret"

### Deploy Modal Changes

- [ ] Test locally: `modal run -m agent_sandbox.app`
- [ ] Deploy: `modal deploy -m agent_sandbox.deploy`
- [ ] Verify Modal backend is accessible

## Cloudflare Deployment

### Development Deployment

- [ ] Run `npm run dev` to start local dev server
- [ ] Test locally: `curl http://localhost:8787/health`
- [ ] Test query endpoint (see testing section below)
- [ ] Stop dev server (Ctrl+C)

### Production Deployment

- [ ] Run `npm run deploy`
- [ ] Wait for deployment to complete (~1-2 minutes)
- [ ] Note the deployed URL (e.g., `your-worker.workers.dev`)
- [ ] Verify deployment: `curl https://your-worker.workers.dev/health`

### Verify Durable Objects

- [ ] Check DO bindings: `wrangler durable-objects list`
- [ ] Should see `SessionAgent` and `EventBus`

## Testing

### Basic Health Check

```bash
curl https://your-worker.workers.dev/health
# Expected: {"ok":true,"service":"cloudflare-control-plane"}
```

### Test Authentication

```bash
# This should fail (no auth)
curl -X POST https://your-worker.workers.dev/query \
  -H "Content-Type: application/json" \
  -d '{"question":"Test"}'
# Expected: 401 Unauthorized

# Generate a test token (see AUTH.md)
# Then test with valid token
```

### Test Query Endpoint

```bash
# Replace <token> with valid session token
curl -X POST https://your-worker.workers.dev/query \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is 2+2?",
    "session_id": "test-session-123"
  }'
# Expected: {"ok":true,"session_id":"test-session-123","messages":[...]}
```

### Test WebSocket

```bash
# Install wscat if not already installed
npm install -g wscat

# Connect to streaming endpoint
wscat -c "wss://your-worker.workers.dev/query_stream" \
  -H "Authorization: Bearer <token>"

# Send query
> {"question":"What is the capital of Canada?","session_id":"test-123"}

# Should receive:
# < {"type":"connection_ack",...}
# < {"type":"query_start",...}
# < {"type":"assistant_message",...}
# < {"type":"query_complete",...}
```

### Test EventBus

```bash
# Connect to event bus
wscat -c "wss://your-worker.workers.dev/ws?user_id=test-user&session_ids=test-123"

# Should receive:
# < {"type":"connection_ack",...}

# In another terminal, send a query to test-123
# You should see broadcasts in the EventBus connection
```

### Test Integration with Modal

- [ ] Verify requests reach Modal backend (check Modal logs)
- [ ] Verify auth tokens are validated
- [ ] Verify agent execution completes
- [ ] Verify results return to client

## Monitoring Setup

### Cloudflare Analytics

- [ ] Open Cloudflare dashboard
- [ ] Navigate to Workers & Pages > your-worker
- [ ] Check Analytics tab for metrics
- [ ] Set up alerts (optional):
  - High error rate
  - High latency
  - Unusual traffic patterns

### Logging

- [ ] Tail logs in real-time: `wrangler tail`
- [ ] Set up log aggregation (Datadog, Splunk, etc.) if needed

### Custom Dashboards

- [ ] Create dashboard for key metrics:
  - Request volume by endpoint
  - P50/P95/P99 latency
  - Error rate
  - WebSocket connection count
  - DO invocation count
  - Cost per day

## Post-Deployment

### Smoke Tests

- [ ] Test from different geographic locations (VPN)
- [ ] Test with multiple concurrent clients
- [ ] Test session resumption (same session_id, different requests)
- [ ] Test rate limiting (exceed limits)
- [ ] Test error scenarios (invalid inputs, missing auth, etc.)

### Performance Testing

- [ ] Run load test with k6 or similar
- [ ] Verify P99 latency < 10s
- [ ] Verify WebSocket connection stability
- [ ] Check DO storage usage

### Security Audit

- [ ] Verify all secrets are set correctly
- [ ] Check CORS configuration
- [ ] Test authentication thoroughly
- [ ] Review rate limiting settings
- [ ] Ensure no secrets in code or logs

### Documentation

- [ ] Update internal wiki with deployment info
- [ ] Document API endpoints for team
- [ ] Share authentication guide
- [ ] Create runbook for common issues

## Rollout Strategy

### Phase 1: Internal Testing (Day 1-7)

- [ ] Deploy to staging environment
- [ ] Test with internal team only
- [ ] Collect feedback
- [ ] Fix any issues found

### Phase 2: Canary (Day 8-14)

- [ ] Route 10% of traffic to Cloudflare (by user ID hash)
- [ ] Monitor metrics closely
- [ ] Keep Modal gateway as fallback
- [ ] Increase to 25% if stable

### Phase 3: Gradual Rollout (Day 15-35)

- [ ] Increase to 50% traffic
- [ ] Enable WebSocket features for Cloudflare users
- [ ] Migrate active sessions to DO storage
- [ ] Increase to 90% if stable

### Phase 4: Full Migration (Day 36-42)

- [ ] Route 100% traffic to Cloudflare
- [ ] Deprecate Modal gateway code
- [ ] Archive old deployment configs
- [ ] Announce to all users

### Phase 5: Optimization (Ongoing)

- [ ] Monitor costs daily
- [ ] Tune caching and storage
- [ ] Optimize WebSocket usage
- [ ] Add new features based on feedback

## Rollback Plan

### Quick Rollback (< 5 minutes)

If critical issues arise:

1. Update DNS to point back to Modal gateway
2. Or deploy previous Worker version: `wrangler rollback`
3. Monitor for issues
4. Investigate root cause

### Feature Flag Rollback

If using feature flags:

1. Set `ROUTE_TO_CLOUDFLARE=false` in environment
2. Redeploy Worker
3. Traffic routes to Modal gateway
4. Fix issues and re-enable

### Data Migration Rollback

If session data issues:

1. Disable new session creation in DO
2. Keep existing sessions in DO
3. New sessions use Modal Dict
4. Investigate and fix
5. Re-enable DO storage

## Troubleshooting

### Worker Not Responding

- Check Wrangler dashboard for errors
- Verify KV namespace exists
- Check DO bindings are correct
- Review recent deployments for breaking changes

### Authentication Failures

- Verify secrets match between Cloudflare and Modal
- Check token format and expiration
- Review auth middleware logs in Modal

### WebSocket Connection Issues

- Check CORS headers
- Verify Upgrade header is sent
- Test with wscat first
- Check client implementation

### High Latency

- Check DO invocation time in metrics
- Verify Modal backend is responsive
- Consider adding caching (KV)
- Review cold start times

### High Costs

- Check DO storage usage (archive old sessions)
- Review KV operation count (cache TTLs)
- Monitor request volume (rate limit if needed)
- Optimize WebSocket connection count

## Support

- **Cloudflare Support**: https://support.cloudflare.com
- **Modal Support**: https://modal.com/support
- **Documentation**: See README.md and linked docs
- **Internal Slack**: #agent-sandbox
- **On-Call**: [Your on-call rotation]

## Completion

- [ ] All checklist items completed
- [ ] Tests passing
- [ ] Monitoring configured
- [ ] Team notified
- [ ] Documentation updated
- [ ] Runbook created

**Deployment Date**: ******\_******  
**Deployed By**: ******\_******  
**Version**: ******\_******  
**Notes**: ******\_******
