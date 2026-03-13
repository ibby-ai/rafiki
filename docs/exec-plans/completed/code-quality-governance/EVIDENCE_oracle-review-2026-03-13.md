# Oracle Review Evidence - 2026-03-13

## Summary
- Submission mode: new API session via
  `scripts/oracle/code_quality_governance_review.sh --real-run`
- Slug: `code-quality-governance-review`
- Final Oracle state: `completed`
- Oracle cost snapshot: `$9.425`
- Material governance verdict: material issues remain; Oracle returned one
  SEV-0, two SEV-1, four SEV-2, and one SEV-3 finding
- Post-review follow-up status (2026-03-13 14:32 ACDT): all must-fix findings
  from that Oracle answer are now resolved in-repo; two SEV-2 items remain as
  explicit dated deferrals and one SEV-3 item remains a non-blocking residual risk

## Exact Commands Run
```bash
set -a && source ./.env && set +a && export ORACLE_HOME_DIR=.oracle && oracle status --hours 24 --limit 10
set -a && source ./.env && set +a && export ORACLE_HOME_DIR=.oracle && scripts/oracle/code_quality_governance_review.sh --real-run
set -a && source ./.env && set +a && export ORACLE_HOME_DIR=.oracle && oracle status --hours 24 --limit 10
set -a && source ./.env && set +a && export ORACLE_HOME_DIR=.oracle && oracle session code-quality-governance-review
```

## Session Lifecycle
- The pre-submit `oracle status --hours 24 --limit 10` snapshot showed no
  existing session rows, so this run created a new Oracle session rather than
  reattaching to a prior slug.
- `scripts/oracle/code_quality_governance_review.sh --real-run` launched a real
  API submission and reported `Session running in background. Reattach via:
  oracle session code-quality-governance-review`.
- The final status snapshot showed:

```text
Recent Sessions
Status     Model        Mode    Timestamp           Chars    Cost    Slug
completed  gpt-5.2-pro  api     03/13/2026 12:58 PM  1851   $9.425  code-quality-governance-review
```

- Reattaching with `oracle session code-quality-governance-review` returned:
  - `Status: completed`
  - `Response: response=resp_0dde9b3a794b16c70069b37662298c819c8576b573114937c8 | request=req_863cb3f49a074836bf4436caf3347d60 | status=completed`
- The CLI display still showed `Models: gpt-5.2-pro — pending` even though the
  response was complete. The response status and answer payload were treated as
  authoritative.

## Oracle Answer Summary
- SEV-0:
  - `/jobs/**` ownership enforcement can fail open because
    `edge-control-plane/src/routes/jobs-proxy.ts` only rejects mismatches when
    both the upstream payload and auth context provide the compared identity
    field, while `edge-control-plane/src/contracts/public-api.ts` still accepts
    missing/null identity fields for job status responses.
- SEV-1:
  - `docs/references/api-usage.md` does not document multiple publicly
    reachable Worker routes exposed via `edge-control-plane/src/index.ts` and
    `edge-control-plane/src/durable-objects/session-agent.ts`.
  - Worker CORS handling in `edge-control-plane/src/index.ts` omits `PATCH`
    even though `docs/references/api-usage.md` documents
    `PATCH /schedules/{schedule_id}`.
- SEV-2:
  - `edge-control-plane/src/auth/session-auth.ts` compares session-token
    signatures with string equality instead of constant-time verification.
  - `scripts/quality/validate_code_quality_waivers.py` does not verify that
    waiver `scope` and `rule` metadata match the suppressions that consume the
    waiver ids.
  - Boundary tooling configs can be weakened without a config-integrity
    contract test for `edge-control-plane/dependency-cruiser.cjs` and
    `.importlinter`.
  - `scripts/quality/write_code_quality_proof.py` does not record a git SHA or
    other commit-addressable revision identifier in the proof artifact.
- SEV-3:
  - `edge-control-plane/src/routes/jobs-proxy.ts` forces
    `Content-Type: application/json` on passthrough non-JSON upstream errors.

## Findings Classification
### Must-Fix Now
- SEV-0: fail-closed ownership enforcement for `/jobs/**`.
- SEV-1: document or block the currently undocumented public Worker/session
  routes.
- SEV-1: add `PATCH` to Worker CORS preflight to match the documented schedule
  update route.
- SEV-2: bind waiver metadata to actual suppression usage in
  `scripts/quality/validate_code_quality_waivers.py`.
- SEV-2: add config-integrity contract coverage for
  `edge-control-plane/dependency-cruiser.cjs` and `.importlinter`.

### Intentional Deferrals
- SEV-2: `edge-control-plane/src/auth/session-auth.ts` uses string equality
  instead of constant-time verification for session-token signature checks.
  Reason: this is a meaningful auth-boundary hardening gap, but invalid
  signatures are still rejected today and the fail-open ownership, public API
  contract, and governance-bypass findings are higher-priority blockers for
  this rollout.
  Owner: Platform Engineering
  Follow-up reference:
  `docs/exec-plans/completed/code-quality-governance/tasks/TASK_06_code-quality-governance.md`
- SEV-2: proof artifacts are not yet commit-addressable because
  `scripts/quality/write_code_quality_proof.py` omits git SHA metadata.
  Reason: this weakens audit fidelity but does not create a fail-open transport
  path or silently disable the current blocking governance checks.
  Owner: Platform Engineering
  Follow-up reference:
  `docs/exec-plans/completed/code-quality-governance/tasks/TASK_06_code-quality-governance.md`

### Non-Blocking Residual Risk
- SEV-3: jobs-proxy passthrough errors currently mislabel some non-JSON
  upstream responses as JSON.
- Oracle reiterated two already-known rollout limitations:
  - repo-wide `pytest` still fails in pre-existing unrelated controller-rollout
    and sandbox-auth suites
  - advisory hub modules remain non-blocking by design for wave 1

## Follow-up Resolution (2026-03-13 14:32 ACDT)

### Implemented Fixes
- SEV-0 `/jobs/**` ownership now fails closed:
  - `edge-control-plane/src/contracts/public-api.ts` now requires
    `session_id` on job-status payloads.
  - `edge-control-plane/src/routes/jobs-proxy.ts` now returns deterministic
    `502` responses when authenticated `user_id` / `tenant_id` claims cannot be
    revalidated because the upstream payload omitted those identity fields.
  - Regression coverage:
    - `edge-control-plane/tests/contracts/public-api.contracts.test.ts`
    - `edge-control-plane/tests/integration/jobs-proxy.integration.test.ts`
- SEV-1 public API contract alignment:
  - `docs/references/api-usage.md` now documents the public
    `/session/{session_id}/state`, `/messages`, `GET/POST/DELETE /queue`,
    `DELETE /queue/{prompt_id}`, and `/ws` / `/events` surfaces.
  - `edge-control-plane/src/index.ts` now blocks undocumented
    `/session/{id}` and `/session/{id}/query` ingress paths.
  - Regression coverage:
    - `edge-control-plane/tests/integration/worker-governance.integration.test.ts`
- SEV-1 CORS parity:
  - Worker CORS preflight now allows `PATCH`, matching the documented
    `PATCH /schedules/{schedule_id}` route.
- SEV-2 waiver audit integrity:
  - `scripts/quality/validate_code_quality_waivers.py` now enforces waiver
    `scope` and `rule` against the suppressions that consume each waiver id.
  - Targeted Python tests now cover scope mismatch and rule mismatch failures.
- SEV-2 boundary config integrity:
  - `scripts/quality/check_python_boundary_config.py` and
    `tests/test_python_boundary_config.py` assert the live `.importlinter`
    contracts remain intact.
  - `edge-control-plane/tests/contracts/public-api.contracts.test.ts` now
    asserts the required forbidden rules in
    `edge-control-plane/dependency-cruiser.cjs`.

### Remaining Explicit Deferrals
- SEV-2: `edge-control-plane/src/auth/session-auth.ts` constant-time signature
  verification hardening remains deferred for a later auth-boundary wave.
- SEV-2: `scripts/quality/write_code_quality_proof.py` still omits git SHA
  metadata in generated proof artifacts.

### Remaining Non-Blocking Residual Risk
- SEV-3: jobs-proxy passthrough errors can still mislabel some non-JSON
  upstream error bodies as `Content-Type: application/json`.

### Validation Evidence
- `uv run python scripts/quality/check_docs_governance.py` -> pass
- `uv run python scripts/quality/check_python_governance.py` -> pass
- `uv run python scripts/quality/check_python_boundary_config.py` -> pass
- `uv run python -m pytest tests/test_code_quality_waivers.py tests/test_python_boundary_config.py`
  -> pass (`4 passed`)
- `uv run ruff check .` -> pass
- `npm --prefix edge-control-plane run check` -> pass
- `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> pass
- `npm --prefix edge-control-plane run check:contracts` -> pass (`12 passed`)
- `npm --prefix edge-control-plane run test:integration` -> pass (`15 passed`)
- `npm --prefix edge-control-plane run docs:api` -> pass
- `npm --prefix edge-control-plane run check:boundaries` -> pass
