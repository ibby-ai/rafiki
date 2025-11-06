# Repository Guidelines

## Project Structure & Module Organization

The project is organized as a Python package `agent_sandbox/` following Modal's best practices for multi-file projects. Entry points live at `agent_sandbox/app.py` (Modal app declaration) and `agent_sandbox/agents/loop.py` (agent loop). Shared components are organized into subpackages:

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

- `uv pip install -r uv.lock` — sync local dependencies (`pip install -r uv.lock` if `uv` is unavailable).
- `modal run -m agent_sandbox.app` — build the sandbox image if needed and execute the agent end to end.
- `modal run -m agent_sandbox.app::run_agent_remote --question "..."` — call the agent function for ad‑hoc questions.
- `modal serve -m agent_sandbox.app` — run a hot-reloading dev loop against Modal.
- `modal deploy -m agent_sandbox.deploy` — promote the current definition to production.

## Coding Style & Naming Conventions

Target Python 3.11+ features only when they remain compatible with Modal runtime images. Follow PEP 8 defaults: 4-space indentation, snake_case for functions and variables, UpperCamelCase for classes. Keep module-level constants uppercase. Prefer type hints on new functions, and keep environment or tool names descriptive (`calculate_tool`, not `calc`). Strings that surface in prompts should live in `agent_sandbox/prompts/prompts.py`.

## Testing Guidelines

The test suite uses `pytest` with filenames `test_*.py` mirroring the package layout. Before submitting changes, run `modal run -m agent_sandbox.app` and `modal run -m agent_sandbox.app::run_agent_remote --question "health check"` to confirm the agent boots, tools register, and streaming output works. Mark long-running Modal calls with `@pytest.mark.slow`. Capture regression coverage for new behaviors whenever practical.

## Commit & Pull Request Guidelines

Existing commits use short, present-tense statements (`making sandbox persistent`). Continue that format: lowercase verb phrases under ~60 characters. For pull requests, include 1) a concise summary of the change, 2) instructions to reproduce new behavior, 3) screenshots or transcripts when agent responses change, and 4) links to related issues or tickets.

## Security & Secrets

Never hardcode API keys. All Anthropic credentials must stay in the `anthropic-secret` Modal secret with key `ANTHROPIC_API_KEY`. When adding new secrets, update `agent_sandbox/config/settings.py` and document required setup steps in `README.md`. Avoid committing generated artifacts that might expose credentials or user data.

## ExecPlans

When writing complex features or refactoring, you should create an ExecPlan as described in the .agent/plans/PLANS.md file. This plan should be stored in the `.agent/plans/{feature_name}/` directory and it should be accompanied by a task list in the `.agent/tasks/{feature_name}/` directory. Place any temporary research, clones, etc., in the .gitignored subdirectory of the .agent/ directory.
