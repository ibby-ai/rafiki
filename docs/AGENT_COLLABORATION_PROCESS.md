# Agent Collaboration Process

This document defines the mandatory sub-agent workflow for all coding agents
working in this repository.

## Scope

- Applies to any non-trivial planning, implementation, or review task.
- Complements `AGENTS.md`; does not replace architecture, security, reliability,
  or execution-planning guardrails.

## Required Reviewers

Every non-trivial plan or implementation batch must include:

- one sub-agent for code-level risk review
- one sub-agent for docs/evidence consistency review

The local governance reviewer `.claude/agents/boundary-enforcer.md` is also
mandatory whenever the change touches:

- architectural boundary changes
- contract-scope documentation changes
- transport/runtime validation changes
- agent-definition changes
- governance/process documentation changes

## Mandatory Process

1. Planning phase:
   - Draft the implementation or review plan in the canonical docs path.
   - Run the required reviewers before finalizing the plan.
   - Incorporate findings or explicitly document why a finding is deferred.

2. Implementation phase:
   - After each meaningful code or documentation batch, rerun the relevant reviewers.
   - For governance-sensitive work, rerun `boundary-enforcer` before closing the batch.
   - Address all high and medium findings before closure, or document a dated deferral.

3. Finalization phase:
   - Record which reviewers were used.
   - Record their findings.
   - Record whether each material finding was applied or intentionally deferred.
   - Link the final evidence bundle or proof artifact when the change introduces new governance checks.

## Evidence Expectations

Keep an auditable trail of:

- which sub-agents were used
- what they reported
- what changed as a result
- where any deferred finding is tracked

Store this evidence in the active ExecPlan and related canonical docs in the
same change wave as the implementation.

## Non-Negotiables

- This process is mandatory unless a user explicitly instructs otherwise.
- `boundary-enforcer` is read-only and must not be used as an implementation worker.
- Existing repository guardrails still apply, including:
  - specs-first documentation updates
  - `uv` for Python and `npm` for the Worker package
  - tenant and session boundary safety
  - explicit evidence for security and reliability changes
