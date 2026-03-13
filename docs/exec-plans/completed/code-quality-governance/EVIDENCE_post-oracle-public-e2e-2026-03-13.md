# Post-Oracle Public E2E Evidence - 2026-03-13

## Summary
- Primary proof path used: deployed public Worker
  `https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev` forwarding to
  the deployed Modal gateway
  `https://saidiibrahim--modal-backend-http-app.modal.run`.
- Operator/debug path used to recover the token source and isolate runtime
  faults: local Modal dev serve (`uv run modal serve -m modal_backend.main`)
  plus local Worker dev (`npm run dev` in `edge-control-plane`).
- Live issue 1: the public Worker was stale before repair. `PATCH` was missing
  from CORS preflight, and blocked/unsupported session routes still returned
  `401` instead of the intended `404` / `405`.
- Live issue 2: the production Modal HTTP app was stopped and required a
  redeploy before public ingress could complete authenticated flows.
- Live issue 3: the public Worker `SESSION_SIGNING_SECRET` had drifted from the
  canonical local helper source, so helper-generated tokens initially failed
  with `401 Invalid token signature`.
- Live issue 4: the Oracle follow-up introduced a real runtime regression in
  the job path. `modal_backend.main.submit_job()` did not forward
  `session_id` into `enqueue_job()`, so public `/jobs/{id}` reads failed with
  deterministic Worker `502 Invalid job status response from Modal backend`.
- Repair result: after redeploying the Worker, redeploying Modal, realigning
  the public Worker `SESSION_SIGNING_SECRET`, and fixing the Modal job enqueue
  path, the public authenticated matrix passed for `/query`, `/query_stream`,
  queue/state/messages, `GET` + `POST /session/{id}/stop`, schedules
  create/list/get/patch, `/submit`, `/jobs/{id}`, `/jobs/{id}/artifacts`, and
  `/events`.
- Artifact download was not exercised because the live artifact manifest for
  the proof job contained no files, so there was no safe public download target
  to fetch in this run.

## Files Changed In This Wave
- `modal_backend/jobs.py`
- `modal_backend/main.py`
- `tests/test_jobs_enqueue.py`
- `docs/references/api-usage.md`
- `docs/exec-plans/completed/code-quality-governance/PLAN_code-quality-governance.md`
- `docs/exec-plans/completed/code-quality-governance/tasks/TASK_06_code-quality-governance.md`
- `docs/exec-plans/completed/code-quality-governance/EVIDENCE_post-oracle-public-e2e-2026-03-13.md`

## Exact Commands Run

### 1) Initial public Worker verification and stale-deploy proof

```bash
curl -sS -D - https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/health

curl -sS -D - -o /dev/null -X OPTIONS 'https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/schedules/sched-preflight' \
  -H 'Origin: https://example.com' \
  -H 'Access-Control-Request-Method: PATCH' \
  -H 'Access-Control-Request-Headers: authorization,content-type'

curl -sS -D - -o /dev/null 'https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/session/sess-contract-404'
curl -sS -D - -o /dev/null 'https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/session/sess-contract-404/query'
curl -sS -D - -o /dev/null -X POST 'https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/session/sess-contract-405/state'
curl -sS -D - -o /dev/null -X DELETE 'https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/session/sess-contract-405/messages'
```

### 2) Local/operator recovery path for token-source and auth isolation

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
source .venv/bin/activate
uv run modal serve -m modal_backend.main

cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane
npm run dev
```

### 3) Local Worker regression gates before redeploy

```bash
npm --prefix edge-control-plane run test:integration
npm --prefix edge-control-plane run check:contracts
cd edge-control-plane && ./node_modules/.bin/tsc --noEmit
```

### 4) Public Worker redeploy repair

```bash
npm --prefix edge-control-plane run deploy

curl -sS -D - https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/health
curl -sS -D - -o /dev/null -X OPTIONS 'https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/schedules/sched-preflight' \
  -H 'Origin: https://example.com' \
  -H 'Access-Control-Request-Method: PATCH' \
  -H 'Access-Control-Request-Headers: authorization,content-type'
curl -sS -D - -o /dev/null 'https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/session/sess-contract-404'
curl -sS -D - -o /dev/null 'https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/session/sess-contract-404/query'
curl -sS -D - -o /dev/null -X POST 'https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/session/sess-contract-405/state'
curl -sS -D - -o /dev/null -X DELETE 'https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/session/sess-contract-405/messages'
```

### 5) Initial invalid-token proof against the deployed Worker

```bash
set -euo pipefail
SESSION_SIGNING_SECRET="$(awk -F= '/^SESSION_SIGNING_SECRET=/{print substr($0, index($0,$2))}' edge-control-plane/.dev.vars)"
TOKEN="$(node edge-control-plane/scripts/generate-session-token.js --user-id e2e-user-oracle --tenant-id e2e-tenant-oracle --session-id sess-oracle-e2e-20260313-query --ttl-seconds 3600 --secret "$SESSION_SIGNING_SECRET")"
curl -sS -D - -X POST 'https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/query' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"question":"Return exactly: e2e-ok","session_id":"sess-oracle-e2e-20260313-query"}'

set -euo pipefail
INTERNAL_AUTH_SECRET="$(awk -F= '/^INTERNAL_AUTH_SECRET=/{print substr($0, index($0,$2))}' .env)"
TOKEN="$(node edge-control-plane/scripts/generate-session-token.js --user-id e2e-user-oracle --tenant-id e2e-tenant-oracle --session-id sess-oracle-e2e-20260313-query2 --ttl-seconds 3600 --secret "$INTERNAL_AUTH_SECRET")"
curl -sS -D - -X POST 'https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/query' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"question":"Return exactly: e2e-ok","session_id":"sess-oracle-e2e-20260313-query2"}'
```

### 6) Restore production Modal app and align public Worker signing secret

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
source .venv/bin/activate
uv run modal deploy -m modal_backend.deploy

cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane
printf '%s' "$SESSION_SIGNING_SECRET" | ./node_modules/.bin/wrangler secret put SESSION_SIGNING_SECRET
```

### 7) Targeted regression fix validation for the Modal job path

```bash
cd /Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki
source .venv/bin/activate
uv run python -m pytest tests/test_jobs_enqueue.py
uv run python -m pytest tests/test_schemas_jobs.py
uv run modal deploy -m modal_backend.deploy
```

### 8) Public `/events` proof with real event fan-out

```bash
set -euo pipefail
PUBLIC_BASE="https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev"
USER_ID="e2e-user-public"
TENANT_ID="e2e-tenant-public"
SESSION_ID="sess-public-events-003"
SESSION_SIGNING_SECRET="$(awk -F= '/^SESSION_SIGNING_SECRET=/{print substr($0, index($0,$2))}' edge-control-plane/.dev.vars)"
TOKEN="$(node edge-control-plane/scripts/generate-session-token.js --user-id "$USER_ID" --tenant-id "$TENANT_ID" --session-id "$SESSION_ID" --ttl-seconds 3600 --secret "$SESSION_SIGNING_SECRET")"
export PUBLIC_BASE USER_ID TENANT_ID SESSION_ID TOKEN
node <<'NODE'
const base = process.env.PUBLIC_BASE;
const token = process.env.TOKEN;
const wsUrl = base.replace(/^http/, 'ws') + `/events?session_id=${encodeURIComponent(process.env.SESSION_ID)}&user_id=${encodeURIComponent(process.env.USER_ID)}&tenant_id=${encodeURIComponent(process.env.TENANT_ID)}&token=${encodeURIComponent(token)}`;
const output = {
  websocket: wsUrl.replace(token, 'REDACTED'),
  received: [],
  submit: null,
};
let resolved = false;
function done(code) {
  if (resolved) return;
  resolved = true;
  try { ws.close(); } catch {}
  console.log(JSON.stringify(output, null, 2));
  setTimeout(() => process.exit(code), 25);
}
const ws = new WebSocket(wsUrl);
const timeout = setTimeout(() => {
  done(output.received.some((msg) => msg.type === 'connection_ack') ? 0 : 1);
}, 10000);
let triggered = false;
ws.addEventListener('message', async (event) => {
  const msg = JSON.parse(String(event.data));
  output.received.push({
    type: msg.type,
    session_id: msg.session_id ?? null,
    data_keys: msg.data && typeof msg.data === 'object' ? Object.keys(msg.data).sort() : [],
  });
  if (msg.type === 'connection_ack' && !triggered) {
    triggered = true;
    try {
      const res = await fetch(`${base}/submit`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          question: 'Return exactly: event-bus-ok',
          session_id: process.env.SESSION_ID,
        }),
      });
      const text = await res.text();
      let body;
      try { body = JSON.parse(text); } catch { body = text; }
      output.submit = { status: res.status, body };
    } catch (error) {
      output.submit = { error: String(error) };
    }
  }
  if (msg.type === 'job_submitted') {
    clearTimeout(timeout);
    done(0);
  }
});
ws.addEventListener('error', (error) => {
  clearTimeout(timeout);
  output.error = String(error?.message || error);
  done(1);
});
ws.addEventListener('close', () => {
  if (!resolved && output.received.some((msg) => msg.type === 'connection_ack')) {
    clearTimeout(timeout);
    done(0);
  }
});
NODE
```

### 9) Fresh authenticated public Worker matrix rerun

```bash
set -euo pipefail
PUBLIC_BASE="https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev"
USER_ID="e2e-user-public"
TENANT_ID="e2e-tenant-public"
SESSION_SIGNING_SECRET="$(awk -F= '/^SESSION_SIGNING_SECRET=/{print substr($0, index($0,$2))}' edge-control-plane/.dev.vars)"
q_token() {
  node edge-control-plane/scripts/generate-session-token.js \
    --user-id "$USER_ID" \
    --tenant-id "$TENANT_ID" \
    --session-id "$1" \
    --ttl-seconds 3600 \
    --secret "$SESSION_SIGNING_SECRET"
}
QUERY_SESSION="sess-public-query-20260313-rerun"
STREAM_SESSION="sess-public-stream-20260313-rerun"
QUEUE_SESSION="sess-public-queue-20260313-rerun"
SCHEDULE_SESSION="sess-public-schedule-20260313-rerun"
JOB_SESSION="sess-public-job-20260313-rerun"
export PUBLIC_BASE USER_ID TENANT_ID \
  QUERY_SESSION STREAM_SESSION QUEUE_SESSION SCHEDULE_SESSION JOB_SESSION \
  QUERY_TOKEN="$(q_token "$QUERY_SESSION")" \
  STREAM_TOKEN="$(q_token "$STREAM_SESSION")" \
  QUEUE_TOKEN="$(q_token "$QUEUE_SESSION")" \
  SCHEDULE_TOKEN="$(q_token "$SCHEDULE_SESSION")" \
  JOB_TOKEN="$(q_token "$JOB_SESSION")"
node <<'NODE'
const base = process.env.PUBLIC_BASE;
const userId = process.env.USER_ID;
const tenantId = process.env.TENANT_ID;

async function requestJson(method, path, token, body, extraHeaders = {}) {
  const headers = { ...extraHeaders };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(`${base}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let parsed;
  try { parsed = JSON.parse(text); } catch { parsed = text; }
  return {
    status: res.status,
    headers: {
      'access-control-allow-methods': res.headers.get('access-control-allow-methods'),
      'content-type': res.headers.get('content-type'),
    },
    body: parsed,
  };
}

async function requestOptions(path) {
  const res = await fetch(`${base}${path}`, {
    method: 'OPTIONS',
    headers: {
      Origin: 'https://example.com',
      'Access-Control-Request-Method': 'PATCH',
      'Access-Control-Request-Headers': 'authorization,content-type',
    },
  });
  return {
    status: res.status,
    headers: {
      'access-control-allow-methods': res.headers.get('access-control-allow-methods'),
      'access-control-allow-origin': res.headers.get('access-control-allow-origin'),
    },
  };
}

async function runQueryStream(sessionId, token) {
  const wsUrl = `${base.replace(/^http/, 'ws')}/query_stream?session_id=${encodeURIComponent(sessionId)}&token=${encodeURIComponent(token)}`;
  const result = { websocket: wsUrl.replace(token, 'REDACTED'), message_types: [] };
  return await new Promise((resolve) => {
    const ws = new WebSocket(wsUrl);
    const timeout = setTimeout(() => {
      try { ws.close(); } catch {}
      resolve(result);
    }, 25000);
    let sent = false;
    ws.addEventListener('message', (event) => {
      const msg = JSON.parse(String(event.data));
      result.message_types.push(msg.type);
      if (msg.type === 'connection_ack' && !sent) {
        sent = true;
        ws.send(JSON.stringify({ question: 'Return exactly: stream-e2e-ok' }));
      }
      if (msg.type === 'query_complete' || msg.type === 'query_error') {
        clearTimeout(timeout);
        try { ws.close(); } catch {}
        resolve(result);
      }
    });
    ws.addEventListener('error', (error) => {
      clearTimeout(timeout);
      result.error = String(error?.message || error);
      try { ws.close(); } catch {}
      resolve(result);
    });
  });
}

(async () => {
  const out = {
    query: null,
    query_stream: null,
    stop: {},
    queue: {},
    schedules: {},
    jobs: {},
  };

  out.query = await requestJson('POST', '/query', process.env.QUERY_TOKEN, {
    question: 'Return exactly: e2e-ok',
    session_id: process.env.QUERY_SESSION,
  });

  out.query_stream = await runQueryStream(process.env.STREAM_SESSION, process.env.STREAM_TOKEN);
  out.stop.before = await requestJson('GET', `/session/${process.env.STREAM_SESSION}/stop`, process.env.STREAM_TOKEN);
  out.stop.request = await requestJson('POST', `/session/${process.env.STREAM_SESSION}/stop`, process.env.STREAM_TOKEN, {
    mode: 'graceful',
    reason: 'public-e2e-rerun',
  });
  out.stop.after = await requestJson('GET', `/session/${process.env.STREAM_SESSION}/stop`, process.env.STREAM_TOKEN);

  out.queue.enqueue = await requestJson('POST', `/session/${process.env.QUEUE_SESSION}/queue`, process.env.QUEUE_TOKEN, {
    question: 'queued follow-up',
  });
  const promptId = out.queue.enqueue.body?.prompt_id;
  out.queue.list = await requestJson('GET', `/session/${process.env.QUEUE_SESSION}/queue`, process.env.QUEUE_TOKEN);
  out.queue.state = await requestJson('GET', `/session/${process.env.QUEUE_SESSION}/state`, process.env.QUEUE_TOKEN);
  out.queue.messages = await requestJson('GET', `/session/${process.env.QUEUE_SESSION}/messages`, process.env.QUEUE_TOKEN);
  if (promptId) {
    out.queue.delete_one = await requestJson('DELETE', `/session/${process.env.QUEUE_SESSION}/queue/${encodeURIComponent(promptId)}`, process.env.QUEUE_TOKEN);
  }
  out.queue.clear = await requestJson('DELETE', `/session/${process.env.QUEUE_SESSION}/queue`, process.env.QUEUE_TOKEN);

  out.schedules.preflight = await requestOptions('/schedules/sched-preflight');
  const runAt = Math.floor(Date.now() / 1000) + 3600;
  out.schedules.create = await requestJson('POST', '/schedules', process.env.SCHEDULE_TOKEN, {
    name: 'oracle-public-schedule-rerun',
    question: 'Return exactly: scheduled-e2e-ok',
    schedule_type: 'one_off',
    run_at: runAt,
    timezone: 'UTC',
    enabled: true,
  });
  out.schedules.list = await requestJson('GET', '/schedules', process.env.SCHEDULE_TOKEN);

  out.jobs.submit = await requestJson('POST', '/submit', process.env.JOB_TOKEN, {
    question: 'Return exactly: job-e2e-ok',
    session_id: process.env.JOB_SESSION,
  });
  const jobId = out.jobs.submit.body?.job_id;
  if (jobId) {
    const ownershipQuery = `?session_id=${encodeURIComponent(process.env.JOB_SESSION)}&user_id=${encodeURIComponent(userId)}&tenant_id=${encodeURIComponent(tenantId)}`;
    out.jobs.status = await requestJson('GET', `/jobs/${jobId}${ownershipQuery}`, process.env.JOB_TOKEN);
    out.jobs.artifacts = await requestJson('GET', `/jobs/${jobId}/artifacts${ownershipQuery}`, process.env.JOB_TOKEN);
  }

  console.log(JSON.stringify(out, null, 2));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
NODE
```

### 10) Schedule CRUD rerun with explicit `session_id` query scope

```bash
set -euo pipefail
PUBLIC_BASE="https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev"
USER_ID="e2e-user-public"
TENANT_ID="e2e-tenant-public"
SESSION_ID="sess-public-schedule-20260313-rerun-2"
SESSION_SIGNING_SECRET="$(awk -F= '/^SESSION_SIGNING_SECRET=/{print substr($0, index($0,$2))}' edge-control-plane/.dev.vars)"
TOKEN="$(node edge-control-plane/scripts/generate-session-token.js --user-id "$USER_ID" --tenant-id "$TENANT_ID" --session-id "$SESSION_ID" --ttl-seconds 3600 --secret "$SESSION_SIGNING_SECRET")"
export PUBLIC_BASE SESSION_ID TOKEN
node <<'NODE'
const base = process.env.PUBLIC_BASE;
const token = process.env.TOKEN;
const sessionId = process.env.SESSION_ID;
async function req(method, path, body) {
  const res = await fetch(`${base}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let parsed;
  try { parsed = JSON.parse(text); } catch { parsed = text; }
  return { status: res.status, body: parsed };
}
(async () => {
  const runAt = Math.floor(Date.now() / 1000) + 3600;
  const scope = `?session_id=${encodeURIComponent(sessionId)}`;
  const create = await req('POST', `/schedules${scope}`, {
    name: 'oracle-public-schedule-rerun-2',
    question: 'Return exactly: scheduled-e2e-ok',
    schedule_type: 'one_off',
    run_at: runAt,
    timezone: 'UTC',
    enabled: true,
  });
  const scheduleId = create.body?.schedule_id;
  const list = await req('GET', `/schedules${scope}`);
  const get = scheduleId ? await req('GET', `/schedules/${scheduleId}${scope}`) : null;
  const patch = scheduleId ? await req('PATCH', `/schedules/${scheduleId}${scope}`, {
    name: 'oracle-public-schedule-rerun-2-patched',
    enabled: false,
  }) : null;
  const getAfter = scheduleId ? await req('GET', `/schedules/${scheduleId}${scope}`) : null;
  console.log(JSON.stringify({ scope, create, list, get, patch, getAfter }, null, 2));
})().catch((error) => { console.error(error); process.exit(1); });
NODE
```

## PASS / FAIL Matrix
| Check | Result | Evidence |
| --- | --- | --- |
| Public Worker `/health` before repair | PASS | `HTTP 200` with `{"ok":true,"service":"rafiki-control-plane"}` |
| Public schedule `PATCH` preflight before repair | FAIL | `Access-Control-Allow-Methods` omitted `PATCH` (`GET, POST, DELETE, OPTIONS`) |
| Public blocked alias `GET /session/{id}` before repair | FAIL | returned `401` instead of documented `404` |
| Public blocked alias `GET /session/{id}/query` before repair | FAIL | returned `401` instead of documented `404` |
| Public unsupported `POST /session/{id}/state` before repair | FAIL | returned `401` instead of documented `405` |
| Public unsupported `DELETE /session/{id}/messages` before repair | FAIL | returned `401` instead of documented `405` |
| Local Worker regression tests before deploy | PASS | `npm --prefix edge-control-plane run test:integration` -> `15 passed` |
| Local Worker contract tests before deploy | PASS | `npm --prefix edge-control-plane run check:contracts` -> `12 passed` |
| Local TypeScript check before deploy | PASS | `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` |
| Canonical public Worker deploy | PASS | `npm --prefix edge-control-plane run deploy` -> deployed version `0cfeea3e-e192-4397-896b-3734c84a9b9c` |
| Public Worker `/health` after repair | PASS | `HTTP 200`; CORS now includes `PATCH` |
| Public schedule `PATCH` preflight after repair | PASS | `HTTP 204`; `Access-Control-Allow-Methods: GET, POST, PATCH, DELETE, OPTIONS` |
| Public blocked alias `GET /session/{id}` after repair | PASS | `HTTP 404` |
| Public blocked alias `GET /session/{id}/query` after repair | PASS | `HTTP 404` |
| Public unsupported `POST /session/{id}/state` after repair | PASS | `HTTP 405` |
| Public unsupported `DELETE /session/{id}/messages` after repair | PASS | `HTTP 405` |
| Public authenticated `/query` before secret repair | FAIL | `HTTP 401`; `{"ok":false,"error":"Invalid token signature"}` |
| Production Modal restore | PASS | `uv run modal deploy -m modal_backend.deploy` restored the stopped public gateway |
| Public Worker secret realignment | PASS | `wrangler secret put SESSION_SIGNING_SECRET` succeeded |
| Public `/query` after secret repair | PASS | `HTTP 200`; stable `session_id: sess-public-query-20260313-rerun`; result `e2e-ok` |
| Public `/query_stream` | PASS | received `connection_ack`, `session_update`, `query_start`, `assistant_message`, `execution_state`, `query_complete` |
| Public `GET /session/{id}/stop` before request | PASS | `HTTP 200`; `status: not_found` |
| Public `POST /session/{id}/stop` | PASS | `HTTP 200`; `status: requested`; `requested_by: e2e-user-public` |
| Public `GET /session/{id}/stop` after request | PASS | `HTTP 200`; `status: requested` |
| Public queue enqueue/list/state/messages/delete/clear | PASS | all routes returned `HTTP 200`; queue/state scope and prompt lifecycle matched the session token |
| Bare-token schedule create/list without `session_id` query scope | FAIL (expected current auth behavior) | `HTTP 403`; `Session not authorized` |
| Public schedule create/list/get/patch with `?session_id=...` | PASS | all routes returned `HTTP 200`; patched resource reflected `enabled: false` and updated `name` |
| Public `/submit` before Modal fix | FAIL | Worker returned deterministic `502 Invalid job status response from Modal backend` because upstream status payload omitted `session_id` |
| Targeted Modal regression tests for job fix | PASS | `uv run python -m pytest tests/test_jobs_enqueue.py` (`5 passed`) and `uv run python -m pytest tests/test_schemas_jobs.py` (`6 passed`) |
| Public `/submit` + `/jobs/{id}` after Modal fix | PASS | `HTTP 200`; status payload includes `session_id: sess-public-job-20260313-rerun` |
| Public `/jobs/{id}/artifacts` after Modal fix | PASS | `HTTP 200`; manifest returned `root` plus empty `files` array |
| Public `/events` subscription | PASS | received `connection_ack`, `presence_update`, and `job_submitted` after live `/submit` |
| Public artifact download | NOT RUN | artifact manifest for the proof job contained zero files, so there was no safe public download target to fetch |

## Classification
- Repaired deployment/config drift:
  - stale public Worker deployment
  - stopped production Modal HTTP app
  - mismatched public Worker `SESSION_SIGNING_SECRET`
- Repaired follow-up regression introduced by the Oracle must-fix change wave:
  - Modal `/submit` path dropped `session_id` during enqueue, causing public
    `/jobs/**` reads to fail Worker runtime validation with deterministic `502`
- Observed current public-auth contract nuance, not a new runtime break:
  - schedule routes require explicit `session_id` query scope
    (`/schedules?session_id=...`) because the current Worker auth helper only
    authorizes session-scoped routes when the request names a target session

## Closure Status
- `code-quality-governance` is now ready for closure.
- Public Cloudflare -> Modal E2E proof is complete for the required Worker
  surface, with one explicit non-blocking note: artifact download could not be
  exercised because the proof job produced an empty artifact manifest.
- Remaining intentional follow-up remains unchanged from the Oracle task:
  constant-time session-token verification hardening in
  `edge-control-plane/src/auth/session-auth.ts`, proof artifact git-SHA
  metadata in `scripts/quality/write_code_quality_proof.py`, and the
  previously recorded residual risk that jobs-proxy passthrough errors can
  still mislabel some non-JSON upstream errors as JSON.
