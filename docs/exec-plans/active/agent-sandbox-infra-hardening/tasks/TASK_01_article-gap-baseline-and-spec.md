---
task_id: 01
plan_id: PLAN_agent-sandbox-infra-hardening
plan_file: ../PLAN_agent-sandbox-infra-hardening.md
title: Build article gap baseline and acceptance criteria
phase: Phase 1 - Baseline and scope control
---

- Create a structured gap matrix comparing the referenced X article patterns against Rafiki (`already`, `partial`, `gap`).
- Define explicit acceptance criteria for each `gap` item (security, reliability, test evidence, docs evidence).
- Create `docs/product-specs/agent-sandbox-infra-hardening.md` (or explicitly amend `docs/product-specs/agent-runtime-hardening.md`), update `docs/product-specs/index.md`, and link one canonical active ExecPlan.
- Add an internal reference note under `docs/references/` that captures article URL, retrieval date, and distilled principles used by this plan.
- Record no-goals to prevent uncontrolled architecture churn.

## Done When
- Product spec linkage is unambiguous in `docs/product-specs/index.md`.
- Article-derived principles are documented in-repo without requiring live X access.
- Gap matrix includes clear `owner`, `risk`, and `evidence` fields per item.
