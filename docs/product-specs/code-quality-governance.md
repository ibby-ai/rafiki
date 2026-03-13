# Code Quality Governance

## Problem
Rafiki currently relies on general linting and test coverage, but it does not yet
enforce a durable engineering contract for API documentation, architectural
boundaries, transport/runtime validation, or exception governance across its
mixed Python and Cloudflare TypeScript surfaces. This leaves boundary drift,
undocumented public contracts, and unchecked transport parsing as recurring risks.

## User Outcome
Engineers and coding agents can make changes with an explicit, auditable contract:

- contract-scope APIs must be documented in the stack-appropriate format
- transport/runtime boundaries must validate untrusted inputs
- architectural dependencies must follow documented layer rules
- exceptions must carry owner, scope, reason, and expiry
- CI and agent workflow must reject non-compliant changes

## Scope
- Canonical governance doc and waiver registry under `docs/references/`
- Active ExecPlan and evidence packet for the rollout
- Python governance tooling for targeted doc/type/boundary enforcement
- Worker governance tooling for DTO validation, docs coverage, and dependency rules
- Collaboration-process updates and a dedicated local governance reviewer
- CI and local task wiring for the new checks

## Non-Goals
- Repo-wide strict typing for all Python modules in this wave
- Broad refactors of `modal_backend/main.py`, `modal_backend/jobs.py`, or the
  Worker orchestration entrypoints
- Treating internal Modal or sandbox-controller routes as public API contracts

## Success Metrics
- A canonical governance contract exists and is indexed in repo docs.
- First-wave governance checks block CI for scoped modules.
- Worker ingress/backend DTO parsing uses runtime validation instead of unchecked
  casts for the targeted routes.
- Boundary waivers are machine-readable and CI-validatable.
- Collaboration workflow requires the governance reviewer for the defined change classes.

## Rollout / Risks
- Wave 1 blocks only leaf-like modules and transport DTO seams to avoid freezing
  the orchestration hubs.
- Orchestration-heavy modules remain advisory until they are split or formally
  reclassified with explicit exceptions.
- Boundary rules and Worker DTO parsing can surface pre-existing drift; the plan
  must distinguish intentional deferrals from newly introduced regressions.

## Linked ExecPlan
- `docs/exec-plans/completed/code-quality-governance/PLAN_code-quality-governance.md`
