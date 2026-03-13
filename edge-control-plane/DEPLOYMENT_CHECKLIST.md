# Deployment Checklist

Use this checklist for the canonical public Worker deploy.
For the full end-to-end sequence, evidence expectations, and rollout validation, use `docs/references/runbooks/cloudflare-modal-e2e.md`.

## Prerequisites

- [ ] `edge-control-plane/wrangler.jsonc` top-level vars still point at the production Modal backend.
- [ ] Local-only values remain under `env.development`.
- [ ] `npm install` has been run in `edge-control-plane/`.
- [ ] You are authenticated with Cloudflare (`wrangler login`).
- [ ] The matching Modal backend is already deployed and healthy.

## Cloudflare Secrets and Bindings

- [ ] Generate or retrieve shared secrets:
  - `INTERNAL_AUTH_SECRET`
  - `SESSION_SIGNING_SECRET`
- [ ] Upload Worker secrets:

  ```bash
  wrangler secret put INTERNAL_AUTH_SECRET
  wrangler secret put SESSION_SIGNING_SECRET
  ```

- [ ] Confirm `SESSION_CACHE` exists and `wrangler.jsonc` has the correct KV ID.
- [ ] Confirm the `development` environment duplicates the non-inheritable DO, KV, and rate-limit bindings.
- [ ] Keep `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` out of the baseline checklist unless you are deliberately using a non-canonical route that requires them.

## Modal Secret Parity

- [ ] Ensure Modal has matching shared auth:

  ```bash
  uv run modal secret create internal-auth-secret \
    INTERNAL_AUTH_SECRET="<same-value-as-cloudflare>"
  ```

- [ ] Ensure sandbox Modal credentials exist when the runtime needs in-sandbox Modal access:

  ```bash
  uv run modal secret create modal-auth-secret \
    SANDBOX_MODAL_TOKEN_ID="<token-id>" \
    SANDBOX_MODAL_TOKEN_SECRET="<token-secret>"
  ```

## Deploy

- [ ] Run the canonical public deploy:

  ```bash
  npm run deploy
  ```

- [ ] Record the deployed Worker URL.
- [ ] Verify `/health` immediately:

  ```bash
  curl https://your-worker.workers.dev/health
  ```

## Public Request Verification

- [ ] Generate a session token with the canonical helper:

  ```bash
  TOKEN="$(node ./scripts/generate-session-token.js \
    --user-id deploy-check-user \
    --tenant-id deploy-check-tenant \
    --session-id deploy-check-session \
    --ttl-seconds 3600 \
    --secret "$SESSION_SIGNING_SECRET")"
  ```

- [ ] Verify an authenticated public `/query`:

  ```bash
  curl -X POST https://your-worker.workers.dev/query \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"question":"What is 2 + 2?","session_id":"deploy-check-session"}'
  ```

- [ ] If you are doing a rollout or release-signoff wave, continue with `/query_stream`, queue/state checks, `/service_info`, and `/pool/status` per the canonical runbook.

## Local Development Reminder

- [ ] Use `npm run dev` for local Worker development.
- [ ] Do not repoint top-level Worker vars to the dev Modal target.
- [ ] Treat `wrangler dev --env development` as the only supported local Worker path.
