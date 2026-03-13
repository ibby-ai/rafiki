"""Validate that canonical code-quality governance docs are indexed and wired."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_SNIPPETS = {
    "docs/product-specs/index.md": "code-quality-governance.md",
    "docs/references/index.md": "Code Quality Governance",
    "docs/AGENT_COLLABORATION_PROCESS.md": "boundary-enforcer",
    "ARCHITECTURE.md": "Code quality governance",
}

EXEC_PLAN_INDEX_SNIPPETS: tuple[str, ...] = (
    "active/code-quality-governance/PLAN_code-quality-governance.md",
    "completed/code-quality-governance/PLAN_code-quality-governance.md",
)


def contains_any(content: str, snippets: Iterable[str]) -> bool:
    """Return True when any snippet is present in the provided content."""

    return any(snippet in content for snippet in snippets)


def main() -> int:
    missing: list[str] = []
    for relative_path, snippet in REQUIRED_SNIPPETS.items():
        content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        if snippet not in content:
            missing.append(f"{relative_path} missing snippet: {snippet}")
    exec_plan_index = (REPO_ROOT / "docs/exec-plans/index.md").read_text(encoding="utf-8")
    if not contains_any(exec_plan_index, EXEC_PLAN_INDEX_SNIPPETS):
        snippets = " or ".join(EXEC_PLAN_INDEX_SNIPPETS)
        missing.append(f"docs/exec-plans/index.md missing snippet: {snippets}")
    if missing:
        print("DOCS_GOVERNANCE_INVALID", file=sys.stderr)
        for item in missing:
            print(item, file=sys.stderr)
        return 1
    print("DOCS_GOVERNANCE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
