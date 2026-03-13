"""Validate the code-quality waiver registry and scoped suppression usage."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WAIVER_PATH = REPO_ROOT / "docs" / "references" / "code-quality-waivers.json"
KNOWN_RULES = {
    "python-docstrings",
    "python-import-linter",
    "python-mypy-governance",
    "worker-dependency-cruiser",
    "worker-typedoc",
    "worker-zod-contracts",
}
SCOPED_GLOBS = (
    "modal_backend/models/**/*.py",
    "modal_backend/security/**/*.py",
    "modal_backend/platform_services/webhooks.py",
    "modal_backend/api/serialization.py",
    "edge-control-plane/src/auth/**/*.ts",
    "edge-control-plane/src/contracts/**/*.ts",
)
TYPE_IGNORE_RE = re.compile(r"#\s*type:\s*ignore")
DOCSTRING_NOQA_RE = re.compile(r"#\s*noqa\b(?=.*\b(?:D|DOC)\d*)")
NOQA_RE = re.compile(r"#\s*noqa\b")
PYRIGHT_IGNORE_RE = re.compile(r"#\s*pyright:\s*ignore")
TS_IGNORE_RE = re.compile(r"//\s*@ts-ignore")
TS_EXPECT_ERROR_RE = re.compile(r"//\s*@ts-expect-error")
BIOME_IGNORE_RE = re.compile(r"biome-ignore")
ESLINT_DISABLE_RE = re.compile(r"eslint-disable")
WAIVER_MARKER_RE = re.compile(r"code-quality-waiver:\s*([A-Za-z0-9._-]+)")


@dataclass(frozen=True)
class WaiverEntry:
    """Validated waiver metadata used for suppression auditing."""

    expires_on: str
    id: str
    owner: str
    reason: str
    rule: str
    scope: str
    tracking_ref: str


def load_registry(path: Path = WAIVER_PATH) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Waiver registry must be a JSON object")
    if data.get("version") != 1:
        raise ValueError("Waiver registry version must be 1")
    waivers = data.get("waivers")
    if not isinstance(waivers, list):
        raise ValueError("Waiver registry must include a waivers list")
    return data


def validate_waivers(data: dict[str, object]) -> dict[str, WaiverEntry]:
    errors: list[str] = []
    known_waivers: dict[str, WaiverEntry] = {}
    today = date.today()
    waivers = data["waivers"]
    assert isinstance(waivers, list)
    for index, waiver in enumerate(waivers):
        entry_errors: list[str] = []
        if not isinstance(waiver, dict):
            errors.append(f"waivers[{index}] must be an object")
            continue
        for field in (
            "id",
            "rule",
            "scope",
            "owner",
            "reason",
            "expires_on",
            "tracking_ref",
        ):
            value = waiver.get(field)
            if not isinstance(value, str) or not value.strip():
                entry_errors.append(f"waivers[{index}].{field} must be a non-empty string")
        waiver_id = waiver.get("id")
        if isinstance(waiver_id, str) and waiver_id:
            if waiver_id in known_waivers:
                entry_errors.append(f"duplicate waiver id: {waiver_id}")
        rule = waiver.get("rule")
        if isinstance(rule, str) and rule not in KNOWN_RULES:
            entry_errors.append(f"unknown waiver rule: {rule}")
        expires_on = waiver.get("expires_on")
        if isinstance(expires_on, str):
            try:
                expires = date.fromisoformat(expires_on)
            except ValueError:
                entry_errors.append(f"invalid expires_on date: {expires_on}")
            else:
                if expires < today:
                    entry_errors.append(f"expired waiver: {waiver_id or index} ({expires_on})")
        if entry_errors:
            errors.extend(entry_errors)
            continue
        assert isinstance(waiver_id, str)
        assert isinstance(rule, str)
        assert isinstance(waiver["scope"], str)
        assert isinstance(waiver["owner"], str)
        assert isinstance(waiver["reason"], str)
        assert isinstance(expires_on, str)
        assert isinstance(waiver["tracking_ref"], str)
        known_waivers[waiver_id] = WaiverEntry(
            expires_on=expires_on,
            id=waiver_id,
            owner=waiver["owner"],
            reason=waiver["reason"],
            rule=rule,
            scope=waiver["scope"],
            tracking_ref=waiver["tracking_ref"],
        )
    if errors:
        raise ValueError("\n".join(errors))
    return known_waivers


def marker_for_line(lines: list[str], index: int) -> str | None:
    for candidate in (index, index - 1):
        if candidate < 0:
            continue
        match = WAIVER_MARKER_RE.search(lines[candidate])
        if match:
            return match.group(1)
    return None


def scoped_files(
    repo_root: Path = REPO_ROOT,
    scoped_globs: tuple[str, ...] = SCOPED_GLOBS,
) -> list[Path]:
    files: set[Path] = set()
    for pattern in scoped_globs:
        files.update(repo_root.glob(pattern))
    return sorted(path for path in files if path.is_file())


def infer_waiver_rule(line: str) -> tuple[str | None, str]:
    """Map a supported suppression line to the governance rule it can waive."""

    if TYPE_IGNORE_RE.search(line):
        return "python-mypy-governance", "type: ignore suppression"
    if PYRIGHT_IGNORE_RE.search(line):
        return "python-mypy-governance", "pyright ignore suppression"
    if DOCSTRING_NOQA_RE.search(line):
        return "python-docstrings", "docstring noqa suppression"
    if NOQA_RE.search(line):
        return None, "unsupported noqa suppression without docstring rule codes"
    if TS_IGNORE_RE.search(line):
        return None, "unsupported @ts-ignore suppression"
    if TS_EXPECT_ERROR_RE.search(line):
        return None, "unsupported @ts-expect-error suppression"
    if BIOME_IGNORE_RE.search(line):
        return None, "unsupported biome-ignore suppression"
    if ESLINT_DISABLE_RE.search(line):
        return None, "unsupported eslint-disable suppression"
    return None, ""


def path_matches_scope(path: Path, scope: str, repo_root: Path) -> bool:
    """Return true when a suppression path is covered by a waiver scope glob."""

    return path in {candidate for candidate in repo_root.glob(scope) if candidate.is_file()}


def validate_suppressions(
    waivers: dict[str, WaiverEntry],
    *,
    repo_root: Path = REPO_ROOT,
    scoped_globs: tuple[str, ...] = SCOPED_GLOBS,
) -> None:
    errors: list[str] = []
    for path in scoped_files(repo_root=repo_root, scoped_globs=scoped_globs):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            inferred_rule, description = infer_waiver_rule(line)
            if not description:
                continue
            waiver_id = marker_for_line(lines, index)
            if not waiver_id:
                errors.append(
                    f"{path.relative_to(repo_root)}:{index + 1} suppression missing code-quality-waiver marker"
                )
                continue
            waiver = waivers.get(waiver_id)
            if waiver is None:
                errors.append(
                    f"{path.relative_to(repo_root)}:{index + 1} references unknown waiver id {waiver_id}"
                )
                continue
            if inferred_rule is None:
                errors.append(
                    f"{path.relative_to(repo_root)}:{index + 1} uses {description}; extend waiver validation before attaching waiver {waiver_id}"
                )
                continue
            if waiver.rule != inferred_rule:
                errors.append(
                    f"{path.relative_to(repo_root)}:{index + 1} uses waiver {waiver_id} for {description}, but registry rule is {waiver.rule}"
                )
            if not path_matches_scope(path, waiver.scope, repo_root):
                errors.append(
                    f"{path.relative_to(repo_root)}:{index + 1} uses waiver {waiver_id} outside declared scope {waiver.scope}"
                )
    if errors:
        raise ValueError("\n".join(errors))


def main() -> int:
    try:
        data = load_registry()
        waivers = validate_waivers(data)
        validate_suppressions(waivers)
    except Exception as exc:  # noqa: BLE001 - emit a stable single-line failure summary
        print(f"CODE_QUALITY_WAIVERS_INVALID: {exc}", file=sys.stderr)
        return 1

    print("CODE_QUALITY_WAIVERS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
