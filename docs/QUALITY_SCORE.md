# QUALITY SCORE

## Metadata
- Review date (YYYY-MM-DD): 2026-03-13
- Owner: Platform Engineering
- Scope: Repository knowledge system, architecture docs, execution-plan governance, and code-quality governance enforcement

## Rubric (1-5)
- `1`: Missing or unreliable
- `2`: Partial and frequently stale
- `3`: Adequate and usable with gaps
- `4`: Strong, mostly current, low ambiguity
- `5`: Excellent, current, auditable, and enforced in workflow

## Scorecard
| Dimension | Score (1-5) | Evidence | Required Action |
|---|---|---|---|
| Product intent clarity | 4 | Canonical product-spec index now includes governance, runtime-readiness, and rollout specs | Expand spec coverage as remaining advisory modules are promoted into blocking scope. |
| Architecture clarity | 5 | Architecture index plus governance layer map now define blocking vs advisory scope explicitly | Keep the layer map current when modules move between advisory and blocking scope. |
| Plan/task traceability | 5 | Active/completed plan split plus governance task pack, reviewer evidence, and proof artifact are in repo | Preserve proof artifacts and close active plans promptly once rollout follow-ups finish. |
| Operational references | 5 | References index now includes the governance contract, waiver registry, and public API validation expectations | Re-run docs sanity checks whenever contract-scope docs or indexes change. |
| Security/reliability governance | 5 | Governance contract, CI gates, waiver registry, and reliability/security ledgers now reflect the rollout | Ratchet additional advisory modules only with evidence-backed validation. |

## Action Item Expectations
- Every score below `4` must have a dated action item in the next active ExecPlan.
- Re-score after meaningful process or architecture changes.
- Include links to changed docs in PR descriptions when scores move.

## Re-Score (2026-03-02) - Agent Sandbox Infra Hardening

### Change Wave Scope
- ExecPlan: `docs/exec-plans/completed/agent-sandbox-infra-hardening/PLAN_agent-sandbox-infra-hardening.md`
- Tasks completed: 01-06 (secret surface split, runtime hardening, tool isolation, budget rails, artifact token hardening, doc/governance sync)

### Evidence Snapshot
- `uv run python -m pytest tests/test_controller_runtime_openai.py tests/test_controller_tools.py tests/test_schemas_sandbox.py tests/test_internal_auth_middleware.py tests/test_settings_openai.py tests/test_tools_calculate.py tests/test_runtime_hardening.py tests/test_jobs_security.py tests/test_artifact_access.py tests/test_sandbox_auth_header.py` -> passed (`86 passed`)
- `npm --prefix edge-control-plane run check` -> passed
- `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> passed
- `npm --prefix edge-control-plane run test:integration` -> passed (`3 passed`)
- Runtime checks:
  - `uv run modal run -m modal_backend.main` -> passed after local-entrypoint auth guard update
  - `uv run modal run -m modal_backend.main::run_agent_remote --question "sandbox hardening smoke check"` -> passed
  - Budget denial smoke via local Worker (`/query`, `/query_stream`, `/session/{id}/queue`) -> deterministic `429` denial payloads verified

### Updated Scorecard
| Dimension | Previous | New | Rationale |
|---|---|---|---|
| Product intent clarity | 3 | 4 | Dedicated hardening product spec with measurable gap matrix and acceptance criteria. |
| Architecture clarity | 4 | 4 | Architecture now documents scoped auth, budget authority, and artifact-token flow. |
| Plan/task traceability | 4 | 5 | Task pack includes explicit evidence/remediation/doc-sync contracts with timestamped progress entries. |
| Operational references | 4 | 5 | Runbook/config/API/tool docs updated in the same implementation wave with executable commands. |
| Security/reliability governance | 3 | 4 | Security/reliability docs now include dated hardening evidence and residual-risk framing. |

## Re-Score (2026-03-02) - Runtime Readiness Hardening Follow-up

### Change Wave Scope
- Product spec: `docs/product-specs/runtime-readiness-hardening.md`
- ExecPlan: `docs/exec-plans/completed/runtime-readiness-hardening/PLAN_runtime-readiness-hardening.md`
- Runtime code/tests/docs updated for deterministic startup readiness recovery

### Evidence Snapshot
- `uv run python -m pytest tests/test_sandbox_auth_header.py tests/test_settings_openai.py` -> passed (`22 passed`)
- Required validation matrix -> passed (`96 passed`, `2 warnings`)
- Cloudflare <-> Modal E2E rerun -> failed (`500 {"ok":false,"error":"Unknown error"}`) with healthy `/health` probes and mitigation logged

### Score Impact
| Dimension | Previous | New | Rationale |
|---|---|---|---|
| Architecture clarity | 4 | 5 | Lifecycle docs now include startup-timeout diagnostics and bounded retry semantics. |
| Operational references | 5 | 5 | Canonical runbook/troubleshooting now includes readiness-timeout triage and `.venv` activation contract. |
| Security/reliability governance | 4 | 5 | Reliability/security docs now include this wave's startup hardening evidence and residual risk framing. |

## Re-Score (2026-03-02) - TD-003 `/query` Live E2E Closure

### Change Wave Scope
- Runtime fixes:
  - `modal_backend/main.py` (`/query` upstream error normalization)
  - `modal_backend/settings/settings.py` (sandbox secret surface includes `modal-auth-secret` when enabled)
  - `modal_backend/agent_runtime/base.py` and `modal_backend/api/controller.py` (writable OpenAI session DB fallback)
- Tests:
  - `tests/test_query_proxy_error_normalization.py`
  - `tests/test_agent_runtime_session_fallback.py`
  - `tests/test_controller_runtime_openai.py` updates
- Docs/governance sync:
  - runbook/config/troubleshooting/runtime-docs/design docs + tech debt tracker

### Evidence Snapshot
- `npm --prefix edge-control-plane run check` -> passed
- `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> passed
- `npm --prefix edge-control-plane run test:integration` -> passed (`3 passed`)
- `uv run python -m pytest tests/test_controller_runtime_openai.py tests/test_controller_tools.py tests/test_schemas_sandbox.py tests/test_internal_auth_middleware.py tests/test_settings_openai.py tests/test_tools_calculate.py tests/test_runtime_hardening.py tests/test_jobs_security.py tests/test_artifact_access.py tests/test_sandbox_auth_header.py tests/test_query_proxy_error_normalization.py tests/test_agent_runtime_session_fallback.py` -> passed (`106 passed`)
- Live E2E:
  - Worker `/query` failure reproduced as `500 {"ok":false,"error":"Unknown error"}`
  - intermediate concrete failures surfaced and remediated (`AuthError token missing`, `readonly database`)
  - final Worker `/query` rerun -> `200` with `ok:true`, expected `session_id`, and non-empty `messages`

### Score Impact
| Dimension | Previous | New | Rationale |
|---|---|---|---|
| Operational references | 5 | 5 | Failure signatures/remediation paths now cover TD-003 token/db edge cases and concrete Worker error expectations. |
| Security/reliability governance | 5 | 5 | TD-003 moved from open debt to closed with auditable matrix + live E2E proof and strict-auth preservation. |

## Re-Score (2026-03-09) - Modal SDK 1.3.5 Upgrade

### Change Wave Scope
- ExecPlan: `docs/exec-plans/completed/modal-sdk-1-3-5-upgrade/PLAN_modal-sdk-1-3-5-upgrade.md`
- Runtime/test/docs updates for Modal dependency freshness, async-interface safety, and deterministic teardown behavior

### Evidence Snapshot
- `uv run python -m pytest tests/test_sandbox_auth_header.py tests/test_query_proxy_error_normalization.py` -> passed (`28 passed`)
- `uv run python -m pytest tests/test_schedules.py tests/test_jobs_enqueue.py tests/test_jobs_cancellation.py tests/test_jobs_security.py` -> passed (`21 passed`)
- `uv run python -W error -m pytest -o asyncio_default_fixture_loop_scope=function tests/test_sandbox_auth_header.py -k 'prewarm or get_or_start_background_sandbox_aio or terminate'` -> passed (`8 passed`)
- `npm --prefix edge-control-plane run check` -> passed
- `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> passed

### Score Impact
| Dimension | Previous | New | Rationale |
|---|---|---|---|
| Architecture clarity | 5 | 5 | Runtime docs now capture the Modal 1.3.5 async-interface contract and explicit teardown semantics. |
| Operational references | 5 | 5 | Operator docs now pin the repo Modal floor and the repo-local Python validation command shape. |
| Security/reliability governance | 5 | 5 | Reliability evidence now includes warning-sensitive async regression coverage for the upgraded SDK. |

## Re-Score (2026-03-10) - Controller Rollout Cutover Safety

### Change Wave Scope
- Product spec: `docs/product-specs/controller-rollout-cutover-safety.md`
- ExecPlan: `docs/exec-plans/completed/controller-rollout-cutover-safety/PLAN_controller-rollout-cutover-safety.md`
- Runtime/test/docs updates for authoritative active-pointer rollout, guarded generation-transition commit, lease-start admission, private readiness gates, request-lease drain accounting, and serve-safe cutover validation

### Evidence Snapshot
- `uv run python -m pytest tests/test_controller_rollout.py` -> passed (`37 passed`)
- `uv run python -m pytest tests/test_sandbox_auth_header.py -k 'prewarm or stop_session or get_or_start_background_sandbox'` -> passed (`12 passed`)
- `uv run python -m pytest tests/test_internal_auth_middleware.py tests/test_settings_openai.py` -> passed (`27 passed`)
- `npm --prefix edge-control-plane run check` -> passed
- `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> passed
- `npm --prefix edge-control-plane run test:integration` -> passed (`3 passed`)
- Deterministic harnesses still cover stale recovered-service bootstrap, empty-pointer bootstrap, and concrete overlap rejection.
- Deployed spawned-drain proof now correlates schedule -> execution -> completion for two consecutive public-ingress cutovers with persisted drain call ids and Modal app logs.
- Generated proof artifact: `docs/generated/controller-rollout-cutover-safety-proof-2026-03-10T13-48-41-1030.json`
- Documentation surfaces updated together across product spec, active plan, architecture, configuration, troubleshooting, and canonical E2E runbook
- Live deployed cutover proof now passes end-to-end through the canonical public Worker:
  - public Worker repair configured `INTERNAL_AUTH_SECRET` and `SESSION_SIGNING_SECRET`, redeployed `rafiki-control-plane`, and returned `/health` `200` at `https://rafiki-control-plane.ibrahim-aka-ajax.workers.dev`.
  - the deployed Modal surface initially had `active: null`, so A was primed once through the deployed internal `/query` path before pre-cutover public ingress proof.
  - deployed cutover `1 -> 2` scheduled spawned drain `fc-01KKAV7J9BHCF70NNHFZEFF2AQ` for `sb-bPtHWeXLDnPjPelePXo3Yw`; the first public Worker `/query` after cutover returned `HTTP 200` on the first try, and `/query_stream`, queue, and state checks passed.
  - deployed cutover `2 -> 3` scheduled spawned drain `fc-01KKAV8YCS28RD8F8YQH464TT2` for `sb-flJnocn5fMxdPxld6aUmdE`; the first public Worker `/query` after cutover returned `HTTP 200` on the first try, and `/query_stream`, queue, and state checks passed.
  - both cutovers recorded `drain_timeout_reached=false`, `inflight_at_termination.total=0`, and matching `drain_execution_call_id`.
  - this proof wave still records `dirty_worktree=true`, so clean-commit reproducibility remains a separate signoff limitation.

### Score Impact
| Dimension | Previous | New | Rationale |
|---|---|---|---|
| Product intent clarity | 4 | 5 | Dedicated rollout product spec now defines promotion gates, rollback order, drain rules, and first-query success criteria. |
| Architecture clarity | 5 | 5 | Design docs now encode active-pointer authority, guarded generation-transition commit, no fixed-name routing authority, and lease-based drain accounting. |
| Operational references | 5 | 5 | Runbook/config/troubleshooting now distinguish deployed vs local cutover trigger paths, inline-drain fallback semantics, and the exact cutover proof sequence. |
| Security/reliability governance | 5 | 5 | Governance docs now include changed-surface regression gates, live cutover proof, and explicit stale-writer/prewarm fail-closed guarantees. |

## Follow-up (2026-03-10) - Cloudflare Deploy Target Hardening

### Change Wave Scope
- Worker config/scripts/docs updates that remove the manual production `--var` override requirement for `rafiki-control-plane`

### Evidence Snapshot
- top-level `edge-control-plane/wrangler.jsonc` is now production-safe for the canonical public Worker
- `env.development` carries the local/dev Modal target plus explicit Durable Object script names for isolated dev state
- `npm run dev` now expands to `wrangler dev --env development`
- `cd edge-control-plane && ./node_modules/.bin/wrangler deploy --dry-run` -> passed
- `cd edge-control-plane && ./node_modules/.bin/wrangler deploy --dry-run --env development` -> passed
- `npm --prefix edge-control-plane run check` -> passed
- `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> passed
- `npm --prefix edge-control-plane run test:integration` -> passed (`3 passed`)

### Score Impact
| Dimension | Previous | New | Rationale |
|---|---|---|---|
| Product intent clarity | 5 | 5 | Product-facing rollout intent did not change. |
| Architecture clarity | 5 | 5 | Worker environment separation is clearer, but overall architecture score is unchanged. |
| Operational references | 5 | 5 | Production deploys no longer depend on ad hoc CLI overrides; docs now encode the safer default directly. |
| Security/reliability governance | 5 | 5 | The P3 operator-footgun is closed in config/scripts/docs, with validation evidence captured in the same wave. |

## Re-Score (2026-03-13) - Code Quality Governance

### Change Wave Scope
- Product spec: `docs/product-specs/code-quality-governance.md`
- ExecPlan: `docs/exec-plans/completed/code-quality-governance/PLAN_code-quality-governance.md`
- Runtime/test/docs/CI updates for enforceable API docs, boundary checks,
  waiver auditing, review workflow, and the Oracle must-fix follow-up batch

### Evidence Snapshot
- `uv run python scripts/quality/check_docs_governance.py` -> pass
- `uv run python scripts/quality/check_python_governance.py` -> pass
- `uv run python scripts/quality/check_python_boundary_config.py` -> pass
- `uv run python -m pytest tests/test_code_quality_waivers.py tests/test_python_boundary_config.py`
  -> pass (`4 passed`)
- `uv run ruff check .` -> pass
- `npm --prefix edge-control-plane run check` -> pass
- `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> pass
- `npm --prefix edge-control-plane run test:integration` -> pass (`15 passed`)
- `npm --prefix edge-control-plane run check:contracts` -> pass (`12 passed`)
- `npm --prefix edge-control-plane run docs:api` -> pass
- `npm --prefix edge-control-plane run check:boundaries` -> pass
- `uv run pytest` -> failed with unrelated baseline failures in `tests/test_controller_rollout.py` and `tests/test_sandbox_auth_header.py`; current reruns fluctuate between `8` and `9` failures in those unchanged suites.
- Generated proof artifact: `docs/generated/code-quality-governance-proof-2026-03-13T11-59-01+1030.json` (`rollout_checks_passed=true`, remaining failures classified as pre-existing unrelated)

### Score Impact
| Dimension | Previous | New | Rationale |
|---|---|---|---|
| Architecture clarity | 5 | 5 | The architecture entry point now includes an explicit governance layer map and wave-1 boundary scope. |
| Plan/task traceability | 5 | 5 | The rollout ships with a canonical plan pack, reviewer workflow changes, and a machine-readable proof artifact. |
| Operational references | 5 | 5 | Governance rules, waivers, API validation behavior, public session/event routes, and required commands are now documented together. |
| Security/reliability governance | 5 | 5 | Boundary validation, waiver auditing, config-integrity assertions, and CI enforcement are durable even though unrelated baseline pytest failures remain outside this rollout scope. |
