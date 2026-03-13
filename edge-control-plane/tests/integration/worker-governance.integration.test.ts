import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import { verifySessionToken } from "../../src/auth/session-auth";
import {
  buildCorsHeaders,
  getPublicSessionRoutePolicy,
} from "../../src/routes/public-worker-contract";
import { handleSchedulesEndpoint } from "../../src/routes/schedules-proxy";
import {
  buildSessionStopProxyResponse,
  parseSessionStopProxyRequest,
} from "../../src/routes/session-stop-proxy";
import type { Env } from "../../src/types";

const SESSION_ID = "sess-123";
const TENANT_ID = "tenant-1";
const USER_ID = "user-1";
const SESSION_SIGNING_SECRET = "session-signing-secret";
const INVALID_TOKEN_PAYLOAD_RE = /Invalid token payload/;
const PATCH_METHOD_RE = /\bPATCH\b/;

function buildSessionToken(sessionId: string): string {
  const now = Date.now();
  return buildSignedToken({
    expires_at: now + 60_000,
    issued_at: now - 1000,
    session_ids: [sessionId],
    tenant_id: TENANT_ID,
    user_id: USER_ID,
  });
}

function buildSignedToken(payload: Record<string, unknown>): string {
  const payloadString = JSON.stringify(payload);
  const payloadBytes = Buffer.from(payloadString, "utf8");
  const payloadBase64 = payloadBytes.toString("base64");
  const signatureBase64 = createHmac(
    "sha256",
    Buffer.from(SESSION_SIGNING_SECRET, "utf8")
  )
    .update(payloadBytes)
    .digest("base64");
  return `${payloadBase64}.${signatureBase64}`;
}

function createDurableObjectNamespaceStub(): DurableObjectNamespace {
  return {
    get: () =>
      ({
        fetch: async () => new Response("ok", { status: 200 }),
      }) as DurableObjectStub,
    idFromName: (name: string) =>
      ({
        toString: () => name,
      }) as DurableObjectId,
  } as DurableObjectNamespace;
}

function createEnv(): Env {
  const sessionCache = new Map<string, string>();
  return {
    ENVIRONMENT: "development",
    EVENT_BUS: createDurableObjectNamespaceStub(),
    INTERNAL_AUTH_SECRET: "internal-auth-secret",
    MODAL_API_BASE_URL: "https://modal.test",
    MODAL_TOKEN_ID: "modal-token-id",
    MODAL_TOKEN_SECRET: "modal-token-secret",
    SESSION_AGENT: createDurableObjectNamespaceStub(),
    SESSION_CACHE: {
      get: (key: string) => Promise.resolve(sessionCache.get(key) ?? null),
      put: (key: string, value: string) => {
        sessionCache.set(key, value);
        return Promise.resolve();
      },
    } as KVNamespace,
    SESSION_SIGNING_SECRET,
  };
}

async function withMockedFetch(
  handler: (request: Request, callIndex: number) => Promise<Response>,
  run: () => Promise<void>
): Promise<void> {
  const originalFetch = globalThis.fetch;
  let callIndex = 0;

  globalThis.fetch = ((
    input: RequestInfo | URL,
    init?: RequestInit
  ): Promise<Response> => {
    const request = input instanceof Request ? input : new Request(input, init);
    const currentCallIndex = callIndex;
    callIndex += 1;
    return handler(request, currentCallIndex);
  }) as typeof globalThis.fetch;

  try {
    await run();
  } finally {
    globalThis.fetch = originalFetch;
  }
}

test("POST /schedules rejects malformed JSON before auth or forwarding", async () => {
  const response = await handleSchedulesEndpoint(
    new Request("https://worker.test/schedules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{",
    }),
    createEnv(),
    "/schedules"
  );

  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), {
    error: "Invalid JSON request body",
    ok: false,
  });
});

test("Worker CORS preflight allows PATCH for schedule updates", () => {
  const response = buildCorsHeaders();
  assert.match(response["Access-Control-Allow-Methods"] || "", PATCH_METHOD_RE);
});

test("Worker session routes block undocumented aliases and passthroughs", () => {
  assert.equal(getPublicSessionRoutePolicy(""), null);
  assert.equal(getPublicSessionRoutePolicy("/query"), null);
});

test("Worker session state enforces GET-only semantics at the edge", () => {
  assert.deepEqual(getPublicSessionRoutePolicy("/state"), {
    allowedMethods: ["GET"],
    forwardPath: "/state",
  });
});

test("GET /schedules/:id rejects invalid upstream payloads with 502", async () => {
  const env = createEnv();
  const token = buildSessionToken(SESSION_ID);

  await withMockedFetch(
    (request, callIndex) => {
      assert.equal(callIndex, 0);
      assert.equal(new URL(request.url).pathname, "/schedules/sched-123");
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        })
      );
    },
    async () => {
      const request = new Request(
        "https://worker.test/schedules/sched-123?session_id=sess-123&user_id=user-1&tenant_id=tenant-1",
        {
          headers: {
            Authorization: `Bearer ${token}`,
          },
          method: "GET",
        }
      );
      const response = await handleSchedulesEndpoint(
        request,
        env,
        new URL(request.url).pathname
      );

      assert.equal(response.status, 502);
      assert.deepEqual(await response.json(), {
        error: "Invalid schedule response from Modal backend",
        ok: false,
      });
    }
  );
});

test("session tokens reject malformed identity claims", async () => {
  const token = buildSignedToken({
    expires_at: Date.now() + 60_000,
    issued_at: Date.now() - 1000,
    session_ids: ["sess-123"],
    user_id: 42,
  });

  await assert.rejects(
    verifySessionToken(token, SESSION_SIGNING_SECRET),
    INVALID_TOKEN_PAYLOAD_RE
  );
});

test("stop proxy preserves GET as a read-only status request", async () => {
  const parsed = await parseSessionStopProxyRequest(
    new Request("https://worker.test/session/sess-123/stop", {
      method: "GET",
    })
  );

  assert.deepEqual(parsed, { method: "GET" });
});

test("stop proxy preserves POST stop mode and reason", async () => {
  const parsed = await parseSessionStopProxyRequest(
    new Request("https://worker.test/session/sess-123/stop", {
      body: JSON.stringify({
        mode: "immediate",
        reason: "user requested stop",
      }),
      method: "POST",
    })
  );

  if (parsed instanceof Response) {
    assert.fail(`Expected parsed stop request, got response ${parsed.status}`);
  }
  assert.equal(parsed.method, "POST");
  assert.deepEqual(parsed.body, {
    mode: "immediate",
    reason: "user requested stop",
  });
});

test("stop proxy rejects client-controlled requested_by", async () => {
  const parsed = await parseSessionStopProxyRequest(
    new Request("https://worker.test/session/sess-123/stop", {
      body: JSON.stringify({
        mode: "graceful",
        requested_by: "tester",
      }),
      method: "POST",
    })
  );

  assert.ok(parsed instanceof Response);
  assert.equal(parsed.status, 400);
  assert.deepEqual(await parsed.json(), {
    error: "Invalid session stop request body",
    ok: false,
  });
});

test("stop proxy rejects invalid upstream stop payloads with 502", () => {
  const response = buildSessionStopProxyResponse(
    {
      data: { ok: true, session_id: "sess-123", status: "stopped" },
      ok: true,
      status: 200,
    },
    "Invalid session stop response from Modal backend"
  );

  assert.equal(response.status, 502);
});
