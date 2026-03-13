"""Run the governance validation bundle and write a proof artifact."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_ROOT = REPO_ROOT / "edge-control-plane"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "generated"
MAX_CAPTURE_CHARS = 4000
KNOWN_PRE_EXISTING_PYTEST_FAILURES = frozenset(
    {
        "tests/test_controller_rollout.py::test_ensure_active_pointer_from_registry_recovers_single_active_service",
        "tests/test_controller_rollout.py::test_get_or_start_background_sandbox_bootstraps_after_stale_registry_recovery_fails_closed",
        "tests/test_controller_rollout.py::test_get_or_start_background_sandbox_aio_bootstraps_after_stale_registry_recovery_fails_closed",
        "tests/test_controller_rollout.py::test_persist_active_controller_pointer_allows_only_one_concurrent_writer",
        "tests/test_controller_rollout.py::test_schedule_controller_drain_spawned_path_matches_inline_terminal_state",
        "tests/test_sandbox_auth_header.py::test_reuse_by_name_missing_scoped_secret_falls_back_to_create",
        "tests/test_sandbox_auth_header.py::test_attach_missing_scoped_secret_retries_then_fails",
        "tests/test_sandbox_auth_header.py::test_async_warm_pool_claim_uses_poll_aio",
        "tests/test_sandbox_auth_header.py::test_tunnel_discovery_failure_retries_then_fails",
    }
)


@dataclass
class ProofCommand:
    name: str
    category: Literal["baseline", "governance"]
    command: list[str]
    cwd: Path


COMMANDS: list[ProofCommand] = [
    ProofCommand(
        name="validate_code_quality_waivers",
        category="governance",
        command=[sys.executable, "scripts/quality/validate_code_quality_waivers.py"],
        cwd=REPO_ROOT,
    ),
    ProofCommand(
        name="check_docs_governance",
        category="governance",
        command=[sys.executable, "scripts/quality/check_docs_governance.py"],
        cwd=REPO_ROOT,
    ),
    ProofCommand(
        name="check_python_governance",
        category="governance",
        command=[sys.executable, "scripts/quality/check_python_governance.py"],
        cwd=REPO_ROOT,
    ),
    ProofCommand(
        name="ruff_check",
        category="baseline",
        command=[sys.executable, "-m", "ruff", "check", "."],
        cwd=REPO_ROOT,
    ),
    ProofCommand(
        name="pytest",
        category="baseline",
        command=[sys.executable, "-m", "pytest"],
        cwd=REPO_ROOT,
    ),
    ProofCommand(
        name="worker_check",
        category="baseline",
        command=["npm", "--prefix", "edge-control-plane", "run", "check"],
        cwd=REPO_ROOT,
    ),
    ProofCommand(
        name="worker_tsc",
        category="baseline",
        command=["./node_modules/.bin/tsc", "--noEmit"],
        cwd=EDGE_ROOT,
    ),
    ProofCommand(
        name="worker_integration_tests",
        category="baseline",
        command=["npm", "--prefix", "edge-control-plane", "run", "test:integration"],
        cwd=REPO_ROOT,
    ),
    ProofCommand(
        name="worker_contract_tests",
        category="governance",
        command=["npm", "--prefix", "edge-control-plane", "run", "check:contracts"],
        cwd=REPO_ROOT,
    ),
    ProofCommand(
        name="worker_api_docs",
        category="governance",
        command=["npm", "--prefix", "edge-control-plane", "run", "docs:api"],
        cwd=REPO_ROOT,
    ),
    ProofCommand(
        name="worker_boundaries",
        category="governance",
        command=["npm", "--prefix", "edge-control-plane", "run", "check:boundaries"],
        cwd=REPO_ROOT,
    ),
]


def timestamp_slug() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H-%M-%S%z")


def default_output_path() -> Path:
    return DEFAULT_OUTPUT_DIR / f"code-quality-governance-proof-{timestamp_slug()}.json"


def truncate(value: str) -> str:
    if len(value) <= MAX_CAPTURE_CHARS:
        return value
    return value[-MAX_CAPTURE_CHARS:]


def extract_pytest_failed_tests(output: str) -> set[str]:
    return {match.group(1) for match in re.finditer(r"^FAILED\s+(\S+::\S+)", output, re.MULTILINE)}


def classify_command_result(
    command_name: str,
    passed: bool,
    stdout: str,
    stderr: str,
) -> dict[str, object]:
    if passed:
        return {
            "evidence": [],
            "kind": "passed",
            "reason": "Command passed.",
        }

    if command_name == "pytest":
        failed_tests = extract_pytest_failed_tests(f"{stdout}\n{stderr}")
        if failed_tests and failed_tests.issubset(KNOWN_PRE_EXISTING_PYTEST_FAILURES):
            return {
                "evidence": sorted(failed_tests),
                "kind": "pre_existing_unrelated",
                "reason": (
                    "Current pytest failures are limited to the known pre-existing "
                    "controller rollout and sandbox auth suites outside wave-1 "
                    "governance scope."
                ),
            }

    return {
        "evidence": [],
        "kind": "unclassified_blocker",
        "reason": "Command failed without an approved advisory, waiver, or baseline classification.",
    }


def git_dirty_worktree() -> bool:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return bool(result.stdout.strip())


def run_bundle() -> tuple[list[dict[str, object]], bool]:
    results: list[dict[str, object]] = []
    all_passed = True

    for item in COMMANDS:
        completed = subprocess.run(
            item.command,
            cwd=item.cwd,
            capture_output=True,
            check=False,
            text=True,
        )
        passed = completed.returncode == 0
        classification = classify_command_result(
            item.name,
            passed,
            completed.stdout,
            completed.stderr,
        )
        all_passed = all_passed and passed
        results.append(
            {
                **asdict(item),
                "classification": classification,
                "cwd": str(item.cwd),
                "passed": passed,
                "returncode": completed.returncode,
                "stdout_tail": truncate(completed.stdout),
                "stderr_tail": truncate(completed.stderr),
            }
        )
    return results, all_passed


def build_proof(results: list[dict[str, object]], all_passed: bool) -> dict[str, object]:
    remaining_failures = {
        "advisory_scope_exceptions": [],
        "pre_existing_unrelated_failures": [],
        "unclassified_blockers": [],
        "waived_exceptions": [],
    }
    rollout_checks_passed = True

    for result in results:
        classification = result["classification"]
        assert isinstance(classification, dict)
        kind = classification.get("kind")
        if kind == "passed":
            continue
        if kind == "advisory_scope_exception":
            remaining_failures["advisory_scope_exceptions"].append(result["name"])
            continue
        if kind == "pre_existing_unrelated":
            remaining_failures["pre_existing_unrelated_failures"].append(
                {
                    "command": result["name"],
                    "evidence": classification.get("evidence", []),
                    "reason": classification.get("reason"),
                }
            )
            continue
        if kind == "waived_exception":
            remaining_failures["waived_exceptions"].append(result["name"])
            continue

        remaining_failures["unclassified_blockers"].append(
            {
                "command": result["name"],
                "reason": classification.get("reason"),
            }
        )
        rollout_checks_passed = False

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "repo_root": str(REPO_ROOT),
        "proof_type": "code-quality-governance",
        "all_passed": all_passed,
        "rollout_checks_passed": rollout_checks_passed,
        "dirty_worktree": git_dirty_worktree(),
        "waiver_registry": "docs/references/code-quality-waivers.json",
        "blocking_scope": {
            "python": [
                "modal_backend/models/**",
                "modal_backend/security/**",
                "modal_backend/platform_services/webhooks.py",
                "modal_backend/api/serialization.py",
            ],
            "worker": [
                "edge-control-plane/src/auth/**",
                "edge-control-plane/src/contracts/**",
            ],
        },
        "remaining_failures": remaining_failures,
        "commands": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output_path(),
        help="Output path for the generated proof artifact.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    results, all_passed = run_bundle()
    proof = build_proof(results, all_passed)
    args.output.write_text(f"{json.dumps(proof, indent=2)}\n", encoding="utf-8")
    print(f"WROTE_CODE_QUALITY_PROOF {args.output}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
