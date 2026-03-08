# Cloudflare <-> Modal E2E Runbook

This is the canonical end-to-end test runbook for the Cloudflare Worker control plane and Modal backend.

## Prerequisites

### Local tooling

- Python + `uv`
- Node.js 20+
- Modal CLI authenticated: `modal setup`
- Wrangler authenticated: `wrangler login`
- `wscat` installed for WebSocket verification: `npm install -g wscat`

### Required secrets

Modal (must exist in the authenticated Modal workspace):

```bash
modal secret create openai-secret OPENAI_API_KEY=<your-openai-key>
modal secret create internal-auth-secret INTERNAL_AUTH_SECRET=<shared-internal-secret>
# Required when ENABLE_MODAL_AUTH_SECRET=true (default):
modal secret create modal-auth-secret \
  SANDBOX_MODAL_TOKEN_ID=<token-id> \
  SANDBOX_MODAL_TOKEN_SECRET=<token-secret>
```

Cloudflare Worker secrets (in `edge-control-plane`):

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane
wrangler secret put INTERNAL_AUTH_SECRET
wrangler secret put SESSION_SIGNING_SECRET
```

Notes:

- The standard local Cloudflare <-> Modal `/health`, `/query`, `/query_stream`, queue, and
  state flow only consumes `INTERNAL_AUTH_SECRET` and `SESSION_SIGNING_SECRET` on the Worker.
- `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` are not consumed by the current canonical
  `edge-control-plane/src` E2E request path documented in this runbook.

### Required bindings

- KV binding `SESSION_CACHE` configured in `edge-control-plane/wrangler.jsonc`
- `MODAL_API_BASE_URL` set in `edge-control-plane/wrangler.jsonc`
- Optional but recommended: `RATE_LIMITER` binding in `edge-control-plane/wrangler.jsonc`

## Startup Order (Required)

1. Start Modal backend first:

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
source .venv/bin/activate
uv run modal serve -m modal_backend.main
```

2. In a second terminal, start Worker dev server:

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane
npm run dev
# equivalent explicit command:
# npx wrangler dev
```

## Standard Environment Setup

Run this in a third terminal before cURL/WebSocket tests:

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
source .venv/bin/activate

export MODAL_API_BASE_URL="$(rg -o '"MODAL_API_BASE_URL": "[^"]+"' edge-control-plane/wrangler.jsonc | sed -E 's/.*: "([^"]+)"/\1/')"
export DEV_URL="$MODAL_API_BASE_URL"
export WORKER_URL="http://localhost:8787"

# Must match the value configured via wrangler secret put SESSION_SIGNING_SECRET
export SESSION_SIGNING_SECRET="<your-session-signing-secret>"
```

Auth consistency expectations:

- `INTERNAL_AUTH_SECRET` must be the same value in both:
  - Cloudflare Worker secret `INTERNAL_AUTH_SECRET`
  - Modal secret `internal-auth-secret` (`INTERNAL_AUTH_SECRET=...`)
- Session tokens for Worker requests must be signed with `SESSION_SIGNING_SECRET`.

## Generate Session Token (Single Step)

Use the helper script below (do not hand-build token signatures):

```bash
TOKEN="$(node edge-control-plane/scripts/generate-session-token.js \
  --user-id e2e-user \
  --tenant-id e2e-tenant \
  --session-id sess-e2e-001 \
  --ttl-seconds 3600 \
  --secret "$SESSION_SIGNING_SECRET")"
```

Token output is directly usable as:

```http
Authorization: Bearer <TOKEN>
```

## E2E Test Steps

### 0) Edge quality gates

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
npm --prefix edge-control-plane run check
cd edge-control-plane && ./node_modules/.bin/tsc --noEmit
npm --prefix edge-control-plane run test:integration
```

Expected:

- lint check passes (`ultracite check`).
- type-check passes.
- integration tests pass for ownership precheck, artifact token forwarding, and malformed path `400` handling.

### 1) Worker health

```bash
curl -sS "$WORKER_URL/health"
```

Expected:

- HTTP `200`
- JSON includes `{"ok":true,"service":"rafiki-control-plane"}`

### 2) `/query` (sync)

```bash
curl -sS -X POST "$WORKER_URL/query" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Return exactly: e2e-ok",
    "session_id": "sess-e2e-001"
  }'
```

Expected:

- HTTP `200`
- JSON includes `"ok": true`
- JSON includes `"session_id": "sess-e2e-001"`
- JSON includes non-empty `messages`

Readiness-hardening expectations:

- If startup is slow, Modal logs may include one recycle+retry line:
  - `Handled retryable sandbox startup failure (async)`
  - `Retrying background sandbox startup after retryable failure (async)`
- A second timeout fails deterministically with:
  - `Background sandbox startup failed after 2 attempts`

Failure-observability expectation:

- Upstream sandbox failures should return a concrete `error` string from Worker `/query`.
- `{"ok":false,"error":"Unknown error"}` indicates a response-mapping regression and should be treated as a bug.

### 3) `/query_stream` (WebSocket)

```bash
wscat -c "ws://localhost:8787/query_stream?session_id=sess-e2e-001&token=$TOKEN"
```

After connect, send:

```json
{"question":"Return exactly: stream-e2e-ok","session_id":"sess-e2e-001"}
```

Expected WebSocket events include:

- `connection_ack`
- `query_start`
- one or more `assistant_message` and/or `execution_state`
- terminal `query_complete` (or `query_error` on failure)

### 4) Queue and state verification

Queue one prompt:

```bash
curl -sS -X POST "$WORKER_URL/session/sess-e2e-001/queue" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"queued follow-up","session_id":"sess-e2e-001"}'
```

Check queue:

```bash
curl -sS "$WORKER_URL/session/sess-e2e-001/queue" \
  -H "Authorization: Bearer $TOKEN"
```

Check state:

```bash
curl -sS "$WORKER_URL/session/sess-e2e-001/state" \
  -H "Authorization: Bearer $TOKEN"
```

Expected:

- queue response includes `"ok": true`
- queue response includes `queue_size >= 1` after enqueue
- state response includes `"ok": true` and `state.session_id == "sess-e2e-001"`

### 5) Runtime Hardening Verification (Task 03)

Run local hardening checks:

```bash
uv run python -m pytest tests/test_runtime_hardening.py
```

Expected:

- env scrubbing assertions pass for sensitive keys.
- writable-root parsing assertions pass.

Optional live controller check:

- `/runtime_hardening` is exposed by the sandbox controller, not the top-level
  `http_app`, so `curl "$DEV_URL/runtime_hardening"` returns `404` in the current topology.
- Treat `uv run python -m pytest tests/test_runtime_hardening.py` as the canonical gate unless
  you are intentionally calling the controller URL with scoped sandbox auth.

### 6) Session Budget Denial Smoke Checks (Task 05)

Run Worker with constrained budget vars (local smoke mode):

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane
./node_modules/.bin/wrangler dev --local --port 8787 \
  --var SESSION_SIGNING_SECRET:test-session-secret \
  --var MAX_SESSION_QUERY_BUDGET_REQUESTS:1 \
  --var MAX_SESSION_QUERY_BUDGET_USD:10 \
  --var ESTIMATED_COST_PER_1K_CHARS_USD:0.002
```

Generate token:

```bash
TOKEN="$(node edge-control-plane/scripts/generate-session-token.js \
  --user-id e2e-user \
  --tenant-id e2e-tenant \
  --session-id sess-budget-queue-001 \
  --ttl-seconds 3600 \
  --secret test-session-secret)"
```

Non-stream denial after budget is consumed:

```bash
curl -sS -X POST "$WORKER_URL/query" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"second query denial","session_id":"sess-budget-queue-001"}'
```

Expected:

- HTTP `429` with `code: "request_budget_exceeded"` or `code: "cost_budget_exceeded"`.

Queue denial preflight:

```bash
curl -sS -X POST "$WORKER_URL/session/sess-budget-queue-001/queue" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"queued budget check","session_id":"sess-budget-queue-001"}'
```

Expected:

- HTTP `429` with deterministic `code` and `details` payload.

Stream denial:

```bash
wscat -c "ws://localhost:8787/query_stream?session_id=sess-budget-queue-001&token=$TOKEN"
```

Send:

```json
{"question":"stream after budget","session_id":"sess-budget-queue-001"}
```

Expected stream event:

- `query_error` with `code` (`request_budget_exceeded` or `cost_budget_exceeded`) and stable `details`.

### 7) Worker Job/Artifact Proxy Integration Tests

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
npm --prefix edge-control-plane run test:integration
```

Expected:

- `GET /jobs/:id/artifacts` denies ownership mismatch with deterministic `403`.
- `GET /jobs/:id/artifacts/:path` forwards `X-Artifact-Access-Token`.
- malformed encoded artifact path returns deterministic `400` and does not proxy artifact download.

### 8) Artifact Abuse-Case Validation (Task 06)

```bash
uv run python -m pytest tests/test_jobs_security.py tests/test_artifact_access.py
```

Expected:

- path traversal protections hold.
- expired/tampered/cross-session/revoked artifact tokens are denied deterministically.

## Remediation Notes (Hardening Tasks 02-06)

Scoped sandbox auth remediation:

1. Recycle/recreate affected sandboxes so every active entry has `sandbox_session_secret`.
2. Re-run:
   - `uv run python -m pytest tests/test_internal_auth_middleware.py tests/test_sandbox_auth_header.py`
   - `/query` and `/query_stream` smoke checks from this runbook.
3. Verify transition state:
   - `curl -sS "$DEV_URL/pool/status" -H "X-Internal-Auth: <signed-token>" | jq '{missing_scoped_secret_count,scoped_secret_transition_stable}'`
4. Keep strict mode enabled; there is no legacy internal-auth fallback path.

Readiness-timeout remediation:

1. Confirm timeout contract and logs:
   - `SERVICE_TIMEOUT` is set to a positive integer (`>=1`, default `60`).
   - logs include phase/attempt/sandbox diagnostics for timeout cases.
2. Recycle the active service sandbox:
   - `modal run -m modal_backend.main::terminate_service_sandbox`
3. Restart Modal backend and re-run `/query` + `/query_stream` smoke:
   - `source .venv/bin/activate && uv run modal serve -m modal_backend.main`
4. If repeated timeouts continue after one retry, treat as readiness incident and capture logs with:
   - timeout phase (`reuse_by_name`, `warm_pool_claim`, `create_or_attach`)
   - sandbox id
   - poll/tunnel diagnostics
   before escalating.

Sandbox secret/runtime remediation:

1. Ensure `modal-auth-secret` is available to both function and sandbox secret surfaces when `ENABLE_MODAL_AUTH_SECRET=true`.
2. Recycle existing named/warm sandboxes after secret-surface or runtime-hardening changes:
   - `modal run -m modal_backend.main::terminate_service_sandbox`
   - clear stale `sandbox-sessions` metadata entry if present.
3. Re-run `/query` smoke and confirm no `AuthError: Token missing` or SQLite `readonly database` failures.

Budget rails rollback:

1. Remove/raise `MAX_SESSION_QUERY_BUDGET_*` vars.
2. Re-run non-stream/stream/queue smoke checks.
3. Confirm no false-positive `429` denials.

Artifact token rollback:

1. Set `REQUIRE_ARTIFACT_ACCESS_TOKEN=false` temporarily.
2. Keep actor-scope + path traversal protections enabled.
3. Re-run `uv run python -m pytest tests/test_jobs_security.py tests/test_artifact_access.py`.

## Failure Triage Matrix

| Symptom | Likely Cause | Fast Checks | Fix |
| --- | --- | --- | --- |
| `401` with `Missing authorization token` or `Invalid token signature` from Worker | Missing/invalid session token | Ensure `Authorization: Bearer <token>` is present and generated by `generate-session-token.js` | Regenerate token with correct `SESSION_SIGNING_SECRET`, `session_id`/`session_ids`, and valid TTL |
| `5xx` from Worker query paths with upstream/connect errors | Modal app unavailable or wrong `MODAL_API_BASE_URL` | `curl "$WORKER_URL/health"`, then verify `MODAL_API_BASE_URL` export and Worker logs | Start/restart `uv run modal serve -m modal_backend.main`; correct `MODAL_API_BASE_URL` in `wrangler.jsonc` |
| `500` from Worker query path with Modal log `Background sandbox startup failed after 2 attempts` | Sandbox controller startup failure exceeded retry budget | Check Modal logs for `Handled retryable sandbox startup failure` and `Retrying ... retryable failure`; verify `SERVICE_TIMEOUT` | Recycle sandbox (`terminate_service_sandbox`), restart with `.venv` active, re-run `/query` smoke, then escalate with diagnostics if still failing |
| `500` from Worker `/query` with `Token missing. Could not authenticate client` | Sandbox runtime missing Modal API credentials (`modal-auth-secret` not injected on sandbox surface or stale sandbox without refreshed env) | Call Modal `/query` directly with `X-Internal-Auth` and inspect `error_type`; verify secret surface config | Ensure sandbox surface includes `modal-auth-secret`, recycle named/warm sandboxes, and rerun `/query` |
| `500` from Worker `/query` with `attempt to write a readonly database` | OpenAI session SQLite path not writable after runtime privilege drop | Check controller startup logs for session DB fallback; inspect sandbox `writable_probe` from `/runtime_hardening` | Use writable fallback (`/tmp/openai_agents_sessions.sqlite3`) and recycle sandbox so new runtime path takes effect |
| Modal returns `401` with `Missing internal auth token` or `Invalid token signature` | `INTERNAL_AUTH_SECRET` mismatch between Worker and Modal | Verify Worker secret and Modal `internal-auth-secret` hold same value | Re-set both secrets to identical value and restart services |
| Worker returns query error indicating invalid payload/schema from Modal | Request or response validation mismatch | Inspect response body from `/query` or stream `query_error` data | Send schema-valid request body; confirm Modal endpoint responses are JSON as expected |

## Related Docs

- `docs/references/configuration.md`
- `docs/references/troubleshooting.md`
- `edge-control-plane/API.md`
- `edge-control-plane/INTEGRATION.md`
