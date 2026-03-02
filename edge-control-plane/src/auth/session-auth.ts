import type { AuthContext, Env, SessionToken } from "../types";

const encoder = new TextEncoder();
const DEFAULT_SKEW_MS = 60_000;

export class AuthError extends Error {
  status: number;
  constructor(message: string, status = 401) {
    super(message);
    this.status = status;
  }
}

export interface AuthenticatedRequest extends AuthContext {
  session_id: string;
  session_key?: string | null;
}

function base64DecodeToBytes(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function base64EncodeBytes(bytes: ArrayBuffer | Uint8Array): string {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let binary = "";
  for (const byte of view) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

function normalizeToken(rawToken: string): string {
  const trimmed = rawToken.trim();
  if (trimmed.toLowerCase().startsWith("bearer ")) {
    return trimmed.slice(7).trim();
  }
  return trimmed;
}

function requireSecret(secret?: string): string {
  if (!secret?.trim()) {
    throw new AuthError("Session signing secret not configured", 500);
  }
  return secret.trim();
}

export async function verifySessionToken(
  rawToken: string,
  secret: string,
  nowMs: number = Date.now(),
  skewMs: number = DEFAULT_SKEW_MS
): Promise<AuthContext> {
  const token = normalizeToken(rawToken);
  if (!token) {
    throw new AuthError("Missing authorization token", 401);
  }

  const parts = token.split(".");
  if (parts.length !== 2) {
    throw new AuthError("Invalid token format", 401);
  }

  const [payloadB64, signatureB64] = parts;
  let payloadBytes: Uint8Array;
  try {
    payloadBytes = base64DecodeToBytes(payloadB64);
  } catch {
    throw new AuthError("Invalid token payload", 401);
  }

  let payload: SessionToken;
  try {
    payload = JSON.parse(
      new TextDecoder().decode(payloadBytes)
    ) as SessionToken;
  } catch {
    throw new AuthError("Invalid token payload", 401);
  }

  if (!payload || typeof payload !== "object") {
    throw new AuthError("Invalid token payload", 401);
  }

  const issuedAt = Number(payload.issued_at);
  const expiresAt = Number(payload.expires_at);
  if (!(Number.isFinite(issuedAt) && Number.isFinite(expiresAt))) {
    throw new AuthError("Invalid token timestamps", 401);
  }

  if (issuedAt > nowMs + skewMs) {
    throw new AuthError("Token issued in the future", 401);
  }
  if (expiresAt < nowMs - skewMs) {
    throw new AuthError("Token expired", 401);
  }
  if (expiresAt < issuedAt) {
    throw new AuthError("Invalid token timestamps", 401);
  }

  const keyData = encoder.encode(secret);
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    keyData,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", cryptoKey, payloadBytes);
  const expectedSignature = base64EncodeBytes(signature);

  if (expectedSignature !== signatureB64) {
    throw new AuthError("Invalid token signature", 401);
  }

  const sessionIds = Array.isArray(payload.session_ids)
    ? payload.session_ids.filter(
        (value): value is string => typeof value === "string"
      )
    : undefined;

  if (
    !sessionIds?.length &&
    typeof payload.session_id === "string" &&
    payload.session_id
  ) {
    return {
      user_id: payload.user_id || undefined,
      tenant_id: payload.tenant_id || undefined,
      session_ids: [payload.session_id],
      issued_at: issuedAt,
      expires_at: expiresAt,
    };
  }

  return {
    user_id: payload.user_id || undefined,
    tenant_id: payload.tenant_id || undefined,
    session_ids: sessionIds,
    issued_at: issuedAt,
    expires_at: expiresAt,
  };
}

function extractAuthToken(request: Request): string | null {
  const header = request.headers.get("Authorization");
  if (header) {
    if (!header.toLowerCase().startsWith("bearer ")) {
      throw new AuthError("Invalid authorization header", 401);
    }
    return header.slice(7).trim();
  }

  const url = new URL(request.url);
  const tokenParam = url.searchParams.get("token");
  return tokenParam ? tokenParam.trim() : null;
}

function normalizeOptionalId(value?: string | null): string | undefined {
  if (!value) {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function resolveSessionTtlSeconds(env: Env): number {
  const raw = env.SESSION_KEY_TTL_SECONDS;
  const parsed = raw ? Number.parseInt(raw, 10) : Number.NaN;
  if (Number.isFinite(parsed) && parsed > 0) {
    return parsed;
  }
  return 60 * 60 * 24 * 30; // 30 days
}

function buildSessionKeyCacheKey(scope: string, sessionKey: string): string {
  return `session_key:${scope}:${sessionKey}`;
}

function assertRequestedContextMatches(
  context: AuthContext,
  requestedUserId: string | undefined,
  requestedTenantId: string | undefined
): void {
  if (
    context.user_id &&
    requestedUserId &&
    context.user_id !== requestedUserId
  ) {
    throw new AuthError("user_id mismatch", 403);
  }
  if (
    context.tenant_id &&
    requestedTenantId &&
    context.tenant_id !== requestedTenantId
  ) {
    throw new AuthError("tenant_id mismatch", 403);
  }
}

function enforceSessionAuthorization(
  sessionIds: string[] | undefined,
  resolvedSessionId: string | undefined
): void {
  if (!resolvedSessionId && sessionIds && sessionIds.length > 0) {
    throw new AuthError("Session not authorized", 403);
  }

  if (
    resolvedSessionId &&
    sessionIds &&
    sessionIds.length > 0 &&
    !sessionIds.includes(resolvedSessionId)
  ) {
    throw new AuthError("Session not authorized", 403);
  }
}

async function resolveSessionIdFromKey(options: {
  env: Env;
  resolvedSessionId: string | undefined;
  sessionKey: string | undefined;
  cacheScope: string;
  sessionIds: string[] | undefined;
}): Promise<string | undefined> {
  if (options.resolvedSessionId || !options.sessionKey) {
    return options.resolvedSessionId;
  }

  const cacheKey = buildSessionKeyCacheKey(
    options.cacheScope,
    options.sessionKey
  );
  const cached = await options.env.SESSION_CACHE.get(cacheKey);
  if (cached) {
    return cached;
  }

  if (options.sessionIds && options.sessionIds.length > 0) {
    return options.resolvedSessionId;
  }

  const generatedSessionId = crypto.randomUUID();
  await options.env.SESSION_CACHE.put(cacheKey, generatedSessionId, {
    expirationTtl: resolveSessionTtlSeconds(options.env),
  });
  return generatedSessionId;
}

export async function authenticateClientRequest(options: {
  request: Request;
  env: Env;
  sessionId?: string | null;
  sessionKey?: string | null;
  userId?: string | null;
  tenantId?: string | null;
  requireUserOrTenant?: boolean;
}): Promise<AuthenticatedRequest> {
  const token = extractAuthToken(options.request);
  if (!token) {
    throw new AuthError("Missing authorization token", 401);
  }

  const secret = requireSecret(options.env.SESSION_SIGNING_SECRET);
  const context = await verifySessionToken(token, secret);

  const requestedUserId = normalizeOptionalId(options.userId);
  const requestedTenantId = normalizeOptionalId(options.tenantId);
  assertRequestedContextMatches(context, requestedUserId, requestedTenantId);

  const resolvedUserId = context.user_id || requestedUserId;
  const resolvedTenantId = context.tenant_id || requestedTenantId;

  if (options.requireUserOrTenant && !resolvedUserId && !resolvedTenantId) {
    throw new AuthError("Missing user or tenant context", 401);
  }

  const sessionIds = context.session_ids;
  const incomingSessionId = normalizeOptionalId(options.sessionId);
  const sessionKey = normalizeOptionalId(options.sessionKey);

  const cacheScope = resolvedTenantId || resolvedUserId || "anonymous";
  let resolvedSessionId = await resolveSessionIdFromKey({
    env: options.env,
    resolvedSessionId: incomingSessionId,
    sessionKey,
    cacheScope,
    sessionIds,
  });
  enforceSessionAuthorization(sessionIds, resolvedSessionId);

  if (!resolvedSessionId) {
    resolvedSessionId = crypto.randomUUID();
  }
  enforceSessionAuthorization(sessionIds, resolvedSessionId);

  if (sessionKey) {
    const cacheKey = buildSessionKeyCacheKey(cacheScope, sessionKey);
    await options.env.SESSION_CACHE.put(cacheKey, resolvedSessionId, {
      expirationTtl: resolveSessionTtlSeconds(options.env),
    });
  }

  return {
    session_id: resolvedSessionId,
    session_key: sessionKey,
    user_id: resolvedUserId,
    tenant_id: resolvedTenantId,
    session_ids: sessionIds,
    issued_at: context.issued_at,
    expires_at: context.expires_at,
  };
}
