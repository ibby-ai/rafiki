# ExecPlans

ExecPlans and their task files are the canonical way to plan and track complex
implementation or refactor work in this repository. They are living documents
and must stay current as the work proceeds. The sections `Progress`,
`Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` are
required and must be updated in the same change wave as code or docs changes.

## Purpose / Big Picture

Explain the intended outcome in a few sentences. State the user-visible or
operator-visible result and how someone can tell the work succeeded.

## Surprises & Discoveries

Document unexpected behaviors, bugs, optimizations, or architectural findings
discovered during implementation. Pair each observation with evidence.

- Observation: ...
- Evidence: ...

## Decision Log

Record every material decision in this format:

- Decision: ...
- Rationale: ...
- Date/Author: ...

## Outcomes & Retrospective

Summarize outcomes, gaps, and lessons learned at major milestones or at
completion. Compare the result against the original purpose.

## Context and Orientation

Describe the current state as if the reader knows nothing. Name the key files,
modules, and contracts by full path. Define non-obvious terms. Do not assume
knowledge of prior plans.

## Plan of Work

Describe the sequence of edits and additions in prose. For each change, name
the file and location and state what will change.

## Concrete Steps

Write each major edit as a separate markdown task file in
`docs/exec-plans/active/{feature_name}/tasks/`, using YAML frontmatter that
links the task back to its parent plan. Example:

```yaml
---
task_id: 01
plan_id: PLAN_{feature_name}
plan_file: ../PLAN_{feature_name}.md
title: Audit current implementation and define input mapping
phase: Phase 1 - Data Model & Request Construction
---
```

## Progress

Use a checkbox list for granular progress. Every stopping point must be
documented here, including partial completion states.

- [x] `(tasks/TASK_01_feature_name.md)` `(2026-03-13 11:55 ACDT)` Example completed step.
- [ ] `(tasks/TASK_02_feature_name.md)` Example incomplete step.
- [ ] `(tasks/TASK_03_feature_name.md)` Example partial step with remaining work called out.

## Sub-Agent Collaboration Evidence

Record the required reviewer activity here for the active plan.

- Reviewer: `<sub-agent id or name>`
  - Scope: ...
  - Findings: ...
  - Resolution: applied | deferred (with reason)

Include `boundary-enforcer` whenever the work changes architecture boundaries,
contract-scope docs, runtime validation, agent definitions, or governance/process docs.

## Testing Approach

Describe the validation strategy for the feature. Name the exact command bundles
that must pass and distinguish blocking checks from advisory checks if needed.

## Proof / Evidence Artifacts

List any generated proof artifacts or machine-readable evidence files under
`docs/generated/` that should ship with the change.

## Constraints & Considerations

Describe important constraints, rollout boundaries, baseline debt, or explicit
non-goals encountered while working on the feature.
