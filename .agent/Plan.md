# ExecPlans

ExecSpecs (and their associated tasks) are used to plan and track the progress of complex features or refactoring. The resulting files are living documents and should be updated as the work proceeds. The sections `Progress`, `Suprises & Discoveries`, `Decision Log` and `Outcomes & Retrospective` must be kept up to date as the work proceeds.

This file outlines what an ExecPlan is and what it should contain.

## Purpose / Big Picture

Explain in a few sentences what someone gains after this changes and how they can see it working. State the user-visible behavior you will enable.

## Suprises & Discoveries

Document unexpected behaviors, bugs, optimizations, or insights discovered during implementation. Provide consice evidence.

- Observation: ...
- Evidence: ...

## Decision Log

Record every decision made while working on the plan in the format:

- Decision: ...
- Rationale: ...
- Date/Author: ...

## Outcomes & Retrospective

Summarise outcomes, gaps, and lessons learned at major milestones or at completion. Compare the result against the original purpose.

## Context and Orientation

Decribe the current state relevant to this task as if the reader knows nothing. Name the key files and modules by full path. Define any non-obvious term you will use. Do not refer to prior plans.

## Plan of Work

Describe, in prose, the sequence of edits and additions. For each edit, name the file and location and what to insert or change.

## Concrete Steps

Write each edit as a separate markdown file in the .agent/tasks/{feature_name}/ directory, with a YAML frontmatter linking it to its parent plan. For example:

```yaml
---
task_id: 01
plan_id: PLAN_{feature_name}
plan_file: ../../plans/PLAN_{feature_name}.md
title: Audit current implementation and define input mapping
phase: Phase 1 - Data Model & Request Construction
```

## Progress

Use a list with checkboxes to summarize granular steps. Every stopping point must be documented here, even if it requires splitting a partially completed task into two ("done" vs "remaining"). This section must always reflect the current state of the work.

[x] (TASK*01*{feature_name}.md) (2025-10-11 12:00) Example completed step.

[ ] (TASK*02*{feature_name}.md) Example incomplete step.

[ ] (TASK_03_feature_name.md) Example partially completed step (complted: X, remaining: Y)

## Testing Approach

Describe the testing approach for the feature.

## Constraints & Considerations

Describe any constraints or considerations you encountered while working on the feature.
