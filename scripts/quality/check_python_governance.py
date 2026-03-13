"""Run the scoped Python code-quality governance checks."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_SCOPE = [
    "modal_backend/models",
    "modal_backend/security",
    "modal_backend/platform_services/webhooks.py",
    "modal_backend/api/serialization.py",
]


def require_binary(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise RuntimeError(f"Required binary not found on PATH: {name}")
    return binary


def run_command(command: list[str]) -> None:
    print(f"$ {' '.join(command)}")
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def main() -> int:
    try:
        python = sys.executable
        ruff = require_binary("ruff")
        lint_imports = require_binary("lint-imports")
        run_command([python, "scripts/quality/validate_code_quality_waivers.py"])
        run_command([python, "scripts/quality/check_python_boundary_config.py"])
        run_command(
            [
                ruff,
                "check",
                "--preview",
                "--select",
                "D,DOC",
                *PYTHON_SCOPE,
            ]
        )
        run_command([python, "-m", "mypy", "--config-file", "mypy-governance.ini"])
        run_command([lint_imports, "--config", ".importlinter"])
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    except Exception as exc:  # noqa: BLE001 - stable governance failure summary
        print(f"PYTHON_GOVERNANCE_FAILED: {exc}", file=sys.stderr)
        return 1

    print("PYTHON_GOVERNANCE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
