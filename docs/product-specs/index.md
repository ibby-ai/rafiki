# Product Specs Index

`docs/product-specs/` is the canonical location for product intent and requirement documents.

## Rules
- Store one spec per product capability or major feature.
- Keep specs implementation-agnostic where possible.
- Link each in-flight spec to an active ExecPlan in `docs/exec-plans/active/`.

## Current Specs
- `agent-runtime-hardening.md` - request/tool guardrails and trace correlation for OpenAI agent runtime.
- `agent-sandbox-infra-hardening.md` - control-plane authority, runtime hardening, secret minimization, budget/artifact rails.
- `runtime-readiness-hardening.md` - sandbox startup timeout diagnostics, guarded recycle+retry, and deterministic failure semantics.
- `controller-rollout-cutover-safety.md` - authoritative active-pointer promotion, private warmup gates, drain/rollback semantics, and first-query post-cutover guarantees.

## New Spec Template
```md
# <Feature Name>

## Problem
## User Outcome
## Scope
## Non-Goals
## Success Metrics
## Rollout / Risks
## Linked ExecPlan
```
