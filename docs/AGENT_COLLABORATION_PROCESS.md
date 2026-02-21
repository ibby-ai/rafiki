# Agent Collaboration Process

This document defines the mandatory sub-agent workflow for all coding agents working in this repository.

## Scope

- Applies to any non-trivial planning, implementation, or review task.
- Complements `AGENTS.md`; does not replace other engineering guardrails.

## Mandatory Process (Sub-Agents Required)

1. Planning phase:
   - Draft an implementation/review plan.
   - Spawn sub-agents to critique the draft before finalizing it.
   - Minimum required reviewers:
     - one sub-agent for code-level risk review
     - one sub-agent for docs/evidence consistency review
   - Incorporate useful feedback, then finalize the plan.

2. Implementation/review phase:
   - After each meaningful code/doc change batch, spawn reviewer sub-agent(s).
   - Use feedback to refine the changes.
   - Address all high/medium findings before closing, or explicitly document why a finding is deferred.

3. Finalization phase:
   - Include an explicit summary of sub-agent findings.
   - For each meaningful finding, state whether it was:
     - applied
     - intentionally deferred (with reason)

## Evidence Expectations

- Keep an auditable trail of:
  - which sub-agents were used
  - what they reported
  - what changed as a result

## Non-Negotiables

- This process is mandatory unless a user explicitly instructs otherwise.
- All existing repository guardrails still apply:
  - specs-first
  - Bun toolchain
  - Supabase access boundaries
  - multi-tenant and RLS safety
