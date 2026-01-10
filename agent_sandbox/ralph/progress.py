"""Progress tracking for Ralph loops.

Maintains a progress.txt file that tracks iteration history and decisions.
Supports automatic file rotation when size exceeds limits.
"""

from datetime import UTC, datetime
from pathlib import Path


def init_progress(workspace: Path, prd_name: str) -> None:
    """Initialize progress file.

    Args:
        workspace: Path to the workspace directory.
        prd_name: Name of the PRD being worked on.
    """
    progress_path = workspace / "progress.txt"
    progress_path.write_text(f"# Ralph Progress Log: {prd_name}\n")


def append_progress(workspace: Path, entry: str, max_size_kb: int = 512) -> None:
    """Append entry to progress.txt with size rotation.

    If the progress file exceeds max_size_kb, it is archived with a timestamp
    and a new file is started.

    Args:
        workspace: Path to the workspace directory.
        entry: Log entry to append.
        max_size_kb: Maximum file size in KB before rotation (default: 512).
    """
    progress_path = workspace / "progress.txt"

    # Rotate if too large
    if progress_path.exists() and progress_path.stat().st_size > max_size_kb * 1024:
        archive = workspace / f"progress.{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.txt"
        progress_path.rename(archive)
        progress_path.write_text("# Ralph Progress Log (continued)\n")

    timestamp = datetime.now(UTC).isoformat()
    with progress_path.open("a") as f:
        f.write(f"\n## [{timestamp}]\n{entry}\n")


def read_progress(workspace: Path) -> str:
    """Read progress file contents.

    Args:
        workspace: Path to the workspace directory.

    Returns:
        Contents of progress.txt, or empty string if file doesn't exist.
    """
    progress_path = workspace / "progress.txt"
    if progress_path.exists():
        return progress_path.read_text()
    return ""
