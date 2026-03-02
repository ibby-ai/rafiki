# Next.js + Supabase BFF Integration Guide

This guide shows how a Next.js client should integrate with Rafiki using a backend-for-frontend (BFF) pattern so browser code never holds Rafiki signing secrets or long-lived worker tokens.

## Scope and Architecture Contract

- Public ingress remains the Cloudflare control plane.
- Next.js server routes proxy client requests to the Worker.
- Modal backend remains internal-only behind `X-Internal-Auth`.

Request path:

1. Browser -> Next.js API route.
2. Next.js route validates Supabase auth from server-side cookies.
3. Next.js route derives trusted scope claims (`user_id`, `tenant_id`, `session_id`/`session_ids`).
4. Next.js route mints a short-lived Rafiki session token (HMAC payload/signature).
5. Next.js route calls Worker with `Authorization: Bearer <rafiki_token>`.
6. Worker validates token, enforces scope/session rules, then forwards to Modal with internal auth headers.

## Why BFF for Next.js

- Prevents Rafiki tokens from living in browser JavaScript.
- Centralizes identity mapping and tenant enforcement on your server.
- Lets you apply endpoint-specific policy and audit before forwarding.
- Keeps worker and modal secrets out of the client.

## Important Auth Realities

1. Worker auth does not accept Supabase JWTs directly.
   - The Worker expects its own signed token (`base64(payload).base64(signature)`), with `issued_at` and `expires_at` in milliseconds.
2. Token claims must be authoritative.
   - If token claims omit `user_id`/`tenant_id`, request-level values may be accepted. Always mint claims from verified server-side identity.
3. Session restrictions are enforced when `session_ids` are present.
   - If a request resolves to a session not in claims, Worker returns `403`.
   - For non-session endpoints such as `/schedules` or `/jobs`, prefer tokens without `session_id`/`session_ids` unless you also pass an authorized session resolver (`session_id` or `session_key`) on the request.
4. `session_key` is KV-mapped by scope with a long default TTL.
   - Scope precedence is `tenant_id -> user_id -> anonymous`; ensure stable claims and explicit TTL policy.

## Required Environment Variables (Next.js Server)

- `RAFIKI_WORKER_URL` (for example `https://rafiki-control-plane.example.workers.dev`)
- `RAFIKI_SESSION_SIGNING_SECRET` (must match Worker `SESSION_SIGNING_SECRET`)
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`

Do not expose `RAFIKI_SESSION_SIGNING_SECRET` to client bundles.

## Token Minting Utility (Next.js Server)

```ts
// lib/rafiki/token.ts
import crypto from "node:crypto";

type RafikiClaims = {
  issued_at: number;
  expires_at: number;
  user_id: string;
  tenant_id?: string;
  session_id?: string;
  session_ids?: string[];
};

function toB64(input: Buffer): string {
  return input.toString("base64");
}

export function mintRafikiToken(input: {
  secret: string;
  userId: string;
  tenantId?: string;
  sessionId?: string;
  sessionIds?: string[];
  ttlMs?: number;
}): string {
  const now = Date.now();
  const claims: RafikiClaims = {
    issued_at: now,
    expires_at: now + (input.ttlMs ?? 5 * 60 * 1000),
    user_id: input.userId,
    ...(input.tenantId ? { tenant_id: input.tenantId } : {}),
    ...(input.sessionId ? { session_id: input.sessionId } : {}),
    ...(input.sessionIds?.length ? { session_ids: input.sessionIds } : {}),
  };

  const payload = Buffer.from(JSON.stringify(claims), "utf8");
  const signature = crypto
    .createHmac("sha256", Buffer.from(input.secret, "utf8"))
    .update(payload)
    .digest();

  return `${toB64(payload)}.${toB64(signature)}`;
}
```

## Proxy Route Pattern (App Router)

This example proxies schedule creation.

```ts
// app/api/rafiki/schedules/route.ts
import crypto from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { mintRafikiToken } from "@/lib/rafiki/token";

const WORKER_URL = process.env.RAFIKI_WORKER_URL!;
const RAFIKI_SECRET = process.env.RAFIKI_SESSION_SIGNING_SECRET!;

async function lookupTenantIdForUser(userId: string): Promise<string | null> {
  // Replace with your DB membership lookup (for example:
  // select tenant_id from memberships where user_id = $1 and status = 'active').
  return null;
}

export async function POST(req: NextRequest) {
  const cookieStore = await cookies();
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll() {
          // No-op for read-only route path; set where needed.
        },
      },
    }
  );

  const { data, error } = await supabase.auth.getUser();
  if (error || !data.user) {
    return NextResponse.json({ ok: false, error: "Unauthorized" }, { status: 401 });
  }

  // Derive tenant from your own membership model; do not trust request body
  // or user-provided headers for tenant scope.
  const tenantId = await lookupTenantIdForUser(data.user.id);
  if (!tenantId) {
    return NextResponse.json({ ok: false, error: "No tenant membership" }, { status: 403 });
  }
  const body = await req.json();

  const token = mintRafikiToken({
    secret: RAFIKI_SECRET,
    userId: data.user.id,
    tenantId,
    ttlMs: 5 * 60 * 1000,
  });

  // Strip identity fields from client payload; server owns identity mapping.
  const forwardedBody = {
    name: body.name,
    question: body.question,
    schedule_type: body.schedule_type,
    run_at: body.run_at,
    cron: body.cron,
    timezone: body.timezone,
    enabled: body.enabled,
    agent_type: body.agent_type,
    metadata: body.metadata,
    webhook: body.webhook,
  };

  const resp = await fetch(`${WORKER_URL}/schedules`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      "x-request-id": req.headers.get("x-request-id") ?? crypto.randomUUID(),
    },
    body: JSON.stringify(forwardedBody),
    cache: "no-store",
  });

  const text = await resp.text();
  return new NextResponse(text, {
    status: resp.status,
    headers: { "Content-Type": resp.headers.get("Content-Type") ?? "application/json" },
  });
}
```

## Endpoint Routing Strategy

Recommended default:

1. Proxy via Next.js BFF:
   - `POST /query`
   - `POST /submit`
   - `GET/DELETE /jobs/{job_id}`
   - `POST/GET /schedules`
   - `GET/PATCH/DELETE /schedules/{schedule_id}`
   - Exclude `POST /schedules/dispatch` from user-facing routes
2. Realtime options:
   - Option A: proxy WebSocket/SSE through Next.js (more control, more complexity).
   - Option B: issue very short-lived worker token from Next.js, then let browser connect directly to Worker for `/query_stream` or `/ws`.

Important: Worker `/query_stream` is WebSocket upgrade at edge, while Modal `/query_stream` is SSE internally. Plan transport behavior explicitly.
If using browser-direct WebSockets, token query params can leak via URL surfaces (logs/history). Prefer BFF proxy for sensitive workloads, or use very short TTL tokens and strict transport controls.

## Critical Safety Caveats

1. Treat job IDs as sensitive.
   - Current `/jobs/{job_id}` lookup is not strongly actor-owned in backend storage by default. Keep your own `(job_id -> owner)` mapping in your app database and enforce ownership before proxying.
2. Treat `POST /schedules/dispatch` as admin/internal only.
   - Current dispatch endpoint triggers due scans globally; do not expose it to normal end users.
3. Do not promise session continuity for async job mode.
   - Job enqueue/processing path is queue-based and not equivalent to session-bound interactive query flow.

## CSRF and Request Integrity

If your BFF uses cookie-authenticated browser requests, enforce CSRF protections for mutating endpoints:

- `Origin`/`Host` checks
- anti-CSRF token validation
- `SameSite=Lax` or stricter cookies where possible

Also propagate request correlation IDs:

- Browser -> Next.js: `x-request-id`
- Next.js -> Worker: same header
- Log and trace this ID in both systems.

## Local Dev and Validation

1. Start Modal and Worker using the canonical runbook:
   - `docs/references/runbooks/cloudflare-modal-e2e.md`
2. Validate auth flow:
   - Supabase-authenticated request to Next BFF route
   - Next minting a short-lived Rafiki token
   - Worker acceptance and successful forward to Modal
3. Validate negative cases:
   - expired token -> `401`
   - mismatched tenant claim vs requested tenant -> `403`
   - unauthorized session_id when `session_ids` claim exists -> `403`

## Troubleshooting

- `401 Missing authorization token` from Worker:
  - Next route did not attach Worker bearer token.
- `401 Invalid token signature`:
  - `RAFIKI_SESSION_SIGNING_SECRET` does not match Worker `SESSION_SIGNING_SECRET`.
- `403 user_id mismatch` or `tenant_id mismatch`:
  - Client/body scope differs from token claims; ensure BFF strips client identity fields.
- Session unexpectedly changes with `session_key`:
  - Verify KV TTL/scope behavior and stable `tenant_id`/`user_id` claims.

## References

- `edge-control-plane/src/auth/sessionAuth.ts`
- `edge-control-plane/src/auth/internalAuth.ts`
- `edge-control-plane/src/index.ts`
- `modal_backend/security/cloudflare_auth.py`
- `docs/references/runbooks/cloudflare-modal-e2e.md`
- `docs/references/troubleshooting.md`
