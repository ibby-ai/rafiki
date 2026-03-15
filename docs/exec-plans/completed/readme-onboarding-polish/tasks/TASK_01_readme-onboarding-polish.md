---
task_id: 01
plan_id: PLAN_readme-onboarding-polish
plan_file: ../PLAN_readme-onboarding-polish.md
title: Rewrite the root README and sample env for first-time developer onboarding
phase: Phase 1 - Public Landing Surface
---

## Goal
Make the root README minimal, accurate, and developer-usable on first read.
The README should explain the product boundary, default local setup, LangSmith's
role, and the handoff to the canonical Cloudflare path. The sample env file
must no longer create an avoidable tracing-secret failure for first-time setup.

## Checklist
- [x] Add a LangSmith badge and rationale to the root README.
- [x] Add a short prerequisites block and local setup path that includes the
  local `INTERNAL_AUTH_SECRET` requirement.
- [x] Add a compact setup matrix that distinguishes required versus optional
  secrets across local env, Modal, and Worker surfaces.
- [x] Keep the README Modal-local-first while explicitly stating that public
  client traffic goes through the Cloudflare Worker.
- [x] Make `.env.example` safe for first-time setup by default while preserving
  the LangSmith integration as documented opt-in behavior.
