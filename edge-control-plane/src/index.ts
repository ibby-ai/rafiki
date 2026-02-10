/**
 * Cloudflare Worker Entry Point
 * 
 * Main API gateway that routes requests to:
 * - SessionAgent DOs for session-specific operations
 * - EventBus DOs for real-time updates
 * - Modal backend for job execution
 * 
 * Authentication flow:
 * 1. Client → Worker: Bearer token or session token
 * 2. Worker validates token and extracts user/session context
 * 3. Worker → DO: Internal authenticated request
 * 4. DO → Modal: Signed request with internal auth token
 */

import { buildInternalAuthToken } from "./auth/internalAuth";
import { authenticateClientRequest, AuthError } from "./auth/sessionAuth";
import { EventBus } from "./durable-objects/EventBus";
import { SessionAgent } from "./durable-objects/SessionAgent";
import type {
    Env,
    JobEventMessage,
    JobSubmitRequest,
    JobSubmitResponse,
    JobStatusResponse,
    QueryRequest,
    ScheduleCreateRequest,
    ScheduleListResponse,
    ScheduleResponse,
    ScheduleUpdateRequest
} from "./types";

// Export Durable Objects
export { EventBus, SessionAgent };

/**
 * Main Worker fetch handler
 */
export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;
    
    // CORS headers for browser clients
    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization",
      "Access-Control-Max-Age": "86400"
    };
    
    // Handle preflight requests
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders });
    }
    
    try {
      // Route requests
      let response: Response;
      
      if (path === "/health") {
        response = new Response(
          JSON.stringify({ ok: true, service: "edge-control-plane" }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      } else if (path === "/query") {
        response = await handleQuery(request, env);
      } else if (path === "/query_stream") {
        response = await handleQueryStream(request, env);
      } else if (path === "/submit") {
        response = await handleJobSubmit(request, env, ctx);
      } else if (path.startsWith("/jobs/")) {
        response = await handleJobsEndpoint(request, env, ctx, path);
      } else if (path === "/schedules" || path.startsWith("/schedules/")) {
        response = await handleSchedulesEndpoint(request, env, path);
      } else if (path.startsWith("/session/")) {
        response = await handleSessionEndpoint(request, env, path);
      } else if (path === "/ws" || path === "/events") {
        response = await handleEventBusConnection(request, env);
      } else {
        response = new Response("Not found", { status: 404 });
      }
      
      // WebSocket responses must be returned as-is
      if (response.status === 101 || response.webSocket) {
        return response;
      }

      // Add CORS headers to response
      const headers = new Headers(response.headers);
      for (const [key, value] of Object.entries(corsHeaders)) {
        headers.set(key, value);
      }

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers
      });
    } catch (error) {
      if (error instanceof AuthError) {
        return new Response(
          JSON.stringify({ ok: false, error: error.message }),
          { status: error.status, headers: { "Content-Type": "application/json", ...corsHeaders } }
        );
      }
      console.error("Worker error:", error);
      return new Response(
        JSON.stringify({ 
          ok: false, 
          error: error instanceof Error ? error.message : "Unknown error" 
        }),
        { 
          status: 500, 
          headers: { 
            "Content-Type": "application/json",
            ...corsHeaders
          } 
        }
      );
    }
  }
} satisfies ExportedHandler<Env>;

async function enforceRateLimit(options: {
  env: Env;
  key: string;
  route: string;
}): Promise<Response | null> {
  if (!options.env.RATE_LIMITER) return null;
  const rateKey = `${options.route}:${options.key}`;
  const result = await options.env.RATE_LIMITER.limit({ key: rateKey });
  if (result.success) return null;
  return new Response(
    JSON.stringify({ ok: false, error: "Rate limit exceeded" }),
    {
      status: 429,
      headers: {
        "Content-Type": "application/json",
        ...(result.reset ? { "Retry-After": String(result.reset) } : {})
      }
    }
  );
}

function scheduleJobEvent(
  env: Env,
  ctx: ExecutionContext,
  auth: { session_id: string; user_id?: string; tenant_id?: string },
  message: JobEventMessage
): void {
  const busName = auth.tenant_id || auth.user_id || "anonymous";
  const doId = env.EVENT_BUS.idFromName(busName);
  const doStub = env.EVENT_BUS.get(doId);
  const filter: { session_ids?: string[]; user_ids?: string[]; tenant_ids?: string[] } = {};
  if (auth.session_id) filter.session_ids = [auth.session_id];
  if (auth.user_id) filter.user_ids = [auth.user_id];
  if (auth.tenant_id) filter.tenant_ids = [auth.tenant_id];

  ctx.waitUntil(
    doStub.fetch(
      new Request("https://internal/broadcast", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, filter })
      })
    )
  );
}

/**
 * Handle query requests (sync and streaming)
 */
async function handleQuery(request: Request, env: Env): Promise<Response> {
  const body = await request.json() as QueryRequest;

  const auth = await authenticateClientRequest({
    request,
    env,
    sessionId: body.session_id,
    sessionKey: body.session_key,
    userId: body.user_id,
    tenantId: body.tenant_id
  });

  const rateKey = auth.user_id || auth.tenant_id || auth.session_id;
  const rateLimited = await enforceRateLimit({ env, key: rateKey, route: "/query" });
  if (rateLimited) return rateLimited;

  const forwardedBody: QueryRequest = {
    ...body,
    session_id: auth.session_id,
    session_key: auth.session_key,
    user_id: auth.user_id,
    tenant_id: auth.tenant_id
  };

  // Get SessionAgent DO
  const doId = env.SESSION_AGENT.idFromName(auth.session_id);
  const doStub = env.SESSION_AGENT.get(doId);

  // For non-streaming, forward to SessionAgent and return response
  const response = await doStub.fetch(
    new Request("https://internal/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(forwardedBody)
    })
  );

  return response;
}

/**
 * Handle streaming query via WebSocket upgrade
 */
async function handleQueryStream(request: Request, env: Env): Promise<Response> {
  if (request.headers.get("Upgrade") !== "websocket") {
    return new Response("WebSocket upgrade required", { status: 426 });
  }

  const url = new URL(request.url);
  const auth = await authenticateClientRequest({
    request,
    env,
    sessionId: url.searchParams.get("session_id"),
    sessionKey: url.searchParams.get("session_key"),
    userId: url.searchParams.get("user_id"),
    tenantId: url.searchParams.get("tenant_id")
  });
  const rateKey = auth.user_id || auth.tenant_id || auth.session_id;
  const rateLimited = await enforceRateLimit({ env, key: rateKey, route: "/query_stream" });
  if (rateLimited) return rateLimited;
  url.searchParams.delete("token");
  url.searchParams.set("session_id", auth.session_id);
  if (auth.session_key) url.searchParams.set("session_key", auth.session_key);
  if (auth.user_id) url.searchParams.set("user_id", auth.user_id);
  if (auth.tenant_id) url.searchParams.set("tenant_id", auth.tenant_id);

  const doId = env.SESSION_AGENT.idFromName(auth.session_id);
  const doStub = env.SESSION_AGENT.get(doId);

  return doStub.fetch(
    new Request(`https://internal/query_stream?${url.searchParams.toString()}`, request)
  );
}

/**
 * Handle job submission
 */
async function handleJobSubmit(
  request: Request,
  env: Env,
  ctx: ExecutionContext
): Promise<Response> {
  const body = await request.json() as JobSubmitRequest;

  const auth = await authenticateClientRequest({
    request,
    env,
    sessionId: body.session_id,
    sessionKey: body.session_key,
    userId: body.user_id,
    tenantId: body.tenant_id
  });
  const rateKey = auth.user_id || auth.tenant_id || auth.session_id;
  const rateLimited = await enforceRateLimit({ env, key: rateKey, route: "/submit" });
  if (rateLimited) return rateLimited;
  
  // Generate job ID
  const jobId = crypto.randomUUID();
  
  // Forward to Modal backend for job queueing
  const modalUrl = `${env.MODAL_API_BASE_URL}/submit`;
  const authToken = await buildInternalAuthToken(env.INTERNAL_AUTH_SECRET);
  
  const response = await fetch(modalUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Internal-Auth": authToken
    },
    body: JSON.stringify({
      ...body,
      job_id: jobId,
      session_id: auth.session_id,
      session_key: auth.session_key,
      user_id: auth.user_id,
      tenant_id: auth.tenant_id
    })
  });
  
  if (!response.ok) {
    const error = await response.text();
    return new Response(
      JSON.stringify({ ok: false, error }),
      { status: response.status, headers: { "Content-Type": "application/json" } }
    );
  }
  
  const result: JobSubmitResponse = { ok: true, job_id: jobId };
  const jobEvent: JobEventMessage = {
    type: "job_submitted",
    session_id: auth.session_id,
    timestamp: Date.now(),
    data: {
      job_id: jobId,
      status: "queued",
      user_id: auth.user_id,
      tenant_id: auth.tenant_id
    }
  };
  scheduleJobEvent(env, ctx, auth, jobEvent);
  
  return new Response(
    JSON.stringify(result),
    { status: 200, headers: { "Content-Type": "application/json" } }
  );
}

/**
 * Handle job-related endpoints
 */
async function handleJobsEndpoint(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
  path: string
): Promise<Response> {
  // Extract job ID from path
  const match = path.match(/^\/jobs\/([^\/]+)(\/.*)?$/);
  if (!match) {
    return new Response("Invalid job path", { status: 400 });
  }
  
  const jobId = match[1];
  const subpath = match[2] || "";
  
  const auth = await authenticateClientRequest({
    request,
    env,
    sessionId: new URL(request.url).searchParams.get("session_id"),
    sessionKey: new URL(request.url).searchParams.get("session_key"),
    userId: new URL(request.url).searchParams.get("user_id"),
    tenantId: new URL(request.url).searchParams.get("tenant_id")
  });

  const modalUrl = `${env.MODAL_API_BASE_URL}${path}`;
  const authToken = await buildInternalAuthToken(env.INTERNAL_AUTH_SECRET);
  
  const response = await fetch(modalUrl, {
    method: request.method,
    headers: {
      "Content-Type": "application/json",
      "X-Internal-Auth": authToken,
      "X-Session-Id": auth.session_id,
      "X-Session-Key": auth.session_key || "",
      "X-User-Id": auth.user_id || "",
      "X-Tenant-Id": auth.tenant_id || ""
    }
  });

  if (response.ok) {
    const clone = response.clone();
    ctx.waitUntil(
      (async () => {
        try {
          const payload = (await clone.json()) as JobStatusResponse;
          const jobEvent: JobEventMessage = {
            type: "job_status",
            session_id: payload.session_id || auth.session_id,
            timestamp: Date.now(),
            data: {
              job_id: payload.job_id || jobId,
              status: payload.status,
              user_id: payload.user_id || auth.user_id,
              tenant_id: payload.tenant_id || auth.tenant_id,
              payload
            }
          };
          scheduleJobEvent(env, ctx, auth, jobEvent);
        } catch (error) {
          console.warn("Failed to publish job_status event", error);
        }
      })()
    );
  }

  return response;
}

/**
 * Handle session-specific endpoints
 */
async function handleSessionEndpoint(request: Request, env: Env, path: string): Promise<Response> {
  // Extract session ID from path
  const match = path.match(/^\/session\/([^\/]+)(\/.*)?$/);
  if (!match) {
    return new Response("Invalid session path", { status: 400 });
  }
  
  const sessionId = match[1];
  const subpath = match[2] || "";

  const auth = await authenticateClientRequest({
    request,
    env,
    sessionId,
    sessionKey: new URL(request.url).searchParams.get("session_key"),
    userId: new URL(request.url).searchParams.get("user_id"),
    tenantId: new URL(request.url).searchParams.get("tenant_id")
  });
  if (auth.session_id !== sessionId) {
    return new Response("Session mismatch", { status: 403 });
  }
  
  // Get SessionAgent DO
  const doId = env.SESSION_AGENT.idFromName(auth.session_id);
  const doStub = env.SESSION_AGENT.get(doId);

  const forwardUrl = new URL(`https://internal${subpath || "/state"}`);
  forwardUrl.searchParams.set("session_id", auth.session_id);
  if (auth.session_key) forwardUrl.searchParams.set("session_key", auth.session_key);
  if (auth.user_id) forwardUrl.searchParams.set("user_id", auth.user_id);
  if (auth.tenant_id) forwardUrl.searchParams.set("tenant_id", auth.tenant_id);

  // Forward request to DO
  const response = await doStub.fetch(
    new Request(forwardUrl.toString(), {
      method: request.method,
      headers: request.headers,
      body: request.body
    })
  );
  
  return response;
}

/**
 * Handle schedule CRUD endpoints by forwarding to Modal backend.
 */
async function handleSchedulesEndpoint(
  request: Request,
  env: Env,
  path: string
): Promise<Response> {
  let body: ScheduleCreateRequest | ScheduleUpdateRequest | undefined;
  if (request.method === "PATCH" || (request.method === "POST" && path === "/schedules")) {
    body = await request.json() as ScheduleCreateRequest | ScheduleUpdateRequest;
  }

  const url = new URL(request.url);
  const auth = await authenticateClientRequest({
    request,
    env,
    sessionId: url.searchParams.get("session_id"),
    sessionKey: url.searchParams.get("session_key"),
    userId: url.searchParams.get("user_id"),
    tenantId: url.searchParams.get("tenant_id")
  });

  const modalUrl = new URL(`${env.MODAL_API_BASE_URL}${path}`);
  for (const [key, value] of url.searchParams.entries()) {
    if (key === "token") continue;
    modalUrl.searchParams.set(key, value);
  }

  const authToken = await buildInternalAuthToken(env.INTERNAL_AUTH_SECRET);
  const response = await fetch(modalUrl.toString(), {
    method: request.method,
    headers: {
      "Content-Type": "application/json",
      "X-Internal-Auth": authToken,
      "X-Session-Id": auth.session_id,
      "X-Session-Key": auth.session_key || "",
      "X-User-Id": auth.user_id || "",
      "X-Tenant-Id": auth.tenant_id || ""
    },
    body: body ? JSON.stringify(body) : undefined
  });

  // Narrow response shapes for TS and keep passthrough payload unchanged.
  if (response.ok && request.method === "GET" && path === "/schedules") {
    const payload = await response.clone().json() as ScheduleListResponse;
    return new Response(JSON.stringify(payload), {
      status: response.status,
      headers: response.headers
    });
  }
  if (response.ok && request.method === "GET" && path.startsWith("/schedules/")) {
    const payload = await response.clone().json() as ScheduleResponse;
    return new Response(JSON.stringify(payload), {
      status: response.status,
      headers: response.headers
    });
  }

  return response;
}

/**
 * Handle EventBus WebSocket connection
 */
async function handleEventBusConnection(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  
  // Extract user/tenant context for EventBus routing
  const auth = await authenticateClientRequest({
    request,
    env,
    sessionId: url.searchParams.get("session_id"),
    sessionKey: url.searchParams.get("session_key"),
    userId: url.searchParams.get("user_id"),
    tenantId: url.searchParams.get("tenant_id"),
    requireUserOrTenant: true
  });
  const rateKey = auth.user_id || auth.tenant_id || auth.session_id;
  const rateLimited = await enforceRateLimit({ env, key: rateKey, route: url.pathname });
  if (rateLimited) return rateLimited;
  url.searchParams.delete("token");
  if (auth.user_id) url.searchParams.set("user_id", auth.user_id);
  if (auth.tenant_id) url.searchParams.set("tenant_id", auth.tenant_id);
  if (auth.session_id) url.searchParams.set("session_id", auth.session_id);
  if (auth.session_key) url.searchParams.set("session_key", auth.session_key);
  
  // Use user_id or tenant_id as EventBus DO name
  const busName = auth.tenant_id || auth.user_id || "anonymous";
  const doId = env.EVENT_BUS.idFromName(busName);
  const doStub = env.EVENT_BUS.get(doId);
  
  // Forward WebSocket upgrade to EventBus DO
  const response = await doStub.fetch(
    new Request(`https://internal/?${url.searchParams}`, {
      method: "GET",
      headers: request.headers
    })
  );
  
  return response;
}
