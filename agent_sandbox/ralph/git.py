"""Git operations for Ralph loops.

Handles git initialization, commits, and log retrieval.

TODO: Add remote push support. See .agent/git-remote-push-guide.md for
implementation details including authentication options (SSH keys, PATs,
GitHub Apps), secret management, and branch strategy considerations.
"""

import subprocess
from pathlib import Path


def init_git(workspace: Path) -> None:
    """Initialize git repo in workspace.

    Skips initialization if the workspace is already a git repository
    (e.g., from git_clone initialization).

    Args:
        workspace: Path to the workspace directory.
    """
    # Skip if already a git repo (e.g., from git_clone)
    git_dir = workspace / ".git"
    if git_dir.exists():
        return

    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "ralph@modal.local"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Ralph Wiggum"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )


def commit_changes(workspace: Path, message: str) -> str | None:
    """Stage all and commit. Returns commit SHA or None if nothing to commit.

    Args:
        workspace: Path to the workspace directory.
        message: Commit message.

    Returns:
        Short commit SHA (8 chars) if commit was made, None if nothing to commit.
    """
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True, capture_output=True)

    # Check if there are changes to commit
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=workspace, capture_output=True, text=True
    )
    if not result.stdout.strip():
        return None

    subprocess.run(["git", "commit", "-m", message], cwd=workspace, check=True, capture_output=True)

    # Get commit SHA
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True, text=True
    )
    return result.stdout.strip()[:8]


def get_git_log(workspace: Path, n: int = 10) -> str:
    """Get recent git log.

    Args:
        workspace: Path to the workspace directory.
        n: Number of commits to retrieve.

    Returns:
        Git log output as string.
    """
    result = subprocess.run(
        ["git", "log", f"-{n}", "--oneline"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    return result.stdout
