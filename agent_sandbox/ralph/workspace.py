"""Workspace initialization for Ralph loops.

Handles setting up the workspace from various sources: empty, git clone,
or template.

Workspace Source Types
----------------------

**Type: "empty" (default)**

    {"workspace_source": {"type": "empty"}}

- Does nothing beyond creating the directory
- Claude starts with a blank slate

**Type: "git_clone"**

    {
        "workspace_source": {
            "type": "git_clone",
            "git_url": "https://github.com/org/repo.git",
            "git_branch": "main"
        }
    }

- Clones the repo into the workspace directory
- Optionally checks out a specific branch (git_branch is optional)
- Claude works on existing code

**Type: "template"**

    {
        "workspace_source": {
            "type": "template",
            "template_path": "fastapi-starter"
        }
    }

- Copies from /data/templates/{template_path}/ into the workspace
- Requires pre-populated templates in the Agent SDK volume
- Useful for boilerplate projects

Note: git_clone was verified working with https://github.com/snarktank/ralph
See docs/ralph-git-clone-verification.md for details.
"""

import shutil
import subprocess
from pathlib import Path

from agent_sandbox.utils.cli import claude_cli_env, demote_to_claude

from .schemas import WorkspaceSource


def _git_subprocess_kwargs() -> dict[str, object]:
    """Return subprocess kwargs to run git as the claude user when possible.

    This mirrors the pattern in git.py to ensure consistent git ownership.
    """
    import os

    if os.getuid() != 0:
        return {}
    try:
        return {
            "env": claude_cli_env(),
            "preexec_fn": demote_to_claude(),
        }
    except RuntimeError:
        return {}


def initialize_workspace(workspace: Path, source: WorkspaceSource) -> None:
    """Initialize workspace with source code.

    Args:
        workspace: Path to the workspace directory to initialize.
        source: WorkspaceSource configuration specifying how to initialize.

    Raises:
        FileNotFoundError: If template source is specified but template doesn't exist.
        subprocess.CalledProcessError: If git clone fails.
    """
    workspace.mkdir(parents=True, exist_ok=True)

    if source.type == "git_clone" and source.git_url:
        branch_args = ["-b", source.git_branch] if source.git_branch else []
        subprocess.run(
            ["git", "clone", *branch_args, source.git_url, "."],
            cwd=workspace,
            check=True,
            capture_output=True,
            **_git_subprocess_kwargs(),
        )
    elif source.type == "template" and source.template_path:
        template = Path(f"/data/templates/{source.template_path}")
        if template.exists():
            shutil.copytree(template, workspace, dirs_exist_ok=True)
        else:
            raise FileNotFoundError(f"Template not found: {template}")
    # "empty" type: workspace already created, nothing more to do
