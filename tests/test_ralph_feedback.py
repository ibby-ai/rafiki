"""Tests for Ralph feedback loop execution."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_sandbox.ralph.feedback import (
    ALLOWED_FEEDBACK_COMMANDS,
    FeedbackResult,
    run_feedback_loops,
    validate_feedback_commands,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    return ws


class TestValidateFeedbackCommands:
    """Tests for validate_feedback_commands function."""

    def test_allowed_commands_pass(self):
        """Test that allowlisted commands are accepted."""
        commands = ["pytest", "npm run test", "ruff check ."]
        result = validate_feedback_commands(commands)

        assert result == ["pytest", "npm run test", "ruff check ."]

    def test_disallowed_command_raises(self):
        """Test that non-allowlisted commands raise ValueError."""
        commands = ["rm -rf /"]

        with pytest.raises(ValueError, match="Disallowed feedback command"):
            validate_feedback_commands(commands)

    def test_empty_commands_list(self):
        """Test empty command list returns empty."""
        result = validate_feedback_commands([])
        assert result == []

    def test_whitespace_commands_filtered(self):
        """Test whitespace-only commands are filtered."""
        commands = ["pytest", "  ", "", "npm run test"]
        result = validate_feedback_commands(commands)

        assert result == ["pytest", "npm run test"]

    def test_npm_run_with_simple_script(self):
        """Test npm run with simple alphanumeric script names."""
        commands = ["npm run build", "npm run my_script", "npm run test-coverage"]
        result = validate_feedback_commands(commands)

        assert len(result) == 3

    def test_npm_run_with_dangerous_script(self):
        """Test npm run with dangerous script names is rejected."""
        commands = ["npm run test; rm -rf /"]

        with pytest.raises(ValueError, match="Disallowed script name"):
            validate_feedback_commands(commands)

    def test_pnpm_and_yarn_supported(self):
        """Test pnpm and yarn run commands are supported."""
        commands = ["pnpm run test", "yarn run lint"]
        result = validate_feedback_commands(commands)

        assert len(result) == 2

    def test_all_allowed_commands(self):
        """Test that all commands in ALLOWED_FEEDBACK_COMMANDS are valid."""
        result = validate_feedback_commands(list(ALLOWED_FEEDBACK_COMMANDS))
        assert len(result) == len(ALLOWED_FEEDBACK_COMMANDS)


class TestFeedbackResult:
    """Tests for FeedbackResult dataclass."""

    def test_passed_result(self):
        """Test creating a passed result."""
        result = FeedbackResult(passed=True, output="All tests passed")
        assert result.passed is True
        assert result.output == "All tests passed"
        assert result.failed_command is None

    def test_failed_result(self):
        """Test creating a failed result."""
        result = FeedbackResult(
            passed=False,
            output="Test failed",
            failed_command="pytest",
        )
        assert result.passed is False
        assert result.failed_command == "pytest"


class TestRunFeedbackLoops:
    """Tests for run_feedback_loops function."""

    @patch("agent_sandbox.ralph.feedback.subprocess.run")
    def test_successful_command(self, mock_run, workspace: Path):
        """Test running a successful command."""
        mock_run.return_value = MagicMock(
            stdout="Success output",
            stderr="",
            returncode=0,
        )

        result = run_feedback_loops(workspace, ["pytest"])

        assert result.passed is True
        assert result.failed_command is None
        mock_run.assert_called_once()

    @patch("agent_sandbox.ralph.feedback.subprocess.run")
    def test_failing_command(self, mock_run, workspace: Path):
        """Test running a failing command."""
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="Test failed",
            returncode=1,
        )

        result = run_feedback_loops(workspace, ["pytest"])

        assert result.passed is False
        assert result.failed_command == "pytest"

    @patch("agent_sandbox.ralph.feedback.subprocess.run")
    def test_stops_on_first_failure(self, mock_run, workspace: Path):
        """Test that execution stops on first failing command."""
        # First command succeeds, second fails
        mock_run.side_effect = [
            MagicMock(stdout="OK", stderr="", returncode=0),
            MagicMock(stdout="", stderr="Failed", returncode=1),
            MagicMock(stdout="OK", stderr="", returncode=0),  # Should not be called
        ]

        result = run_feedback_loops(
            workspace,
            ["pytest", "pytest -v", "npm test"],
        )

        assert result.passed is False
        assert result.failed_command == "pytest -v"
        # Should have called only twice (stopped after second failure)
        assert mock_run.call_count == 2

    def test_empty_commands_passes(self, workspace: Path):
        """Test empty command list returns passed."""
        result = run_feedback_loops(workspace, [])

        assert result.passed is True
        assert result.output == ""

    @patch("agent_sandbox.ralph.feedback.subprocess.run")
    def test_timeout_handling(self, mock_run, workspace: Path):
        """Test command timeout is handled."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pytest", timeout=1)

        result = run_feedback_loops(workspace, ["pytest"], timeout=1)

        assert result.passed is False
        assert "TIMEOUT" in result.output
        assert result.failed_command == "pytest"

    @patch("agent_sandbox.ralph.feedback.subprocess.run")
    def test_output_captured(self, mock_run, workspace: Path):
        """Test that command output is captured."""
        mock_run.return_value = MagicMock(
            stdout="pytest version 7.4.0",
            stderr="",
            returncode=0,
        )

        result = run_feedback_loops(workspace, ["pytest"])

        assert "pytest" in result.output.lower()

    def test_disallowed_command_raises(self, workspace: Path):
        """Test disallowed command raises ValueError."""
        with pytest.raises(ValueError, match="Disallowed"):
            run_feedback_loops(workspace, ["cat /etc/passwd"])


class TestAllowedFeedbackCommands:
    """Tests for ALLOWED_FEEDBACK_COMMANDS constant."""

    def test_common_test_commands_present(self):
        """Test common test commands are in allowlist."""
        assert "pytest" in ALLOWED_FEEDBACK_COMMANDS
        assert "npm run test" in ALLOWED_FEEDBACK_COMMANDS
        assert "npm test" in ALLOWED_FEEDBACK_COMMANDS

    def test_common_lint_commands_present(self):
        """Test common lint commands are in allowlist."""
        assert "npm run lint" in ALLOWED_FEEDBACK_COMMANDS
        assert "ruff check ." in ALLOWED_FEEDBACK_COMMANDS

    def test_common_build_commands_present(self):
        """Test common build commands are in allowlist."""
        assert "npm run build" in ALLOWED_FEEDBACK_COMMANDS
        assert "cargo check" in ALLOWED_FEEDBACK_COMMANDS

    def test_no_dangerous_commands(self):
        """Test no dangerous commands are in allowlist."""
        dangerous = ["rm", "mv", "cp", "chmod", "chown", "sudo", "curl", "wget"]
        for cmd in ALLOWED_FEEDBACK_COMMANDS:
            for danger in dangerous:
                assert not cmd.startswith(danger)
