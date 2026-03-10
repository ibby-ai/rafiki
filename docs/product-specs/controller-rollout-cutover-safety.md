# Controller Rollout Cutover Safety

## Problem
Controlled recycle/deploy currently risks exposing user-visible cold-start failures when the active controller is terminated before a verified replacement is promoted.

## User Outcome
During controlled cutover, users stay on active controller A while replacement controller B is created privately, validated with real readiness gates, promoted atomically, and then A is drained safely. The first public Worker `/query` after cutover returns `200` on the first try.

## Why This Matters In Practice
- Real users hit Cloudflare first, not Modal directly. If the first public request after cutover fails, the deploy was not actually safe even if internal Modal checks passed.
- Example: a user sends the next message in an active chat during deploy. Their message must land on a verified B, not on an old A that is shutting down and not on a half-started replacement.
- Example: a streaming response is still finishing on A while new traffic starts hitting B. Stop/cancel routing and in-flight tracking must keep the old stream stable while fresh work moves to the new controller.
- Example: a queued follow-up job fires during recycle. It must route to the authoritative active generation, not to a draining controller that is about to terminate.
- Example: an operator rotates secrets or recycles production during business hours. The first real public `/query` after that change cannot be a cold-start gamble or a user-visible `500`.
- Human meaning: deploys, restarts, and recovery events should look boring from the outside. Users should not be able to tell which request crossed the A -> B handoff.

## Scope
- Introduce authoritative shared rollout state (active pointer + generation + lifecycle status) in a dedicated Modal Dict.
- Make request routing pointer-first with generation-aware worker cache invalidation.
- Keep A live while preparing B; no user request can be responsible for creating/verifying B.
- Make the promotion linearization point a fail-closed generation transition from active generation `G` to `G+1`.
  - Promotion commit must only succeed while rollout lock ownership is still held and the active pointer still reports generation `G`.
  - Stale writers must not be able to overwrite a newer active pointer after lock expiry or overlapping rollout attempts.
- Require B readiness gates before promotion:
  - `/health_check`
  - scoped sandbox secret metadata presence
  - synthetic direct controller `/query` using dedicated synthetic session id
- Track in-flight work per controller and route stop/cancel calls to draining controller when needed.
- Admit fresh requests only at lease start against the authoritative active pointer generation.
  - Prewarm claims are hints, not authority; if a claim is stale at admission time it must reroute to the active pointer path or fail closed.
- Enforce explicit drain lifecycle (`active` -> `draining` -> `terminated`) with bounded timeout.
- Keep rollback target metadata until drain completion.
- Persist drain schedule/execution correlation metadata for deployed proof:
  - scheduled `drain_call_id`
  - terminal `drain_execution_call_id`
  - drain timeout and in-flight termination outcome
- Start deployed proof by validating or repairing the canonical public Cloudflare Worker:
  - confirm `INTERNAL_AUTH_SECRET` parity with Modal
  - confirm `SESSION_SIGNING_SECRET` is configured
  - deploy the Worker against the production Modal base URL
  - derive the canonical `workers.dev` URL from deploy output, not inference
- Require at least two consecutive deployed cutovers for spawned-drain proof acceptance.
- Sanitize rollout observability surfaces to avoid exposing scoped secret material.

## Non-Goals
- Reintroducing legacy gateway-to-sandbox internal-auth fallback behavior.
- Weakening scoped sandbox auth or widening secret surface contracts.
- Replacing Cloudflare public ingress with direct Modal access.

## Success Metrics
- First public Worker `/query` after controlled cutover succeeds on first try (`200`).
- Public-worker proof packet records the exact deployed `workers.dev` URL, deployment version, and secret-parity preflight used for the cutover wave.
- Promotion occurs only after all three readiness gates pass.
- Post-promotion traffic converges on new active generation across hot workers.
- No new requests are admitted to draining controller A.
- Drain ends with either zero in-flight or bounded timeout fallback, with recorded outcome.
- Deployed proof shows `drain_status.mode="spawned"` and correlates schedule -> execution -> completion using `drain_call_id`, `drain_execution_call_id`, FunctionCall evidence, and logs.
- Rollout observability does not expose `sandbox_session_secret`.
- Operators can explain the cutover in user-facing terms: no first-request outage, no stream drop caused by cutover, and no queued work routed into a dying controller.

## Rollout / Risks
- Risk: split-brain routing if workers keep stale local cache.
  - Mitigation: generation-aware request-entry refresh from shared active pointer.
- Risk: failed candidate promotion strands traffic.
  - Mitigation: rollback-safe promotion order keeps A active until B verification completes.
- Risk: stale rollout writer resurrects an older candidate after lock expiry.
  - Mitigation: guarded generation-transition commit rejects writers that lose rollout-lock ownership or observe an advanced active generation.
- Risk: in-flight requests dropped during cutover.
  - Mitigation: explicit drain state + in-flight lease tracking before termination.
- Risk: fresh traffic reaches draining A through stale prewarm or cached route state.
  - Mitigation: admit requests only at lease start against the active pointer generation; stale claims reroute or fail closed before forwarding.
- Risk: cutover trigger path differs between `modal serve` and deployed app webhooks.
  - Mitigation: runbook must use serve-safe trigger path during local validation and deployed function invocation path in production.

## Linked ExecPlan
- `docs/exec-plans/completed/controller-rollout-cutover-safety/PLAN_controller-rollout-cutover-safety.md`
