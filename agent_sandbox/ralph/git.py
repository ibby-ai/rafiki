"""Git operations for Ralph loops.

Handles git initialization, commits, log retrieval, and remote push operations.
"""

import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from agent_sandbox.utils.cli import claude_cli_env, demote_to_claude


def _git_subprocess_kwargs() -> dict[str, object]:
    """Return subprocess kwargs to run git as the claude user when possible."""
    if os.getuid() != 0:
        return {}
    try:
        return {
            "env": claude_cli_env(),
            "preexec_fn": demote_to_claude(),
        }
    except RuntimeError:
        return {}


def init_git(workspace: Path) -> None:
    """Initialize git repo in workspace.

    Skips git init if the workspace is already a git repository
    (e.g., from git_clone initialization), but always configures
    user identity for making commits.

    Args:
        workspace: Path to the workspace directory.
    """
    # Only run git init if not already a git repo
    git_dir = workspace / ".git"
    if not git_dir.exists():
        subprocess.run(
            ["git", "init"],
            cwd=workspace,
            check=True,
            capture_output=True,
            **_git_subprocess_kwargs(),
        )

    # Always configure user identity for commits (needed for both new and cloned repos)
    subprocess.run(
        ["git", "config", "user.email", "ibrahim.aka.ajax@gmail.com"],
        cwd=workspace,
        check=True,
        capture_output=True,
        **_git_subprocess_kwargs(),
    )
    subprocess.run(
        ["git", "config", "user.name", "Ibrahim Saidi"],
        cwd=workspace,
        check=True,
        capture_output=True,
        **_git_subprocess_kwargs(),
    )


def commit_changes(workspace: Path, message: str) -> str | None:
    """Stage all and commit. Returns commit SHA or None if nothing to commit.

    Args:
        workspace: Path to the workspace directory.
        message: Commit message.

    Returns:
        Short commit SHA (8 chars) if commit was made, None if nothing to commit.

    Raises:
        RuntimeError: If git commands fail, with the actual git error message.
    """
    # Stage all files
    result = subprocess.run(
        ["git", "add", "-A"],
        cwd=workspace,
        capture_output=True,
        text=True,
        **_git_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"git add failed: {result.stderr.strip() or result.stdout.strip()}")

    # Check if there are changes to commit
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=workspace,
        capture_output=True,
        text=True,
        **_git_subprocess_kwargs(),
    )
    if not result.stdout.strip():
        return None

    # Commit changes
    result = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=workspace,
        capture_output=True,
        text=True,
        **_git_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"git commit failed: {result.stderr.strip() or result.stdout.strip()}")

    # Get commit SHA
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        capture_output=True,
        text=True,
        **_git_subprocess_kwargs(),
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
        **_git_subprocess_kwargs(),
    )
    return result.stdout


def get_authenticated_url(repo_url: str, token: str) -> str:
    """Convert GitHub HTTPS URL to use token authentication.

    Example:
        https://github.com/user/repo.git
        -> https://x-access-token:TOKEN@github.com/user/repo.git

    Args:
        repo_url: The GitHub repository HTTPS URL.
        token: The GitHub Personal Access Token.

    Returns:
        URL with embedded token authentication.

    Raises:
        ValueError: If the URL does not use HTTPS.
    """
    parsed = urlparse(repo_url)
    if parsed.scheme != "https":
        raise ValueError(f"Remote URL must use HTTPS, got: {parsed.scheme}")
    # Use x-access-token format for GitHub PAT
    auth_netloc = f"x-access-token:{token}@{parsed.netloc}"
    return urlunparse(parsed._replace(netloc=auth_netloc))


def configure_remote(workspace: Path, remote_url: str, remote_name: str = "origin") -> None:
    """Add or update a git remote.

    Args:
        workspace: Path to the workspace directory.
        remote_url: The remote URL to set.
        remote_name: Name of the remote (default: "origin").
    """
    # Check if remote exists
    result = subprocess.run(
        ["git", "remote", "get-url", remote_name],
        cwd=workspace,
        capture_output=True,
        text=True,
        **_git_subprocess_kwargs(),
    )
    if result.returncode == 0:
        # Update existing remote
        subprocess.run(
            ["git", "remote", "set-url", remote_name, remote_url],
            cwd=workspace,
            check=True,
            capture_output=True,
            **_git_subprocess_kwargs(),
        )
    else:
        # Add new remote
        subprocess.run(
            ["git", "remote", "add", remote_name, remote_url],
            cwd=workspace,
            check=True,
            capture_output=True,
            **_git_subprocess_kwargs(),
        )


def push_to_remote(
    workspace: Path,
    branch: str = "main",
    remote_name: str = "origin",
    force: bool = False,
) -> None:
    """Push current HEAD to remote branch.

    Pushes the current HEAD to the specified remote branch. This allows
    pushing work done on any local branch (e.g., main) to a different
    remote branch name (e.g., ralph-output).

    Args:
        workspace: Path to the workspace directory.
        branch: Remote branch name to push to (default: "main").
        remote_name: Name of the remote (default: "origin").
        force: Whether to force push (default: False).

    Raises:
        subprocess.CalledProcessError: If the push fails.
    """
    # Use HEAD:branch to push current HEAD to the specified remote branch
    # This avoids requiring a local branch with the same name
    refspec = f"HEAD:refs/heads/{branch}"
    cmd = ["git", "push", remote_name, refspec]
    if force:
        cmd.insert(2, "--force")
    result = subprocess.run(
        cmd,
        cwd=workspace,
        capture_output=True,
        text=True,
        **_git_subprocess_kwargs(),
    )
    if result.returncode != 0:
        # Include the command in the error for debugging
        cmd_str = " ".join(cmd)
        raise subprocess.CalledProcessError(
            result.returncode,
            cmd_str,
            output=result.stdout,
            stderr=f"[cmd: {cmd_str}] {result.stderr}",
        )
