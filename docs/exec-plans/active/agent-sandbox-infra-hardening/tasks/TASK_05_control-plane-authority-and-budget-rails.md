---
task_id: 05
plan_id: PLAN_agent-sandbox-infra-hardening
plan_file: ../PLAN_agent-sandbox-infra-hardening.md
title: Strengthen control-plane session authority and budget guardrails
phase: Phase 5 - Control-plane authority
---

- Define and implement a single-source-of-truth policy for session history ownership between Durable Objects and runtime session storage, including cutover and rollback mechanics.
- Define the authoritative budget unit(s) (request count, token count, cost, or combined model) and implement pre-flight guardrails in `edge-control-plane` before forwarding execution requests to Modal.
- Ensure run cancellation, queue semantics, and trace IDs remain consistent after authority changes.
- Add validation and observability checks for denied requests, budget thresholds, and replay/resume behavior.

## Done When
- Authority cutover protocol is documented (owner, migration/backfill, rollback trigger, and explicit no-indefinite-dual-write rule).
- Budget thresholds are deterministic and enforced with observable denial events.
- Replay/resume/cancel behavior passes cross-boundary regression checks.
- Rollback notes explicitly document how to return to prior authority ownership and guardrail logic if cutover issues appear.
