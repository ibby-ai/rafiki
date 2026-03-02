# PLAN_agent-sandbox-followup-hardening

## Purpose / Big Picture
Complete the next hardening wave after `PLAN_agent-sandbox-infra-hardening` by removing edge lint baseline debt, adding worker proxy integration coverage for jobs/artifacts, and enforcing strict scoped sandbox auth.

## Scope Completed
1. **Phase 1: Edge lint remediation**
- Drove `npm --prefix edge-control-plane run check` to green.
- Kept behavior stable while accepting safe refactors introduced by remediation tooling.
- Updated governance/docs to remove the old `Found 164 errors` baseline exception language.

2. **Phase 2: Worker proxy integration tests**
- Added/kept integration coverage for:
  - ownership precheck on job/artifact routes,
  - artifact token propagation on artifact download,
  - malformed artifact path encoding (`decodeURIComponent` failure) returning deterministic `400`.
- Added command entrypoint: `npm --prefix edge-control-plane run test:integration`.

3. **Phase 3: Strict scoped sandbox auth cutover**
- Removed legacy sandbox fallback controls from settings/runtime.
- Enforced scoped sandbox auth in middleware and gateway-to-sandbox header path with deterministic failure on missing scoped secrets.
- Added warm-pool transition visibility in `/pool/status`:
  - `missing_scoped_secret_count`
  - `scoped_secret_transition_stable`

## Files Changed (Wave-Specific)
- `edge-control-plane/tests/integration/jobs-proxy.integration.test.ts`
- `modal_backend/settings/settings.py`
- `modal_backend/security/cloudflare_auth.py`
- `modal_backend/main.py`
- `tests/test_internal_auth_middleware.py`
- `tests/test_settings_openai.py`
- `tests/test_sandbox_auth_header.py`
- `docs/references/configuration.md`
- `docs/references/runbooks/cloudflare-modal-e2e.md`
- `docs/design-docs/cloudflare-hybrid-architecture.md`
- `docs/references/api-usage.md`
- `docs/QUALITY_SCORE.md`
- `docs/RELIABILITY.md`
- `docs/SECURITY.md`
- `docs/exec-plans/completed/agent-sandbox-infra-hardening/PLAN_agent-sandbox-infra-hardening.md`

## Validation Matrix
- `npm --prefix edge-control-plane run check` -> **pass**
- `cd edge-control-plane && ./node_modules/.bin/tsc --noEmit` -> **pass**
- `npm --prefix edge-control-plane run test:integration` -> **pass** (`3 passed`)
- `uv run python -m pytest tests/test_controller_runtime_openai.py tests/test_controller_tools.py tests/test_schemas_sandbox.py tests/test_internal_auth_middleware.py tests/test_settings_openai.py tests/test_tools_calculate.py tests/test_runtime_hardening.py tests/test_jobs_security.py tests/test_artifact_access.py tests/test_sandbox_auth_header.py` -> **pass** (`86 passed`, `2 warnings`)

## Sub-Agent Collaboration Evidence
- Planning/code-risk review: `019cac3a-9f71-7173-8403-e713d46f2af7`
  - Findings applied:
    - accepted route extraction + file-renamed import fixes,
    - completed lint-zero path,
    - validated integration harness gap closure and fallback-retirement target files.
- Planning/docs-evidence review: `019cac48-de9a-72a3-b9a0-4887088060c5`
  - Findings applied:
    - updated required reference docs in same wave,
    - added validation matrix command evidence,
    - removed obsolete lint-baseline exception language from governance/docs artifacts.
- Interrupted reviewer attempts (deferred, no findings consumed):
  - `019cac3a-9fb5-71f2-801c-d79ed9c25f45`
  - `019cac42-d4d0-7981-b9db-f7ffc3e81a05`
  - `019cac46-c51b-7243-8d17-794da16c1a53`
- Post-implementation review batch:
  - Code-risk reviewer: `019cac5b-329a-70a0-9e70-a521f38b55d5`
    - Findings applied:
      - hardened `_add_sandbox_auth_header` strict path to reject missing scoped secrets.
      - added strict scoped-auth regression coverage (`tests/test_sandbox_auth_header.py`).
  - Docs/evidence reviewer: `019cac5b-3306-7dc1-ad18-ca17a1d6d5f6`
    - Deferred (reason): response contained broad completion claims without verifiable file-level findings; no actionable diffs were applied from this output.
- Final closure review batch:
  - Code-risk reviewer: `019cac65-f960-7070-8377-7d922ba88278`
    - Findings applied: none (no high/medium findings).
    - Low-risk note tracked: warm-pool secret drift should be monitored to avoid deterministic `503` bursts.
  - Docs/evidence reviewer: `019cac65-fa0b-7e91-b979-9666424d15cb`
    - Findings applied: none (confirmed required docs/governance coverage and validation matrix consistency).

## Deferred Findings
- None at high/medium severity for this wave.
- Residual follow-up remains operational: keep warm-pool/session-secret metadata consistent across long-lived sandboxes.

## Outcomes & Retrospective
- Lint baseline exception is retired; edge quality gates are now green.
- Worker proxy behavior is covered by deterministic integration tests and command-gated in docs.
- Legacy sandbox fallback path is fully removed; scoped sandbox auth is now strict-only.
