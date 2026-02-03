# Authentication & Routing Design

This document describes the authentication flow and routing logic between Cloudflare Worker, Durable Objects, and Modal backend.

## Architecture Overview

```
┌─────────────┐
│   Client    │
└──────┬──────┘
       │ Bearer token
       ▼
┌──────────────────────┐
│ Cloudflare Worker    │
│ - Validate token     │
│ - Extract context    │
│ - Route to DO        │
└──────┬───────────────┘
       │ Internal request
       ▼
┌──────────────────────┐
│ SessionAgent DO      │
│ - Session state      │
│ - Sign requests      │
└──────┬───────────────┘
       │ Signed request
       ▼
┌──────────────────────┐
│ Modal Backend        │
│ - Verify signature   │
│ - Execute            │
└──────────────────────┘
```

## Authentication Flow

### 1. Client → Worker Authentication

**Options:**

#### Option A: Session Tokens (Recommended)

Client provides a session-scoped token that contains user and session context.

**Token Format:**

```
Bearer <base64(payload)>.<signature>
```

**Payload:**

```json
{
  "user_id": "user-123",
  "tenant_id": "tenant-456",
  "session_ids": ["sess_abc", "sess_def"],
  "issued_at": 1234567890000,
  "expires_at": 1234567890000
}
```

**Signature:** HMAC-SHA256 using `SESSION_SIGNING_SECRET`

**Validation (Worker):**

```typescript
function validateSessionToken(token: string, env: Env): SessionToken | null {
  const parts = token.split(".");
  if (parts.length !== 2) return null;

  const [payloadB64, signatureB64] = parts;
  const payload = JSON.parse(atob(payloadB64));

  // Check expiration
  if (payload.expires_at < Date.now()) return null;

  // Verify signature
  const expectedSig = await hmacSign(payloadB64, env.SESSION_SIGNING_SECRET);
  if (expectedSig !== signatureB64) return null;

  return payload as SessionToken;
}
```

#### Option B: API Keys

Client provides a simple API key mapped to user/tenant in KV.

**Token Format:**

```
Bearer sk_live_abc123...
```

**Validation (Worker):**

```typescript
async function validateApiKey(
  key: string,
  env: Env
): Promise<UserContext | null> {
  const context = await env.SESSION_CACHE.get<UserContext>(
    `apikey:${key}`,
    "json"
  );
  if (!context) return null;

  return {
    user_id: context.user_id,
    tenant_id: context.tenant_id,
    permissions: context.permissions,
  };
}
```

#### Option C: OAuth/JWT (External IdP)

Client provides JWT from external identity provider (Auth0, Clerk, etc.).

**Validation (Worker):**

```typescript
import { verify } from "@cfworker/jwt";

async function validateJWT(
  token: string,
  env: Env
): Promise<JWTPayload | null> {
  try {
    const payload = await verify(token, env.JWT_PUBLIC_KEY);
    return payload;
  } catch (error) {
    return null;
  }
}
```

**Recommended:** Start with **Option A (Session Tokens)** for simplicity and control. Add OAuth later if needed.

---

### 2. Worker → DO Communication

Worker and DOs are in the same security boundary (Cloudflare platform), so no authentication is strictly needed. However, we can pass context via request headers or URL.

**Context Passing:**

```typescript
// Worker side
const doStub = env.SESSION_AGENT.get(doId);
const response = await doStub.fetch(
  new Request("https://internal/query", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-Id": userContext.user_id,
      "X-Tenant-Id": userContext.tenant_id,
    },
    body: JSON.stringify(request),
  })
);
```

```typescript
// DO side
async fetch(request: Request): Promise<Response> {
  const userId = request.headers.get("X-User-Id");
  const tenantId = request.headers.get("X-Tenant-Id");

  // Use context for authorization
  if (!this.canAccess(userId, tenantId)) {
    return new Response("Forbidden", { status: 403 });
  }

  // Process request
  // ...
}
```

---

### 3. DO → Modal Backend Authentication

**Two-Layer Security:**

#### Layer 1: Internal Service Token

DO signs requests with internal secret to prove they're from authorized control plane.

**Token Generation (DO):**

```typescript
async function generateInternalAuthToken(env: Env): Promise<string> {
  const payload = {
    service: "cloudflare-worker",
    issued_at: Date.now(),
    expires_at: Date.now() + 300000, // 5 minutes
  };

  const payloadStr = JSON.stringify(payload);
  const signature = await hmacSHA256(payloadStr, env.INTERNAL_AUTH_SECRET);

  return `${btoa(payloadStr)}.${signature}`;
}
```

**Token Validation (Modal):**

```python
# agent_sandbox/middleware/cloudflare_auth.py
import hmac
import hashlib
import json
import base64
from fastapi import HTTPException, Header

def verify_internal_token(authorization: str = Header(...)) -> dict:
    """Verify internal auth token from Cloudflare Worker."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")

    token = authorization[7:]
    parts = token.split(".")
    if len(parts) != 2:
        raise HTTPException(401, "Invalid token format")

    payload_b64, signature_b64 = parts

    # Decode payload
    try:
        payload_str = base64.b64decode(payload_b64).decode("utf-8")
        payload = json.loads(payload_str)
    except Exception:
        raise HTTPException(401, "Invalid payload")

    # Check expiration
    if payload["expires_at"] < time.time() * 1000:
        raise HTTPException(401, "Token expired")

    # Verify signature
    secret = os.environ["INTERNAL_AUTH_SECRET"]
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256
    ).digest()
    expected_sig_b64 = base64.b64encode(expected_sig).decode("utf-8")

    if not hmac.compare_digest(expected_sig_b64, signature_b64):
        raise HTTPException(401, "Invalid signature")

    return payload
```

#### Layer 2: Modal Connect Tokens (Optional)

For additional security, use Modal's native connect tokens.

**Token Generation (DO):**

```typescript
// When starting sandbox, generate connect token
const sandbox = await modal.Sandbox.create({
  // ... config
});

const connectToken = await sandbox.createConnectToken();

// Store in session state
this.sessionState.modal_connect_token = connectToken;

// Use for subsequent requests
const response = await fetch(modalUrl, {
  headers: {
    Authorization: `Bearer ${connectToken}`,
    "X-Internal-Auth": internalToken,
  },
});
```

**Recommended:** Use **Layer 1 (Internal Service Token)** initially. Add Layer 2 for production hardening.

---

## Routing Logic

### Session ID Resolution

**Priority:**

1. Explicit `session_id` in request
2. `session_key` lookup in KV/DO storage
3. Create new session

```typescript
async function resolveSessionId(
  request: QueryRequest,
  env: Env
): Promise<string> {
  // 1. Explicit session_id
  if (request.session_id) {
    return request.session_id;
  }

  // 2. Session key lookup
  if (request.session_key) {
    const cached = await env.SESSION_CACHE.get(`skey:${request.session_key}`);
    if (cached) {
      return cached;
    }
  }

  // 3. Create new session
  const newSessionId = crypto.randomUUID();

  // Cache session_key → session_id mapping if provided
  if (request.session_key) {
    await env.SESSION_CACHE.put(
      `skey:${request.session_key}`,
      newSessionId,
      { expirationTtl: 86400 } // 24 hours
    );
  }

  return newSessionId;
}
```

### DO Name Derivation

**SessionAgent DO:**

```typescript
// Use session_id as DO name for single-tenancy
const doId = env.SESSION_AGENT.idFromName(sessionId);

// Or use user_id for multi-session DOs
const doId = env.SESSION_AGENT.idFromName(userId);
```

**EventBus DO:**

```typescript
// Use tenant_id for tenant-wide event bus
const doId = env.EVENT_BUS.idFromName(tenantId);

// Or use user_id for per-user event bus
const doId = env.EVENT_BUS.idFromName(userId);

// Or "global" for single event bus
const doId = env.EVENT_BUS.idFromName("global");
```

**Recommended:** Use `session_id` for SessionAgent (one DO per session) and `tenant_id` or `user_id` for EventBus (shared across sessions).

---

## Authorization

### Session Ownership

**Who can access a session?**

1. **Session owner** (user who created it)
2. **Tenant members** (if multi-tenancy enabled)
3. **Shared collaborators** (for multiplayer)

**Check (DO):**

```typescript
private canAccessSession(userId: string, tenantId: string): boolean {
  // Owner check
  if (this.sessionState?.user_id === userId) {
    return true;
  }

  // Tenant check
  if (this.sessionState?.tenant_id === tenantId) {
    return true;
  }

  // Collaborator check (from DB)
  const collaborators = this.getCollaborators();
  if (collaborators.includes(userId)) {
    return true;
  }

  return false;
}
```

### Action Permissions

**Permission model:**

```typescript
interface Permissions {
  can_read: boolean; // View session state and messages
  can_write: boolean; // Send prompts and modify session
  can_stop: boolean; // Stop execution
  can_delete: boolean; // Delete session
  can_share: boolean; // Add collaborators
}
```

**Check (DO):**

```typescript
private getPermissions(userId: string, tenantId: string): Permissions {
  const isOwner = this.sessionState?.user_id === userId;
  const isTenant = this.sessionState?.tenant_id === tenantId;
  const isCollaborator = this.getCollaborators().includes(userId);

  if (isOwner) {
    return {
      can_read: true,
      can_write: true,
      can_stop: true,
      can_delete: true,
      can_share: true
    };
  }

  if (isTenant || isCollaborator) {
    return {
      can_read: true,
      can_write: true,
      can_stop: true,
      can_delete: false,
      can_share: false
    };
  }

  return {
    can_read: false,
    can_write: false,
    can_stop: false,
    can_delete: false,
    can_share: false
  };
}
```

---

## Rate Limiting

### Per-User Rate Limits

Use Cloudflare KV for simple rate limiting:

```typescript
async function checkRateLimit(
  userId: string,
  limit: number,
  window: number,
  env: Env
): Promise<boolean> {
  const key = `ratelimit:${userId}:${Math.floor(Date.now() / window)}`;

  const current = await env.SESSION_CACHE.get(key);
  const count = current ? parseInt(current) : 0;

  if (count >= limit) {
    return false; // Rate limit exceeded
  }

  await env.SESSION_CACHE.put(key, (count + 1).toString(), {
    expirationTtl: window / 1000,
  });

  return true;
}
```

**Usage:**

```typescript
// In Worker fetch handler
const allowed = await checkRateLimit(userId, 60, 60000, env); // 60 req/min
if (!allowed) {
  return new Response("Rate limit exceeded", { status: 429 });
}
```

---

## Security Best Practices

1. **Always use HTTPS** - Cloudflare Workers enforce this by default
2. **Validate all inputs** - Use Zod or similar for schema validation
3. **Short token expiry** - Session tokens: 1 hour, Internal tokens: 5 minutes
4. **Rotate secrets regularly** - Use Cloudflare Secrets versioning
5. **Log authentication failures** - Send to Cloudflare Analytics Engine
6. **Implement CORS carefully** - Whitelist specific origins in production
7. **Use constant-time comparison** - For signature verification (use `crypto.subtle.timingSafeEqual`)
8. **Store secrets in Wrangler Secrets** - Never commit to version control
9. **Implement IP allowlisting** - For internal Modal backend endpoints
10. **Monitor for anomalies** - Track failed auth attempts per user/IP

---

## Secrets Management

### Required Secrets

```bash
# Modal API access
wrangler secret put MODAL_TOKEN_ID
wrangler secret put MODAL_TOKEN_SECRET

# Internal authentication
wrangler secret put INTERNAL_AUTH_SECRET
wrangler secret put SESSION_SIGNING_SECRET

# Optional: JWT validation
wrangler secret put JWT_PUBLIC_KEY
```

### Secret Rotation

```bash
# Generate new secret
NEW_SECRET=$(openssl rand -hex 32)

# Update in Wrangler
wrangler secret put INTERNAL_AUTH_SECRET <<< "$NEW_SECRET"

# Update in Modal (Modal Secrets UI or CLI)
modal secret create internal-auth-secret INTERNAL_AUTH_SECRET=$NEW_SECRET

# Restart services to pick up new secret
```

---

## Testing Authentication

### Generate Test Token

```typescript
// test-token.ts
import crypto from "crypto";

const payload = {
  user_id: "test-user-123",
  tenant_id: "test-tenant-456",
  session_ids: ["sess_abc"],
  issued_at: Date.now(),
  expires_at: Date.now() + 3600000, // 1 hour
};

const payloadStr = JSON.stringify(payload);
const secret = process.env.SESSION_SIGNING_SECRET!;

const signature = crypto
  .createHmac("sha256", secret)
  .update(Buffer.from(payloadStr).toString("base64"))
  .digest("base64");

const token = `${Buffer.from(payloadStr).toString("base64")}.${signature}`;

console.log("Test token:", token);
```

### Test Requests

```bash
# Set token
TOKEN="<generated-token>"

# Test query
curl -X POST https://worker.example.com/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "Test query"}'

# Test WebSocket
wscat -c "wss://worker.example.com/ws?user_id=test-user-123" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Migration from Modal-only to Cloudflare

### Phase 1: Parallel Deployment

- Deploy Cloudflare Worker alongside existing Modal gateway
- Route subset of traffic (by user ID or percentage) to Cloudflare
- Monitor metrics (latency, error rates, WebSocket connections)

### Phase 2: Gradual Rollout

- Increase traffic percentage to Cloudflare: 10% → 50% → 90%
- Keep Modal gateway as fallback
- Implement feature flags for easy rollback

### Phase 3: Full Cutover

- Route all traffic to Cloudflare Worker
- Deprecate Modal `@modal.asgi_app()` gateway
- Keep Modal backend for execution only

### Phase 4: Cleanup

- Remove unused Modal gateway code
- Update documentation
- Archive old deployment configs
