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
- Observation: `uv run pytest ...` executed against a stale interpreter entrypoint in this workspace and caused false collection failures (`ModuleNotFoundError: agents`); `uv run python -m pytest ...` is hermetic and passed.
- Evidence: local validation runs on 2026-03-02 (`25/20/17/8 test` command sets).
- Observation: `npm --prefix edge-control-plane run check` was non-zero during this wave and required a dedicated follow-up remediation pass; `tsc --noEmit` remained green.
- Evidence: control-plane validation commands on 2026-03-02.
- Observation: Queue budget denials were previously only visible during drain-time execution; adding queue preflight budget checks gives deterministic `429` denial contracts before enqueue.
- Evidence: `edge-control-plane/src/durable-objects/SessionAgent.ts`, budget smoke outputs in runbook commands.

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

- Decision: Track edge lint debt as an explicit follow-up wave while requiring `tsc --noEmit` pass for this plan closeout.
- Rationale: Security/runtime controls were prioritized first; lint debt was scheduled for immediate follow-up remediation instead of blocking this hardening batch.
- Date/Author: 2026-03-02 / Codex

- Decision: Enforce actor-scope checks on Worker and Modal job read/artifact list/download paths, not only artifact file download.
- Rationale: Prevent cross-session job metadata and artifact enumeration leakage.
- Date/Author: 2026-03-02 / Codex

- Decision: Require `X-Sandbox-Id` on scoped sandbox auth token validation.
- Rationale: Token-to-runtime binding must include explicit sandbox identity for replay/misbinding resistance.
- Date/Author: 2026-03-02 / Codex

- Decision: Add queue endpoint budget preflight checks (non-mutating) for deterministic queued-request denials.
- Rationale: Makes budget rails observable and testable for queued prompts without relying on drain-time execution success.
- Date/Author: 2026-03-02 / Codex

## Outcomes & Retrospective
- Completed Tasks 01-06 with same-wave code + docs/governance updates and runtime validation evidence.
- Implemented scoped sandbox auth (`X-Sandbox-Session-Auth` + `X-Sandbox-Id`), split secret surfaces, runtime hardening report, AST-only calculate evaluator, stricter Bash/WebFetch policy, DO budget rails, authority headers, and scoped artifact token verification/revocation.
- Validation:
  - Python suites passed (`86 passed`) across runtime/tool/schema/auth/artifact/security tests after follow-up hardening regression updates.
  - `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` passed.
  - `npm --prefix edge-control-plane run check` now passes after dedicated remediation in the follow-up hardening wave.
  - Runtime commands (`modal run` local entrypoint + `run_agent_remote`) completed.
  - Budget denial smoke validated for non-stream, stream, and queued prompt flows.
- Residual follow-up: monitor warm-pool scoped-secret consistency; lint remediation and worker proxy integration coverage are completed in the follow-up wave.

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
[ ] Progress entry template for completed tasks: `[x] (TASK_xx_*.md) (YYYY-MM-DD HH:MM) evidence: <commands + outcomes>; rollback: <trigger + steps + verification + doc/link>; subagents: <ids>; findings: <high/medium applied|deferred(reason)>`
[x] (TASK_01_article-gap-baseline-and-spec.md) (2026-03-02 11:10 ACDT) evidence: `rg -n "agent-sandbox-infra-hardening" docs/product-specs/index.md` -> spec indexed; `test -f docs/product-specs/agent-sandbox-infra-hardening.md` -> exists; `test -f docs/references/agent-sandbox-infra-hardening-article-note.md` -> exists; rollback: docs-only rollback by reverting new spec/reference/index entries (`docs/product-specs/agent-sandbox-infra-hardening.md`, `docs/product-specs/index.md`, `docs/references/agent-sandbox-infra-hardening-article-note.md`, `docs/references/index.md`); subagents: `019cabf5-46a1-7682-80ff-2a02388801ba`, `019cabf5-46c7-7b10-81c3-a5beb2d43a1c`; findings: high/medium applied for task evidence schema + doc-sync checklist hardening, deferred CI doc-lint follow-up
[x] (TASK_02_secret-surface-minimization.md) (2026-03-02 11:54 ACDT) evidence: `uv run python -m pytest tests/test_internal_auth_middleware.py tests/test_settings_openai.py` -> `25 passed`; `rg -n "INTERNAL_AUTH_SECRET|MODAL_TOKEN_ID|MODAL_TOKEN_SECRET|SANDBOX_MODAL_TOKEN" modal_backend/main.py modal_backend/settings/settings.py` -> sandbox runtime env explicitly sets `REQUIRE_INTERNAL_AUTH_SECRET=false` and sandbox secret surface excludes internal/modal auth secret injection; scoped sandbox token path verified with required `X-Sandbox-Id`; remediation: trigger `/query` or `/query_stream` auth failures -> recycle/recreate affected sandboxes until scoped secrets are present, rerun `uv run python -m pytest tests/test_internal_auth_middleware.py tests/test_sandbox_auth_header.py`, rerun runbook query smoke (`docs/references/configuration.md`, `docs/references/runbooks/cloudflare-modal-e2e.md`); subagents: `019cabf5-46a1-7682-80ff-2a02388801ba`, `019cabf5-46c7-7b10-81c3-a5beb2d43a1c`; findings: high applied (`X-Sandbox-Id` required), medium deferred (direct secret wiring refactor across all sandbox lookup paths; compatibility risk in active warm pools)
[x] (TASK_03_runtime-hardening.md) (2026-03-02 11:55 ACDT) evidence: `uv run python -m pytest tests/test_controller_runtime_openai.py tests/test_runtime_hardening.py tests/test_internal_auth_middleware.py` -> pass (`20 + 26 test coverage`, including runtime auth regression); runtime startup hardening wired in `modal_backend/api/controller.py` with `/runtime_hardening` status endpoint; `uv run modal run -m modal_backend.main` -> completed with session output; runbook runtime hardening and budget-denial checks executed (`docs/references/runbooks/cloudflare-modal-e2e.md`); rollback: trigger runtime instability from privilege/env scrub -> restore prior startup hardening defaults and sandbox user mode, rerun runtime + query regression commands in runbook (`docs/references/runbooks/cloudflare-modal-e2e.md`, `docs/design-docs/cloudflare-hybrid-architecture.md`, `docs/references/configuration.md`); subagents: `019cabf5-46a1-7682-80ff-2a02388801ba`, `019cabf5-46c7-7b10-81c3-a5beb2d43a1c`; findings: medium deferred (sandbox modal-auth credential removal may reduce volume API capabilities; intentional least-privilege posture with documented rollback)
[x] (TASK_04_tool-isolation-and-safe-execution.md) (2026-03-02 11:56 ACDT) evidence: `uv run python -m pytest tests/test_tools_calculate.py tests/test_controller_tools.py` -> `17 passed`; `rg -n "eval\\(" modal_backend/mcp_tools` -> no matches; AST-only arithmetic evaluator and Bash/WebFetch policy tightening shipped; rollback: trigger tool regression -> restore prior calculator/registry behavior behind temporary guard and rerun tool policy suite + `/query` smoke (`docs/references/tool-development.md`, `docs/references/api-usage.md`); subagents: `019cabf5-46a1-7682-80ff-2a02388801ba`, `019cabf5-46c7-7b10-81c3-a5beb2d43a1c`; findings: high/medium applied for deterministic denial contracts and local workdir fallback behavior
[x] (TASK_05_control-plane-authority-and-budget-rails.md) (2026-03-02 11:57 ACDT) evidence: `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> pass; budget denial smoke (`/query`, `/query_stream`, `/session/{id}/queue`) under constrained vars produced deterministic `429/query_error` with stable `code/details`; `npm --prefix edge-control-plane run check` -> pass (follow-up wave removed baseline debt); authority header (`X-Session-History-Authority: durable-object`) enforced in DO forwarding and Modal query endpoints; rollback: trigger false-positive denials/replay regressions -> relax budget vars + revert authority gate toggles, rerun denial smoke + queue/stream checks (`docs/design-docs/cloudflare-hybrid-architecture.md`, `docs/references/runbooks/cloudflare-modal-e2e.md`, `docs/references/api-usage.md`); subagents: `019cabf5-46a1-7682-80ff-2a02388801ba`, `019cabf5-46c7-7b10-81c3-a5beb2d43a1c`; findings: medium applied (queue preflight budget check prevents non-observable queued denials)
[x] (TASK_06_artifact-transfer-and-doc-governance.md) (2026-03-02 11:58 ACDT) evidence: `uv run python -m pytest tests/test_jobs_security.py tests/test_artifact_access.py` -> `8 passed`; `npm --prefix edge-control-plane run check` -> pass; Worker now pre-validates actor ownership for `GET /jobs/{id}`, artifact list, and artifact downloads; download path requires scoped artifact token with expiry/signature/session/job/path binding + revocation support; governance docs re-scored with dated command evidence (`docs/QUALITY_SCORE.md`, `docs/RELIABILITY.md`, `docs/SECURITY.md`); rollback: trigger artifact transfer regressions -> set `REQUIRE_ARTIFACT_ACCESS_TOKEN=false` temporarily while retaining actor-scope/path protections, rerun artifact/security tests + runbook checks (`docs/references/configuration.md`, `docs/references/runbooks/cloudflare-modal-e2e.md`); subagents: `019cabf5-46a1-7682-80ff-2a02388801ba`, `019cabf5-46c7-7b10-81c3-a5beb2d43a1c`; findings: high applied (artifact list/status actor-scope enforcement)

## Sub-Agent Collaboration Evidence
- Planning phase reviewers (2026-03-01):
  - Code risk review agent: `019ca811-8946-7df3-b065-173994f9aa38`
  - Docs/governance review agent: `019ca811-896c-7ee1-a5df-93bc99b312d6`
- Implementation kickoff reviewers (2026-03-02):
  - Code risk review agent: `019cabf5-46a1-7682-80ff-2a02388801ba`
  - Docs/governance review agent: `019cabf5-46c7-7b10-81c3-a5beb2d43a1c`
- Implementation review batch (2026-03-02, Tasks 02-06 closeout):
  - Code risk review agent: `019cabf5-46a1-7682-80ff-2a02388801ba`
  - Docs/governance review agent: `019cabf5-46c7-7b10-81c3-a5beb2d43a1c`
- Final closure review (2026-03-02):
  - Code risk review agent: `019cabf5-46a1-7682-80ff-2a02388801ba` -> no remaining high/medium blockers
  - Docs/governance review agent: `019cabf5-46c7-7b10-81c3-a5beb2d43a1c` -> no remaining high/medium blockers
- Applied findings:
  - replaced invalid control-plane test commands
  - added phase exit gates and explicit collaboration evidence requirements
  - tightened ambiguous task language with measurable acceptance criteria
  - added durable article-reference handling to task scope
  - added required `Evidence Capture`, `Rollback Notes`, and `Required Doc Sync` blocks to task pack
  - expanded Progress template to require sub-agent IDs and applied/deferred finding status per completed task
  - required `X-Sandbox-Id` binding for scoped sandbox token verification
  - enforced actor-scope checks for Worker + Modal job read/artifact list/download paths
  - added queue preflight budget-denial contract for deterministic queued-request rails
  - converted pytest evidence commands to hermetic `uv run python -m pytest`
  - filled plan/doc governance closure requirements and dated score updates
- Deferred findings:
  - CI/doc-lint enforcement of task schema consistency across all active plans (deferred to future governance sweep)
  - direct sandbox-secret wiring refactor to eliminate metadata lookup ambiguity (deferred for compatibility with active warm pool transition)

## Testing Approach
- Unit tests:
  - `uv run python -m pytest tests/test_controller_runtime_openai.py`
  - `uv run python -m pytest tests/test_controller_tools.py`
  - `uv run python -m pytest tests/test_schemas_sandbox.py`
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
- Any secret/auth format changes must include rotation and remediation steps.
- Keep docs/spec/exec-plan updates in the same change wave as runtime changes.
