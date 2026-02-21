# Repository Guidelines

## Project Structure & Module Organization

The project is organized as a Python package `modal_backend/` following Modal's best practices for multi-file projects. Entry points live at `modal_backend/main.py` (Modal app declaration) and `modal_backend/agent_runtime/loop.py` (agent loop). Shared components are organized into subpackages:

- `config/`: Configuration management with Pydantic Settings
- `prompts/`: System prompts and default questions
- `tools/`: MCP tool system with registry and individual tool implementations
- `schemas/`: Pydantic models for request/response validation
- `sandbox/`: Sandbox utilities and volume operations
- `services/`: Cross-cutting services like logging
- `controllers/`: FastAPI service for background sandbox
- `agents/`: Agent execution logic

Place new utilities or agents in appropriate subpackages and export them when needed. The test suite mirrors the package layout in `tests/`.

## Build, Test, and Development Commands

This is a **uv-based project**. Always activate the virtual environment before running commands.

### Setup

- `source .venv/bin/activate` — activate the virtual environment (required before other commands).
- `uv sync` — sync dependencies from `pyproject.toml` and `uv.lock`.
- `pip install modal` and `modal setup` — install and authenticate the Modal CLI.
- `modal secret create openai-secret OPENAI_API_KEY=<your-key>` — create the required OpenAI secret (use `modal secret list` to verify).

### Running

- `modal run -m modal_backend.main` — build the sandbox image if needed and execute the agent end to end.
- `modal run -m modal_backend.main::run_agent_remote --question "..."` — call the agent function for ad‑hoc questions.
- `modal serve -m modal_backend.main` — run a hot-reloading dev loop against Modal.
- During `modal serve` E2E sessions, avoid concurrent `modal run -m modal_backend.main*` against the same dev label: a concurrent run can steal `*-http-app-dev.modal.run`. Use a separate deployed app/label for parallel work when needed.
- For parallel local edge work (`wrangler dev`), keep `modal serve` running. The image context is intentionally scoped in `modal_backend/main.py` (mounts `pyproject.toml` + `modal_backend/`) to avoid reload churn from unrelated repo paths.
- `modal deploy -m modal_backend.deploy` — promote the current definition to production.
- `make serve` — convenience wrapper for `modal serve -m modal_backend.main`.
- `make run` / `make deploy` — Makefile wrappers for running or deploying with Modal.
- `make curl Q="..."` / `make stream Q="..."` — POST against the dev HTTP endpoints.
- `make health` / `make info` — check service health or info endpoints. Set `DEV_URL` to the dev endpoint URL; use `MODAL_PROXY_KEY` and `MODAL_PROXY_SECRET` when hitting proxied endpoints.

### Testing

- `uv run pytest` — run the test suite.
- `uv run pytest tests/test_specific.py -v` — run specific tests with verbose output.

## Coding Style & Naming Conventions

Target Python 3.11+ features only when they remain compatible with Modal runtime images. Follow PEP 8 defaults: 4-space indentation, snake_case for functions and variables, UpperCamelCase for classes. Keep module-level constants uppercase. Prefer type hints on new functions, and keep environment or tool names descriptive (`calculate_tool`, not `calc`). Strings that surface in prompts should live in `modal_backend/instructions/prompts.py`.

## Pre-commit Hooks

The repository uses pre-commit with ruff for automated linting and formatting. Hooks run automatically on `git commit`.

- **ruff** — lints code and auto-fixes issues (import ordering, unused imports, style violations).
- **ruff-format** — formats code consistently (like Black, but faster).

### Required: Run After Making Changes

**Always run the ruff linter and formatter after making any code changes:**

```bash
uv run ruff check --fix .
uv run ruff format .
```

This ensures code quality and prevents commit failures from hook violations.

### Other Commands

- `uv run pre-commit run --all-files` — run all hooks on the entire codebase.

If a commit fails due to hook violations, the hooks will auto-fix what they can. Stage the fixes and commit again.

## Service Management & Debugging

- `modal run -m modal_backend.main::terminate_service_sandbox` — force a final volume commit and stop the background sandbox.
- `modal run -m modal_backend.main::snapshot_service` — snapshot the current service filesystem.
- `modal run -m modal_backend.main::process_job_queue` — consume queued jobs in development.
- `modal run -m modal_backend.main::tail_logs` — stream sandbox logs during troubleshooting.
- `modal sandbox logs <sandbox-id>` — inspect logs for a specific sandbox.
- `modal app list` / `modal app logs <app-name>` — locate deployed apps and view logs.
- `modal container list` — inspect running Modal containers.
- `modal volume ls <volume>` / `modal volume get <volume> <remote> <local>` / `modal volume rm <volume> <remote>` — inspect and manage persisted files.
- Cloudflare Worker control plane checks:
  - Auth must be active first: `cd edge-control-plane && wrangler whoami` (if not logged in, run `wrangler login`).
  - If worker does not exist yet, first-time setup is: create KV namespace, set `kv_namespaces[].id` in `edge-control-plane/wrangler.jsonc`, set required secrets (`INTERNAL_AUTH_SECRET`, `SESSION_SIGNING_SECRET`), then deploy.
  - Deploy command: `cd edge-control-plane && wrangler deploy`.
  - Validate deployed health: `curl -i https://<worker>.workers.dev/health` expecting `200` and `{"ok":true,"service":"edge-control-plane"}`.

## Testing Guidelines

The test suite uses `pytest` with filenames `test_*.py` mirroring the package layout. Before submitting changes, run `modal run -m modal_backend.main` and `modal run -m modal_backend.main::run_agent_remote --question "health check"` to confirm the agent boots, tools register, and streaming output works. Mark long-running Modal calls with `@pytest.mark.slow`. Capture regression coverage for new behaviors whenever practical.

For schedule edge E2E via deployed Worker:
- Use Bearer session tokens signed with `SESSION_SIGNING_SECRET`.
- Verify route chain through Worker (not just direct Modal):
  - `POST /schedules?session_id=...` -> `200`
  - `GET /schedules?session_id=...` -> `200`
  - `GET /schedules/{id}?session_id=...` -> `200`
  - `POST /schedules/dispatch?session_id=...` -> `200`
- Verify auth scoping passthrough by listing with a different tenant token and confirming foreign schedules are not returned.

## Commit & Pull Request Guidelines

Existing commits use short, present-tense statements (`making sandbox persistent`). Continue that format: lowercase verb phrases under ~60 characters. For pull requests, include 1) a concise summary of the change, 2) instructions to reproduce new behavior, 3) screenshots or transcripts when agent responses change, and 4) links to related issues or tickets.

## Security & Secrets

Never hardcode API keys. All OpenAI credentials must stay in the `openai-secret` Modal secret with key `OPENAI_API_KEY`. When adding new secrets, update `modal_backend/settings/settings.py` and document required setup steps in `README.md`. Avoid committing generated artifacts that might expose credentials or user data.

## Browser Automation

Use `agent-browser` for web automation. Run `agent-browser --help` for all commands.

Core workflow:

1. `agent-browser open <url>` - Navigate to page
2. `agent-browser snapshot -i` - Get interactive elements with refs (@e1, @e2)
3. `agent-browser click @e1` / `fill @e2 "text"` - Interact using refs
4. Re-snapshot after page changes

## ExecPlans

When writing complex features or refactoring, you should create an ExecPlan as described in the .agent/plans/Plan.md file. This plan should be stored in the `.agent/plans/{feature_name}/` directory and it should be accompanied by a task list in the `.agent/tasks/{feature_name}/` directory. Place any temporary research, clones, etc., in the .gitignored subdirectory of the .agent/ directory.
