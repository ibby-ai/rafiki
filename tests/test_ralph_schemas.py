"""Tests for Ralph schemas."""

import pytest
from pydantic import ValidationError

from agent_sandbox.ralph.schemas import (
    IterationResult,
    IterationStatus,
    Prd,
    PrdItem,
    RalphLoopResult,
    RalphLoopStatus,
    RalphStartRequest,
    RalphStartResponse,
    RalphStatusResponse,
    WorkspaceSource,
)


class TestPrdItem:
    """Tests for PrdItem schema."""

    def test_minimal_prd_item(self):
        """Test PrdItem with only required fields."""
        item = PrdItem(id="1", category="functional", description="Test task")
        assert item.id == "1"
        assert item.category == "functional"
        assert item.description == "Test task"
        assert item.steps == []
        assert item.passes is False
        assert item.priority == 0

    def test_full_prd_item(self):
        """Test PrdItem with all fields."""
        item = PrdItem(
            id="task-1",
            category="technical",
            description="Implement feature X",
            steps=["Run tests", "Check coverage"],
            passes=True,
            priority=10,
        )
        assert item.id == "task-1"
        assert item.category == "technical"
        assert item.steps == ["Run tests", "Check coverage"]
        assert item.passes is True
        assert item.priority == 10

    def test_prd_item_serialization(self):
        """Test PrdItem JSON serialization."""
        item = PrdItem(id="1", category="quality", description="Test")
        data = item.model_dump()
        assert data["id"] == "1"
        assert data["passes"] is False

        # Round-trip
        item2 = PrdItem.model_validate(data)
        assert item2 == item


class TestPrd:
    """Tests for Prd schema."""

    def test_prd_with_single_story(self):
        """Test Prd with single user story."""
        prd = Prd(
            name="test-project",
            userStories=[PrdItem(id="1", category="functional", description="Task 1")],
        )
        assert prd.name == "test-project"
        assert len(prd.userStories) == 1

    def test_prd_with_multiple_stories(self):
        """Test Prd with multiple user stories."""
        prd = Prd(
            name="complex-project",
            userStories=[
                PrdItem(id="1", category="functional", description="Task 1", priority=1),
                PrdItem(id="2", category="technical", description="Task 2", priority=2),
                PrdItem(id="3", category="quality", description="Task 3", priority=0),
            ],
        )
        assert len(prd.userStories) == 3
        assert prd.userStories[1].priority == 2

    def test_prd_json_roundtrip(self):
        """Test Prd JSON serialization round-trip."""
        prd = Prd(
            name="test",
            userStories=[PrdItem(id="1", category="functional", description="Task")],
        )
        json_str = prd.model_dump_json()
        prd2 = Prd.model_validate_json(json_str)
        assert prd2.name == prd.name
        assert len(prd2.userStories) == len(prd.userStories)


class TestWorkspaceSource:
    """Tests for WorkspaceSource schema."""

    def test_default_workspace_source(self):
        """Test default WorkspaceSource is empty type."""
        source = WorkspaceSource()
        assert source.type == "empty"
        assert source.git_url is None
        assert source.git_branch is None
        assert source.template_path is None

    def test_git_clone_source(self):
        """Test git_clone workspace source."""
        source = WorkspaceSource(
            type="git_clone",
            git_url="https://github.com/user/repo.git",
            git_branch="main",
        )
        assert source.type == "git_clone"
        assert source.git_url == "https://github.com/user/repo.git"
        assert source.git_branch == "main"

    def test_template_source(self):
        """Test template workspace source."""
        source = WorkspaceSource(
            type="template",
            template_path="python-starter",
        )
        assert source.type == "template"
        assert source.template_path == "python-starter"

    def test_invalid_type_rejected(self):
        """Test that invalid type is rejected."""
        with pytest.raises(ValidationError):
            WorkspaceSource(type="invalid")


class TestIterationResult:
    """Tests for IterationResult schema."""

    def test_completed_iteration(self):
        """Test completed iteration result."""
        result = IterationResult(
            iteration=1,
            task_id="task-1",
            task_description="Create hello.py",
            status=IterationStatus.COMPLETED,
            cli_exit_code=0,
            feedback_passed=True,
            commit_sha="abc123",
        )
        assert result.iteration == 1
        assert result.status == IterationStatus.COMPLETED
        assert result.commit_sha == "abc123"
        assert result.error is None

    def test_failed_iteration(self):
        """Test failed iteration result."""
        result = IterationResult(
            iteration=2,
            task_id="task-2",
            status=IterationStatus.FAILED,
            cli_exit_code=1,
            feedback_passed=False,
            error="Test failed",
        )
        assert result.status == IterationStatus.FAILED
        assert result.error == "Test failed"
        assert result.commit_sha is None

    def test_iteration_with_cli_output(self):
        """Test iteration result with cli_output field."""
        result = IterationResult(
            iteration=1,
            task_id="task-1",
            status=IterationStatus.COMPLETED,
            cli_exit_code=0,
            feedback_passed=True,
            cli_output="This is the CLI output",
        )
        assert result.cli_output == "This is the CLI output"

    def test_iteration_cli_output_defaults_to_none(self):
        """Test cli_output defaults to None."""
        result = IterationResult(
            iteration=1,
            status=IterationStatus.RUNNING,
            cli_exit_code=0,
            feedback_passed=False,
        )
        assert result.cli_output is None


class TestRalphLoopResult:
    """Tests for RalphLoopResult schema."""

    def test_complete_result(self):
        """Test complete loop result."""
        prd = Prd(
            name="test",
            userStories=[PrdItem(id="1", category="functional", description="Task", passes=True)],
        )
        result = RalphLoopResult(
            job_id="job-123",
            status=RalphLoopStatus.COMPLETE,
            iterations_completed=3,
            iterations_max=10,
            tasks_completed=1,
            tasks_total=1,
            iteration_results=[],
            final_prd=prd,
        )
        assert result.status == RalphLoopStatus.COMPLETE
        assert result.tasks_completed == 1
        assert result.final_prd is not None

    def test_failed_result(self):
        """Test failed loop result."""
        result = RalphLoopResult(
            job_id="job-456",
            status=RalphLoopStatus.FAILED,
            iterations_completed=5,
            iterations_max=10,
            tasks_completed=2,
            tasks_total=5,
            error="Max consecutive failures reached",
        )
        assert result.status == RalphLoopStatus.FAILED
        assert result.error is not None

    def test_max_iterations_result(self):
        """Test max iterations reached result."""
        result = RalphLoopResult(
            job_id="job-789",
            status=RalphLoopStatus.MAX_ITERATIONS,
            iterations_completed=10,
            iterations_max=10,
            tasks_completed=3,
            tasks_total=5,
        )
        assert result.status == RalphLoopStatus.MAX_ITERATIONS


class TestRalphStartRequest:
    """Tests for RalphStartRequest schema."""

    def test_minimal_request(self):
        """Test request with only required fields."""
        prd = Prd(
            name="test",
            userStories=[PrdItem(id="1", category="functional", description="Task")],
        )
        request = RalphStartRequest(prd=prd)
        assert request.prd == prd
        assert request.workspace_source.type == "empty"
        assert request.max_iterations == 10
        assert request.timeout_per_iteration == 300
        assert request.allowed_tools == ["Read", "Write", "Bash", "Glob", "Grep"]
        assert request.feedback_commands == []
        assert request.auto_commit is True

    def test_full_request(self):
        """Test request with all fields."""
        prd = Prd(
            name="test",
            userStories=[PrdItem(id="1", category="functional", description="Task")],
        )
        request = RalphStartRequest(
            prd=prd,
            workspace_source=WorkspaceSource(
                type="git_clone",
                git_url="https://github.com/user/repo.git",
            ),
            max_iterations=20,
            timeout_per_iteration=600,
            first_iteration_timeout=900,
            allowed_tools=["Read", "Write"],
            feedback_commands=["npm run test"],
            feedback_timeout=180,
            auto_commit=False,
            max_consecutive_failures=5,
        )
        assert request.max_iterations == 20
        assert request.allowed_tools == ["Read", "Write"]
        assert request.feedback_commands == ["npm run test"]
        assert request.auto_commit is False
        assert request.first_iteration_timeout == 900

    def test_first_iteration_timeout_defaults_to_none(self):
        """Test first_iteration_timeout defaults to None."""
        prd = Prd(
            name="test",
            userStories=[PrdItem(id="1", category="functional", description="Task")],
        )
        request = RalphStartRequest(prd=prd)
        assert request.first_iteration_timeout is None

    def test_first_iteration_timeout_validation(self):
        """Test first_iteration_timeout validation constraints."""
        prd = Prd(
            name="test",
            userStories=[PrdItem(id="1", category="functional", description="Task")],
        )

        # Valid range: 60-3600
        request = RalphStartRequest(prd=prd, first_iteration_timeout=600)
        assert request.first_iteration_timeout == 600

        # Too low
        with pytest.raises(ValidationError):
            RalphStartRequest(prd=prd, first_iteration_timeout=30)

        # Too high
        with pytest.raises(ValidationError):
            RalphStartRequest(prd=prd, first_iteration_timeout=4000)

    def test_validation_constraints(self):
        """Test validation constraints on request fields."""
        prd = Prd(
            name="test",
            userStories=[PrdItem(id="1", category="functional", description="Task")],
        )

        # max_iterations must be >= 1
        with pytest.raises(ValidationError):
            RalphStartRequest(prd=prd, max_iterations=0)

        # max_iterations must be <= 100
        with pytest.raises(ValidationError):
            RalphStartRequest(prd=prd, max_iterations=101)

        # timeout_per_iteration must be >= 60
        with pytest.raises(ValidationError):
            RalphStartRequest(prd=prd, timeout_per_iteration=30)


class TestRalphStartResponse:
    """Tests for RalphStartResponse schema."""

    def test_response(self):
        """Test start response."""
        response = RalphStartResponse(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            call_id="fc-abc123",
        )
        assert response.job_id == "550e8400-e29b-41d4-a716-446655440000"
        assert response.call_id == "fc-abc123"
        assert response.status == "started"


class TestRalphStatusResponse:
    """Tests for RalphStatusResponse schema."""

    def test_running_status(self):
        """Test running status response."""
        response = RalphStatusResponse(
            job_id="job-123",
            status="running",
            current_iteration=3,
            max_iterations=10,
            tasks_completed=1,
            tasks_total=5,
            current_task="task-2",
        )
        assert response.status == "running"
        assert response.current_task == "task-2"
        assert response.result is None

    def test_complete_status_with_result(self):
        """Test complete status with result."""
        loop_result = RalphLoopResult(
            job_id="job-123",
            status=RalphLoopStatus.COMPLETE,
            iterations_completed=5,
            iterations_max=10,
            tasks_completed=5,
            tasks_total=5,
        )
        response = RalphStatusResponse(
            job_id="job-123",
            status="complete",
            current_iteration=5,
            max_iterations=10,
            tasks_completed=5,
            tasks_total=5,
            result=loop_result,
        )
        assert response.result is not None
        assert response.result.status == RalphLoopStatus.COMPLETE
