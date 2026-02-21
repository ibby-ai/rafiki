---
task_id: 04
plan_id: PLAN_modal-advanced-followups
plan_file: ../PLAN_modal-advanced-followups.md
title: Proxy Auth token docs and client headers
phase: Phase 3 - Security & Client Enablement
---

## Objective

Document Proxy Auth token creation and update client examples to send `Modal-Key`/`Modal-Secret` headers when required.

## Scope

- Add clear workspace steps to create a Proxy Auth token and store credentials securely.
- Update `examples/05_http_endpoints/client.py` and `examples/05_http_endpoints/run.sh` to accept and send Proxy Auth headers (via env vars or CLI args).
- Ensure `README.md` and `docs/references/api-usage.md` highlight the updated client usage.

## Files

- `examples/05_http_endpoints/client.py`
- `examples/05_http_endpoints/run.sh`
- `README.md`
- `docs/references/api-usage.md`

## Acceptance Criteria

- Example clients can send Proxy Auth headers when configured.
- Documentation includes steps to create Proxy Auth tokens and pass them to clients.
