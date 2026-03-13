# Architecture Entry Point

Start here for system design context, then drill into the detailed docs:

1. Core beliefs and documentation standards: `docs/design-docs/core-beliefs.md`
2. Cloudflare control-plane architecture: `docs/design-docs/cloudflare-hybrid-architecture.md`
3. System architecture (Modal execution backend within the Cloudflare-first boundary): `docs/design-docs/architecture-overview.md`
4. Controller runtime details: `docs/design-docs/controllers-background-service.md`
5. Modal gateway mechanics: `docs/design-docs/modal-ingress.md`
6. Multi-agent architecture: `docs/design-docs/multi-agent-architecture.md`

Execution planning for architecture changes is tracked in `docs/exec-plans/`.

## Code quality governance

The canonical engineering contract for code-quality enforcement lives at
`docs/references/code-quality-governance.md`.

### Layer map

- Python foundation: `modal_backend.models`, `modal_backend.settings`
- Python cross-cutting infra: `modal_backend.security`, `modal_backend.platform_services`, `modal_backend.instructions`, `modal_backend.tracing`
- Python runtime: `modal_backend.agent_runtime`, `modal_backend.mcp_tools`
- Python domain/orchestration: `modal_backend.jobs`, `modal_backend.schedules`, `modal_backend.controller_rollout`
- Python transport/composition: `modal_backend.api`, `modal_backend.main`
- Worker foundation: `edge-control-plane/src/contracts/**`, `edge-control-plane/src/types.ts`
- Worker auth boundary: `edge-control-plane/src/auth/**`
- Worker transport/orchestration: `edge-control-plane/src/routes/**`, `edge-control-plane/src/durable-objects/**`, `edge-control-plane/src/index.ts`

### Wave 1 enforcement notes

- Blocking governance is intentionally limited to leaf-like Python modules plus
  Worker auth/contracts.
- Orchestration hubs remain advisory until DTO drift and boundary extraction are
  reduced enough to promote them safely into blocking scope.
