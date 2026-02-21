# ExecPlan: Claude CLI Dedicated Sandbox

## Purpose / Big Picture
Deliver a dedicated Modal Sandbox and persistent Volume for the Claude Code CLI workflow, keeping it isolated from the Agent SDK sandbox/volume. Ralph should run inside the same dedicated CLI sandbox environment so all CLI-generated artifacts live under the CLI volume mount.

## Suprises & Discoveries
- Observation: Sandbox volume writes sync when the sandbox terminates; explicit commit is not required in the sandbox path.
- Evidence: Modal sandbox volume behavior in documentation and existing app patterns.

## Decision Log
- Decision: Execute CLI + Ralph runs via `modal.Sandbox.exec` with new sandbox runner modules and a dedicated CLI volume mount at `/data-cli`.
- Rationale: Provides container-level isolation for CLI workloads and avoids contention with the Agent SDK volume.
- Date/Author: 2026-01-09 / Codex

## Outcomes & Retrospective
- CLI and Ralph now run inside dedicated sandboxes with a separate CLI volume.
- HTTP app mounts the CLI volume for Ralph status reads.
- Live status still depends on volume sync; consider periodic commit if real-time polling becomes required.

## Context and Orientation
- `modal_backend/main.py`: Orchestrates Modal functions, sandboxes, and HTTP endpoints. Updated to create CLI sandboxes and mount the CLI volume.
- `modal_backend/settings/settings.py`: Adds CLI sandbox/volume settings.
- `modal_backend/sandbox_runtime/cli_runner.py`: Executes Claude CLI inside a sandbox.
- `modal_backend/ralph/runner.py`: Executes Ralph loop inside a sandbox.
- `docs/references/configuration.md`: Documents new CLI settings.

## Plan of Work
1. Add CLI-specific settings and volume helpers; create sandbox creation helper.
2. Implement sandbox runners for CLI and Ralph execution.
3. Wire CLI + Ralph orchestrators to sandbox runners and update status read paths.
4. Update documentation and run ruff checks.

## Concrete Steps
See tasks in `docs/exec-plans/completed/claude_cli_sandbox/tasks/`.

## Progress
[x] (TASK_01_claude_cli_sandbox.md) (2026-01-09) Identify separation points and settings additions.
[x] (TASK_02_claude_cli_sandbox.md) (2026-01-09) Implement CLI sandbox runner + volume helpers.
[x] (TASK_03_claude_cli_sandbox.md) (2026-01-09) Wire Ralph sandbox runner + update endpoints/docs.
[x] (TASK_04_claude_cli_sandbox.md) (2026-01-09) Lint/format pass.

## Testing Approach
- `uv run ruff check --fix .`
- `uv run ruff format .`
- Manual (user-run): `modal run -m modal_backend.main::run_claude_cli_remote --prompt "..."` and `modal serve -m modal_backend.main` with `/ralph/start` polling.

## Constraints & Considerations
- Modal network access is restricted in this environment; Modal runs need to be executed by the user.
- Volume sync for sandboxes happens on termination; live polling may require future commit strategy.
