"""Entry point for running the Ralph loop inside a sandbox."""

from __future__ import annotations

import argparse
import json
import sys

from agent_sandbox.config.settings import get_settings
from agent_sandbox.jobs import job_workspace_root, normalize_job_id
from agent_sandbox.ralph.loop import run_ralph_loop
from agent_sandbox.ralph.schemas import Prd, WorkspaceSource
from agent_sandbox.utils.cli import claude_cli_env, maybe_chown_for_claude, require_claude_cli_auth


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ralph sandbox runner")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prd-json", required=True)
    parser.add_argument("--workspace-source-json", required=True)
    parser.add_argument("--prompt-template", default=None)
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--timeout-per-iteration", type=int, default=300)
    parser.add_argument("--allowed-tools", default="")
    parser.add_argument("--feedback-commands", default="")
    parser.add_argument("--feedback-timeout", type=int, default=120)
    parser.add_argument("--max-consecutive-failures", type=int, default=3)
    parser.add_argument(
        "--auto-commit",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    return parser


def _parse_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> int:
    args = _build_parser().parse_args()
    settings = get_settings()

    normalized_job_id = normalize_job_id(args.job_id)
    if not normalized_job_id:
        print("job_id must be a valid UUID", file=sys.stderr)
        return 1

    env = claude_cli_env()
    require_claude_cli_auth(env)

    workspace = job_workspace_root(settings.claude_cli_fs_root, normalized_job_id)
    workspace.mkdir(parents=True, exist_ok=True)
    maybe_chown_for_claude(workspace)

    prd = Prd.model_validate_json(args.prd_json)
    workspace_source = WorkspaceSource.model_validate_json(args.workspace_source_json)
    allowed_tools = _parse_list(args.allowed_tools)
    feedback_commands = _parse_list(args.feedback_commands)

    try:
        result = run_ralph_loop(
            job_id=normalized_job_id,
            prd=prd,
            workspace=workspace,
            workspace_source=workspace_source,
            prompt_template=args.prompt_template,
            max_iterations=args.max_iterations,
            timeout_per_iteration=args.timeout_per_iteration,
            allowed_tools=allowed_tools or None,
            feedback_commands=feedback_commands or None,
            feedback_timeout=args.feedback_timeout,
            auto_commit=args.auto_commit,
            max_consecutive_failures=args.max_consecutive_failures,
        )
        print(result.model_dump_json(), flush=True)
        return 0
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
        print(json.dumps(payload), flush=True)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
