# PLAN_agent-sandbox-infra-hardening

## Purpose / Big Picture
Adopt the highest-impact security and scaling ideas from the X article "How We Built Secure, Scalable Agent Sandbox Infrastructure" (Larsen Cundric, Feb 27, 2026: https://x.com/larsencc/status/2027225210412470668) and translate them into a staged Rafiki hardening roadmap. The outcome should be lower secret blast radius, stronger runtime isolation, clearer control-plane authority, and better operational evidence without breaking the current Cloudflare-first architecture.

## Suprises & Discoveries
- Observation: The sandbox runtime currently receives a broad secret bundle that includes provider credentials and internal auth material.
- Evidence: `modal_backend/settings/settings.py`, `modal_backend/main.py`.
- Observation: One high-risk tool path still relies on `eval()` in runtime code.
- Evidence: `modal_backend/mcp_tools/calculate_tool.py`.
- Observation: Current docs describe strong Cloudflare-first intent, but active plans and architecture narratives are not fully synchronized on status and next gates.
- Evidence: `docs/design-docs/cloudflare-hybrid-architecture.md`, `docs/exec-plans/index.md`, `docs/exec-plans/active/phase-3-cloudflare-first/PLAN_phase-3-cloudflare-first.md`.

## Decision Log
- Decision: Treat the article as a reference architecture pattern, not a direct stack migration mandate.
- Rationale: Rafiki already has Cloudflare + Modal primitives in production flow; incremental hardening gives better delivery safety than wholesale replacement.
- Date/Author: 2026-03-01 / Codex

- Decision: Prioritize blast-radius reduction and tool/runtime hardening before deeper session architecture shifts.
- Rationale: Secret exposure and unsafe execution paths are higher immediate risk than model-context topology optimizations.
- Date/Author: 2026-03-01 / Codex

- Decision: Keep Cloudflare Worker + Durable Objects as the public/control-plane boundary.
- Rationale: This is already the canonical Phase 3 direction and aligns with the article's "control plane holds truth" principle.
- Date/Author: 2026-03-01 / Codex

- Decision: Make the external article durable in-repo via an internal reference summary before implementation tasks proceed.
- Rationale: Future agents should not depend on mutable social content to interpret security requirements.
- Date/Author: 2026-03-01 / Codex

## Outcomes & Retrospective
Not started.

## Context and Orientation
External reference that motivated this plan:
- Title: "How We Built Secure, Scalable Agent Sandbox Infrastructure"
- Author: Larsen Cundric
- Source: `https://x.com/larsencc/status/2027225210412470668`
- Accessed: 2026-03-01 (Australia/Adelaide)
- Core principles extracted for Rafiki:
  - control plane owns credentials and policy decisions
  - agent runtime should be disposable and least-privileged
  - high-risk execution paths require explicit isolation and policy rails
  - scaling and reliability boundaries must be independently testable

Key surfaces impacted by this plan:
- Control plane routing/auth/session orchestration:
  - `edge-control-plane/src/index.ts`
  - `edge-control-plane/src/durable-objects/SessionAgent.ts`
  - `edge-control-plane/src/auth/sessionAuth.ts`
- Modal runtime and sandbox lifecycle:
  - `modal_backend/main.py`
  - `modal_backend/api/controller.py`
  - `modal_backend/settings/settings.py`
- Tool execution policy:
  - `modal_backend/mcp_tools/registry.py`
  - `modal_backend/mcp_tools/calculate_tool.py`
- Job/artifact and workspace handling:
  - `modal_backend/jobs.py`
  - `modal_backend/main.py` (`/jobs/*`, artifacts, workspace cleanup)
- Canonical docs/specs/runbooks:
  - `docs/product-specs/`
  - `docs/design-docs/cloudflare-hybrid-architecture.md`
  - `docs/references/runbooks/cloudflare-modal-e2e.md`
  - `docs/QUALITY_SCORE.md`, `docs/RELIABILITY.md`, `docs/SECURITY.md`

## Plan of Work
1. Establish a specification-quality baseline that maps article ideas to Rafiki status (`already`, `partial`, `gap`) and defines measurable acceptance criteria for each gap.
2. Reduce credential blast radius by splitting secret injection per execution surface, enforcing explicit sandbox secret allowlists, and applying scoped/ephemeral sandbox credentials.
3. Harden runtime startup and execution posture (non-root execution path, environment scrubbing, least-privilege defaults, and tighter tool policy contracts).
4. Remove unsafe evaluation paths and introduce stronger isolation semantics for high-risk tool execution workflows.
5. Strengthen control-plane authority (session/context state ownership and pre-flight budget controls) while preserving compatibility with current APIs.
6. Add artifact transfer hardening (presigned/scoped access pattern), codify environment-mode parity contracts, and update reliability/security/quality docs with validation evidence.

## Phase Exit Gates
- Do not start `TASK_N+1` until `TASK_N` evidence is recorded in `Progress` with date and command results.
- Each task must include rollback notes for auth/runtime behavior changes.
- Documentation for changed behavior must be updated in the same change wave.
- Every meaningful implementation batch must include sub-agent reviewer evidence per `docs/AGENT_COLLABORATION_PROCESS.md`.

## Concrete Steps
- [tasks/TASK_01_article-gap-baseline-and-spec.md](./tasks/TASK_01_article-gap-baseline-and-spec.md)
- [tasks/TASK_02_secret-surface-minimization.md](./tasks/TASK_02_secret-surface-minimization.md)
- [tasks/TASK_03_runtime-hardening.md](./tasks/TASK_03_runtime-hardening.md)
- [tasks/TASK_04_tool-isolation-and-safe-execution.md](./tasks/TASK_04_tool-isolation-and-safe-execution.md)
- [tasks/TASK_05_control-plane-authority-and-budget-rails.md](./tasks/TASK_05_control-plane-authority-and-budget-rails.md)
- [tasks/TASK_06_artifact-transfer-and-doc-governance.md](./tasks/TASK_06_artifact-transfer-and-doc-governance.md)

## Progress
[ ] Progress entry template for completed tasks: `[x] (TASK_xx_*.md) (YYYY-MM-DD HH:MM) evidence: <commands + outcomes>; rollback: <doc/link>`
[ ] (TASK_01_article-gap-baseline-and-spec.md) Create article-to-Rafiki gap matrix, durable reference summary, and acceptance criteria.
[ ] (TASK_02_secret-surface-minimization.md) Enforce sandbox secret allowlist and record before/after secret-surface evidence.
[ ] (TASK_03_runtime-hardening.md) Implement non-root/env-scrub hardening and record UID/writable-path verification evidence.
[ ] (TASK_04_tool-isolation-and-safe-execution.md) Remove unsafe eval and ship malicious-payload regression coverage.
[ ] (TASK_05_control-plane-authority-and-budget-rails.md) Deliver authority cutover protocol and budget guardrails with denial telemetry evidence.
[ ] (TASK_06_artifact-transfer-and-doc-governance.md) Add scoped artifact transfer and same-wave docs/governance updates with dated evidence.

## Sub-Agent Collaboration Evidence
- Planning phase reviewers (2026-03-01):
  - Code risk review agent: `019ca811-8946-7df3-b065-173994f9aa38`
  - Docs/governance review agent: `019ca811-896c-7ee1-a5df-93bc99b312d6`
- Applied findings:
  - replaced invalid control-plane test commands
  - added phase exit gates and explicit collaboration evidence requirements
  - tightened ambiguous task language with measurable acceptance criteria
  - added durable article-reference handling to task scope
- Deferred findings:
  - none at plan-draft stage

## Testing Approach
- Unit tests:
  - `uv run pytest tests/test_controller_runtime_openai.py`
  - `uv run pytest tests/test_controller_tools.py`
  - `uv run pytest tests/test_schemas_sandbox.py`
- Control-plane checks:
  - `npm --prefix edge-control-plane run check`
  - `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit`
- End-to-end/runtime validation:
  - `uv run modal run -m modal_backend.main`
  - `uv run modal run -m modal_backend.main::run_agent_remote --question "sandbox hardening smoke check"`
  - Worker + Modal E2E steps from `docs/references/runbooks/cloudflare-modal-e2e.md`

## Constraints & Considerations
- Maintain API compatibility for existing `/query`, `/query_stream`, `/submit`, and `/jobs/*` clients unless a breaking change is explicitly documented and staged.
- Preserve Cloudflare-first ingress and `X-Internal-Auth` requirements for all non-health internal endpoints.
- Any secret/auth format changes must include rotation and rollback steps.
- Keep docs/spec/exec-plan updates in the same change wave as runtime changes.
