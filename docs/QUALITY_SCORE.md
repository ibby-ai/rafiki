# QUALITY SCORE

## Metadata
- Review date (YYYY-MM-DD): 2026-03-02
- Owner: Platform Engineering
- Scope: Repository knowledge system, architecture docs, and execution plan governance

## Rubric (1-5)
- `1`: Missing or unreliable
- `2`: Partial and frequently stale
- `3`: Adequate and usable with gaps
- `4`: Strong, mostly current, low ambiguity
- `5`: Excellent, current, auditable, and enforced in workflow

## Scorecard
| Dimension | Score (1-5) | Evidence | Required Action |
|---|---|---|---|
| Product intent clarity | 3 | Canonical product-spec index exists, but no specs yet | Add initial product specs for core capabilities. |
| Architecture clarity | 4 | Canonical design-doc taxonomy and index established | Keep architecture docs updated with each design change. |
| Plan/task traceability | 4 | Active/completed plan split with linked tasks in repo | Ensure active plans close out and move to completed. |
| Operational references | 4 | References taxonomy and migrated docs in place | Audit quarterly for stale examples and endpoint drift. |
| Security/reliability governance | 3 | Baseline governance docs added | Add measurable SLO/security review cadence entries. |

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
