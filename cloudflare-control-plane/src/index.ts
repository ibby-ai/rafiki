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
import { EventBus } from "./durable-objects/EventBus";
import { SessionAgent } from "./durable-objects/SessionAgent";
import type {
    Env,
    JobSubmitRequest,
    JobSubmitResponse,
    QueryRequest
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
          JSON.stringify({ ok: true, service: "cloudflare-control-plane" }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      } else if (path === "/query") {
        response = await handleQuery(request, env);
      } else if (path === "/query_stream") {
        response = await handleQueryStream(request, env);
      } else if (path === "/submit") {
        response = await handleJobSubmit(request, env);
      } else if (path.startsWith("/jobs/")) {
        response = await handleJobsEndpoint(request, env, path);
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

/**
 * Handle query requests (sync and streaming)
 */
async function handleQuery(request: Request, env: Env): Promise<Response> {
  const body = await request.json() as QueryRequest;

  // Resolve or create session
  const sessionId = body.session_id || body.session_key || crypto.randomUUID();
  const forwardedBody: QueryRequest = {
    ...body,
    session_id: sessionId
  };

  // Get SessionAgent DO
  const doId = env.SESSION_AGENT.idFromName(sessionId);
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
  const sessionId =
    url.searchParams.get("session_id") ||
    url.searchParams.get("session_key") ||
    crypto.randomUUID();
  if (!url.searchParams.get("session_id")) {
    url.searchParams.set("session_id", sessionId);
  }

  const doId = env.SESSION_AGENT.idFromName(sessionId);
  const doStub = env.SESSION_AGENT.get(doId);

  return doStub.fetch(
    new Request(`https://internal/query_stream?${url.searchParams.toString()}`, request)
  );
}

/**
 * Handle job submission
 */
async function handleJobSubmit(request: Request, env: Env): Promise<Response> {
  const body = await request.json() as JobSubmitRequest;
  
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
      job_id: jobId
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
  
  return new Response(
    JSON.stringify(result),
    { status: 200, headers: { "Content-Type": "application/json" } }
  );
}

/**
 * Handle job-related endpoints
 */
async function handleJobsEndpoint(request: Request, env: Env, path: string): Promise<Response> {
  // Extract job ID from path
  const match = path.match(/^\/jobs\/([^\/]+)(\/.*)?$/);
  if (!match) {
    return new Response("Invalid job path", { status: 400 });
  }
  
  const jobId = match[1];
  const subpath = match[2] || "";
  
  // Forward to Modal backend
  const modalUrl = `${env.MODAL_API_BASE_URL}${path}`;
  const authToken = await buildInternalAuthToken(env.INTERNAL_AUTH_SECRET);
  
  const response = await fetch(modalUrl, {
    method: request.method,
    headers: {
      "Content-Type": "application/json",
      "X-Internal-Auth": authToken
    }
  });
  
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
  
  // Get SessionAgent DO
  const doId = env.SESSION_AGENT.idFromName(sessionId);
  const doStub = env.SESSION_AGENT.get(doId);

  const forwardUrl = new URL(`https://internal${subpath || "/state"}`);
  forwardUrl.searchParams.set("session_id", sessionId);

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
 * Handle EventBus WebSocket connection
 */
async function handleEventBusConnection(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  
  // Extract user/tenant context for EventBus routing
  const userId = url.searchParams.get("user_id") || "anonymous";
  const tenantId = url.searchParams.get("tenant_id");
  
  // Use user_id or tenant_id as EventBus DO name
  const busName = tenantId || userId;
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
