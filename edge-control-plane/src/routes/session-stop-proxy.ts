import {
  SessionStopRequestSchema,
  SessionStopResponseSchema,
} from "../contracts/public-api";
import type { ModalBackendResponse, SessionStopRequest } from "../types";

export type SessionStopProxyRequest =
  | { method: "GET" }
  | { body: SessionStopRequest; method: "POST" };

function jsonError(message: string, status: number): Response {
  return new Response(JSON.stringify({ ok: false, error: message }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Parse the public `/session/{id}/stop` request into a method-specific proxy shape.
 */
export async function parseSessionStopProxyRequest(
  request: Request
): Promise<Response | SessionStopProxyRequest> {
  if (request.method === "GET") {
    return { method: "GET" };
  }

  if (request.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  const rawBody = request.body ? await request.text() : "";
  if (!rawBody.length) {
    return {
      body: SessionStopRequestSchema.parse({}),
      method: "POST",
    };
  }

  let requestBody: unknown;
  try {
    requestBody = JSON.parse(rawBody);
  } catch {
    return jsonError("Invalid JSON request body", 400);
  }

  const parsedBody = SessionStopRequestSchema.safeParse(requestBody);
  if (!parsedBody.success) {
    return jsonError("Invalid session stop request body", 400);
  }

  return {
    body: parsedBody.data,
    method: "POST",
  };
}

/**
 * Validate and normalize a Modal stop/status response before returning it publicly.
 */
export function buildSessionStopProxyResponse(
  modalResponse: ModalBackendResponse,
  invalidPayloadMessage: string
): Response {
  if (!modalResponse.ok) {
    return jsonError(
      modalResponse.error || "Modal error",
      modalResponse.status || 502
    );
  }

  const stopResponse = SessionStopResponseSchema.safeParse(modalResponse.data);
  if (!stopResponse.success) {
    return jsonError(invalidPayloadMessage, 502);
  }

  return new Response(JSON.stringify(stopResponse.data), {
    status: modalResponse.status,
    headers: { "Content-Type": "application/json" },
  });
}
