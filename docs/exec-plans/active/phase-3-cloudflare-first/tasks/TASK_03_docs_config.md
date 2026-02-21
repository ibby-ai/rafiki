---
task_id: 03
plan_id: PLAN_phase-3-cloudflare-first
plan_file: ../PLAN_phase-3-cloudflare-first.md
title: Update config and docs for Phase 3 Cloudflare-first cutover
phase: Phase 3 - Documentation & Config
---

## Steps
- Update `edge-control-plane/wrangler.jsonc` with Modal app URL placeholder and rate limit binding.
- Update Cloudflare docs and integration docs to remove TODOs and reflect enforced auth, rate limiting, KV cache, presence, and Cloudflare-only public entry.
- Update `docs/design-docs/cloudflare-hybrid-architecture.md` and `docs/references/api-usage.md` for Phase 3 architecture and new endpoints.
- Add Phase 3 breaking changes to `CHANGELOG.md`.
