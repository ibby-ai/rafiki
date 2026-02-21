# Git Remote Push Implementation Guide

This guide covers how to extend `modal_backend/ralph/git.py` to support pushing commits to remote repositories (GitHub, GitLab, Bitbucket, etc.).

## Current State

The Ralph git module only creates **local commits** within the Modal sandbox. Commits are stored in `/data/jobs/<job_id>/.git` but are not pushed anywhere.

## Implementation Requirements

### 1. Authentication Options

| Method | How it works | Security | Best for |
|--------|--------------|----------|----------|
| **SSH Deploy Key** | Mount a repo-specific key as a Modal Secret | Good - scoped to single repo | Single repo automation |
| **Personal Access Token** | Use HTTPS URL with token | Moderate - can scope permissions | Quick setup, multiple repos |
| **GitHub App** | Generate installation tokens on demand | Best - fine-grained, rotates automatically | Production, organization-wide |

### 2. New Functions to Add

```python
def configure_remote(workspace: Path, remote_url: str, remote_name: str = "origin") -> None:
    """Add a git remote to the workspace.

    Args:
        workspace: Path to the workspace directory.
        remote_url: URL of the remote repository (HTTPS or SSH).
        remote_name: Name for the remote (default: origin).
    """
    # Check if remote already exists
    result = subprocess.run(
        ["git", "remote", "get-url", remote_name],
        cwd=workspace,
        capture_output=True,
    )
    if result.returncode == 0:
        # Remote exists, update it
        subprocess.run(
            ["git", "remote", "set-url", remote_name, remote_url],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
    else:
        # Add new remote
        subprocess.run(
            ["git", "remote", "add", remote_name, remote_url],
            cwd=workspace,
            check=True,
            capture_output=True,
        )


def push_to_remote(
    workspace: Path,
    branch: str = "main",
    remote_name: str = "origin",
    force: bool = False,
) -> None:
    """Push commits to remote repository.

    Args:
        workspace: Path to the workspace directory.
        branch: Branch to push.
        remote_name: Name of the remote.
        force: Whether to force push (use with caution).
    """
    cmd = ["git", "push", "-u", remote_name, branch]
    if force:
        cmd.insert(2, "--force")

    subprocess.run(cmd, cwd=workspace, check=True, capture_output=True)
```

### 3. Secret Management

#### Store credentials in Modal

```bash
# Option A: GitHub Personal Access Token
modal secret create github-token GITHUB_TOKEN=ghp_xxxxxxxxxxxx

# Option B: SSH deploy key
modal secret create github-ssh-key SSH_PRIVATE_KEY="$(cat ~/.ssh/deploy_key)"

# Option C: GitHub App credentials
modal secret create github-app \
  GITHUB_APP_ID=123456 \
  GITHUB_PRIVATE_KEY="$(cat private-key.pem)"
```

#### Mount secrets in app.py

```python
@app.function(
    secrets=[modal.Secret.from_name("github-token")],
    ...
)
def run_ralph_remote(...):
    # Token available as os.environ["GITHUB_TOKEN"]
    pass
```

### 4. SSH Key Setup

For SSH authentication, configure the key in the container:

```python
import os
from pathlib import Path

def setup_ssh_key() -> None:
    """Configure SSH key for git operations."""
    ssh_key = os.environ.get("SSH_PRIVATE_KEY")
    if not ssh_key:
        return

    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)

    # Write the private key
    key_path = ssh_dir / "id_ed25519"
    key_path.write_text(ssh_key)
    key_path.chmod(0o600)

    # Configure SSH to accept GitHub's host key
    config_path = ssh_dir / "config"
    config_path.write_text(
        "Host github.com\n"
        "  StrictHostKeyChecking no\n"
        "  UserKnownHostsFile /dev/null\n"
    )
    config_path.chmod(0o600)
```

### 5. HTTPS with Token

For PAT authentication, embed the token in the URL:

```python
def get_authenticated_url(repo_url: str, token: str) -> str:
    """Convert a GitHub URL to use token authentication.

    Args:
        repo_url: Original repo URL (e.g., https://github.com/user/repo.git)
        token: GitHub Personal Access Token

    Returns:
        URL with embedded token (e.g., https://x-access-token:TOKEN@github.com/user/repo.git)
    """
    if repo_url.startswith("https://github.com/"):
        return repo_url.replace(
            "https://github.com/",
            f"https://x-access-token:{token}@github.com/"
        )
    return repo_url
```

### 6. Integration with Ralph Loop

Add options to `RalphStartRequest` schema:

```python
class RalphStartRequest(BaseModel):
    # ... existing fields ...

    # Remote push options
    push_on_complete: bool = False
    remote_url: str | None = None
    target_branch: str = "ralph-output"
    create_pr: bool = False
```

Update `run_ralph_loop()` to push after completion:

```python
# At the end of run_ralph_loop(), after all tasks complete:
if push_on_complete and remote_url:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        auth_url = get_authenticated_url(remote_url, token)
        configure_remote(workspace, auth_url)
    else:
        setup_ssh_key()
        configure_remote(workspace, remote_url)

    push_to_remote(workspace, branch=target_branch)
```

## Design Considerations

### Branch Strategy

| Option | Pros | Cons |
|--------|------|------|
| Push to `main` | Simple | Dangerous, may break things |
| Push to feature branch | Safe, reviewable | Requires PR merge |
| Create PR automatically | Full workflow | Requires GitHub API integration |

### Conflict Handling

If the remote has new commits:

```python
def push_to_remote(..., allow_rebase: bool = False):
    try:
        subprocess.run(["git", "push", ...], check=True, ...)
    except subprocess.CalledProcessError:
        if allow_rebase:
            subprocess.run(["git", "pull", "--rebase", remote_name, branch], ...)
            subprocess.run(["git", "push", ...], check=True, ...)
        else:
            raise
```

### Security Considerations

1. **Scope tokens minimally** - Use fine-grained PATs with only `contents: write` permission
2. **Use deploy keys for single repos** - They can't access other repos
3. **Rotate secrets regularly** - Set expiration on PATs
4. **Audit push access** - Log what repos Ralph pushes to
5. **Consider branch protection** - Require PR reviews even for Ralph's changes

## Future Enhancements

- [ ] GitHub App integration for automatic token rotation
- [ ] Create Pull Requests via GitHub API
- [ ] Support for GitLab/Bitbucket APIs
- [ ] Signed commits (GPG)
- [ ] Branch protection bypass for bot accounts
