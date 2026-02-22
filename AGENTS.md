# AGENTS Map

Repository knowledge is the system of record. Agents should route work through the canonical docs below and keep them current in the same PR as code changes.

## Read Order
1. `AGENTS.md`
2. `docs/AGENT_COLLABORATION_PROCESS.md`
3. `ARCHITECTURE.md`
4. `docs/product-specs` (index)
5. `docs/design-docs/index.md`
6. `docs/references/index.md`
7. `docs/exec-plans/index.md`
8. `docs/QUALITY_SCORE.md`, `docs/RELIABILITY.md`, `docs/SECURITY.md`

## Canonical Path Map
- Product intent and requirements: `docs/product-specs`
- Architecture and system design: `docs/design-docs/`
- Mandatory sub-agent planning/review/finalization workflow: `docs/AGENT_COLLABORATION_PROCESS.md`
- Operational references and runbooks: `docs/references/`
  - Canonical Cloudflare ↔ Modal E2E runbook: `docs/references/runbooks/cloudflare-modal-e2e.md`
  - Cloudflare runtime entrypoint index: `docs/references/runtime-docs-overview.md`
- Canonical session token helper for E2E/public Worker auth:
  - `edge-control-plane/scripts/generate-session-token.js`
- Execution planning and tracking:
  - `docs/exec-plans/PLAN_TEMPLATE.md`
  - `docs/exec-plans/active/<feature>/`
  - `docs/exec-plans/completed/<feature>/`
  - `docs/exec-plans/tech-debt-tracker.md`
- Governance guides: `docs/DESIGN.md`, `docs/FRONTEND.md`, `docs/PLANS.md`, `docs/PRODUCT_SENSE.md`, `docs/QUALITY_SCORE.md`, `docs/RELIABILITY.md`, `docs/SECURITY.md`
- Generated reference artifacts: `docs/generated/`

## Routing by Work Type
- New feature, behavior change, or API contract change:
  - Update/create product spec under `docs/product-specs`
  - Add/update active ExecPlan under `docs/exec-plans/active/`
  - Update architecture docs in `docs/design-docs/` if design changed
- Bug fix or small implementation change:
  - Update relevant reference docs in `docs/references/`
  - If Cloudflare ↔ Modal E2E behavior/docs are touched, update `docs/references/runbooks/cloudflare-modal-e2e.md` and related links in the same PR
  - If multi-step/high-risk, track with an active ExecPlan
- Refactor or migration:
  - Create/update an active ExecPlan and link impacted architecture docs
  - Move completed plans to `docs/exec-plans/completed/` when done
- Reliability/security hardening:
  - Record scoring and action items in `docs/QUALITY_SCORE.md`
  - Update `docs/RELIABILITY.md` and/or `docs/SECURITY.md`

## Required Engineering Guardrails
- The sub-agent collaboration process in `docs/AGENT_COLLABORATION_PROCESS.md` is mandatory.

## Deprecated Paths
- `specs` is deprecated. Use `docs/product-specs`.
- `.agent` is deprecated. Use `docs/exec-plans/*`.
- Do not add new tracked files under legacy paths.
