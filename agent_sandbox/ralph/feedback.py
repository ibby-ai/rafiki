"""Feedback loop execution for Ralph loops.

Runs validation commands (tests, linting, etc.) after each iteration
to verify task completion. Uses an allowlist for security.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Security: Allowlist of permitted feedback commands
ALLOWED_FEEDBACK_COMMANDS = {
    "npm run test",
    "npm run lint",
    "npm run typecheck",
    "npm run build",
    "npm test",
    "pnpm run test",
    "pnpm run lint",
    "pnpm run typecheck",
    "pytest",
    "pytest -v",
    "python -m pytest",
    "make test",
    "make lint",
    "make check",
    "ruff check .",
    "ruff format --check .",
    "cargo test",
    "cargo check",
    "go test ./...",
    "go vet ./...",
}


def validate_feedback_commands(commands: list[str]) -> list[str]:
    """Validate and sanitize feedback commands against allowlist.

    Args:
        commands: List of commands to validate.

    Returns:
        List of validated commands.

    Raises:
        ValueError: If a command is not in the allowlist.
    """
    validated = []
    for cmd in commands:
        cmd = cmd.strip()
        if not cmd:
            continue
        if cmd in ALLOWED_FEEDBACK_COMMANDS:
            validated.append(cmd)
        elif cmd.startswith(("npm run ", "pnpm run ", "yarn run ")):
            # Allow npm/pnpm/yarn run with simple script names (no shell chars)
            parts = cmd.split(" ", 2)
            if len(parts) >= 3:
                script = parts[2]
                if script.replace("-", "").replace("_", "").isalnum():
                    validated.append(cmd)
                else:
                    raise ValueError(f"Disallowed script name in: {cmd}")
            else:
                raise ValueError(f"Invalid command format: {cmd}")
        else:
            raise ValueError(f"Disallowed feedback command: {cmd}")
    return validated


@dataclass
class FeedbackResult:
    """Result of running feedback loops."""

    passed: bool
    output: str
    failed_command: str | None = None


def run_feedback_loops(workspace: Path, commands: list[str], timeout: int = 120) -> FeedbackResult:
    """Run all feedback commands. Returns on first failure.

    Args:
        workspace: Path to the workspace directory.
        commands: List of commands to run.
        timeout: Timeout in seconds for each command.

    Returns:
        FeedbackResult with pass/fail status and output.
    """
    # Validate commands first
    validated_commands = validate_feedback_commands(commands)
    outputs = []

    for cmd in validated_commands:
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            outputs.append(f"$ {cmd}\n{result.stdout}\n{result.stderr}")

            if result.returncode != 0:
                return FeedbackResult(passed=False, output="\n".join(outputs), failed_command=cmd)
        except subprocess.TimeoutExpired:
            return FeedbackResult(
                passed=False,
                output="\n".join(outputs) + f"\n$ {cmd}\nTIMEOUT after {timeout}s",
                failed_command=cmd,
            )

    return FeedbackResult(passed=True, output="\n".join(outputs))
