"""Tests for Ralph main loop orchestrator."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_sandbox.ralph.loop import STOP_SIGNAL, build_prompt, run_cli, run_ralph_loop
from agent_sandbox.ralph.prd import read_prd
from agent_sandbox.ralph.schemas import (
    IterationStatus,
    Prd,
    PrdItem,
    RalphLoopStatus,
    WorkspaceSource,
)


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
                description="Create hello.py that prints Hello World",
                steps=["Run python hello.py", "Verify output"],
                priority=2,
            ),
            PrdItem(
                id="task-2",
                category="technical",
                description="Add unit tests",
                steps=["Run pytest"],
                priority=1,
            ),
        ],
    )


class TestBuildPrompt:
    """Tests for build_prompt function."""

    def test_build_prompt_with_steps(self):
        """Test building prompt with verification steps."""
        template = "Task: {task_id}\nDesc: {task_description}\nSteps:\n{task_steps}\nWork in: {workspace_path}"

        prompt = build_prompt(
            template,
            task_id="task-1",
            task_description="Create hello.py",
            task_steps=["Run tests", "Check output"],
            workspace_path="/data/jobs/test",
        )

        assert "task-1" in prompt
        assert "Create hello.py" in prompt
        assert "Run tests" in prompt
        assert "Check output" in prompt

    def test_build_prompt_without_steps(self):
        """Test building prompt without verification steps."""
        template = "Task: {task_id}\nSteps:\n{task_steps}\nPath: {workspace_path}"

        prompt = build_prompt(
            template,
            task_id="task-1",
            task_description="Test",
            task_steps=[],
            workspace_path="/tmp/workspace",
        )

        assert "(none specified)" in prompt

    def test_build_prompt_preserves_template(self):
        """Test that other template content is preserved."""
        template = "# Header\n{task_id}: {task_description}\n## Steps\n{task_steps}\n# Footer\n{workspace_path}"

        prompt = build_prompt(
            template,
            task_id="id",
            task_description="desc",
            task_steps=["step"],
            workspace_path="/workspace",
        )

        assert "# Header" in prompt
        assert "# Footer" in prompt


class TestRunCli:
    """Tests for run_cli function."""

    @patch("agent_sandbox.ralph.loop.subprocess.run")
    @patch("agent_sandbox.ralph.loop.claude_cli_env")
    @patch("agent_sandbox.ralph.loop.demote_to_claude")
    def test_run_cli_success(self, mock_demote, mock_env, mock_run, workspace: Path):
        """Test successful CLI execution."""
        workspace.mkdir(parents=True)
        mock_env.return_value = {"PATH": "/usr/bin"}
        mock_demote.return_value = lambda: None
        mock_run.return_value = MagicMock(
            stdout="Success output",
            stderr="",
            returncode=0,
        )

        output, exit_code = run_cli(
            workspace,
            prompt="Test prompt",
            allowed_tools=["Read", "Write"],
            timeout=60,
        )

        assert exit_code == 0
        assert "Success output" in output

    @patch("agent_sandbox.ralph.loop.subprocess.run")
    @patch("agent_sandbox.ralph.loop.claude_cli_env")
    @patch("agent_sandbox.ralph.loop.demote_to_claude")
    def test_run_cli_failure(self, mock_demote, mock_env, mock_run, workspace: Path):
        """Test failed CLI execution."""
        workspace.mkdir(parents=True)
        mock_env.return_value = {"PATH": "/usr/bin"}
        mock_demote.return_value = lambda: None
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="Error message",
            returncode=1,
        )

        output, exit_code = run_cli(
            workspace,
            prompt="Test prompt",
            allowed_tools=["Read"],
            timeout=60,
        )

        assert exit_code == 1
        assert "Error message" in output

    @patch("agent_sandbox.ralph.loop.subprocess.run")
    @patch("agent_sandbox.ralph.loop.claude_cli_env")
    @patch("agent_sandbox.ralph.loop.demote_to_claude")
    def test_run_cli_timeout(self, mock_demote, mock_env, mock_run, workspace: Path):
        """Test CLI timeout handling."""
        import subprocess

        workspace.mkdir(parents=True)
        mock_env.return_value = {"PATH": "/usr/bin"}
        mock_demote.return_value = lambda: None
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=60)

        output, exit_code = run_cli(
            workspace,
            prompt="Test prompt",
            allowed_tools=[],
            timeout=60,
        )

        assert exit_code == 124
        assert "timed out" in output.lower()

    @patch("agent_sandbox.ralph.loop.subprocess.run")
    @patch("agent_sandbox.ralph.loop.claude_cli_env")
    @patch("agent_sandbox.ralph.loop.demote_to_claude")
    def test_run_cli_exception(self, mock_demote, mock_env, mock_run, workspace: Path):
        """Test CLI exception handling."""
        workspace.mkdir(parents=True)
        mock_env.return_value = {"PATH": "/usr/bin"}
        mock_demote.return_value = lambda: None
        mock_run.side_effect = Exception("Unexpected error")

        output, exit_code = run_cli(
            workspace,
            prompt="Test prompt",
            allowed_tools=[],
            timeout=60,
        )

        assert exit_code == 1
        assert "Unexpected error" in output


class TestRunRalphLoop:
    """Tests for run_ralph_loop function."""

    @patch("agent_sandbox.ralph.loop.run_cli")
    def test_loop_completes_all_tasks(self, mock_run_cli, workspace: Path, sample_prd: Prd):
        """Test loop completes when all tasks are done."""

        # Mock CLI to succeed and simulate marking tasks complete
        def mock_cli_side_effect(*args, **kwargs):
            # Read current PRD and mark next task complete
            prd = read_prd(workspace)
            for item in prd.userStories:
                if not item.passes:
                    item.passes = True
                    break
            from agent_sandbox.ralph.prd import write_prd

            write_prd(workspace, prd)
            return ("Task completed", 0)

        mock_run_cli.side_effect = mock_cli_side_effect

        result = run_ralph_loop(
            job_id="test-job",
            prd=sample_prd,
            workspace=workspace,
            workspace_source=WorkspaceSource(type="empty"),
            max_iterations=10,
        )

        assert result.status == RalphLoopStatus.COMPLETE
        assert result.tasks_completed == 2
        assert result.tasks_total == 2

    @patch("agent_sandbox.ralph.loop.run_cli")
    def test_loop_stops_on_stop_signal(self, mock_run_cli, workspace: Path, sample_prd: Prd):
        """Test loop stops when STOP_SIGNAL is detected."""
        mock_run_cli.return_value = (f"Done! {STOP_SIGNAL}", 0)

        result = run_ralph_loop(
            job_id="test-job",
            prd=sample_prd,
            workspace=workspace,
            workspace_source=WorkspaceSource(type="empty"),
            max_iterations=10,
        )

        assert result.status == RalphLoopStatus.COMPLETE
        assert result.iterations_completed == 1

    @patch("agent_sandbox.ralph.loop.run_cli")
    @patch("agent_sandbox.ralph.loop.run_feedback_loops")
    def test_loop_reaches_max_iterations(
        self, mock_feedback, mock_run_cli, workspace: Path, sample_prd: Prd
    ):
        """Test loop stops at max iterations."""
        from agent_sandbox.ralph.feedback import FeedbackResult

        # CLI succeeds but feedback fails - so tasks don't get marked complete
        mock_run_cli.return_value = ("Partial progress", 0)
        mock_feedback.return_value = FeedbackResult(
            passed=False, output="Test failed", failed_command="pytest"
        )

        result = run_ralph_loop(
            job_id="test-job",
            prd=sample_prd,
            workspace=workspace,
            workspace_source=WorkspaceSource(type="empty"),
            max_iterations=3,
            feedback_commands=["pytest"],  # Enable feedback to block task completion
        )

        assert result.status == RalphLoopStatus.MAX_ITERATIONS
        assert result.iterations_completed == 3
        assert result.iterations_max == 3

    @patch("agent_sandbox.ralph.loop.run_cli")
    def test_loop_handles_cli_failures(self, mock_run_cli, workspace: Path, sample_prd: Prd):
        """Test loop continues after CLI failures."""
        # First call fails, second succeeds and completes both tasks
        call_count = 0

        def mock_cli(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ("CLI failed", 1)
            # Mark all tasks complete
            prd = read_prd(workspace)
            for item in prd.userStories:
                item.passes = True
            from agent_sandbox.ralph.prd import write_prd

            write_prd(workspace, prd)
            return ("Success", 0)

        mock_run_cli.side_effect = mock_cli

        result = run_ralph_loop(
            job_id="test-job",
            prd=sample_prd,
            workspace=workspace,
            workspace_source=WorkspaceSource(type="empty"),
            max_iterations=10,
            max_consecutive_failures=3,
        )

        assert result.status == RalphLoopStatus.COMPLETE
        assert len(result.iteration_results) == 2
        assert result.iteration_results[0].status == IterationStatus.FAILED

    @patch("agent_sandbox.ralph.loop.run_cli")
    def test_loop_fails_after_max_consecutive_failures(
        self, mock_run_cli, workspace: Path, sample_prd: Prd
    ):
        """Test loop fails after max consecutive failures."""
        mock_run_cli.return_value = ("CLI error", 1)

        result = run_ralph_loop(
            job_id="test-job",
            prd=sample_prd,
            workspace=workspace,
            workspace_source=WorkspaceSource(type="empty"),
            max_iterations=10,
            max_consecutive_failures=3,
        )

        assert result.status == RalphLoopStatus.FAILED
        assert "Max consecutive failures" in result.error
        assert len(result.iteration_results) == 3

    @patch("agent_sandbox.ralph.loop.run_cli")
    def test_loop_creates_git_commits(self, mock_run_cli, workspace: Path, sample_prd: Prd):
        """Test loop creates git commits on success."""
        # Complete one task per iteration
        iterations = 0

        def mock_cli(*args, **kwargs):
            nonlocal iterations
            iterations += 1
            prd = read_prd(workspace)
            # Mark first incomplete task as complete
            for item in prd.userStories:
                if not item.passes:
                    item.passes = True
                    break
            from agent_sandbox.ralph.prd import write_prd

            write_prd(workspace, prd)
            return ("Success", 0)

        mock_run_cli.side_effect = mock_cli

        result = run_ralph_loop(
            job_id="test-job",
            prd=sample_prd,
            workspace=workspace,
            workspace_source=WorkspaceSource(type="empty"),
            max_iterations=10,
            auto_commit=True,
        )

        # Check that commits were created
        completed_iterations = [
            r for r in result.iteration_results if r.status == IterationStatus.COMPLETED
        ]
        commits_made = [r for r in completed_iterations if r.commit_sha is not None]
        assert len(commits_made) >= 1

    @patch("agent_sandbox.ralph.loop.run_cli")
    def test_loop_skips_commits_when_disabled(self, mock_run_cli, workspace: Path, sample_prd: Prd):
        """Test loop skips commits when auto_commit=False."""

        def mock_cli(*args, **kwargs):
            prd = read_prd(workspace)
            for item in prd.userStories:
                item.passes = True
            from agent_sandbox.ralph.prd import write_prd

            write_prd(workspace, prd)
            return ("Success", 0)

        mock_run_cli.side_effect = mock_cli

        result = run_ralph_loop(
            job_id="test-job",
            prd=sample_prd,
            workspace=workspace,
            workspace_source=WorkspaceSource(type="empty"),
            max_iterations=10,
            auto_commit=False,
        )

        # All iterations should have no commit SHA
        for iteration in result.iteration_results:
            assert iteration.commit_sha is None

    @patch("agent_sandbox.ralph.loop.run_cli")
    @patch("agent_sandbox.ralph.loop.run_feedback_loops")
    def test_loop_runs_feedback_commands(
        self, mock_feedback, mock_run_cli, workspace: Path, sample_prd: Prd
    ):
        """Test loop runs feedback commands."""
        from agent_sandbox.ralph.feedback import FeedbackResult

        mock_run_cli.return_value = ("Success", 0)
        mock_feedback.return_value = FeedbackResult(passed=True, output="Tests passed")

        # Single task PRD
        single_task_prd = Prd(
            name="test",
            userStories=[PrdItem(id="1", category="functional", description="Task", passes=False)],
        )

        _result = run_ralph_loop(
            job_id="test-job",
            prd=single_task_prd,
            workspace=workspace,
            workspace_source=WorkspaceSource(type="empty"),
            max_iterations=5,
            feedback_commands=["pytest"],
        )

        # Feedback should have been called
        mock_feedback.assert_called()
        assert _result is not None  # Verify loop completed

    @patch("agent_sandbox.ralph.loop.run_cli")
    @patch("agent_sandbox.ralph.loop.run_feedback_loops")
    def test_loop_feedback_failure_marks_iteration_failed(
        self, mock_feedback, mock_run_cli, workspace: Path, sample_prd: Prd
    ):
        """Test feedback failure marks iteration as failed."""
        from agent_sandbox.ralph.feedback import FeedbackResult

        mock_run_cli.return_value = ("Success", 0)
        mock_feedback.return_value = FeedbackResult(
            passed=False, output="Tests failed", failed_command="pytest"
        )

        single_task_prd = Prd(
            name="test",
            userStories=[PrdItem(id="1", category="functional", description="Task", passes=False)],
        )

        result = run_ralph_loop(
            job_id="test-job",
            prd=single_task_prd,
            workspace=workspace,
            workspace_source=WorkspaceSource(type="empty"),
            max_iterations=1,
            feedback_commands=["pytest"],
        )

        assert result.iteration_results[0].feedback_passed is False

    def test_loop_initializes_workspace(self, workspace: Path, sample_prd: Prd):
        """Test loop initializes workspace correctly."""
        with patch("agent_sandbox.ralph.loop.run_cli") as mock_cli:
            mock_cli.return_value = (STOP_SIGNAL, 0)

            run_ralph_loop(
                job_id="test-job",
                prd=sample_prd,
                workspace=workspace,
                workspace_source=WorkspaceSource(type="empty"),
                max_iterations=1,
            )

        # Check workspace was created with expected files
        assert workspace.exists()
        assert (workspace / "prd.json").exists()
        assert (workspace / "progress.txt").exists()
        assert (workspace / ".git").exists()

    def test_loop_writes_status_file(self, workspace: Path, sample_prd: Prd):
        """Test loop writes status file during execution."""
        with patch("agent_sandbox.ralph.loop.run_cli") as mock_cli:
            # Check status file during CLI execution
            def check_status(*args, **kwargs):
                status_file = workspace / "status.json"
                assert status_file.exists()
                return (STOP_SIGNAL, 0)

            mock_cli.side_effect = check_status

            run_ralph_loop(
                job_id="test-job",
                prd=sample_prd,
                workspace=workspace,
                workspace_source=WorkspaceSource(type="empty"),
                max_iterations=1,
            )

    def test_loop_writes_final_status_on_complete(self, workspace: Path, sample_prd: Prd):
        """Test loop writes final status before returning complete."""
        with patch("agent_sandbox.ralph.loop.run_cli") as mock_cli:
            mock_cli.return_value = (STOP_SIGNAL, 0)

            result = run_ralph_loop(
                job_id="test-job",
                prd=sample_prd,
                workspace=workspace,
                workspace_source=WorkspaceSource(type="empty"),
                max_iterations=1,
            )

        assert result.status == RalphLoopStatus.COMPLETE
        # Verify final status was written
        from agent_sandbox.ralph.status import read_status

        status = read_status(workspace)
        assert status is not None
        assert status["status"] == "complete"
        assert status["current_task"] is None

    def test_loop_writes_final_status_on_max_iterations(self, workspace: Path, sample_prd: Prd):
        """Test loop writes final status when max iterations reached."""
        with patch("agent_sandbox.ralph.loop.run_cli") as mock_cli:
            mock_cli.return_value = ("Partial progress", 0)

            result = run_ralph_loop(
                job_id="test-job",
                prd=sample_prd,
                workspace=workspace,
                workspace_source=WorkspaceSource(type="empty"),
                max_iterations=1,
            )

        assert result.status == RalphLoopStatus.MAX_ITERATIONS
        from agent_sandbox.ralph.status import read_status

        status = read_status(workspace)
        assert status is not None
        assert status["status"] == "max_iterations"

    def test_loop_writes_final_status_on_failure(self, workspace: Path, sample_prd: Prd):
        """Test loop writes final status when max consecutive failures reached."""
        with patch("agent_sandbox.ralph.loop.run_cli") as mock_cli:
            mock_cli.return_value = ("CLI error", 1)

            result = run_ralph_loop(
                job_id="test-job",
                prd=sample_prd,
                workspace=workspace,
                workspace_source=WorkspaceSource(type="empty"),
                max_iterations=10,
                max_consecutive_failures=2,
            )

        assert result.status == RalphLoopStatus.FAILED
        from agent_sandbox.ralph.status import read_status

        status = read_status(workspace)
        assert status is not None
        assert status["status"] == "failed"

    def test_loop_captures_cli_output(self, workspace: Path, sample_prd: Prd):
        """Test loop captures CLI output in iteration results."""
        with patch("agent_sandbox.ralph.loop.run_cli") as mock_cli:
            # Mark all tasks complete
            def mock_cli_side_effect(*args, **kwargs):
                prd = read_prd(workspace)
                for item in prd.userStories:
                    item.passes = True
                from agent_sandbox.ralph.prd import write_prd

                write_prd(workspace, prd)
                return ("This is the CLI output", 0)

            mock_cli.side_effect = mock_cli_side_effect

            result = run_ralph_loop(
                job_id="test-job",
                prd=sample_prd,
                workspace=workspace,
                workspace_source=WorkspaceSource(type="empty"),
                max_iterations=5,
            )

        assert result.status == RalphLoopStatus.COMPLETE
        assert len(result.iteration_results) >= 1
        assert result.iteration_results[0].cli_output == "This is the CLI output"

    def test_loop_captures_cli_output_on_failure(self, workspace: Path, sample_prd: Prd):
        """Test loop captures CLI output even on failure."""
        with patch("agent_sandbox.ralph.loop.run_cli") as mock_cli:
            mock_cli.return_value = ("Error output from CLI", 1)

            result = run_ralph_loop(
                job_id="test-job",
                prd=sample_prd,
                workspace=workspace,
                workspace_source=WorkspaceSource(type="empty"),
                max_iterations=10,
                max_consecutive_failures=1,
            )

        assert result.status == RalphLoopStatus.FAILED
        assert result.iteration_results[0].cli_output == "Error output from CLI"

    def test_loop_truncates_long_cli_output(self, workspace: Path, sample_prd: Prd):
        """Test loop truncates CLI output longer than 2000 chars."""
        with patch("agent_sandbox.ralph.loop.run_cli") as mock_cli:
            long_output = "x" * 3000
            mock_cli.return_value = (long_output, 1)

            result = run_ralph_loop(
                job_id="test-job",
                prd=sample_prd,
                workspace=workspace,
                workspace_source=WorkspaceSource(type="empty"),
                max_iterations=10,
                max_consecutive_failures=1,
            )

        assert result.iteration_results[0].cli_output is not None
        assert len(result.iteration_results[0].cli_output) == 2000

    @patch("agent_sandbox.ralph.loop.run_cli")
    def test_loop_uses_first_iteration_timeout(
        self, mock_run_cli, workspace: Path, sample_prd: Prd
    ):
        """Test loop uses first_iteration_timeout for first iteration."""
        mock_run_cli.return_value = (STOP_SIGNAL, 0)

        run_ralph_loop(
            job_id="test-job",
            prd=sample_prd,
            workspace=workspace,
            workspace_source=WorkspaceSource(type="empty"),
            max_iterations=1,
            timeout_per_iteration=300,
            first_iteration_timeout=600,
        )

        # Check that run_cli was called with the first_iteration_timeout
        assert mock_run_cli.called
        call_args = mock_run_cli.call_args
        assert call_args.kwargs["timeout"] == 600


class TestBuildPromptWithWorkspace:
    """Tests for build_prompt with workspace_path."""

    def test_build_prompt_includes_workspace_path(self):
        """Test building prompt includes workspace path."""
        template = "Work in: {workspace_path}\nTask: {task_id}\n{task_description}\n{task_steps}"

        prompt = build_prompt(
            template,
            task_id="task-1",
            task_description="Create hello.py",
            task_steps=["Run tests"],
            workspace_path="/data/jobs/test-123",
        )

        assert "/data/jobs/test-123" in prompt
        assert "task-1" in prompt
        assert "Create hello.py" in prompt


class TestStopSignal:
    """Tests for STOP_SIGNAL constant."""

    def test_stop_signal_format(self):
        """Test STOP_SIGNAL has expected format."""
        assert "<promise>" in STOP_SIGNAL
        assert "COMPLETE" in STOP_SIGNAL
        assert "</promise>" in STOP_SIGNAL

    def test_stop_signal_detection(self):
        """Test STOP_SIGNAL can be detected in output."""
        output = f"Task completed successfully.\n{STOP_SIGNAL}\nDone."
        assert STOP_SIGNAL in output
