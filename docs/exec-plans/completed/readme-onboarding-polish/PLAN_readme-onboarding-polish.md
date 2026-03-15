# ExecPlan: README Onboarding Polish

## Purpose / Big Picture
Polish the public onboarding surface before announcement so a developer can land
on the repo, understand the Cloudflare-first system boundary, complete the
default local Modal setup, and discover the full Cloudflare path without having
to reverse-engineer secrets or supporting docs. The visible result is a minimal
root README, synchronized onboarding references, and removal of stale
worker-secret guidance that no longer matches the canonical request path.

## Surprises & Discoveries
- Observation: The root README already had uncommitted edits in the worktree.
- Evidence: `git diff -- README.md` showed a CI badge URL correction and removal
  of the query-flow diagram section before this wave started.
- Observation: The sample env file enables LangSmith tracing by default even
  though first-time setup docs do not explain the required Modal secret; this
  wave flips the example to opt-in tracing so first-time setup does not fail.
- Evidence: `.env.example`, `modal_backend/settings/settings.py`
- Observation: Some edge docs still present Worker-side `MODAL_TOKEN_ID` and
  `MODAL_TOKEN_SECRET` as required secrets despite the canonical docs stating
  they are not part of the baseline public Worker path.
- Evidence: `edge-control-plane/AUTH.md`, `edge-control-plane/API.md`,
  `edge-control-plane/wrangler.jsonc`, `docs/references/runbooks/cloudflare-modal-e2e.md`
- Observation: The canonical Cloudflare <-> Modal runbook also needed the new
  local `.env` / `INTERNAL_AUTH_SECRET` prerequisite once the README contract
  was clarified.
- Evidence: reviewer `019cf024-20bc-7451-b56c-5796334724fa` flagged
  `docs/references/runbooks/cloudflare-modal-e2e.md`

## Decision Log
- Decision: Keep the root README Modal-local-first, but explicitly redirect any
  real client-traffic setup to the Cloudflare-first docs.
- Rationale: This matches the requested landing flow while still preserving the
  actual supported ingress model.
- Date/Author: 2026-03-15 / Codex
- Decision: Make LangSmith opt-in in `.env.example` and document the exact
  `langsmith-secret` contract in onboarding docs.
- Rationale: The integration is part of the project and should be visible, but
  first-time setup should not fail because tracing was implicitly enabled.
- Date/Author: 2026-03-15 / Codex
- Decision: Treat Worker-side `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` as
  exception-path credentials only, not part of the baseline secret checklist.
- Rationale: The current canonical Worker path uses `INTERNAL_AUTH_SECRET` and
  `SESSION_SIGNING_SECRET`; anything else should be documented as non-canonical.
- Date/Author: 2026-03-15 / Codex

## Outcomes & Retrospective
- The root README now stays minimal while clearly separating the local Modal
  smoke path from the canonical Cloudflare-first client ingress path.
- LangSmith is now an explicit, first-class part of the public docs surface:
  the README includes a badge and rationale, configuration docs define the
  `langsmith-secret` contract, and troubleshooting now covers the missing-secret
  failure mode.
- The onboarding contract now distinguishes local `.env`, Modal secrets, and
  Worker secrets explicitly, including the default requirement for
  `modal-auth-secret`.
- The stale Worker `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` guidance has been
  removed from baseline edge auth/setup docs and reduced to non-canonical flow
  notes.
- Reviewer follow-up also pulled the canonical Cloudflare <-> Modal runbook into
  alignment by documenting the required local `.env` / `INTERNAL_AUTH_SECRET`
  setup before `modal serve`.

## Context and Orientation
This wave changes documentation only, but it touches contract-scope onboarding
surfaces that developers use to stand up the repo:

- Root README: `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/README.md`
- Local sample env: `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/.env.example`
- Runtime references:
  - `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/docs/references/configuration.md`
  - `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/docs/references/runtime-docs-overview.md`
  - `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/docs/references/troubleshooting.md`
- Edge onboarding/auth docs:
  - `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane/AUTH.md`
  - `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane/API.md`
  - `/Users/ibrahimsaidi/Desktop/Builds/Modal_Builds/rafiki/edge-control-plane/wrangler.jsonc`

## Plan of Work
1. Create a minimal public README that explains what Rafiki is, what a
   developer must install, which secrets are required by default, and why
   LangSmith is part of the project.
2. Update the supporting configuration/runtime/troubleshooting docs so the
   README contract is true everywhere, especially around `INTERNAL_AUTH_SECRET`,
   `modal-auth-secret`, and `langsmith-secret`.
3. Remove stale Worker secret guidance from edge docs and config comments so
   only non-canonical flows mention Worker `MODAL_TOKEN_ID` /
   `MODAL_TOKEN_SECRET`.
4. Run the docs governance check and required reviewers, then record their
   findings and resolution in this plan before closure.

## Concrete Steps
- Task files live under `docs/exec-plans/active/readme-onboarding-polish/tasks/`.

## Progress
- [x] `(tasks/TASK_01_readme-onboarding-polish.md)` `(2026-03-15 16:48 ACDT)`
  Reworked the root README into a Modal-local-first onboarding flow, added the
  LangSmith badge/rationale, documented the required-vs-optional secret matrix,
  and made `.env.example` safe for first-time setup by default.
- [x] `(tasks/TASK_02_readme-onboarding-polish.md)` `(2026-03-15 16:48 ACDT)`
  Synchronized configuration/runtime/troubleshooting docs, aligned the
  canonical Cloudflare runbook with the new local `.env` prerequisite, and
  removed baseline Worker `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` guidance from
  edge auth/setup docs.
- [x] `(tasks/TASK_03_readme-onboarding-polish.md)` `(2026-03-15 16:48 ACDT)`
  Ran the required reviewer workflow, applied the code-risk/docs-evidence
  follow-up fixes, reran docs governance to `DOCS_GOVERNANCE_OK`, and recorded
  the final reviewer outcomes in the plan.

## Sub-Agent Collaboration Evidence
- Reviewer: `019cf024-20bc-7451-b56c-5796334724fa` (code-risk review)
  - Scope: README/onboarding contract changes and any contradictory secret/setup guidance.
  - Findings: `docs/references/runbooks/cloudflare-modal-e2e.md` still lacked the local `.env` / `INTERNAL_AUTH_SECRET` prerequisite and could still fail with `ValueError: internal_auth_secret must be set`.
  - Resolution: applied by updating the canonical runbook startup and environment setup steps; follow-up rerun returned `No material code-risk issues remain.`
- Reviewer: `019cf024-27ae-7b83-8099-eebaf13d45ee` (docs/evidence review)
  - Scope: ExecPlan completeness, docs synchronization, and user-facing onboarding clarity.
  - Findings: first pass found the active plan/task files stale after the docs landed; second pass found `tasks/TASK_03_readme-onboarding-polish.md` still marked the closure work incomplete.
  - Resolution: applied by updating this plan, task checklists, progress, and outcomes in the same change wave; final confirmatory rerun returned `No material docs/evidence issues remain.`
- Reviewer: `019cf027-1bb7-7960-b880-1978a2e4c1d5` (boundary-enforcer)
  - Scope: contract-scope documentation changes across README, references, and edge auth docs.
  - Findings: `No material governance violations remain.`
  - Resolution: no changes required after the final boundary review.

## Testing Approach
- Verify every README command still maps to a real command, script, or entrypoint.
- Run `uv run python scripts/quality/check_docs_governance.py`.
- Re-scan the docs for stale Worker-secret guidance so only exception-path docs
  mention Worker `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`.

## Proof / Evidence Artifacts
- No generated artifact planned. Evidence is the updated plan, reviewer output,
  and docs governance command result.

## Constraints & Considerations
- Keep the root README minimal; do not turn it into a full deployment manual.
- Preserve the pre-existing uncommitted README edits already in the worktree.
- This is a docs-only wave; no runtime behavior or API contract should change.
