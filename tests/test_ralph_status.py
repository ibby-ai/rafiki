"""Tests for Ralph status file management."""

import json
from pathlib import Path

import pytest

from agent_sandbox.ralph.status import read_status, write_status


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    return ws


class TestWriteStatus:
    """Tests for write_status function."""

    def test_write_status_creates_file(self, workspace: Path):
        """Test write_status creates status.json file."""
        write_status(
            workspace,
            status="running",
            current_iteration=1,
            max_iterations=10,
            tasks_completed=0,
            tasks_total=5,
        )

        status_path = workspace / "status.json"
        assert status_path.exists()

    def test_write_status_content(self, workspace: Path):
        """Test write_status writes correct content."""
        write_status(
            workspace,
            status="running",
            current_iteration=3,
            max_iterations=10,
            tasks_completed=2,
            tasks_total=5,
            current_task="task-3",
        )

        status_path = workspace / "status.json"
        content = json.loads(status_path.read_text())

        assert content["status"] == "running"
        assert content["current_iteration"] == 3
        assert content["max_iterations"] == 10
        assert content["tasks_completed"] == 2
        assert content["tasks_total"] == 5
        assert content["current_task"] == "task-3"

    def test_write_status_without_current_task(self, workspace: Path):
        """Test write_status without current_task."""
        write_status(
            workspace,
            status="complete",
            current_iteration=5,
            max_iterations=10,
            tasks_completed=5,
            tasks_total=5,
        )

        status_path = workspace / "status.json"
        content = json.loads(status_path.read_text())

        assert content["current_task"] is None

    def test_write_status_overwrites(self, workspace: Path):
        """Test write_status overwrites existing file."""
        write_status(
            workspace,
            status="running",
            current_iteration=1,
            max_iterations=10,
            tasks_completed=0,
            tasks_total=5,
        )

        write_status(
            workspace,
            status="complete",
            current_iteration=10,
            max_iterations=10,
            tasks_completed=5,
            tasks_total=5,
        )

        status_path = workspace / "status.json"
        content = json.loads(status_path.read_text())

        assert content["status"] == "complete"
        assert content["current_iteration"] == 10

    def test_write_status_formatted_json(self, workspace: Path):
        """Test write_status writes formatted JSON."""
        write_status(
            workspace,
            status="running",
            current_iteration=1,
            max_iterations=10,
            tasks_completed=0,
            tasks_total=5,
        )

        status_path = workspace / "status.json"
        content = status_path.read_text()

        # Formatted JSON has newlines
        assert "\n" in content


class TestReadStatus:
    """Tests for read_status function."""

    def test_read_status_existing(self, workspace: Path):
        """Test reading existing status file."""
        write_status(
            workspace,
            status="running",
            current_iteration=3,
            max_iterations=10,
            tasks_completed=2,
            tasks_total=5,
            current_task="task-3",
        )

        status = read_status(workspace)

        assert status is not None
        assert status["status"] == "running"
        assert status["current_iteration"] == 3
        assert status["current_task"] == "task-3"

    def test_read_status_nonexistent(self, workspace: Path):
        """Test reading non-existent status returns None."""
        status = read_status(workspace)

        assert status is None

    def test_read_status_after_multiple_writes(self, workspace: Path):
        """Test reading status after multiple writes."""
        for i in range(1, 6):
            write_status(
                workspace,
                status="running",
                current_iteration=i,
                max_iterations=10,
                tasks_completed=i - 1,
                tasks_total=5,
                current_task=f"task-{i}",
            )

        status = read_status(workspace)

        assert status["current_iteration"] == 5
        assert status["current_task"] == "task-5"


class TestStatusIntegration:
    """Integration tests for status read/write."""

    def test_status_roundtrip(self, workspace: Path):
        """Test writing and reading status preserves all fields."""
        original = {
            "status": "running",
            "current_iteration": 7,
            "max_iterations": 15,
            "tasks_completed": 4,
            "tasks_total": 8,
            "current_task": "implement-feature-x",
        }

        write_status(workspace, **original)
        read_back = read_status(workspace)

        assert read_back == original

    def test_status_with_all_statuses(self, workspace: Path):
        """Test writing status with various status values."""
        statuses = ["running", "complete", "failed", "stopped", "max_iterations"]

        for status_value in statuses:
            write_status(
                workspace,
                status=status_value,
                current_iteration=1,
                max_iterations=10,
                tasks_completed=0,
                tasks_total=5,
            )

            status = read_status(workspace)
            assert status["status"] == status_value
