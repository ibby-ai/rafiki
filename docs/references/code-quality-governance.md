# Code Quality Governance

This document is the canonical engineering contract for code quality governance
in Rafiki. It defines which modules are in scope for blocking enforcement,
which layers may depend on which, which APIs must be documented, how untrusted
inputs are validated, and how exceptions are recorded.

## Principles
- Documentation is part of the implementation contract.
- Boundary rules are enforced by tools and CI, not by convention alone.
- Public transport boundaries must validate untrusted inputs at runtime.
- Exceptions must be explicit, owned, time-bounded, and auditable.
- Rollout is incremental; orchestration hubs can be advisory while leaf modules
  are already blocking.

## Public Contract Boundary
- Public/client API: Cloudflare Worker routes documented in `docs/references/api-usage.md`
- Internal/operator API: Modal gateway routes in `modal_backend/main.py`
- Internal runtime API: controller routes in `modal_backend/api/controller.py`

Only the public Worker API is treated as the public contract surface in wave 1.

## Layer Contract
### Python
| Layer | Modules | Allowed Dependencies |
|---|---|---|
| Foundation | `modal_backend.models`, `modal_backend.settings` | stdlib, external libraries |
| Cross-cutting infra | `modal_backend.security`, `modal_backend.platform_services`, `modal_backend.instructions`, `modal_backend.tracing` | foundation |
| Runtime | `modal_backend.agent_runtime`, `modal_backend.mcp_tools` | foundation, cross-cutting infra |
| Domain/orchestration | `modal_backend.jobs`, `modal_backend.schedules`, `modal_backend.controller_rollout` | foundation, cross-cutting infra, runtime |
| Transport/composition | `modal_backend.api`, `modal_backend.main` | all internal layers |

### Worker
| Layer | Modules | Allowed Dependencies |
|---|---|---|
| Foundation | `edge-control-plane/src/contracts`, `edge-control-plane/src/types.ts` | external libraries |
| Auth boundary | `edge-control-plane/src/auth` | foundation |
| Transport/orchestration | `edge-control-plane/src/routes`, `edge-control-plane/src/durable-objects`, `edge-control-plane/src/index.ts` | foundation, auth boundary |

## Wave 1 Blocking Scope
### Python
- `modal_backend/models/**`
- `modal_backend/security/**`
- `modal_backend/platform_services/webhooks.py`
- `modal_backend/api/serialization.py`

### Worker
- `edge-control-plane/src/auth/**`
- `edge-control-plane/src/contracts/**`

## Advisory Scope
- `modal_backend/main.py`
- `modal_backend/jobs.py`
- `modal_backend/api/controller.py`
- `modal_backend/settings/settings.py`
- `edge-control-plane/src/index.ts`
- `edge-control-plane/src/durable-objects/session-agent.ts`
- `edge-control-plane/src/types.ts`

Advisory modules are reviewed by `boundary-enforcer`, but they do not block
wave-1 governance checks unless explicitly promoted into scope.

## Documentation Contract
### Python
- Contract-scope modules and public classes/functions must use Google-style docstrings.
- Docstrings are enforced with Ruff `D` and `DOC` rules in the scoped Python modules.
- Docstrings must explain behavior at boundary-heavy modules, not just restate names.

### Worker TypeScript
- Contract-scope exported APIs must use TSDoc-compatible comments.
- TypeDoc validation is the blocking docs gate for the scoped Worker modules.
- Public contract docs remain anchored to `docs/references/api-usage.md`.

## Runtime Boundary Validation
- Untrusted HTTP request bodies in the Worker must be parsed with Zod schemas.
- Worker parsing of Modal backend JSON responses must also use runtime validation.
- FastAPI/Pydantic models remain the source of truth for Python request and response contracts.

## Waivers
- Waivers live in `docs/references/code-quality-waivers.json`.
- Inline suppressions in scoped files must include an adjacent
  `code-quality-waiver: <id>` marker.
- Every waiver must include:
  - `id`
  - `rule`
  - `scope`
  - `owner`
  - `reason`
  - `expires_on`
  - `tracking_ref`
- Expired, malformed, or unknown-rule waivers fail CI.
- Waiver validation also fails when a suppression uses a waiver outside the
  waiver's declared `scope`, or when the suppression kind does not match the
  waiver's declared `rule`.
- Anonymous ignore comments are not governance-approved waivers.

## Required Commands
- Python governance:
  - `uv run python scripts/quality/validate_code_quality_waivers.py`
  - `uv run python scripts/quality/check_python_boundary_config.py`
  - `uv run python scripts/quality/check_python_governance.py`
- Worker governance:
  - `npm --prefix edge-control-plane run check:contracts`
  - `npm --prefix edge-control-plane run docs:api`
  - `npm --prefix edge-control-plane run check:boundaries`
- Baseline validation:
  - `uv run ruff check .`
  - `uv run pytest`
  - `npm --prefix edge-control-plane run check`
  - `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit`
  - `npm --prefix edge-control-plane run test:integration`
- Aggregated local tasks:
  - `make governance`
  - `make release-quality`
  - `make quality-contracts`
  - `make governance-proof`

## Review Workflow
- The local governance reviewer is `.claude/agents/boundary-enforcer.md`.
- It is mandatory for:
  - architectural boundary changes
  - contract-scope documentation changes
  - transport/runtime validation changes
  - agent-definition changes
  - governance/process doc changes

## Proof Packet
- Proof artifacts live under `docs/generated/`.
- Each artifact must record the command matrix, pass/fail status, and whether
  remaining exceptions are advisory, waived, or explicitly classified as
  pre-existing unrelated baseline failures.
- The canonical proof writer for this rollout is `scripts/quality/write_code_quality_proof.py`.
