# Cloudflare <-> Modal E2E Runbook

This is the canonical end-to-end test runbook for the Cloudflare Worker control plane and Modal backend.

## Why This Runbook Exists

Passing internal Modal checks is not enough for rollout acceptance because real users reach Rafiki through the public Cloudflare Worker first.

- If the first public `/query` after cutover fails, users will experience the deploy as downtime even if Modal-only probes were green.
- If a stream is active during cutover, the old controller must finish draining that work while the new controller takes fresh traffic.
- If a queued job fires during recycle, it must route to the active generation instead of landing on a controller that is already shutting down.
- This runbook therefore treats the first successful public request after A -> B handoff as a user-facing safety check, not just an implementation detail.

## Prerequisites

### Local tooling

- Python + `uv`
- Node.js 20+
- Repo `.venv` synced with `modal>=1.3.5` (`uv sync --extra dev`)
- Modal CLI authenticated: `modal setup`
- Wrangler authenticated: `wrangler login`
- `wscat` installed for WebSocket verification: `npm install -g wscat`

### Required secrets

Local repo environment:

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
cp .env.example .env
# edit .env and set INTERNAL_AUTH_SECRET=<shared-internal-secret>
```

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
- `npm run dev` now uses `wrangler dev --env development`. Keep shared local secrets in
  `edge-control-plane/.dev.vars` unless you intentionally want env-specific files; adding
  `.dev.vars.development` will stop Wrangler from loading the generic `.dev.vars` for that env.

### Required bindings

- KV binding `SESSION_CACHE` configured in `edge-control-plane/wrangler.jsonc`
- top-level `vars.MODAL_API_BASE_URL` set to the production Modal gateway in `edge-control-plane/wrangler.jsonc`
- `env.development.vars.MODAL_API_BASE_URL` set to the local/operator Modal dev gateway in `edge-control-plane/wrangler.jsonc`
- Optional but recommended: `RATE_LIMITER` binding in `edge-control-plane/wrangler.jsonc`

## Startup Order (Required)

1. Start Modal backend first:

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
cp .env.example .env
# edit .env and set INTERNAL_AUTH_SECRET=<shared-internal-secret>
uv sync --extra dev
source .venv/bin/activate
uv run modal serve -m modal_backend.main
```

2. In a second terminal, start Worker dev server:

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane
npm run dev
# equivalent explicit command:
# npx wrangler dev --env development
```

## Standard Environment Setup

Run this in a third terminal before cURL/WebSocket tests:

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
cp .env.example .env
# edit .env and set INTERNAL_AUTH_SECRET=<shared-internal-secret>
uv sync --extra dev
source .venv/bin/activate

export DEV_URL="https://saidiibrahim--modal-backend-http-app-dev.modal.run"
export MODAL_API_BASE_URL="$DEV_URL"
export WORKER_URL="http://localhost:8787"

# Must match the value configured via wrangler secret put SESSION_SIGNING_SECRET
export SESSION_SIGNING_SECRET="<your-session-signing-secret>"
```

Validation note:

- Prefer `uv run python -m pytest ...` over bare `uv run pytest ...` so the repo interpreter and synced Modal SDK are used consistently during Python validation.

Auth consistency expectations:

- `INTERNAL_AUTH_SECRET` must be the same value in both:
  - local `.env`
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

For `GET /service_info` and other Modal dev endpoints behind internal auth, generate a Worker-style internal token:

```bash
INTERNAL_TOKEN="$(uv run python - <<'PY'
from modal_backend.security.cloudflare_auth import build_internal_token
from pathlib import Path

secret = None
for line in Path('edge-control-plane/.dev.vars').read_text().splitlines():
    if line.startswith('INTERNAL_AUTH_SECRET='):
        secret = line.split('=', 1)[1].strip()
        break
if not secret:
    raise SystemExit('INTERNAL_AUTH_SECRET missing from edge-control-plane/.dev.vars')
print(build_internal_token(secret))
PY
)"
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

### 0a) Bootstrap Recovery Replay (Required For Pointer/Bootstrap Changes)

When rollout work changes pointer recovery or first-touch bootstrap, run both local Python replays before the cutover replay:

1. Stale recovered-service replay:
   - seed and terminate a real local controller, then replay bootstrap against that terminated sandbox id:

```bash
uv run python - <<'PY'
import json
from modal_backend.controller_rollout import CONTROLLER_ROLLOUT, build_public_rollout_status, upsert_controller_service
from modal_backend.main import _clear_background_sandbox_state, _terminate_sandbox, get_or_start_background_sandbox

def clear_rollout_store():
    for key, _value in list(CONTROLLER_ROLLOUT.items()):
        if isinstance(key, str) and (
            key == "active_pointer"
            or key == "rollout_lock"
            or key.startswith("service:")
            or key.startswith("promotion-commit:")
            or key.startswith("inflight-lease:")
        ):
            try:
                CONTROLLER_ROLLOUT.pop(key)
            except KeyError:
                pass

clear_rollout_store()
seed, seed_url = get_or_start_background_sandbox()
_terminate_sandbox(seed, wait_for_exit=True)
_clear_background_sandbox_state(expected_sandbox_id=getattr(seed, "object_id", None))
clear_rollout_store()
upsert_controller_service(
    {
        "generation": 1,
        "sandbox_id": seed.object_id,
        "sandbox_name": "svc-stale-bootstrap-proof",
        "service_url": seed_url,
        "image_version": "bootstrap-proof",
        "status": "active",
        "created_at": 1,
        "promoted_at": 1,
        "last_verified_readiness_at": 1,
    }
)
before = build_public_rollout_status()
sb, url = get_or_start_background_sandbox()
after = build_public_rollout_status()
stale = next(service for service in after["services"] if service["sandbox_id"] == seed.object_id)
print(json.dumps({"before_active": before["active"], "returned_sandbox_id": sb.object_id, "returned_url": url, "stale_service": stale, "after_active": after["active"]}, indent=2))
PY
```

   - require the stale row to end in `status=failed` with `failure_reason` containing `attach_active_pointer`, and require the returned controller id to differ from the stale id
2. Empty-pointer replay:
   - clear `controller-rollout-store` and call `get_or_start_background_sandbox()` from empty state:

```bash
uv run python - <<'PY'
import json
from modal_backend.controller_rollout import CONTROLLER_ROLLOUT, build_public_rollout_status
from modal_backend.main import get_or_start_background_sandbox

for key, _value in list(CONTROLLER_ROLLOUT.items()):
    if isinstance(key, str) and (
        key == "active_pointer"
        or key == "rollout_lock"
        or key.startswith("service:")
        or key.startswith("promotion-commit:")
        or key.startswith("inflight-lease:")
    ):
        try:
            CONTROLLER_ROLLOUT.pop(key)
        except KeyError:
            pass

before = build_public_rollout_status()
sb, url = get_or_start_background_sandbox()
after = build_public_rollout_status()
print(json.dumps({"before_active": before["active"], "returned_sandbox_id": sb.object_id, "returned_url": url, "after_active": after["active"]}, indent=2))
PY
```

   - require `before_active == null` and `after_active.active_generation == 1`

Proof boundary:

- If an intentionally tightened `SERVICE_TIMEOUT` hits `Background sandbox startup failed after 2 attempts`, record it as readiness-limit evidence, not as a bootstrap regression, when the default-timeout replay succeeds.
- Archive the outputs from these replays in the current immutable generated proof artifact for the wave. The current public-worker-first spawned-drain proof wave is archived at `docs/generated/controller-rollout-cutover-safety-proof-2026-03-10T13-48-41-1030.json`.

### 0b) Controller Cutover Replay (Required For Rollout Changes)

Establish active controller A explicitly:

```bash
uv run python - <<'PY'
from modal_backend.main import get_or_start_background_sandbox
sb, url = get_or_start_background_sandbox()
print({"sandbox_id": getattr(sb, "object_id", None), "url": url})
PY
```

Record pre-cutover rollout state:

```bash
curl -sS "$DEV_URL/service_info" \
  -H "X-Internal-Auth: $INTERNAL_TOKEN"
```

Trigger safe A->B rollout from the local `modal serve` process:

```bash
uv run python - <<'PY'
from modal_backend.main import terminate_service_sandbox
print(terminate_service_sandbox.local())
PY
```

Record post-cutover rollout state:

```bash
curl -sS "$DEV_URL/service_info" \
  -H "X-Internal-Auth: $INTERNAL_TOKEN"
```

Required expectations:

- pre-cutover `/service_info` reports one active controller generation
- rollout result reports `ok: true`, a new `active_generation`, and `last_verified_readiness_at`
- post-cutover `/service_info` matches the promoted generation and shows the previous controller draining or terminated
- previous controller termination is acceptable as either:
  - spawned drain task result, or
  - local `drain_status.mode=inline` fallback when `modal serve` cannot hydrate `drain_controller_sandbox.spawn()`
- if inline fallback is used, require `status=terminated` and `drain_timeout_reached=false`
- direct live proof of hydrated spawned drain is not available under `modal serve`; local cutover replay alone is not sufficient evidence for deployed spawned-drain acceptance
- immediately after the rollout result returns, run the Worker `/query`, `/query_stream`, queue, and state checks below against the same local stack

### 0c) Deployed Spawned-Drain Cutover Proof (Required For Spawned-Drain Acceptance)

Why this replay is the acceptance gate:

- It proves the real user path works: Cloudflare Worker -> deployed Modal gateway -> promoted controller B.
- It proves A is not killed too early. A should drain while B is already ready for new traffic.
- It proves the public ingress layer does not expose the handoff seam. The first public Worker `/query` after cutover must return `HTTP 200` on the first try.
- Practical interpretation: if this replay fails, a real user could be the one who discovers the deployment problem.

Repair or confirm the canonical public Worker ingress before any cutover proof:

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane
./node_modules/.bin/wrangler whoami
./node_modules/.bin/wrangler deployments list --name rafiki-control-plane
./node_modules/.bin/wrangler secret list
```

Required preflight expectations:

- the checked-in top-level `wrangler.jsonc` vars are production-safe for the canonical public Worker deploy
- local `npm run dev` uses `env.development`, which keeps local/dev Modal routing separate from the canonical public Worker
- if `wrangler secret list` is missing `INTERNAL_AUTH_SECRET` or `SESSION_SIGNING_SECRET`, upload them before proof
- `INTERNAL_AUTH_SECRET` must match the deployed Modal `internal-auth-secret`

Canonical public Worker repair / deploy command:

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane
npm run deploy
```

Derive the canonical `WORKER_URL` from the deploy output and verify health before any rollout trigger:

```bash
curl -sS "$WORKER_URL/health"
```

Deploy the Modal app first:

```bash
uv run modal deploy -m modal_backend.deploy
```

Run the deployed cutover proof against the deployed function path, not `terminate_service_sandbox.local()`:

```bash
uv run python - <<'PY'
import json
import time
from modal import Function, FunctionCall

from modal_backend.main import _build_internal_auth_headers, _http_get_json

MODAL_BASE_URL = "https://saidiibrahim--modal-backend-http-app.modal.run"
fn = Function.from_name("modal-backend", "terminate_service_sandbox")

before = _http_get_json(f"{MODAL_BASE_URL}/service_info", headers=_build_internal_auth_headers())
result = fn.remote()
drain_call_id = result["drain_status"]["drain_call_id"]
call = FunctionCall.from_id(drain_call_id)
immediate = _http_get_json(f"{MODAL_BASE_URL}/service_info", headers=_build_internal_auth_headers())
pool_status = _http_get_json(f"{MODAL_BASE_URL}/pool/status", headers=_build_internal_auth_headers())
call_result = call.get(timeout=120)
call_graph = call.get_call_graph()
time.sleep(2)
after = _http_get_json(f"{MODAL_BASE_URL}/service_info", headers=_build_internal_auth_headers())

print(json.dumps({
    "before": before,
    "result": result,
    "immediate": immediate,
    "pool_status": pool_status,
    "drain_call_id": drain_call_id,
    "drain_result": call_result,
    "drain_call_graph": call_graph,
    "after": after,
}, indent=2))
PY
```

Capture deployed drain audit logs:

```bash
uv run modal app logs <modal-app-id> --timestamps
```

Required acceptance checks:

- run at least two consecutive deployed cutovers
- if `/service_info` reports `active: null`, prime active A once through the deployed Modal path before capturing pre-cutover state
- before each rollout trigger, run one pre-cutover public Worker `/query` with fresh `session_id`, `user_id`, and `tenant_id` to prove A is serving traffic
- after the rollout result returns, do not hit any public Worker route before the first post-cutover `/query`
- use distinct `session_id`, `user_id`, and `tenant_id` tuples for the first post-cutover `/query`, `/query_stream`, and queue/state probes so the proof is not masked by reused DO/session state
- each rollout result returns `drain_status.mode="spawned"`
- each rollout result persists `drain_status.drain_call_id`
- `modal.FunctionCall.from_id(drain_call_id).get()` returns the drain execution result
- `modal.FunctionCall.from_id(drain_call_id).get_call_graph()` ties the scheduled drain call to the terminating controller invocation
- `/service_info` immediately after cutover shows old A `status="draining"`
- a later `/service_info` snapshot shows old A `status="terminated"`
- terminated-service metadata records `drain_timeout_reached`, `inflight_at_termination`, and `drain_execution_call_id`
- active-pointer `rollback_target_*` metadata clears after drain completion
- raw `/service_info` and `/pool/status` outputs must not expose `sandbox_session_secret` or synthetic-session secret material
- app logs must include matching `controller_drain.scheduled`, `controller_drain.start`, and `controller_drain.complete` lines with the same `drain_call_id`

2026-03-10 reference proof:

- artifact: `docs/generated/controller-rollout-cutover-safety-proof-2026-03-10T13-48-41-1030.json`
- deployed Modal app: `ap-NLl3xzI88msREbDi5ocPnR` (`modal-backend`, version `v3`)
- public Worker: `https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev` (`version f6e14f0e-9a6a-40ea-9249-19cd9c035eb7`)
- cutover `1 -> 2`: `drain_call_id=fc-01KKAV7J9BHCF70NNHFZEFF2AQ`
- cutover `2 -> 3`: `drain_call_id=fc-01KKAV8YCS28RD8F8YQH464TT2`
- note: the proof packet records a successful public Worker repair first (`INTERNAL_AUTH_SECRET` + `SESSION_SIGNING_SECRET` configured, `/health` `200`) before the two public-ingress cutovers

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
- when this step is used as the first public post-cutover check, require the first response after rollout to be `HTTP 200`

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
wscat -c "${WORKER_URL/http/ws}/query_stream?session_id=sess-e2e-001&token=$TOKEN"
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
- when this step is used in the cutover replay, run it after the first successful post-cutover `/query`

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
- `GET /service_info` should still report the promoted generation as active after these checks

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
./node_modules/.bin/wrangler dev --env development --local --port 8787 \
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
2. Trigger a safe controller rollout:
   - deployed app path: use the 0c deployed function invocation path (`Function.from_name("modal-backend", "terminate_service_sandbox").remote()`)
   - local `modal serve` path (avoids webhook label-steal stop): `uv run python -c "from modal_backend.main import terminate_service_sandbox; print(terminate_service_sandbox.local())"`
3. Re-run `/query` + `/query_stream` smoke immediately. Restart `modal serve` only if a prior local `modal run` invocation already stopped the webhook app.
4. If repeated timeouts continue after one retry, treat as readiness incident and capture logs with:
   - timeout phase (`warm_pool_claim`, `create_or_attach`, `attach_missing_scoped_secret`, `tunnel_discovery`)
   - sandbox id
   - poll/tunnel diagnostics
   before escalating.

Controlled cutover validation (A->B promotion):

1. Capture active rollout state:
   - `curl -sS "$DEV_URL/service_info" -H "X-Internal-Auth: <signed-token>"`
2. Trigger safe rollout:
   - deployed app path: use the 0c deployed function invocation path (`Function.from_name("modal-backend", "terminate_service_sandbox").remote()`)
   - local `modal serve` path (avoids webhook label-steal stop): `uv run python -c "from modal_backend.main import terminate_service_sandbox; print(terminate_service_sandbox.local())"`
3. The rollout function must privately verify B before promotion:
   - `/health_check`
   - scoped sandbox secret metadata
   - synthetic direct controller `/query`
   If any gate fails, A must remain active and the rollout must return an error.
4. Immediately execute first public Worker `/query` and require HTTP `200` on first try.
   - This is the user-facing canary for safe cutover. If this request fails, the deploy is still user-visible broken even if internal Modal checks passed.
   - Practical examples: the next chat message after deploy, a stream reconnect, or a queued follow-up fired during recycle.
5. Verify post-cutover state:
   - new `active_generation` + `sandbox_id` in `/service_info`
   - `last_verified_readiness_at` is populated for the new active controller
   - previous controller appears `draining`, then `terminated` after drain completion/timeout
   - deployed acceptance requires `drain_status.mode="spawned"` with persisted `drain_call_id` and matching `drain_execution_call_id`
   - deployed acceptance requires drain schedule/execution/completion correlation via Modal FunctionCall evidence and `controller_drain.scheduled/start/complete` app-log lines
   - `/service_info` and `/pool/status` do not expose `sandbox_session_secret`.
6. If the public Worker deployment is unavailable, record the exact blocker text and classify it separately from rollout correctness.

Sandbox secret/runtime remediation:

1. Ensure `modal-auth-secret` is available to both function and sandbox secret surfaces when `ENABLE_MODAL_AUTH_SECRET=true`.
2. Recycle existing named/warm sandboxes after secret-surface or runtime-hardening changes:
   - deployed app path: use the 0c deployed function invocation path (`Function.from_name("modal-backend", "terminate_service_sandbox").remote()`)
   - local `modal serve` path: `uv run python -c "from modal_backend.main import terminate_service_sandbox; print(terminate_service_sandbox.local())"`
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
| `500` from Worker `/query` with `modal-http: app for invoked web endpoint is stopped` during local validation | `modal run` invocation stole/stopped active `modal serve` webhook app | Confirm cutover trigger path and Modal app run logs | For local `modal serve` validation, trigger cutover via `terminate_service_sandbox.local()` instead of `modal run`; restart `modal serve` if app already stopped |
| `500` from Worker `/query` with `Token missing. Could not authenticate client` | Sandbox runtime missing Modal API credentials (`modal-auth-secret` not injected on sandbox surface or stale sandbox without refreshed env) | Call Modal `/query` directly with `X-Internal-Auth` and inspect `error_type`; verify secret surface config | Ensure sandbox surface includes `modal-auth-secret`, recycle named/warm sandboxes, and rerun `/query` |
| `500` from Worker `/query` with `attempt to write a readonly database` | OpenAI session SQLite path not writable after runtime privilege drop | Check controller startup logs for session DB fallback; inspect sandbox `writable_probe` from `/runtime_hardening` | Use writable fallback (`/tmp/openai_agents_sessions.sqlite3`) and recycle sandbox so new runtime path takes effect |
| Modal returns `401` with `Missing internal auth token` or `Invalid token signature` | `INTERNAL_AUTH_SECRET` mismatch between Worker and Modal | Verify Worker secret and Modal `internal-auth-secret` hold same value | Re-set both secrets to identical value and restart services |
| Worker returns query error indicating invalid payload/schema from Modal | Request or response validation mismatch | Inspect response body from `/query` or stream `query_error` data | Send schema-valid request body; confirm Modal endpoint responses are JSON as expected |

## Related Docs

- `docs/references/configuration.md`
- `docs/references/troubleshooting.md`
- `edge-control-plane/API.md`
- `edge-control-plane/INTEGRATION.md`
