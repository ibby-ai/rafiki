"""Run Claude Code CLI inside a sandboxed container."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from agent_sandbox.config.settings import get_settings
from agent_sandbox.jobs import job_workspace_root, normalize_job_id
from agent_sandbox.utils.cli import (
    CLAUDE_CLI_APP_ROOT,
    claude_cli_env,
    demote_to_claude,
    maybe_chown_for_claude,
    require_claude_cli_auth,
)


def _write_result(
    write_result_path: str | None,
    payload: dict,
    job_root: Path | None,
    base_root: Path,
    job_id: str | None,
) -> None:
    if not write_result_path:
        return
    path = Path(write_result_path)
    if not path.is_absolute():
        if job_id and path.parts[:2] == ("jobs", job_id):
            path = base_root / path
        else:
            base = job_root if job_root is not None else base_root
            path = base / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    maybe_chown_for_claude(path)


def _parse_allowed_tools(value: str | None) -> list[str]:
    if not value:
        return []
    return [tool.strip() for tool in value.split(",") if tool.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Claude CLI sandbox runner")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--allowed-tools", default=None)
    parser.add_argument("--dangerously-skip-permissions", action="store_true")
    parser.add_argument("--output-format", choices=["json", "text"], default="json")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--probe", choices=["version", "help", "path"], default=None)
    parser.add_argument("--write-result-path", default=None)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    settings = get_settings()

    normalized_job_id = normalize_job_id(args.job_id)
    job_root = None
    if normalized_job_id:
        job_root = job_workspace_root(settings.claude_cli_fs_root, normalized_job_id)
        job_root.mkdir(parents=True, exist_ok=True)
        maybe_chown_for_claude(job_root)

    env = claude_cli_env()
    require_claude_cli_auth(env)

    cmd = ["claude", "-p", args.prompt, "--output-format", args.output_format]
    if args.dangerously_skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    tools_list = _parse_allowed_tools(args.allowed_tools)
    if tools_list:
        cmd.extend(["--allowedTools", ",".join(tools_list)])
    if args.max_turns is not None:
        cmd.extend(["--max-turns", str(args.max_turns)])

    probe_cmd: list[str] | None = None
    if args.probe:
        if args.probe == "version":
            probe_cmd = ["claude", "--version"]
        elif args.probe == "help":
            probe_cmd = ["claude", "--help"]
        elif args.probe == "path":
            probe_cmd = ["/bin/sh", "-lc", "command -v claude && ls -l $(command -v claude)"]

    payload: dict = {}
    exit_code = 0
    try:
        result = subprocess.run(
            probe_cmd or cmd,
            capture_output=True,
            text=True,
            timeout=args.timeout_seconds,
            cwd=str(job_root) if job_root is not None else str(CLAUDE_CLI_APP_ROOT),
            stdin=subprocess.DEVNULL,
            env=env,
            preexec_fn=demote_to_claude(),
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        if result.returncode != 0:
            exit_code = result.returncode
            payload = {
                "ok": False,
                "error": stderr or stdout or "Claude CLI failed",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
            }
        elif probe_cmd is not None:
            payload = {
                "ok": True,
                "result": None,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
                "cmd": probe_cmd,
                "cwd": str(job_root) if job_root is not None else str(CLAUDE_CLI_APP_ROOT),
                "path": env.get("PATH", ""),
                "home": env.get("HOME", ""),
                "user": env.get("USER", ""),
                "has_anthropic_api_key": bool(env.get("ANTHROPIC_API_KEY")),
                "probe": True,
            }
        else:
            parsed: object = stdout
            if args.output_format == "json":
                try:
                    if stdout:
                        parsed = json.loads(stdout)
                    elif stderr:
                        parsed = json.loads(stderr)
                    else:
                        parsed = None
                except json.JSONDecodeError as exc:
                    exit_code = 1
                    payload = {
                        "ok": False,
                        "error": "Failed to parse Claude CLI JSON output",
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "exit_code": 1,
                        "parse_error": str(exc),
                    }
                else:
                    payload = {
                        "ok": True,
                        "result": parsed,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "exit_code": result.returncode,
                    }
            else:
                payload = {
                    "ok": True,
                    "result": parsed,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.returncode,
                }
    except subprocess.TimeoutExpired:
        exit_code = 124
        payload = {
            "ok": False,
            "error": f"Claude CLI timed out after {args.timeout_seconds}s",
            "stdout": None,
            "stderr": None,
            "exit_code": exit_code,
        }
    except Exception as exc:  # pragma: no cover - unexpected runtime errors
        exit_code = 1
        payload = {
            "ok": False,
            "error": str(exc),
            "stdout": None,
            "stderr": None,
            "exit_code": exit_code,
        }

    if args.debug:
        payload.update(
            {
                "cmd": probe_cmd or cmd,
                "cwd": str(job_root) if job_root is not None else str(CLAUDE_CLI_APP_ROOT),
                "path": env.get("PATH", ""),
                "home": env.get("HOME", ""),
                "user": env.get("USER", ""),
                "has_anthropic_api_key": bool(env.get("ANTHROPIC_API_KEY")),
                "probe": probe_cmd is not None,
            }
        )

    _write_result(
        args.write_result_path,
        payload,
        job_root,
        base_root=Path(settings.claude_cli_fs_root),
        job_id=normalized_job_id,
    )

    print(json.dumps(payload), flush=True)
    return 0 if payload.get("ok") else (exit_code or 1)


if __name__ == "__main__":
    sys.exit(main())
