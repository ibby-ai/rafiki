"""Claude CLI utility functions shared across modules.

This module provides shared functionality for running the Claude Code CLI
in a non-root environment with proper privilege demotion and authentication.
"""

import logging
import os
import pwd
from collections.abc import Callable
from pathlib import Path

_logger = logging.getLogger(__name__)

# Claude CLI user and path configuration
CLAUDE_CLI_USER = "claude"
CLAUDE_CLI_HOME = Path("/home/claude")
CLAUDE_CLI_PATH = f"{CLAUDE_CLI_HOME}/.local/bin:{CLAUDE_CLI_HOME}/.claude/bin"
CLAUDE_CLI_APP_ROOT = CLAUDE_CLI_HOME / "app"


def claude_cli_env() -> dict[str, str]:
    """Build environment dictionary for Claude CLI subprocess execution.

    Returns:
        Environment dict with HOME, USER, and PATH configured for the claude user.
    """
    env = os.environ.copy()
    env["HOME"] = str(CLAUDE_CLI_HOME)
    env["USER"] = CLAUDE_CLI_USER
    env["PATH"] = f"{CLAUDE_CLI_PATH}:{env.get('PATH', '')}"
    return env


def require_claude_cli_auth(env: dict[str, str]) -> None:
    """Ensure Claude CLI has credentials available.

    Args:
        env: Environment dictionary to check for ANTHROPIC_API_KEY.

    Raises:
        RuntimeError: If ANTHROPIC_API_KEY is missing.
    """
    if env.get("ANTHROPIC_API_KEY"):
        return
    raise RuntimeError(
        "ANTHROPIC_API_KEY is missing. Configure the 'anthropic-secret' "
        "Modal secret so Claude CLI can authenticate."
    )


def claude_cli_ids() -> tuple[int, int]:
    """Get UID and GID for the Claude CLI user.

    Returns:
        Tuple of (uid, gid) for the claude user.

    Raises:
        RuntimeError: If the claude user is not found in the system.
    """
    try:
        entry = pwd.getpwnam(CLAUDE_CLI_USER)
    except KeyError as exc:
        raise RuntimeError("Claude CLI user not found; rebuild the image to create it.") from exc
    return entry.pw_uid, entry.pw_gid


def demote_to_claude() -> Callable[[], None]:
    """Create a preexec_fn for subprocess that drops privileges to claude user.

    This function is used as preexec_fn in subprocess.run() to demote the
    process to the non-root claude user before executing the CLI.

    Returns:
        A callable that sets the process UID/GID to the claude user.
    """
    uid, gid = claude_cli_ids()

    def _inner() -> None:
        os.setgid(gid)
        if hasattr(os, "setgroups"):
            os.setgroups([gid])
        os.setuid(uid)

    return _inner


def maybe_chown_for_claude(path: Path) -> None:
    """Change ownership of a path to the claude user if possible.

    This is used to ensure the claude user can write to job workspaces.
    Failures are logged but not raised.

    Args:
        path: Path to change ownership of.
    """
    try:
        uid, gid = claude_cli_ids()
    except RuntimeError:
        _logger.warning("Claude CLI user missing; skipping workspace chown")
        return
    try:
        os.chown(path, uid, gid)
        path.chmod(0o775)
    except PermissionError:
        _logger.warning("Unable to chown workspace for Claude CLI user", exc_info=True)
