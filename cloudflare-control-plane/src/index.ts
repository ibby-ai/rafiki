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
      } else if (path === "/query" || path === "/query_stream") {
        response = await handleQuery(request, env, path === "/query_stream");
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
async function handleQuery(request: Request, env: Env, streaming: boolean): Promise<Response> {
  const body = await request.json() as QueryRequest;
  
  // Resolve or create session
  const sessionId = body.session_id || body.session_key || crypto.randomUUID();
  
  // Get SessionAgent DO
  const doId = env.SESSION_AGENT.idFromName(sessionId);
  const doStub = env.SESSION_AGENT.get(doId);
  
  if (streaming) {
    // For streaming, establish WebSocket connection to SessionAgent
    // and proxy events to client
    return handleStreamingQuery(doStub, body);
  } else {
    // For non-streaming, forward to SessionAgent and return response
    const response = await doStub.fetch(
      new Request("https://internal/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      })
    );
    
    return response;
  }
}

/**
 * Handle streaming query via WebSocket
 */
async function handleStreamingQuery(doStub: DurableObjectStub, body: QueryRequest): Promise<Response> {
  // Create WebSocket pair
  const pair = new WebSocketPair();
  const [client, server] = Object.values(pair);
  
  // Connect to SessionAgent DO via WebSocket
  const doWsResponse = await doStub.fetch(
    new Request("https://internal/query", {
      method: "POST",
      headers: { 
        "Upgrade": "websocket",
        "Content-Type": "application/json"
      },
      body: JSON.stringify(body)
    })
  );
  
  if (doWsResponse.status !== 101) {
    return new Response("Failed to upgrade to WebSocket", { status: 500 });
  }
  
  const doWebSocket = doWsResponse.webSocket;
  if (!doWebSocket) {
    return new Response("No WebSocket in response", { status: 500 });
  }
  
  // Proxy messages between client and DO
  doWebSocket.accept();
  
  doWebSocket.addEventListener("message", (event) => {
    try {
      server.send(event.data);
    } catch (error) {
      console.error("Failed to forward message to client:", error);
    }
  });
  
  server.addEventListener("message", (event) => {
    try {
      doWebSocket.send(event.data);
    } catch (error) {
      console.error("Failed to forward message to DO:", error);
    }
  });
  
  doWebSocket.addEventListener("close", () => {
    try {
      server.close();
    } catch (error) {
      console.error("Failed to close client socket:", error);
    }
  });
  
  server.addEventListener("close", () => {
    try {
      doWebSocket.close();
    } catch (error) {
      console.error("Failed to close DO socket:", error);
    }
  });
  
  return new Response(null, {
    status: 101,
    webSocket: client
  });
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
  const authToken = await generateInternalAuthToken(env);
  
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
  const authToken = await generateInternalAuthToken(env);
  
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
  
  // Forward request to DO
  const response = await doStub.fetch(
    new Request(`https://internal${subpath || "/state"}`, {
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

/**
 * Generate internal authentication token for Modal backend
 */
async function generateInternalAuthToken(env: Env): Promise<string> {
  const payload = {
    service: "cloudflare-worker",
    issued_at: Date.now(),
    expires_at: Date.now() + 300000 // 5 minutes
  };
  
  const payloadStr = JSON.stringify(payload);
  const encoder = new TextEncoder();
  const data = encoder.encode(payloadStr);
  const key = encoder.encode(env.INTERNAL_AUTH_SECRET);
  
  // Simple HMAC signing (in production, use proper JWT library)
  const signature = await crypto.subtle.importKey(
    "raw",
    key,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  ).then(cryptoKey => 
    crypto.subtle.sign("HMAC", cryptoKey, data)
  ).then(sig => 
    btoa(String.fromCharCode(...new Uint8Array(sig)))
  );
  
  return `${btoa(payloadStr)}.${signature}`;
}
