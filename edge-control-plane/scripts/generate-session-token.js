#!/usr/bin/env node

const crypto = require("node:crypto");

function printHelp() {
  console.log(`Generate a Cloudflare session token compatible with src/auth/session-auth.ts.

Usage:
  node edge-control-plane/scripts/generate-session-token.js [options]

Options:
  --user-id <value>           Optional user_id claim
  --tenant-id <value>         Optional tenant_id claim
  --session-id <value>        Single session_id claim
  --session-ids <a,b,c>       Comma-separated session_ids claim
  --ttl-seconds <seconds>     Token TTL in seconds (default: 3600)
  --expires-at-ms <ms>        Absolute expires_at timestamp in milliseconds
  --issued-at-ms <ms>         Absolute issued_at timestamp in milliseconds (default: now)
  --secret <value>            Signing secret (default: SESSION_SIGNING_SECRET env)
  --json                      Print payload + token JSON
  --help                      Show this help

Examples:
  node edge-control-plane/scripts/generate-session-token.js \\
    --user-id e2e-user --tenant-id e2e-tenant --session-id sess-e2e-001

  node edge-control-plane/scripts/generate-session-token.js \\
    --session-ids sess-1,sess-2 --ttl-seconds 1800 --secret "$SESSION_SIGNING_SECRET"
`);
}

function parseArgs(argv) {
  const args = {
    userId: undefined,
    tenantId: undefined,
    sessionId: undefined,
    sessionIds: undefined,
    ttlSeconds: 3600,
    expiresAtMs: undefined,
    issuedAtMs: Date.now(),
    secret: process.env.SESSION_SIGNING_SECRET,
    asJson: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];

    if (arg === "--help" || arg === "-h") {
      printHelp();
      process.exit(0);
    }

    if (arg === "--json") {
      args.asJson = true;
      continue;
    }

    const next = argv[i + 1];
    const readValue = () => {
      if (!next || next.startsWith("--")) {
        throw new Error(`Missing value for ${arg}`);
      }
      i += 1;
      return next;
    };

    switch (arg) {
      case "--user-id":
        args.userId = readValue();
        break;
      case "--tenant-id":
        args.tenantId = readValue();
        break;
      case "--session-id":
        args.sessionId = readValue();
        break;
      case "--session-ids": {
        const raw = readValue();
        const parsed = raw
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean);
        args.sessionIds = parsed.length > 0 ? parsed : undefined;
        break;
      }
      case "--ttl-seconds":
        args.ttlSeconds = Number.parseInt(readValue(), 10);
        break;
      case "--expires-at-ms":
        args.expiresAtMs = Number.parseInt(readValue(), 10);
        break;
      case "--issued-at-ms":
        args.issuedAtMs = Number.parseInt(readValue(), 10);
        break;
      case "--secret":
        args.secret = readValue();
        break;
      default:
        throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return args;
}

function validateArgs(args) {
  if (!args.secret?.trim()) {
    throw new Error(
      "Missing signing secret. Set --secret or SESSION_SIGNING_SECRET."
    );
  }

  if (!Number.isFinite(args.issuedAtMs)) {
    throw new Error("issued_at must be a valid millisecond timestamp.");
  }

  if (args.expiresAtMs === undefined) {
    if (!Number.isFinite(args.ttlSeconds) || args.ttlSeconds <= 0) {
      throw new Error("ttl-seconds must be a positive integer.");
    }
    args.expiresAtMs = args.issuedAtMs + args.ttlSeconds * 1000;
  }

  if (!Number.isFinite(args.expiresAtMs)) {
    throw new Error("expires_at must be a valid millisecond timestamp.");
  }

  if (args.expiresAtMs < args.issuedAtMs) {
    throw new Error("expires_at must be greater than or equal to issued_at.");
  }

  if (args.sessionId && args.sessionIds && args.sessionIds.length > 0) {
    throw new Error("Use either --session-id or --session-ids, not both.");
  }

  if (!args.sessionId && (!args.sessionIds || args.sessionIds.length === 0)) {
    throw new Error("Provide --session-id or --session-ids.");
  }
}

function createPayload(args) {
  const payload = {
    issued_at: args.issuedAtMs,
    expires_at: args.expiresAtMs,
  };

  if (args.userId) {
    payload.user_id = args.userId;
  }
  if (args.tenantId) {
    payload.tenant_id = args.tenantId;
  }
  if (args.sessionId) {
    payload.session_id = args.sessionId;
  } else if (args.sessionIds && args.sessionIds.length > 0) {
    payload.session_ids = args.sessionIds;
  }

  return payload;
}

function signPayload(payload, secret) {
  const payloadBytes = Buffer.from(JSON.stringify(payload), "utf8");
  const payloadB64 = payloadBytes.toString("base64");
  const signatureB64 = crypto
    .createHmac("sha256", Buffer.from(secret, "utf8"))
    .update(payloadBytes)
    .digest("base64");
  return `${payloadB64}.${signatureB64}`;
}

function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    validateArgs(args);
    const payload = createPayload(args);
    const token = signPayload(payload, args.secret.trim());

    if (args.asJson) {
      process.stdout.write(`${JSON.stringify({ payload, token })}\n`);
      return;
    }

    process.stdout.write(`${token}\n`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`Error: ${message}\n`);
    process.stderr.write("Run with --help for usage.\n");
    process.exit(1);
  }
}

main();
