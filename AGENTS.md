# Repository Guidelines

## Project Structure & Module Organization

Entry points live at `main.py` (Modal app declaration) and `runner.py` (agent loop). Shared helpers sit under `utils/`, with `env_templates.py` configuring the Modal image, `prompts.py` storing system text, and `tools.py` defining sample MCP tools. Secrets, environment templates, and runner logic are intentionally separated so updates seldom touch multiple files. Place new utilities or agents in `utils/` and export them when needed. There is no `tests/` directory yet; mirror the package layout when creating one.

## Build, Test, and Development Commands

- `uv pip install -r uv.lock` — sync local dependencies (`pip install -r uv.lock` if `uv` is unavailable).
- `modal run main.py` — build the sandbox image if needed and execute the agent end to end.
- `modal run main.py::sandbox_controller --question "..."` — call the controller entry point for ad‑hoc questions.
- `modal serve main.py` — run a hot-reloading dev loop against Modal.
- `modal deploy main.py` — promote the current definition to production.

## Coding Style & Naming Conventions

Target Python 3.13 features only when they remain compatible with Modal runtime images. Follow PEP 8 defaults: 4-space indentation, snake_case for functions and variables, UpperCamelCase for classes. Keep module-level constants uppercase. Prefer type hints on new functions, and keep environment or tool names descriptive (`calculate_tool`, not `calc`). Strings that surface in prompts should live in `utils/prompts.py`.

## Testing Guidelines

No automated suite ships yet. Before submitting changes, run `modal run main.py` and `modal run main.py::sandbox_controller --question "health check"` to confirm the agent boots, tools register, and streaming output works. If you add tests, prefer `pytest` with filenames `test_*.py` and mark long-running Modal calls with `@pytest.mark.slow`. Capture regression coverage for new behaviors whenever practical.

## Commit & Pull Request Guidelines

Existing commits use short, present-tense statements (`making sandbox persistent`). Continue that format: lowercase verb phrases under ~60 characters. For pull requests, include 1) a concise summary of the change, 2) instructions to reproduce new behavior, 3) screenshots or transcripts when agent responses change, and 4) links to related issues or tickets.

## Security & Secrets

Never hardcode API keys. All Anthropic credentials must stay in the `anthropic-secret` Modal secret with key `ANTHROPIC_API_KEY`. When adding new secrets, update `utils/env_templates.py` and document required setup steps in `README.md`. Avoid committing generated artifacts that might expose credentials or user data.

## ExecPlans

When writing complex features or refactoring, you should create an ExecPlan as described in the .agent/plans/PLANS.md file. This plan should be stored in the `.agent/plans/{feature_name}/` directory and it should be accompanied by a task list in the `.agent/tasks/{feature_name}/` directory. Place any temporary research, clones, etc., in the .gitignored subdirectory of the .agent/ directory.
