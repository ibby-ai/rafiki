# Rafiki

![CI](https://github.com/ibby-ai/rafiki/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Modal](https://img.shields.io/badge/Modal-1.3.5+-8B5CF6.svg)
![Cloudflare Workers](https://img.shields.io/badge/Cloudflare-Workers-F38020.svg?logo=cloudflare&logoColor=white)
![Cloudflare Durable Objects](https://img.shields.io/badge/Cloudflare-Durable%20Objects-F38020.svg?logo=cloudflare&logoColor=white)
![Cloudflare KV](https://img.shields.io/badge/Cloudflare-KV-F38020.svg?logo=cloudflare&logoColor=white)
![Cloudflare Rate Limiting](https://img.shields.io/badge/Cloudflare-Rate%20Limiting-F38020.svg?logo=cloudflare&logoColor=white)
![OpenAI Agents SDK](https://img.shields.io/badge/OpenAI%20Agents%20SDK-0.9.2+-0A66C2.svg)
![LangSmith](https://img.shields.io/badge/LangSmith-Tracing-111827.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

![Rafiki](docs/images/readme-image.png)

Rafiki is a Cloudflare-first agent harness for running stateful OpenAI Agents workflows with secure Modal execution.
Cloudflare Workers + Durable Objects provide the public control plane, while Modal runs the internal execution backend for the current runtime.
The harness remains the stable system boundary even if the underlying agent loop changes.
This project was inspired by Ramp's blog post, [Why we built our background agent](https://builders.ramp.com/post/why-we-built-our-background-agent).

LangSmith is part of Rafiki because agent runs need trace-level observability beyond raw logs. When enabled, it gives developers correlated OpenAI Agents execution traces across local debugging, deployed operations, and incident triage.

## Prerequisites

- Python 3.11+
- `uv`
- Modal account + `uv run modal setup`
- Node.js 20+ and Wrangler only if you need the Cloudflare control plane

## Start Here

1. Sync the repo and set a local `INTERNAL_AUTH_SECRET`.
2. Create the default Modal secrets.
3. Run a local Modal smoke check.
4. When you need real client traffic, switch to the Cloudflare-first runbook.

```bash
cp .env.example .env
# edit .env and set INTERNAL_AUTH_SECRET to a non-empty value

uv sync --extra dev
source .venv/bin/activate

uv run modal setup
uv run modal secret create openai-secret OPENAI_API_KEY=<your-key>
uv run modal secret create internal-auth-secret INTERNAL_AUTH_SECRET=<same-as-.env>
uv run modal secret create modal-auth-secret \
  SANDBOX_MODAL_TOKEN_ID=<token-id> \
  SANDBOX_MODAL_TOKEN_SECRET=<token-secret>

# Optional: only if you want LangSmith tracing
uv run modal secret create langsmith-secret \
  LANGSMITH_API_KEY=<your-langsmith-key> \
  LANGSMITH_PROJECT=<your-project>
```

If you do not want tracing yet, leave `ENABLE_LANGSMITH_TRACING=false` in `.env`.

## Setup Matrix

| Surface       | Required by default              | Name                     | Notes                                                                                                                            |
| ------------- | -------------------------------- | ------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| Local `.env`  | Yes                              | `INTERNAL_AUTH_SECRET`   | Required at settings load time for local Modal runs. Match the Modal and Worker value when using the Cloudflare path.            |
| Modal secret  | Yes                              | `openai-secret`          | Contains `OPENAI_API_KEY`.                                                                                                       |
| Modal secret  | Yes                              | `internal-auth-secret`   | Contains `INTERNAL_AUTH_SECRET` for Worker -> Modal auth and function runtime auth.                                              |
| Modal secret  | Yes                              | `modal-auth-secret`      | Contains `SANDBOX_MODAL_TOKEN_ID` and `SANDBOX_MODAL_TOKEN_SECRET`. Required by default because `ENABLE_MODAL_AUTH_SECRET=true`. |
| Modal secret  | Optional                         | `langsmith-secret`       | Contains `LANGSMITH_API_KEY` and optionally `LANGSMITH_PROJECT`. Create it only when you enable tracing.                         |
| Worker secret | Yes for canonical public ingress | `INTERNAL_AUTH_SECRET`   | Must match Modal `internal-auth-secret`.                                                                                         |
| Worker secret | Yes for canonical public ingress | `SESSION_SIGNING_SECRET` | Used to sign session tokens for client auth.                                                                                     |

## Local Modal Smoke Check

```bash
uv run modal run -m modal_backend.main
uv run modal run -m modal_backend.main::run_agent_remote --question "Explain REST vs gRPC"
```

## Local Service Mode

```bash
uv run modal serve -m modal_backend.main
# or
make serve
```

## Deploy Internal Modal Backend

```bash
uv run modal deploy -m modal_backend.deploy
```

## Cloudflare Control Plane

Use this only when you need the supported public client path. Cloudflare Worker

- Durable Objects are the canonical ingress for client traffic.

```bash
cd edge-control-plane
npm install
wrangler login
wrangler secret put INTERNAL_AUTH_SECRET
wrangler secret put SESSION_SIGNING_SECRET
wrangler kv:namespace create SESSION_CACHE
npm run deploy
```

The Worker depends on the same shared internal auth secret used by Modal, plus a
session-signing secret for client tokens. Worker-side `MODAL_TOKEN_ID` and
`MODAL_TOKEN_SECRET` are not part of the canonical public request path.

For the canonical setup and verification flow, start with `docs/references/runbooks/cloudflare-modal-e2e.md` and `edge-control-plane/README.md`.

## Common Ops

```bash
# Terminate background sandbox
uv run modal run -m modal_backend.main::terminate_service_sandbox

# Snapshot service filesystem
uv run modal run -m modal_backend.main::snapshot_service

# Run tests
uv run python -m pytest
```

## Next Docs

- `docs/references/configuration.md` - full secret and runtime configuration contract
- `docs/references/runbooks/cloudflare-modal-e2e.md` - canonical Cloudflare <-> Modal setup and verification flow
- `docs/references/troubleshooting.md` - startup and auth failure triage
- `docs/design-docs/cloudflare-hybrid-architecture.md` - public control-plane architecture

## Links

- [Modal Documentation](https://modal.com/docs)
- [LangSmith Docs](https://docs.langchain.com/langsmith/home)
- [OpenAI Agents Python Documentation](https://openai.github.io/openai-agents-python/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
