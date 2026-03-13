"""Contract tests for the live Import Linter governance config."""

from __future__ import annotations

from scripts.quality.check_python_boundary_config import (
    load_importlinter_config,
    validate_importlinter_contracts,
)


def test_importlinter_config_keeps_required_governance_contracts() -> None:
    """The live repo config must keep the required forbidden contracts intact."""

    validate_importlinter_contracts(load_importlinter_config())
