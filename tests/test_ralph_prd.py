"""Tests for Ralph PRD management."""

import json
from pathlib import Path

import pytest

from agent_sandbox.ralph.prd import (
    all_tasks_complete,
    get_next_task,
    is_task_complete,
    mark_task_complete,
    read_prd,
    write_prd,
)
from agent_sandbox.ralph.schemas import Prd, PrdItem


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    return tmp_path / "workspace"


@pytest.fixture
def sample_prd() -> Prd:
    """Create a sample PRD for testing."""
    return Prd(
        name="test-project",
        userStories=[
            PrdItem(
                id="task-1",
                category="functional",
                description="Create hello.py",
                steps=["Run python hello.py"],
                priority=2,
            ),
            PrdItem(
                id="task-2",
                category="technical",
                description="Add tests",
                steps=["Run pytest"],
                priority=1,
            ),
            PrdItem(
                id="task-3",
                category="quality",
                description="Add linting",
                priority=0,
            ),
        ],
    )


class TestWritePrd:
    """Tests for write_prd function."""

    def test_write_prd_creates_file(self, workspace: Path, sample_prd: Prd):
        """Test that write_prd creates prd.json file."""
        workspace.mkdir(parents=True)
        write_prd(workspace, sample_prd)

        prd_path = workspace / "prd.json"
        assert prd_path.exists()

    def test_write_prd_content(self, workspace: Path, sample_prd: Prd):
        """Test that write_prd writes correct content."""
        workspace.mkdir(parents=True)
        write_prd(workspace, sample_prd)

        prd_path = workspace / "prd.json"
        content = json.loads(prd_path.read_text())

        assert content["name"] == "test-project"
        assert len(content["userStories"]) == 3
        assert content["userStories"][0]["id"] == "task-1"

    def test_write_prd_overwrites(self, workspace: Path, sample_prd: Prd):
        """Test that write_prd overwrites existing file."""
        workspace.mkdir(parents=True)
        write_prd(workspace, sample_prd)

        # Modify and write again
        sample_prd.name = "modified-project"
        write_prd(workspace, sample_prd)

        prd_path = workspace / "prd.json"
        content = json.loads(prd_path.read_text())
        assert content["name"] == "modified-project"


class TestReadPrd:
    """Tests for read_prd function."""

    def test_read_prd(self, workspace: Path, sample_prd: Prd):
        """Test reading PRD from file."""
        workspace.mkdir(parents=True)
        write_prd(workspace, sample_prd)

        read_result = read_prd(workspace)
        assert read_result.name == sample_prd.name
        assert len(read_result.userStories) == 3

    def test_read_prd_missing_file(self, workspace: Path):
        """Test reading non-existent PRD raises error."""
        workspace.mkdir(parents=True)

        with pytest.raises(FileNotFoundError):
            read_prd(workspace)

    def test_read_prd_invalid_json(self, workspace: Path):
        """Test reading invalid JSON raises error."""
        workspace.mkdir(parents=True)
        prd_path = workspace / "prd.json"
        prd_path.write_text("not valid json")

        with pytest.raises(Exception):  # JSONDecodeError or ValidationError
            read_prd(workspace)


class TestMarkTaskComplete:
    """Tests for mark_task_complete function."""

    def test_mark_task_complete(self, workspace: Path, sample_prd: Prd):
        """Test marking a task as complete."""
        workspace.mkdir(parents=True)
        write_prd(workspace, sample_prd)

        updated_prd = mark_task_complete(workspace, "task-1")

        assert updated_prd.userStories[0].passes is True
        assert updated_prd.userStories[1].passes is False
        assert updated_prd.userStories[2].passes is False

    def test_mark_task_complete_persists(self, workspace: Path, sample_prd: Prd):
        """Test that mark_task_complete persists to file."""
        workspace.mkdir(parents=True)
        write_prd(workspace, sample_prd)

        mark_task_complete(workspace, "task-2")

        # Re-read from file
        prd = read_prd(workspace)
        assert prd.userStories[1].passes is True

    def test_mark_nonexistent_task(self, workspace: Path, sample_prd: Prd):
        """Test marking non-existent task doesn't crash."""
        workspace.mkdir(parents=True)
        write_prd(workspace, sample_prd)

        # Should not raise, just not mark anything
        updated_prd = mark_task_complete(workspace, "nonexistent")

        # All tasks should still be incomplete
        assert all(not t.passes for t in updated_prd.userStories)


class TestGetNextTask:
    """Tests for get_next_task function."""

    def test_get_next_task_by_priority(self, sample_prd: Prd):
        """Test get_next_task returns highest priority task."""
        task = get_next_task(sample_prd)

        assert task is not None
        assert task.id == "task-1"  # priority=2, highest
        assert task.priority == 2

    def test_get_next_task_skips_completed(self, sample_prd: Prd):
        """Test get_next_task skips completed tasks."""
        sample_prd.userStories[0].passes = True  # Complete highest priority

        task = get_next_task(sample_prd)

        assert task is not None
        assert task.id == "task-2"  # Next highest priority

    def test_get_next_task_all_complete(self, sample_prd: Prd):
        """Test get_next_task returns None when all complete."""
        for story in sample_prd.userStories:
            story.passes = True

        task = get_next_task(sample_prd)
        assert task is None

    def test_get_next_task_empty_prd(self):
        """Test get_next_task with empty PRD."""
        prd = Prd(name="empty", userStories=[])

        task = get_next_task(prd)
        assert task is None


class TestAllTasksComplete:
    """Tests for all_tasks_complete function."""

    def test_all_incomplete(self, sample_prd: Prd):
        """Test all_tasks_complete returns False when none complete."""
        assert all_tasks_complete(sample_prd) is False

    def test_some_complete(self, sample_prd: Prd):
        """Test all_tasks_complete returns False when some complete."""
        sample_prd.userStories[0].passes = True
        assert all_tasks_complete(sample_prd) is False

    def test_all_complete(self, sample_prd: Prd):
        """Test all_tasks_complete returns True when all complete."""
        for story in sample_prd.userStories:
            story.passes = True
        assert all_tasks_complete(sample_prd) is True

    def test_empty_prd(self):
        """Test all_tasks_complete with empty PRD."""
        prd = Prd(name="empty", userStories=[])
        assert all_tasks_complete(prd) is True


class TestIsTaskComplete:
    """Tests for is_task_complete function."""

    def test_incomplete_task(self, sample_prd: Prd):
        """Test is_task_complete returns False for incomplete task."""
        assert is_task_complete(sample_prd, "task-1") is False

    def test_complete_task(self, sample_prd: Prd):
        """Test is_task_complete returns True for complete task."""
        sample_prd.userStories[0].passes = True
        assert is_task_complete(sample_prd, "task-1") is True

    def test_nonexistent_task(self, sample_prd: Prd):
        """Test is_task_complete returns False for non-existent task."""
        assert is_task_complete(sample_prd, "nonexistent") is False
