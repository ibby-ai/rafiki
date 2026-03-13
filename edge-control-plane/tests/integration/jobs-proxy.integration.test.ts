import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import { handleJobsEndpoint } from "../../src/routes/jobs-proxy";
import type { Env, JobStatusResponse } from "../../src/types";

const SESSION_ID = "sess-123";
const TENANT_ID = "tenant-1";
const USER_ID = "user-1";
const SESSION_SIGNING_SECRET = "session-signing-secret";

function buildSessionToken(sessionId: string): string {
  const now = Date.now();
  const payload = {
    expires_at: now + 60_000,
    issued_at: now - 1000,
    session_ids: [sessionId],
    tenant_id: TENANT_ID,
    user_id: USER_ID,
  };
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

function createExecutionContext(): ExecutionContext {
  return {
    passThroughOnException: () => {
      // no-op in tests
    },
    waitUntil: (promise: Promise<unknown>) => {
      promise.catch(() => undefined);
    },
  } as ExecutionContext;
}

function createRequest(pathnameWithQuery: string, token: string): Request {
  return new Request(`https://worker.test${pathnameWithQuery}`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
    method: "GET",
  });
}

function decodeSignedTokenPayload(token: string): Record<string, unknown> {
  const [payload] = token.split(".");
  return JSON.parse(Buffer.from(payload, "base64").toString("utf8")) as Record<
    string,
    unknown
  >;
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    headers: { "Content-Type": "application/json" },
    status,
  });
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

test("GET /jobs/:id/artifacts enforces ownership precheck before proxying", async () => {
  const token = buildSessionToken(SESSION_ID);
  const env = createEnv();
  const ctx = createExecutionContext();
  const fetchRequests: Request[] = [];

  await withMockedFetch(
    (request, callIndex) => {
      fetchRequests.push(request);
      if (callIndex === 0) {
        const ownership: JobStatusResponse = {
          created_at: 1,
          job_id: "job-mismatch",
          session_id: "sess-other",
          status: "queued",
          tenant_id: TENANT_ID,
          user_id: USER_ID,
        };
        return jsonResponse(ownership);
      }
      throw new Error(
        "Unexpected downstream proxy fetch after ownership failure"
      );
    },
    async () => {
      const request = createRequest(
        "/jobs/job-mismatch/artifacts?session_id=sess-123&user_id=user-1&tenant_id=tenant-1",
        token
      );
      const response = await handleJobsEndpoint({
        ctx,
        env,
        path: new URL(request.url).pathname,
        request,
        scheduleJobEvent: () => {
          // no-op in tests
        },
      });

      assert.equal(response.status, 403);
      const payload = (await response.json()) as {
        error?: string;
        ok?: boolean;
      };
      assert.equal(payload.error, "Job session mismatch");
      assert.equal(payload.ok, false);
    }
  );

  assert.equal(fetchRequests.length, 1);
  assert.equal(new URL(fetchRequests[0].url).pathname, "/jobs/job-mismatch");
  assert.equal(fetchRequests[0].headers.get("X-Session-Id"), SESSION_ID);
  assert.equal(fetchRequests[0].headers.get("X-User-Id"), USER_ID);
  assert.equal(fetchRequests[0].headers.get("X-Tenant-Id"), TENANT_ID);
});

test("GET /jobs/:id/artifacts/:path forwards scoped artifact access token", async () => {
  const artifactPath = "reports/final.txt";
  const encodedArtifactPath = encodeURIComponent(artifactPath);
  const token = buildSessionToken(SESSION_ID);
  const env = createEnv();
  const ctx = createExecutionContext();
  const fetchRequests: Request[] = [];

  await withMockedFetch(
    (request, callIndex) => {
      fetchRequests.push(request);
      if (callIndex === 0) {
        const ownership: JobStatusResponse = {
          created_at: 2,
          job_id: "job-token",
          session_id: SESSION_ID,
          status: "running",
          tenant_id: TENANT_ID,
          user_id: USER_ID,
        };
        return jsonResponse(ownership);
      }
      if (callIndex === 1) {
        return new Response("artifact-bytes", { status: 200 });
      }
      throw new Error(
        "Unexpected fetch call count for artifact download route"
      );
    },
    async () => {
      const request = createRequest(
        `/jobs/job-token/artifacts/${encodedArtifactPath}?session_id=sess-123&user_id=user-1&tenant_id=tenant-1`,
        token
      );
      const response = await handleJobsEndpoint({
        ctx,
        env,
        path: new URL(request.url).pathname,
        request,
        scheduleJobEvent: () => {
          // no-op in tests
        },
      });

      assert.equal(response.status, 200);
      assert.equal(await response.text(), "artifact-bytes");
    }
  );

  assert.equal(fetchRequests.length, 2);
  assert.equal(new URL(fetchRequests[0].url).pathname, "/jobs/job-token");
  assert.equal(
    new URL(fetchRequests[1].url).pathname,
    `/jobs/job-token/artifacts/${encodedArtifactPath}`
  );

  const artifactToken = fetchRequests[1].headers.get("X-Artifact-Access-Token");
  assert.ok(
    artifactToken,
    "Missing X-Artifact-Access-Token on artifact download proxy"
  );
  const tokenPayload = decodeSignedTokenPayload(artifactToken);
  assert.equal(tokenPayload.service, "cloudflare-worker-artifact");
  assert.equal(tokenPayload.session_id, SESSION_ID);
  assert.equal(tokenPayload.job_id, "job-token");
  assert.equal(tokenPayload.artifact_path, artifactPath);
});

test("GET /jobs/:id/artifacts/:path returns deterministic 400 for malformed encoding", async () => {
  const token = buildSessionToken(SESSION_ID);
  const env = createEnv();
  const ctx = createExecutionContext();
  const fetchRequests: Request[] = [];

  await withMockedFetch(
    (request, callIndex) => {
      fetchRequests.push(request);
      if (callIndex === 0) {
        const ownership: JobStatusResponse = {
          created_at: 3,
          job_id: "job-malformed",
          session_id: SESSION_ID,
          status: "running",
          tenant_id: TENANT_ID,
          user_id: USER_ID,
        };
        return jsonResponse(ownership);
      }
      throw new Error(
        "Malformed artifact path should not forward to modal download route"
      );
    },
    async () => {
      const request = createRequest(
        "/jobs/job-malformed/artifacts/%E0%A4%A?session_id=sess-123&user_id=user-1&tenant_id=tenant-1",
        token
      );
      const response = await handleJobsEndpoint({
        ctx,
        env,
        path: new URL(request.url).pathname,
        request,
        scheduleJobEvent: () => {
          // no-op in tests
        },
      });

      assert.equal(response.status, 400);
      const payload = (await response.json()) as {
        error?: string;
        ok?: boolean;
      };
      assert.equal(payload.error, "Invalid artifact path encoding");
      assert.equal(payload.ok, false);
    }
  );

  assert.equal(fetchRequests.length, 1);
  assert.equal(new URL(fetchRequests[0].url).pathname, "/jobs/job-malformed");
});

test("GET /jobs/:id rejects invalid ownership payloads with 502", async () => {
  const token = buildSessionToken(SESSION_ID);
  const env = createEnv();
  const ctx = createExecutionContext();

  await withMockedFetch(
    () =>
      Promise.resolve(
        jsonResponse({
          ok: true,
        })
      ),
    async () => {
      const request = createRequest(
        "/jobs/job-invalid?session_id=sess-123&user_id=user-1&tenant_id=tenant-1",
        token
      );
      const response = await handleJobsEndpoint({
        ctx,
        env,
        path: new URL(request.url).pathname,
        request,
        scheduleJobEvent: () => {
          // no-op in tests
        },
      });

      assert.equal(response.status, 502);
      assert.deepEqual(await response.json(), {
        error: "Invalid job status response from Modal backend",
        ok: false,
      });
    }
  );
});

test("GET /jobs/:id rejects ownership payloads missing user identity required by auth", async () => {
  const token = buildSessionToken(SESSION_ID);
  const env = createEnv();
  const ctx = createExecutionContext();

  await withMockedFetch(
    () =>
      Promise.resolve(
        jsonResponse({
          created_at: 4,
          job_id: "job-missing-user",
          session_id: SESSION_ID,
          status: "queued",
          tenant_id: TENANT_ID,
        })
      ),
    async () => {
      const request = createRequest(
        "/jobs/job-missing-user?session_id=sess-123&user_id=user-1&tenant_id=tenant-1",
        token
      );
      const response = await handleJobsEndpoint({
        ctx,
        env,
        path: new URL(request.url).pathname,
        request,
        scheduleJobEvent: () => {
          // no-op in tests
        },
      });

      assert.equal(response.status, 502);
      assert.deepEqual(await response.json(), {
        error: "Job status response missing user_id for ownership enforcement",
        ok: false,
      });
    }
  );
});
