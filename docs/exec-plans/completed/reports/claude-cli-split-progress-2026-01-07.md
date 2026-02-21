# Claude CLI Split Progress Report

Date: 2026-01-07

## Summary
We split Claude Code CLI execution into its own Modal image and function, removed CLI installation from the base Agent SDK image, and documented the legacy sandbox CLI endpoint. We validated the new flow and found that CLI failures were caused by depleted Anthropic credits ("Credit balance is too low"), which also explains earlier empty outputs.

## Context
The CLI was originally installed into the same image used for the agent SDK and was executed as a non-root user. This created a permissions mismatch because the repo lived under /root/app while the CLI runs as user `claude`. The goal of the split was to isolate the CLI into its own image rooted in /home/claude/app and avoid /root permissions.

## Changes Made
- Added a dedicated CLI image in `modal_backend/main.py` via `_claude_cli_image()` that:
  - installs the CLI under user `claude`
  - sets workdir to `/home/claude/app`
  - copies the repo to `/home/claude/app`
  - chowns the repo to `claude`
- Added CLI execution helpers to `modal_backend/main.py`:
  - `_claude_cli_env()`, `_claude_cli_ids()`, `_demote_to_claude()`, `_maybe_chown_for_claude()`
- Rewired `/claude_cli` HTTP endpoint to call the new `run_claude_cli_remote` function (dedicated CLI image) instead of the background sandbox.
- Updated `run_claude_cli_remote` to execute the CLI directly and return JSON/text output.
- Removed Claude CLI installation steps from `_base_anthropic_sdk_image()` and trimmed PATH entries there.
- Documented the background-sandbox `/claude_cli` endpoint as legacy in `modal_backend/api/controller.py`.

## Files Touched
- `modal_backend/main.py`
- `modal_backend/api/controller.py`

## Validation Performed
- Lint/format:
  - `uv run ruff check --fix .`
  - `uv run ruff format .`
- CLI function (dedicated image):
  - `modal run -m modal_backend.main::run_claude_cli_remote --prompt "Summarize repo layout" --allowed-tools "Read"`
- HTTP endpoint:
  - `modal serve -m modal_backend.main`
  - `POST /claude_cli` with payload `{"prompt":"Summarize repo layout","allowed_tools":["Read"],"output_format":"text","dangerously_skip_permissions":true}`

## Findings & Issues
- CLI calls failed with `RuntimeError: Claude CLI failed with exit code 1: Credit balance is too low` inside the CLI image. This surfaced in `/tmp/modal_serve.log` when running `/claude_cli`.
- Earlier empty outputs from `/claude_cli` were consistent with the same underlying credit issue.
- With zero Anthropic credit, both `modal run` and the HTTP endpoint return empty output or 500s from the CLI wrapper.

## Notes
- The CLI image now runs from `/home/claude/app` and avoids /root permission issues.
- The base Agent SDK image no longer installs the CLI, reducing build surface area.

## Next Steps (Pending)
- Re-validate CLI output once credits are restored.
- Optionally add clearer error propagation (return stderr/exit code in HTTP 500 body) to make credit failures visible in the API response.

