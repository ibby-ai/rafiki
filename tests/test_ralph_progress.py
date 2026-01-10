"""Tests for Ralph progress tracking."""

from pathlib import Path

import pytest

from agent_sandbox.ralph.progress import append_progress, init_progress, read_progress


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    return ws


class TestInitProgress:
    """Tests for init_progress function."""

    def test_init_progress_creates_file(self, workspace: Path):
        """Test init_progress creates progress.txt file."""
        init_progress(workspace, "test-project")

        progress_path = workspace / "progress.txt"
        assert progress_path.exists()

    def test_init_progress_content(self, workspace: Path):
        """Test init_progress writes correct header."""
        init_progress(workspace, "my-cool-project")

        progress_path = workspace / "progress.txt"
        content = progress_path.read_text()

        assert "# Ralph Progress Log: my-cool-project" in content

    def test_init_progress_overwrites(self, workspace: Path):
        """Test init_progress overwrites existing file."""
        init_progress(workspace, "project-1")
        init_progress(workspace, "project-2")

        progress_path = workspace / "progress.txt"
        content = progress_path.read_text()

        assert "project-2" in content
        assert "project-1" not in content


class TestAppendProgress:
    """Tests for append_progress function."""

    def test_append_to_existing(self, workspace: Path):
        """Test appending to existing progress file."""
        init_progress(workspace, "test")
        append_progress(workspace, "Completed task 1")

        content = read_progress(workspace)
        assert "Completed task 1" in content

    def test_append_multiple_entries(self, workspace: Path):
        """Test appending multiple entries."""
        init_progress(workspace, "test")
        append_progress(workspace, "Entry 1")
        append_progress(workspace, "Entry 2")
        append_progress(workspace, "Entry 3")

        content = read_progress(workspace)
        assert "Entry 1" in content
        assert "Entry 2" in content
        assert "Entry 3" in content

    def test_append_includes_timestamp(self, workspace: Path):
        """Test that appended entries include timestamps."""
        init_progress(workspace, "test")
        append_progress(workspace, "Test entry")

        content = read_progress(workspace)
        # ISO timestamp format includes T separator
        assert "T" in content  # e.g., 2024-01-15T12:00:00

    def test_append_creates_file_if_missing(self, workspace: Path):
        """Test append creates file if progress.txt doesn't exist."""
        # Don't call init_progress first
        append_progress(workspace, "First entry")

        content = read_progress(workspace)
        assert "First entry" in content

    def test_rotation_on_size_limit(self, workspace: Path):
        """Test file rotation when size limit exceeded."""
        init_progress(workspace, "test")

        # Write enough to exceed 1KB limit
        large_entry = "X" * 500
        for _ in range(5):
            append_progress(workspace, large_entry, max_size_kb=1)

        # Check that archive file was created
        archives = list(workspace.glob("progress.*.txt"))
        assert len(archives) >= 1

        # Current progress file should be smaller
        progress_path = workspace / "progress.txt"
        assert progress_path.stat().st_size < 2 * 1024  # Less than 2KB


class TestReadProgress:
    """Tests for read_progress function."""

    def test_read_existing(self, workspace: Path):
        """Test reading existing progress file."""
        init_progress(workspace, "test")
        append_progress(workspace, "Test entry")

        content = read_progress(workspace)
        assert content != ""
        assert "test" in content.lower()

    def test_read_nonexistent(self, workspace: Path):
        """Test reading non-existent file returns empty string."""
        content = read_progress(workspace)
        assert content == ""

    def test_read_empty_file(self, workspace: Path):
        """Test reading empty file."""
        progress_path = workspace / "progress.txt"
        progress_path.write_text("")

        content = read_progress(workspace)
        assert content == ""
