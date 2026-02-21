# ExecPlan: Claude Code CLI Support

## Purpose / Big Picture
Enable Claude Code CLI usage inside the Modal sandbox image and expose it via HTTP endpoints + Modal run commands. This lets users invoke the CLI programmatically alongside the existing Claude Agent SDK workflow, using the same persistent sandbox and secrets.

## Suprises & Discoveries
- Observation: None yet.
- Evidence: N/A.

## Decision Log
- Decision: Install Claude Code CLI via the recommended `curl -fsSL https://claude.ai/install.sh | bash` within the Modal image build.
- Rationale: Matches upstream recommendation and keeps install path consistent with official docs.
- Date/Author: 2026-01-07 / Codex

## Outcomes & Retrospective
- Pending.

## Context and Orientation
- `modal_backend/main.py` builds the Modal image and exposes HTTP proxy endpoints via `web_app`.
- `modal_backend/api/controller.py` runs inside a long-lived sandbox and serves `/query` + `/query_stream`.
- `modal_backend/models/` holds request/response Pydantic models used by endpoints.
- `CLAUDE.md` documents project commands and HTTP usage.

## Plan of Work
1. Update the Modal image builder in `modal_backend/main.py` to install the Claude Code CLI via the curl installer and ensure `claude` is on PATH.
2. Add new schemas for CLI requests/responses in `modal_backend/models/sandbox.py` and `modal_backend/models/responses.py`, then export them in `modal_backend/models/__init__.py`.
3. Implement a `/claude_cli` endpoint in `modal_backend/api/controller.py` that invokes the `claude` binary, handles timeouts, and returns JSON output.
4. Add a proxy endpoint in `modal_backend/main.py` and a `run_claude_cli_remote` Modal function for quick testing.
5. Update `CLAUDE.md` (and any relevant docs) with new commands and curl examples.

## Concrete Steps
See tasks in `docs/exec-plans/completed/claude_code_cli/tasks/`.

## Progress
[x] (TASK_01_claude_code_cli.md) (2026-01-07 00:00) Image install + PATH updates.
[x] (TASK_02_claude_code_cli.md) (2026-01-07 00:00) Schemas + controller endpoint.
[x] (TASK_03_claude_code_cli.md) (2026-01-07 00:00) Proxy endpoint + modal run helper + docs.

## Testing Approach
- Run `uv run ruff check --fix .` and `uv run ruff format .`.
- Manual: `modal run -m modal_backend.main::run_claude_cli_remote --prompt "..." --allowed-tools "Read"`.
- Manual: `modal serve -m modal_backend.main` then `curl -X POST <url>/claude_cli ...`.

## Constraints & Considerations
- Network is restricted in this environment; Modal invocations may need to be run by the user.
