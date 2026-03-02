"""Runtime hardening utilities for sandbox controller startup."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

SENSITIVE_ENV_KEYS = (
    "INTERNAL_AUTH_SECRET",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "SANDBOX_MODAL_TOKEN_ID",
    "SANDBOX_MODAL_TOKEN_SECRET",
)


@dataclass(slots=True)
class RuntimeHardeningReport:
    initial_uid: int
    final_uid: int
    initial_gid: int
    final_gid: int
    privilege_status: str
    scrubbed_keys: list[str]
    writable_roots: list[str]
    writable_probe: dict[str, bool]
    warnings: list[str]

    def model_dump(self) -> dict[str, object]:
        return asdict(self)


def _parse_bool_env(name: str, default: bool) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _parse_int_env(name: str, default: int) -> int:
    value = (os.getenv(name) or "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_writable_roots(agent_fs_root: str) -> list[str]:
    raw = (os.getenv("SANDBOX_WRITABLE_ROOTS") or f"{agent_fs_root},/tmp").split(",")
    roots: list[str] = []
    for root in raw:
        trimmed = root.strip()
        if not trimmed:
            continue
        normalized = str(Path(trimmed))
        if normalized not in roots:
            roots.append(normalized)
    return roots or [agent_fs_root, "/tmp"]


def apply_runtime_hardening(agent_fs_root: str) -> RuntimeHardeningReport:
    """Apply runtime hardening guards and return structured verification metadata."""
    warnings: list[str] = []
    scrubbed: list[str] = []

    initial_uid = os.getuid()
    initial_gid = os.getgid()

    for key in SENSITIVE_ENV_KEYS:
        if key in os.environ:
            scrubbed.append(key)
            os.environ.pop(key, None)

    # Restrict default file mode for newly created files/directories.
    os.umask(0o077)

    privilege_status = "unchanged"
    drop_privileges = _parse_bool_env("SANDBOX_DROP_PRIVILEGES", True)
    if drop_privileges and initial_uid == 0:
        target_uid = _parse_int_env("SANDBOX_RUNTIME_UID", 65534)
        target_gid = _parse_int_env("SANDBOX_RUNTIME_GID", 65534)
        try:
            os.setgid(target_gid)
            os.setuid(target_uid)
            privilege_status = "dropped"
        except OSError as exc:
            warnings.append(f"privilege_drop_failed:{exc}")
            privilege_status = "blocked"
    elif initial_uid != 0:
        privilege_status = "already_non_root"

    final_uid = os.getuid()
    final_gid = os.getgid()
    if final_uid == 0:
        warnings.append("runtime_still_root")

    writable_roots = _parse_writable_roots(agent_fs_root)
    probe_paths = {"/", "/root", "/etc", "/tmp", agent_fs_root, *writable_roots}
    writable_probe: dict[str, bool] = {}
    for path in sorted(probe_paths):
        try:
            writable_probe[path] = os.access(path, os.W_OK)
        except OSError:
            writable_probe[path] = False

    return RuntimeHardeningReport(
        initial_uid=initial_uid,
        final_uid=final_uid,
        initial_gid=initial_gid,
        final_gid=final_gid,
        privilege_status=privilege_status,
        scrubbed_keys=sorted(scrubbed),
        writable_roots=writable_roots,
        writable_probe=writable_probe,
        warnings=warnings,
    )
