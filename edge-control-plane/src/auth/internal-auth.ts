/**
 * Worker-internal token helpers for requests sent to Modal and artifact routes.
 *
 * @module auth/internal-auth
 */

import type { ArtifactAccessToken, InternalAuthToken } from "../types";

const encoder = new TextEncoder();

function base64EncodeBytes(bytes: ArrayBuffer | Uint8Array): string {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let binary = "";
  for (const byte of view) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

async function signPayload(
  secret: string,
  payloadBytes: Uint8Array
): Promise<string> {
  const keyData = encoder.encode(secret);
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    keyData,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", cryptoKey, payloadBytes);
  return base64EncodeBytes(signature);
}

/**
 * Build a signed token string from an arbitrary JSON payload.
 */
export async function buildSignedInternalToken(
  secret: string,
  payload: object
): Promise<string> {
  const payloadStr = JSON.stringify(payload);
  const payloadBytes = encoder.encode(payloadStr);
  const signatureB64 = await signPayload(secret, payloadBytes);
  const payloadB64 = base64EncodeBytes(payloadBytes);
  return `${payloadB64}.${signatureB64}`;
}

/**
 * Mint the short-lived auth token used for Worker-to-Modal requests.
 */
export function buildInternalAuthToken(
  secret: string,
  nowMs: number = Date.now(),
  ttlMs = 300_000
): Promise<string> {
  const payload: InternalAuthToken = {
    service: "cloudflare-worker",
    issued_at: nowMs,
    expires_at: nowMs + ttlMs,
  };
  return buildSignedInternalToken(secret, payload);
}

/**
 * Mint a scoped artifact token for a single job artifact download path.
 */
export async function buildArtifactAccessToken(options: {
  secret: string;
  sessionId: string;
  jobId: string;
  artifactPath: string;
  ttlMs?: number;
}): Promise<string> {
  const nowMs = Date.now();
  const ttlMs = Math.max(1, options.ttlMs ?? 120_000);
  const tokenId = crypto.randomUUID();
  const artifactId = await crypto.subtle.digest(
    "SHA-256",
    encoder.encode(`${options.jobId}:${options.artifactPath}`)
  );
  const artifactIdHex = Array.from(new Uint8Array(artifactId))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 32);

  const payload: ArtifactAccessToken = {
    service: "cloudflare-worker-artifact",
    session_id: options.sessionId,
    job_id: options.jobId,
    artifact_path: options.artifactPath,
    artifact_id: artifactIdHex,
    token_id: tokenId,
    issued_at: nowMs,
    expires_at: nowMs + ttlMs,
  };
  return buildSignedInternalToken(options.secret, payload);
}
