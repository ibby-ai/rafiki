---
task_id: 02
plan_id: PLAN_readme-onboarding-polish
plan_file: ../PLAN_readme-onboarding-polish.md
title: Synchronize onboarding references and remove stale secret guidance
phase: Phase 2 - Supporting Docs Alignment
---

## Goal
Ensure the runtime references, troubleshooting guide, and edge-control-plane
auth docs tell the same onboarding story as the README, especially around
LangSmith, required local env, and the baseline Worker secret contract.

## Checklist
- [x] Add explicit `langsmith-secret` setup guidance to configuration docs.
- [x] Make the local `INTERNAL_AUTH_SECRET` requirement explicit in first-time
  setup references.
- [x] Document that `modal-auth-secret` is required by default because
  `ENABLE_MODAL_AUTH_SECRET=true`.
- [x] Remove baseline Worker `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` guidance
  from canonical edge auth/setup docs and config comments.
