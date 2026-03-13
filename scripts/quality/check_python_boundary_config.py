"""Assert that Import Linter retains the required governance contracts."""

from __future__ import annotations

import configparser
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IMPORTLINTER_PATH = REPO_ROOT / ".importlinter"


@dataclass(frozen=True)
class ImportLinterContract:
    """Required structure for a forbidden Import Linter contract."""

    forbidden_modules: tuple[str, ...]
    name: str
    source_modules: tuple[str, ...]
    as_packages: str | None = None


EXPECTED_CONTRACTS = {
    "importlinter:contract:models_foundation_stays_leaf": ImportLinterContract(
        name="models stay out of runtime and orchestration layers",
        source_modules=("modal_backend.models",),
        forbidden_modules=(
            "modal_backend.agent_runtime",
            "modal_backend.mcp_tools",
            "modal_backend.jobs",
            "modal_backend.schedules",
            "modal_backend.controller_rollout",
            "modal_backend.api",
            "modal_backend.main",
        ),
    ),
    "importlinter:contract:security_stays_cross_cutting": ImportLinterContract(
        name="security stays out of runtime and orchestration layers",
        source_modules=("modal_backend.security",),
        forbidden_modules=(
            "modal_backend.agent_runtime",
            "modal_backend.mcp_tools",
            "modal_backend.jobs",
            "modal_backend.schedules",
            "modal_backend.controller_rollout",
            "modal_backend.api",
            "modal_backend.main",
        ),
    ),
    "importlinter:contract:webhooks_boundary_stays_leaf": ImportLinterContract(
        name="platform webhook helpers stay out of transport and runtime layers",
        source_modules=("modal_backend.platform_services.webhooks",),
        forbidden_modules=(
            "modal_backend.agent_runtime",
            "modal_backend.mcp_tools",
            "modal_backend.api",
            "modal_backend.main",
        ),
        as_packages="False",
    ),
    "importlinter:contract:serialization_stays_transport_local": ImportLinterContract(
        name="serialization helpers stay out of runtime and orchestration layers",
        source_modules=("modal_backend.api.serialization",),
        forbidden_modules=(
            "modal_backend.agent_runtime",
            "modal_backend.mcp_tools",
            "modal_backend.jobs",
            "modal_backend.schedules",
            "modal_backend.controller_rollout",
            "modal_backend.main",
        ),
        as_packages="False",
    ),
}


def normalize_multiline(value: str) -> tuple[str, ...]:
    """Parse Import Linter multi-line values into stable tuples."""

    return tuple(line.strip() for line in value.splitlines() if line.strip())


def load_importlinter_config(
    path: Path = IMPORTLINTER_PATH,
) -> configparser.ConfigParser:
    """Load the live Import Linter config from disk."""

    parser = configparser.ConfigParser()
    if not parser.read(path, encoding="utf-8"):
        raise ValueError(f"Failed to read Import Linter config: {path}")
    return parser


def validate_importlinter_contracts(config: configparser.ConfigParser) -> None:
    """Verify the required governance contracts are present and intact."""

    errors: list[str] = []
    for section, contract in EXPECTED_CONTRACTS.items():
        if not config.has_section(section):
            errors.append(f"missing Import Linter contract section: {section}")
            continue
        if config.get(section, "type", fallback="").strip() != "forbidden":
            errors.append(f"{section} must stay type=forbidden")
        if config.get(section, "name", fallback="").strip() != contract.name:
            errors.append(f"{section} must keep name={contract.name!r}")
        if (
            normalize_multiline(config.get(section, "source_modules", fallback=""))
            != contract.source_modules
        ):
            errors.append(f"{section} source_modules drifted from governance contract")
        if (
            normalize_multiline(config.get(section, "forbidden_modules", fallback=""))
            != contract.forbidden_modules
        ):
            errors.append(f"{section} forbidden_modules drifted from governance contract")
        if contract.as_packages is None:
            if config.has_option(section, "as_packages"):
                errors.append(f"{section} must not set as_packages")
        elif config.get(section, "as_packages", fallback="").strip() != contract.as_packages:
            errors.append(f"{section} must keep as_packages={contract.as_packages}")
    if errors:
        raise ValueError("\n".join(errors))


def main() -> int:
    try:
        validate_importlinter_contracts(load_importlinter_config())
    except Exception as exc:  # noqa: BLE001 - stable governance failure summary
        print(f"PYTHON_BOUNDARY_CONFIG_INVALID: {exc}", file=sys.stderr)
        return 1

    print("PYTHON_BOUNDARY_CONFIG_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
