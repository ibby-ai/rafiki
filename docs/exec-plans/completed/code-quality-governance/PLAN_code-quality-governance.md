# ExecPlan: Code Quality Governance

## Purpose / Big Picture
Land a durable, repo-specific code quality governance system for Rafiki that
enforces API documentation, architectural boundaries, transport/runtime input
validation, and auditable waivers across the Python runtime and Cloudflare
control plane. The visible result is a new contract codified in docs, CI,
local task commands, and agent workflow, plus a proof packet showing the exact
checks run for this rollout.

## Surprises & Discoveries
- Observation: The repo is mixed-stack, with Python at the root and a separate
  Worker package under `edge-control-plane/`.
- Evidence: `pyproject.toml`, `edge-control-plane/package.json`
- Observation: Current CI omits Worker validation and only runs Ruff/format/pytest.
- Evidence: `.github/workflows/ci.yml`
- Observation: `uv run mypy modal_backend tests` is not a safe repo-wide gate yet.
- Evidence: baseline run reported `419` errors during planning.
- Observation: Worker transport drift already exists between local TS interfaces
  and Python Pydantic responses.
- Evidence: `edge-control-plane/src/types.ts` vs `modal_backend/models/responses.py`
  and `modal_backend/models/jobs.py`
- Observation: The initial Import Linter `layers` contract was too broad and
  incorrectly treated advisory orchestration modules as blocking scope.
- Evidence: first `lint-imports --config .importlinter` run flagged
  `modal_backend.agent_runtime -> modal_backend.mcp_tools`,
  `modal_backend.mcp_tools -> modal_backend.jobs`, and
  `modal_backend.schedules -> modal_backend.jobs`
- Observation: Ultracite rejected a `src/contracts/index.ts` barrel file even
  though the contract extraction itself was valid.
- Evidence: `npm --prefix edge-control-plane run check` reported `noBarrelFile`
  against `edge-control-plane/src/contracts/index.ts`
- Observation: Reviewer passes found two additional Worker boundary gaps after
  the first implementation batch: spoofable streaming/queue identity and
  incorrect `/session/{id}/stop` method semantics.
- Evidence: implementation reviewers `019ce4b8-a0c5-70e3-b6ae-38c48ab7ac99`
  and `019ce4b8-ad08-7c10-a103-bed76a33c58e`
- Observation: Root `pytest` still fails outside rollout scope, but the current
  failure set remains concentrated in unchanged controller-rollout and
  sandbox-auth suites, with the exact count fluctuating between reruns.
- Evidence: `uv run pytest` on 2026-03-13 ended with `8 failed` in
  `tests/test_controller_rollout.py` and `tests/test_sandbox_auth_header.py`,
  while the generated proof artifact rerun classified `9 failed` in the same suites
- Observation: The Oracle review completed and returned material governance
  findings, led by a SEV-0 fail-open ownership gap on `/jobs/**` plus public
  API documentation, CORS, waiver-audit, and boundary-tooling integrity issues.
- Evidence: completed `oracle status --hours 24 --limit 10`, completed
  `oracle session code-quality-governance-review`, and
  `docs/exec-plans/completed/code-quality-governance/EVIDENCE_oracle-review-2026-03-13.md`

## Decision Log
- Decision: Scope blocking governance to leaf-like Python modules plus Worker auth/contracts.
- Rationale: `modal_backend/main.py`, `modal_backend/jobs.py`, and core Worker
  orchestration files are currently too mixed to block immediately without
  freezing delivery.
- Date/Author: 2026-03-13 / Codex
- Decision: Use Ruff + targeted mypy + Import Linter for Python, and Zod +
  TypeDoc + dependency-cruiser for the Worker package.
- Rationale: Matches the repo’s current toolchain and closes the highest-value
  gaps with minimal new runtime/tooling surface.
- Date/Author: 2026-03-13 / Codex
- Decision: Treat `docs/references/api-usage.md` as the only public API contract
  doc for this rollout.
- Rationale: Internal Modal and sandbox routes reuse path names and should not
  be conflated with the public Worker API.
- Date/Author: 2026-03-13 / Codex
- Decision: Replace the temporary broad Import Linter layer stack with
  wave-1-specific forbidden contracts.
- Rationale: Advisory orchestration hubs must remain review-only in wave 1, so
  blocking Python governance cannot depend on repo-wide layering cleanliness.
- Date/Author: 2026-03-13 / Codex
- Decision: Remove the Worker contracts barrel file and use direct imports from
  `src/contracts/public-api.ts`.
- Rationale: Ultracite treats barrel files as a maintainability regression in
  this package, and the direct-import path keeps the contract surface explicit.
- Date/Author: 2026-03-13 / Codex
- Decision: Split CI into independent Python and Worker jobs plus an
  always-run proof job.
- Rationale: Worker governance must still execute and publish evidence even
  when baseline Python pytest failures remain unrelated to the rollout.
- Date/Author: 2026-03-13 / Codex
- Decision: Derive Worker streaming and queued-prompt identity only from
  authenticated session context, not request payload fields.
- Rationale: Public WebSocket/query and queue bodies are untrusted input and
  must not be able to spoof `session_id`, `session_key`, `user_id`, or `tenant_id`.
- Date/Author: 2026-03-13 / Codex
- Decision: Extract `/session/{id}/stop` parsing/validation into
  `edge-control-plane/src/routes/session-stop-proxy.ts`.
- Rationale: Stop-route transport concerns are easier to test and reason about
  outside the SessionAgent orchestration class, and the extraction locks in
  `GET` read-only vs `POST` mutating semantics.
- Date/Author: 2026-03-13 / Codex
- Decision: Do not claim rollout closure until the Oracle session returns a
  terminal findings transcript and any material findings are either applied or
  explicitly deferred in the active plan.
- Rationale: A real Oracle submission without findings output is a hard failure
  for signoff evidence; the rollout can stay implemented, but it cannot be
  represented as externally reviewed or closed.
- Date/Author: 2026-03-13 / Codex
- Decision: Treat the SEV-0 `/jobs/**` ownership fail-open, the two SEV-1
  public-contract gaps, and the waiver/boundary-config SEV-2 findings as
  must-fix-now before closure; keep the session-token constant-time hardening
  gap and the proof git-SHA gap as explicit deferrals, and the jobs-proxy
  content-type mismatch as residual risk.
- Rationale: Those must-fix items either violate the claimed public API
  contract directly or leave the governance/waiver enforcement story
  bypassable. The proof metadata gap matters, but it does not invalidate the
  existing evidence packet as strongly as the higher-priority contract and
  fail-closed issues.
- Date/Author: 2026-03-13 / Codex
- Decision: Keep `/ws` and `/events` public, document the DO-backed
  `/state`, `/messages`, and queue routes in `docs/references/api-usage.md`,
  and block undocumented session aliases such as `/session/{id}` and
  `/session/{id}/query` at the Worker edge.
- Rationale: The runbook and existing integration docs already treat the
  state/messages/queue/event-bus surfaces as public, but the alias and direct
  session-query passthroughs were undocumented duplicate ingress paths that
  would keep the public contract ambiguous.
- Date/Author: 2026-03-13 / Codex
- Decision: Treat `.importlinter` and `edge-control-plane/dependency-cruiser.cjs`
  as contract surfaces with dedicated integrity assertions, not just tooling
  inputs.
- Rationale: A weakened config can leave the boundary tools green while the
  actual governance contract has already drifted, so the live config contents
  must be asserted explicitly in tests/scripts.
- Date/Author: 2026-03-13 / Codex

## Outcomes & Retrospective
- Landed a wave-1 governance contract across docs, local commands, CI, and the
  repo-local reviewer workflow for Python plus Cloudflare/TypeScript.
- Added blocking enforcement for scoped Python doc/type/boundary checks,
  Worker contract docs/dependency checks, machine-readable waivers, and proof
  artifact generation.
- Closed reviewer findings around CI durability, WebSocket/query identity
  spoofing, queue identity spoofing, auth-token claim validation, and session
  stop transport semantics.
- Remaining failure surface is the unchanged root `pytest` baseline in
  controller-rollout and sandbox-auth suites. Those failures are classified as
  pre-existing unrelated to the wave-1 governance scope rather than waived.
- Oracle follow-up now resolves the must-fix set: `/jobs/**` ownership fails
  closed on missing identity, the public Worker/session surface is documented
  and trimmed to intended routes, `PATCH` is present in CORS preflight, waiver
  auditing binds suppressions to waiver `scope` + `rule`, and live boundary
  configs have integrity coverage.
- Post-Oracle deployed verification found that the canonical public Worker was
  initially serving stale route/CORS behavior, so this wave now includes a live
  Worker redeploy repair (`0cfeea3e-e192-4397-896b-3734c84a9b9c`), a production
  Modal app redeploy, and a public Worker `SESSION_SIGNING_SECRET` realignment
  back to the canonical helper source before authenticated public proof.
- The same public proof wave caught and fixed a real Oracle follow-up runtime
  regression: Modal `/submit` dropped `session_id` during enqueue, which caused
  Worker `/jobs/**` reads to fail runtime validation with deterministic `502`
  responses. The fix now persists and forwards `session_id`, has targeted
  pytest coverage, and was revalidated live through the public Worker.
- Authenticated public proof is now complete on the deployed edge:
  `/query`, `/query_stream`, queue/state/messages, `GET` + `POST /stop`,
  schedules create/list/get/patch, `/submit`, `/jobs/{id}`,
  `/jobs/{id}/artifacts`, and `/events` all passed. The only non-blocking gap
  is artifact download itself, which could not be exercised because the proof
  job's artifact manifest contained zero files.
- The current public auth helper requires explicit `session_id` query scope for
  schedules and other session-scoped routes that do not already name a session
  in path or body; `docs/references/api-usage.md` now reflects that observed
  runtime behavior.
- Remaining explicit product-level follow-up is otherwise limited to the dated
  session-token constant-time verification deferral and the proof git-SHA
  metadata deferral; the only residual non-blocking runtime risk carried
  forward is the jobs-proxy passthrough content-type mismatch on some non-JSON
  upstream errors.

## Context and Orientation
Key files for this rollout:

- Product spec index: `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/docs/product-specs/index.md`
- References index: `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/docs/references/index.md`
- Collaboration contract: `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/docs/AGENT_COLLABORATION_PROCESS.md`
- Quality governance ledger: `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/docs/QUALITY_SCORE.md`
- Python root config: `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/pyproject.toml`
- Worker package config: `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane/package.json`
- Worker CI: `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/.github/workflows/ci.yml`

Scope boundaries:

- Public API: Cloudflare Worker routes documented in `docs/references/api-usage.md`
- Internal/operator API: Modal gateway routes in `modal_backend/main.py`
- Internal sandbox API: controller routes in `modal_backend/api/controller.py`

## Plan of Work
1. Add canonical governance docs, waiver registry, and active plan artifacts in
   the repo’s standard docs hierarchy.
2. Add Python governance tooling for scoped docstring, typing, and import-boundary checks.
3. Add Worker contract schemas, runtime validation, documentation validation,
   and dependency-boundary checks.
4. Add the `boundary-enforcer` local reviewer and make it mandatory in the
   collaboration process for governance-sensitive changes.
5. Wire governance checks into local commands and CI, then generate a proof
   packet with the validated command matrix.

## Concrete Steps
- Task files live under `docs/exec-plans/completed/code-quality-governance/tasks/`.

## Progress
- [x] (tasks/TASK_01_code-quality-governance.md) (2026-03-13 11:55 ACDT) Created
  spec, active plan pack, and initial governance doc/waiver artifact scaffolding.
- [x] (tasks/TASK_02_code-quality-governance.md) (2026-03-13 11:53 ACDT)
  Implemented scoped Python doc/type/boundary governance with Ruff, targeted
  mypy, Import Linter, waiver validation, and source fixes in first-wave modules.
- [x] (tasks/TASK_03_code-quality-governance.md) (2026-03-13 11:53 ACDT)
  Implemented Worker Zod/TSDoc/dependency governance, route-contract helpers,
  DTO/runtime validation, and focused regression tests.
- [x] (tasks/TASK_04_code-quality-governance.md) (2026-03-13 11:53 ACDT)
  Updated collaboration workflow, local reviewer contract, CI job structure,
  proof artifact generation, and quality/reliability/security ledgers.
- [x] (tasks/TASK_05_code-quality-governance.md) (2026-03-13 14:09 ACDT)
  Submitted the real Oracle API review under slug
  `code-quality-governance-review`, captured the completed session output, and
  synced the findings transcript summary into the active plan artifacts.
- [x] (tasks/TASK_06_code-quality-governance.md) (2026-03-13 14:32 ACDT)
  Resolved the Oracle must-fix findings with fail-closed `/jobs/**`
  enforcement, public API/CORS alignment, stronger waiver auditing, and live
  boundary-config integrity coverage. The only remaining open items are the
  two dated SEV-2 deferrals already recorded for this wave.
- [x] Post-Oracle public Worker E2E proof (2026-03-13 15:14 ACDT)
  Repaired the stale deployed Worker, restored the stopped production Modal
  app, realigned the public Worker `SESSION_SIGNING_SECRET`, fixed the Modal
  `/submit` -> `/jobs/**` `session_id` regression, and completed authenticated
  public proof for query, streaming, session DO routes, schedules, jobs,
  artifact listing, and `/events`.

## Sub-Agent Collaboration Evidence
- Planning code-risk reviewer: `019ce48c-6bbd-7131-9858-dc7d977e599a`
  - Applied: keep orchestration hubs advisory in wave 1; prioritize Worker DTO validation.
- Planning docs/evidence reviewer: `019ce48c-7240-7b31-b7e2-b5fd89b2cc68`
  - Applied: use `.claude/agents/*.md` for the local reviewer; require canonical docs/plan/proof locations.
- Implementation reviewer: `019ce4b8-a769-7381-a9e5-9bb77067a975`
  - Applied: split CI into Python/Worker/proof jobs, remove internal SSE sample
    from the public API contract doc, classify remaining proof failures, and
    refresh plan/evidence state before closure.
- Implementation reviewer: `019ce4b8-ad08-7c10-a103-bed76a33c58e`
  - Applied: fix `/session/{id}/stop` so `GET` stays read-only and `POST`
    preserves `mode`, `reason`, and `requested_by`; add runtime validation for
    signed session-token payload shapes.
- Implementation reviewer: `019ce4b8-a0c5-70e3-b6ae-38c48ab7ac99`
  - Applied: prevent spoofed identity fields in streaming query payloads and
    queued prompts; update docs-governance wiring so the plan index validates
    either the active or completed plan location; remove generated bytecode.
- Boundary-enforcer reviewer: `019ce4c9-4152-7323-84a6-7735d7726b62`
  - Applied: document deterministic `400`/`502` stop-route failures in the
    public API guide, derive `requested_by` from authenticated actor context
    instead of client input, and refresh stale plan/ledger/proof references.

## Testing Approach
- Python: targeted Ruff doc checks, targeted mypy, Import Linter, pytest
- Worker: Ultracite, `tsc --noEmit`, integration tests, TypeDoc validation,
  dependency-cruiser, and DTO validation tests
- Docs/process: index/path sanity checks plus waiver registry validation
- Current results:
  - `uv run python scripts/quality/check_docs_governance.py` -> pass
  - `uv run python scripts/quality/check_python_governance.py` -> pass
  - `uv run python scripts/quality/check_python_boundary_config.py` -> pass
  - `uv run ruff check .` -> pass
  - `uv run python -m pytest tests/test_code_quality_waivers.py tests/test_python_boundary_config.py`
    -> pass (`4 passed`)
  - `npm --prefix edge-control-plane run check` -> pass
  - `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> pass
  - `npm --prefix edge-control-plane run check:contracts` -> pass (`12 passed`)
  - `npm --prefix edge-control-plane run test:integration` -> pass (`15 passed`)
  - `npm --prefix edge-control-plane run docs:api` -> pass
  - `npm --prefix edge-control-plane run check:boundaries` -> pass
  - `uv run pytest` -> fail (`8 failed`, pre-existing unrelated baseline in
    `tests/test_controller_rollout.py` and `tests/test_sandbox_auth_header.py`)
  - `uv run python scripts/quality/write_code_quality_proof.py` -> wrote proof
    artifact with `rollout_checks_passed=true` and the same baseline suites
    classified as pre-existing unrelated
  - `set -a && source ./.env && set +a && export ORACLE_HOME_DIR=.oracle && oracle status --hours 24 --limit 10`
    -> no pre-existing session rows before submission; later snapshot showed
    slug `code-quality-governance-review` as `completed`
  - `set -a && source ./.env && set +a && export ORACLE_HOME_DIR=.oracle && scripts/oracle/code_quality_governance_review.sh --real-run`
    -> submitted a new Oracle API background session with slug
    `code-quality-governance-review`
  - `set -a && source ./.env && set +a && export ORACLE_HOME_DIR=.oracle && oracle session code-quality-governance-review`
    -> reattached to the same session and captured a completed response with
    material findings (`resp_0dde9b3a794b16c70069b37662298c819c8576b573114937c8`,
    `request=req_863cb3f49a074836bf4436caf3347d60`)
  - `curl -sS -D - https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/health`
    (before repair) -> pass for `HTTP 200`, but CORS still omitted `PATCH`
  - `curl -sS -D - -o /dev/null -X OPTIONS 'https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/schedules/sched-preflight' ...`
    (before repair) -> fail; `Access-Control-Allow-Methods` was
    `GET, POST, DELETE, OPTIONS`
  - `curl -sS -D - -o /dev/null 'https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev/session/sess-contract-404'`
    and related alias/method probes (before repair) -> fail; returned `401`
    where the Oracle follow-up contract required `404` / `405`
  - `npm --prefix edge-control-plane run test:integration` -> pass (`15 passed`)
  - `npm --prefix edge-control-plane run check:contracts` -> pass (`12 passed`)
  - `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> pass
  - `npm --prefix edge-control-plane run deploy` -> pass; deployed
    `https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev` as version
    `0cfeea3e-e192-4397-896b-3734c84a9b9c`
  - post-deploy public probes for `/health`, schedule `PATCH` preflight,
    blocked `/session/{id}` aliases, and unsupported session-route methods
    -> pass with live `200` / `204` / `404` / `405` behavior matching the
    Oracle follow-up contract
  - authenticated public `/query` probes using locally available secrets
    initially failed with `401 Invalid token signature`, which identified the
    deployed Worker secret drift
  - `uv run modal deploy -m modal_backend.deploy` -> pass; restored the
    stopped production Modal HTTP app before authenticated public proof
  - `cd edge-control-plane && printf '%s' "$SESSION_SIGNING_SECRET" | ./node_modules/.bin/wrangler secret put SESSION_SIGNING_SECRET`
    -> pass; public Worker tokens minted by the canonical helper now verify
  - `uv run python -m pytest tests/test_jobs_enqueue.py` -> pass (`5 passed`)
  - `uv run python -m pytest tests/test_schemas_jobs.py` -> pass (`6 passed`)
  - second `uv run modal deploy -m modal_backend.deploy` -> pass; published the
    Modal fix that preserves and forwards `session_id` for public job reads
  - authenticated public matrix rerun -> pass for `/query`, `/query_stream`,
    `GET` + `POST /session/{id}/stop`, queue/state/messages, `/submit`,
    `/jobs/{id}`, and `/jobs/{id}/artifacts`
  - authenticated public schedule CRUD with explicit `?session_id=...`
    query scope -> pass for create/list/get/patch; bare-token schedule calls
    without session scope return `403 Session not authorized` under the current
    Worker auth helper and are now documented as such
  - authenticated public `/events` probe -> pass; received
    `connection_ack`, `presence_update`, and `job_submitted`

## Oracle Review Outcome
- Resolved in follow-up:
  - SEV-0: `/jobs/**` ownership enforcement now fails closed. The public job
    status contract requires `session_id`, and the Worker returns deterministic
    `502` responses when upstream payloads omit `user_id` or `tenant_id`
    required by the authenticated actor scope.
  - SEV-1: `docs/references/api-usage.md` now documents the public DO-backed
    session state/messages/queue routes plus `/ws` and `/events`, and the
    Worker blocks undocumented `/session/{id}` and `/session/{id}/query`
    ingress paths with `404`.
  - SEV-1: Worker CORS preflight now includes `PATCH` and regression coverage
    locks that into the public schedule-update contract.
  - SEV-2: waiver validation now binds each suppression to the waiver's
    declared `scope` and `rule`, with deterministic failures when either drift.
  - SEV-2: live config-integrity coverage now asserts the required forbidden
    rules in both `edge-control-plane/dependency-cruiser.cjs` and `.importlinter`.
- Intentional deferral:
  - SEV-2: `edge-control-plane/src/auth/session-auth.ts` uses string equality
    rather than constant-time verification for session-token signature checks.
    Reason: this is a real auth-boundary hardening gap, but current HMAC
    verification still rejects invalid signatures and the higher-priority
    fail-open/public-contract issues take precedence in this rollout wave.
    Owner: Platform Engineering.
    Follow-up reference: `tasks/TASK_06_code-quality-governance.md`.
  - SEV-2: proof artifacts are not yet commit-addressable because
    `scripts/quality/write_code_quality_proof.py` omits git SHA metadata.
    Reason: this weakens audit strength but does not create a fail-open request
    path or silently bypass the current blocking governance checks.
    Owner: Platform Engineering.
    Follow-up reference: `tasks/TASK_06_code-quality-governance.md`.
- Non-blocking residual risk:
  - SEV-3: `edge-control-plane/src/routes/jobs-proxy.ts` forces
    `Content-Type: application/json` on passthrough non-JSON error bodies.
  - Oracle also reiterated two already-documented rollout risks: repo-wide
    `pytest` remains red in pre-existing unrelated suites, and the advisory hub
    modules remain non-blocking by design for wave 1.

## Proof / Evidence
- Canonical proof artifact:
  `docs/generated/code-quality-governance-proof-2026-03-13T11-59-01+1030.json`
- Oracle evidence note:
  `docs/exec-plans/completed/code-quality-governance/EVIDENCE_oracle-review-2026-03-13.md`
- Post-Oracle public E2E evidence note:
  `docs/exec-plans/completed/code-quality-governance/EVIDENCE_post-oracle-public-e2e-2026-03-13.md`
- Current evidence summary:
  - Worker/public contract docs now exclude internal SSE examples, document the
    public DO-backed state/messages/queue/event-bus surfaces, and block the
    undocumented session alias/query passthrough routes at the Worker edge.
  - `/jobs/**` actor-scope enforcement now rejects missing authoritative
    identity fields instead of silently trusting partial upstream payloads.
  - `docs/references/code-quality-waivers.json` remains empty, so no rollout
    exceptions are hidden behind waivers.
  - Waiver auditing and boundary-config integrity now have dedicated targeted
    tests/scripts beyond the baseline tool runs.
  - The proof writer now classifies remaining failures as passed, advisory,
    waived, pre-existing unrelated, or unclassified blockers.
  - The latest proof artifact reports `all_passed=false` because root `pytest`
    still fails, but `rollout_checks_passed=true` because all blocking
    governance checks passed and the remaining baseline failures were classified.
  - The Oracle evidence note now records the completed session metadata, a
    faithful summary of all Oracle findings, and the must-fix/deferral/residual
    classification used to keep the rollout open.
  - The post-Oracle public E2E evidence note records the stale deployed Worker
    repair, the live route/CORS probes that now pass after redeploy, and the
    remaining authenticated-proof blocker caused by the missing deployed public
    session-token signing secret.

## Constraints & Considerations
- Keep wave 1 incremental and evidence-driven. Do not claim repo-wide strictness.
- Preserve the distinction between public Worker contracts and internal Modal/controller routes.
- Existing Python mypy debt is baseline debt unless directly touched by scoped governance files.
- Do not close this active plan until Oracle findings are captured and any
  material issues are either fixed or explicitly deferred in this plan.
- Do not claim rollout closure until a valid deployed public session token
  source is available and the authenticated public-ingress matrix has been
  rerun through the canonical Worker URL.
