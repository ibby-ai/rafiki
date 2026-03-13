"""Focused tests for the code-quality waiver registry validator."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from scripts.quality.validate_code_quality_waivers import (
    WaiverEntry,
    validate_suppressions,
    validate_waivers,
)


def test_validate_waivers_rejects_expired_waiver() -> None:
    """Expired governance waivers must fail validation."""

    expired = (date.today() - timedelta(days=1)).isoformat()
    data = {
        "version": 1,
        "waivers": [
            {
                "id": "waiver-expired",
                "rule": "python-docstrings",
                "scope": "modal_backend/models/example.py",
                "owner": "platform",
                "reason": "Temporary exception for migration",
                "expires_on": expired,
                "tracking_ref": "TD-999",
            }
        ],
    }

    with pytest.raises(ValueError, match="expired waiver"):
        validate_waivers(data)


def test_validate_suppressions_rejects_scope_mismatch(tmp_path: Path) -> None:
    """Waiver ids cannot be consumed outside their declared scope."""

    target = tmp_path / "modal_backend" / "security" / "example.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# code-quality-waiver: waiver-scope\nvalue = 1  # type: ignore[assignment]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="outside declared scope"):
        validate_suppressions(
            {
                "waiver-scope": WaiverEntry(
                    expires_on="2099-12-31",
                    id="waiver-scope",
                    owner="platform",
                    reason="Temporary governance exception",
                    rule="python-mypy-governance",
                    scope="modal_backend/models/**/*.py",
                    tracking_ref="TD-100",
                )
            },
            repo_root=tmp_path,
            scoped_globs=("modal_backend/security/**/*.py",),
        )


def test_validate_suppressions_rejects_rule_mismatch(tmp_path: Path) -> None:
    """Waiver registry rules must match the suppression they justify."""

    target = tmp_path / "modal_backend" / "models" / "example.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# code-quality-waiver: waiver-rule\nvalue = 1  # type: ignore[assignment]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="registry rule is python-docstrings"):
        validate_suppressions(
            {
                "waiver-rule": WaiverEntry(
                    expires_on="2099-12-31",
                    id="waiver-rule",
                    owner="platform",
                    reason="Temporary governance exception",
                    rule="python-docstrings",
                    scope="modal_backend/models/**/*.py",
                    tracking_ref="TD-101",
                )
            },
            repo_root=tmp_path,
            scoped_globs=("modal_backend/models/**/*.py",),
        )
