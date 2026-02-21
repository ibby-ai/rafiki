# ExecPlan: CLI Sandbox Controller Service

## Purpose / Big Picture
Enable a long‑lived Claude CLI sandbox service that executes CLI and Ralph runs via HTTP endpoints, with CLI‑specific resource/timeout settings and reliable volume commits for /data-cli artifacts.

## Suprises & Discoveries
- Observation: (none yet)
- Evidence: n/a

## Decision Log
- Decision: Use a dedicated FastAPI controller inside the CLI sandbox rather than per‑job Sandbox.exec.
- Rationale: Reduces sandbox churn while keeping artifacts on the dedicated CLI volume.
- Date/Author: 2026-01-09 / Codex

- Decision: Add CLI‑specific resource and timeout settings parallel to existing sandbox settings.
- Rationale: Allow CLI workloads to be tuned independently without changing Agent SDK defaults.
- Date/Author: 2026-01-09 / Codex

- Decision: Commit CLI volume writes at the end of CLI/Ralph runs inside the CLI sandbox.
- Rationale: Ensures /data-cli artifacts are available promptly across containers.
- Date/Author: 2026-01-09 / Codex

## Outcomes & Retrospective
- Added CLI-specific sandbox settings, implemented a CLI controller service, and routed CLI/Ralph runs through a long-lived CLI sandbox with explicit CLI volume commits.

## Context and Orientation
The current implementation runs Claude CLI jobs by spawning a fresh Modal sandbox per request via `modal_backend/main.py` and `modal_backend/sandbox_runtime/cli_runner.py`. Ralph runs similarly via `modal_backend/ralph/runner.py`. Settings live in `modal_backend/settings/settings.py`. The existing long‑lived background service is implemented in `modal_backend/api/controller.py` and managed by `get_or_start_background_sandbox()` in `modal_backend/main.py`. There is an unused CLI volume commit helper `_commit_claude_cli_volume()` in `modal_backend/main.py`.

## Plan of Work
1. Add CLI‑specific settings in `modal_backend/settings/settings.py` for service port(s), sandbox resources, and idle/overall timeouts.
2. Create a new FastAPI controller `modal_backend/api/cli_controller.py` that exposes `/health_check`, `/execute`, and `/ralph/execute` endpoints. This controller should:
   - Run Claude CLI with non‑root demotion and write artifacts under `/data-cli/jobs/<job_id>/`.
   - Run the Ralph loop directly in the CLI sandbox workspace.
   - Commit the CLI volume after runs.
3. Add CLI sandbox lifecycle helpers in `modal_backend/main.py` (sync + async) to create/reuse a long‑lived CLI sandbox, and route `run_claude_cli_remote`/`run_ralph_remote` through the controller endpoints.
4. Wire volume commit usage where needed and update `docs/references/configuration.md` to document new CLI settings.

## Concrete Steps
- Create `docs/exec-plans/completed/cli-sandbox-controller/tasks/TASK_01_settings.md` for settings and docs updates.
- Create `docs/exec-plans/completed/cli-sandbox-controller/tasks/TASK_02_controller.md` for CLI controller implementation.
- Create `docs/exec-plans/completed/cli-sandbox-controller/tasks/TASK_03_app_integration.md` for app wiring and commit usage.

## Progress
[x] (TASK_01_settings.md) Add CLI-specific settings and documentation
[x] (TASK_02_controller.md) Implement CLI controller service endpoints
[x] (TASK_03_app_integration.md) Wire CLI sandbox lifecycle + route CLI/Ralph runs

## Testing Approach
- Run `uv run ruff check --fix .` and `uv run ruff format .`.
- Optionally run the existing CLI and Ralph smoke tests from the report if network access is available.

## Constraints & Considerations
- Network access is restricted in this environment, so live Modal tests may need user approval.
- Avoid breaking existing HTTP endpoints; keep /claude_cli and /ralph/start behavior stable.
