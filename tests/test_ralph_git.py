"""Tests for Ralph git operations."""

import subprocess
from pathlib import Path

import pytest

from agent_sandbox.ralph.git import commit_changes, get_git_log, init_git


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    return ws


@pytest.fixture
def git_workspace(workspace: Path) -> Path:
    """Create a workspace with initialized git repo."""
    init_git(workspace)
    return workspace


class TestInitGit:
    """Tests for init_git function."""

    def test_init_git_creates_repo(self, workspace: Path):
        """Test init_git creates .git directory."""
        init_git(workspace)

        git_dir = workspace / ".git"
        assert git_dir.exists()
        assert git_dir.is_dir()

    def test_init_git_sets_user_config(self, workspace: Path):
        """Test init_git sets user email and name."""
        init_git(workspace)

        # Check user.email
        result = subprocess.run(
            ["git", "config", "user.email"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "ralph@modal.local"

        # Check user.name
        result = subprocess.run(
            ["git", "config", "user.name"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "Ralph Wiggum"

    def test_init_git_skips_existing_repo(self, workspace: Path):
        """Test init_git skips if already a git repo."""
        # Initialize with different config
        subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "other@example.com"],
            cwd=workspace,
            check=True,
            capture_output=True,
        )

        # Call init_git
        init_git(workspace)

        # Config should NOT be changed
        result = subprocess.run(
            ["git", "config", "user.email"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "other@example.com"


class TestCommitChanges:
    """Tests for commit_changes function."""

    def test_commit_changes_creates_commit(self, git_workspace: Path):
        """Test commit_changes creates a commit."""
        # Create a file
        (git_workspace / "test.txt").write_text("hello")

        sha = commit_changes(git_workspace, "Add test file")

        assert sha is not None
        assert len(sha) == 8  # Short SHA

    def test_commit_changes_returns_none_if_nothing_to_commit(self, git_workspace: Path):
        """Test commit_changes returns None if nothing to commit."""
        sha = commit_changes(git_workspace, "Empty commit")

        assert sha is None

    def test_commit_changes_stages_all_files(self, git_workspace: Path):
        """Test commit_changes stages all files."""
        # Create multiple files
        (git_workspace / "file1.txt").write_text("content1")
        (git_workspace / "file2.txt").write_text("content2")
        (git_workspace / "subdir").mkdir()
        (git_workspace / "subdir" / "file3.txt").write_text("content3")

        sha = commit_changes(git_workspace, "Add all files")

        assert sha is not None

        # Verify files are committed
        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "HEAD"],
            cwd=git_workspace,
            capture_output=True,
            text=True,
        )
        files = result.stdout.strip().split("\n")
        assert "file1.txt" in files
        assert "file2.txt" in files
        assert "subdir/file3.txt" in files

    def test_commit_changes_with_modified_file(self, git_workspace: Path):
        """Test committing modified files."""
        # Initial commit
        (git_workspace / "test.txt").write_text("v1")
        commit_changes(git_workspace, "Initial")

        # Modify and commit
        (git_workspace / "test.txt").write_text("v2")
        sha = commit_changes(git_workspace, "Update")

        assert sha is not None

    def test_commit_changes_with_deleted_file(self, git_workspace: Path):
        """Test committing deleted files."""
        # Initial commit
        test_file = git_workspace / "test.txt"
        test_file.write_text("content")
        commit_changes(git_workspace, "Initial")

        # Delete and commit
        test_file.unlink()
        sha = commit_changes(git_workspace, "Delete file")

        assert sha is not None


class TestGetGitLog:
    """Tests for get_git_log function."""

    def test_get_git_log_empty_repo(self, git_workspace: Path):
        """Test git log on empty repo."""
        log = get_git_log(git_workspace)

        # Empty repo has no commits, log should be empty or error
        assert log == "" or "fatal" in log.lower()

    def test_get_git_log_with_commits(self, git_workspace: Path):
        """Test git log with commits."""
        (git_workspace / "file1.txt").write_text("content")
        commit_changes(git_workspace, "First commit")

        (git_workspace / "file2.txt").write_text("content")
        commit_changes(git_workspace, "Second commit")

        log = get_git_log(git_workspace)

        assert "First commit" in log
        assert "Second commit" in log

    def test_get_git_log_limit(self, git_workspace: Path):
        """Test git log with limit."""
        # Create 5 commits
        for i in range(5):
            (git_workspace / f"file{i}.txt").write_text(f"content{i}")
            commit_changes(git_workspace, f"Commit {i}")

        # Get only 2 commits
        log = get_git_log(git_workspace, n=2)
        lines = [line for line in log.strip().split("\n") if line]

        assert len(lines) == 2
