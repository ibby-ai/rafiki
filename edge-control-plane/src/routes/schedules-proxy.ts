import { buildInternalAuthToken } from "../auth/internal-auth";
import { authenticateClientRequest } from "../auth/session-auth";
import {
  ScheduleCreateRequestSchema,
  ScheduleListResponseSchema,
  ScheduleResponseSchema,
  ScheduleUpdateRequestSchema,
} from "../contracts/public-api";
import type { Env } from "../types";

function jsonError(message: string, status: number): Response {
  return new Response(JSON.stringify({ ok: false, error: message }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function parseScheduleRequestBody(
  request: Request,
  path: string
): Promise<Response | object | undefined> {
  if (
    request.method !== "PATCH" &&
    !(request.method === "POST" && path === "/schedules")
  ) {
    return undefined;
  }

  let requestBody: unknown;
  try {
    requestBody = await request.json();
  } catch {
    return jsonError("Invalid JSON request body", 400);
  }

  const schema =
    request.method === "POST"
      ? ScheduleCreateRequestSchema
      : ScheduleUpdateRequestSchema;
  const parsedBody = schema.safeParse(requestBody);
  if (!parsedBody.success) {
    return jsonError("Invalid schedule request body", 400);
  }

  return parsedBody.data;
}

function validateScheduleResponse(
  request: Request,
  path: string,
  response: Response
): Response | Promise<Response> {
  if (!response.ok) {
    return response;
  }

  if (request.method === "GET" && path === "/schedules") {
    return validateScheduleListResponse(response);
  }

  if (request.method === "GET" && path.startsWith("/schedules/")) {
    return validateSingleScheduleResponse(response);
  }

  return response;
}

async function validateScheduleListResponse(
  response: Response
): Promise<Response> {
  let payload: unknown;
  try {
    payload = await response.clone().json();
  } catch {
    return jsonError("Invalid schedule list response from Modal backend", 502);
  }

  const parsedPayload = ScheduleListResponseSchema.safeParse(payload);
  if (!parsedPayload.success) {
    return jsonError("Invalid schedule list response from Modal backend", 502);
  }

  return new Response(JSON.stringify(parsedPayload.data), {
    status: response.status,
    headers: response.headers,
  });
}

async function validateSingleScheduleResponse(
  response: Response
): Promise<Response> {
  let payload: unknown;
  try {
    payload = await response.clone().json();
  } catch {
    return jsonError("Invalid schedule response from Modal backend", 502);
  }

  const parsedPayload = ScheduleResponseSchema.safeParse(payload);
  if (!parsedPayload.success) {
    return jsonError("Invalid schedule response from Modal backend", 502);
  }

  return new Response(JSON.stringify(parsedPayload.data), {
    status: response.status,
    headers: response.headers,
  });
}

/**
 * Forward schedule CRUD requests to the Modal backend with Worker auth context.
 */
export async function handleSchedulesEndpoint(
  request: Request,
  env: Env,
  path: string
): Promise<Response> {
  const body = await parseScheduleRequestBody(request, path);
  if (body instanceof Response) {
    return body;
  }

  const url = new URL(request.url);
  const auth = await authenticateClientRequest({
    request,
    env,
    sessionId: url.searchParams.get("session_id"),
    sessionKey: url.searchParams.get("session_key"),
    userId: url.searchParams.get("user_id"),
    tenantId: url.searchParams.get("tenant_id"),
  });

  const modalUrl = new URL(`${env.MODAL_API_BASE_URL}${path}`);
  for (const [key, value] of url.searchParams.entries()) {
    if (key === "token") {
      continue;
    }
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
      "X-Tenant-Id": auth.tenant_id || "",
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  return validateScheduleResponse(request, path, response);
}
