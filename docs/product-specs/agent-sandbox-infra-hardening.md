# Agent Sandbox Infrastructure Hardening

## Problem
Rafiki already enforces Cloudflare-first ingress and internal auth, but high-impact hardening gaps remain:
- sandbox runtime still receives a broader secret surface than required for normal agent execution
- runtime startup posture does not enforce a non-root default or explicit environment scrubbing
- a tool path still uses unsafe expression evaluation
- budget and artifact access controls are not yet session-scoped, deterministic, and denial-observable end-to-end

## User Outcome
Operators can run multi-tenant agent workloads with lower secret blast radius and tighter execution controls, while keeping current API compatibility for `/query`, `/query_stream`, `/submit`, and `/jobs/*`.

## Scope
- Secret-surface minimization and sandbox secret allowlist.
- Session-scoped short-lived gateway-to-sandbox credentials.
- Runtime hardening (privilege posture, environment scrubbing, writable-path policy).
- Tool isolation and deterministic malicious-input denials.
- Control-plane authority and pre-flight budget enforcement.
- Scoped, time-bounded artifact transfer with tamper/expiry/cross-session checks.
- Same-wave architecture/runbook/governance documentation updates.

## Gap Matrix
| Pattern | Status | Owner | Risk | Evidence | Acceptance Criteria |
| --- | --- | --- | --- | --- | --- |
| Control plane owns public auth/session identity | `already` | Edge Platform | Medium | `edge-control-plane/src/auth/sessionAuth.ts`, `edge-control-plane/src/index.ts` | Preserve current session-token validation and DO routing semantics. |
| Sandbox secret allowlist (`OPENAI_API_KEY` + optional tracing only) | `gap` | Runtime Platform | High | `modal_backend/settings/settings.py`, `modal_backend/main.py` | Sandbox runtime excludes `INTERNAL_AUTH_SECRET` and `MODAL_TOKEN_*`; before/after evidence recorded. |
| Session-scoped short-lived gateway->sandbox auth credentials | `gap` | Runtime Platform | High | `modal_backend/security/cloudflare_auth.py`, `modal_backend/main.py` | Sandbox accepts scoped TTL-bound token path; legacy shared-secret path has documented rollback gate. |
| Runtime privilege hardening + env scrubbing | `gap` | Runtime Platform | High | `modal_backend/api/controller.py`, `modal_backend/main.py` | UID/writable-path checks and env scrubbing evidence recorded with compensating controls if non-root drop is blocked. |
| High-risk tool execution safety (`eval` removal + policy tightening) | `gap` | Agent Runtime | High | `modal_backend/mcp_tools/calculate_tool.py`, `modal_backend/mcp_tools/registry.py` | Malicious payloads denied deterministically; behavior covered by regression tests. |
| Control-plane budget pre-flight rails | `partial` | Edge Platform | Medium | `edge-control-plane/src/index.ts`, `edge-control-plane/src/durable-objects/SessionAgent.ts` | Deterministic per-session budget denials emitted before Modal forwarding, including queue/replay paths. |
| Scoped artifact transfer (signed claims + expiry + revocation) | `gap` | Edge + Runtime Platform | High | `edge-control-plane/src/index.ts`, `modal_backend/main.py` | Expired/tampered/cross-session token attempts denied with stable errors; signer claims documented. |
| Governance and evidence synchronization | `partial` | Platform Docs | Medium | `docs/design-docs/cloudflare-hybrid-architecture.md`, `docs/references/runbooks/cloudflare-modal-e2e.md`, `docs/QUALITY_SCORE.md`, `docs/RELIABILITY.md`, `docs/SECURITY.md` | Dated command evidence and rollback notes published in same change wave as runtime behavior changes. |

## Non-Goals
- Migrating away from Cloudflare Worker + Durable Objects.
- Rewriting agent runtime to a different orchestration framework.
- Changing public API envelopes or endpoint names.
- Introducing monthly/long-lived artifact access credentials.

## Success Metrics
- Sandbox runtime no longer receives broad internal signing/modal API secrets.
- Runtime/tool hardening regressions pass with deterministic denial contracts.
- Budget denials and artifact-access denials are observable and test-covered.
- Governance docs reflect dated implementation evidence in the same PR wave.

## Rollout / Risks
- Risk: auth cutover can break sandbox query forwarding.
  - Mitigation: dual-accept transition path, explicit rollback trigger and commands.
- Risk: non-root runtime can break filesystem operations in mounted volumes.
  - Mitigation: writable-path checks, compensating controls, runbook rollback steps.
- Risk: stricter policy checks can reject previously accepted requests.
  - Mitigation: deterministic error contracts and updated API/runbook docs.

## Linked ExecPlan
- `docs/exec-plans/completed/agent-sandbox-infra-hardening/PLAN_agent-sandbox-infra-hardening.md`
