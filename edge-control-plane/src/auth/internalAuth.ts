import type { InternalAuthToken } from "../types";

const encoder = new TextEncoder();

function base64EncodeBytes(bytes: ArrayBuffer | Uint8Array): string {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let binary = "";
  for (let i = 0; i < view.length; i += 1) {
    binary += String.fromCharCode(view[i]);
  }
  return btoa(binary);
}

export async function buildInternalAuthToken(
  secret: string,
  nowMs: number = Date.now(),
  ttlMs: number = 300_000
): Promise<string> {
  const payload: InternalAuthToken = {
    service: "cloudflare-worker",
    issued_at: nowMs,
    expires_at: nowMs + ttlMs
  };

  const payloadStr = JSON.stringify(payload);
  const payloadBytes = encoder.encode(payloadStr);
  const keyData = encoder.encode(secret);

  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    keyData,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", cryptoKey, payloadBytes);

  const payloadB64 = base64EncodeBytes(payloadBytes);
  const signatureB64 = base64EncodeBytes(signature);

  return `${payloadB64}.${signatureB64}`;
}
