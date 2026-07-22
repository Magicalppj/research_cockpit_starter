from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from research_cockpit.storage import load_yaml


STORAGE_LAYOUT_SCHEMA_VERSION = "storage_layout_v1"
STORAGE_POLICY_SCHEMA_VERSION = "storage_policy_v1"
ARTIFACT_ROOT_ENV = "RESEARCH_COCKPIT_ARTIFACT_ROOT"
_PROFILE_FIELDS = {"schema_version", "project_id", "artifact_root"}


@dataclass(frozen=True)
class StorageLayout:
    state_root: Path
    managed_artifact_root: Path | None
    legacy_artifact_root: Path
    source: str
    project_id: str | None = None

    @property
    def managed_writes_enabled(self) -> bool:
        return self.managed_artifact_root is not None

    @property
    def quarantine_root(self) -> Path | None:
        if self.managed_artifact_root is None:
            return None
        return self.managed_artifact_root / ".quarantine"

    def require_managed_artifact_root(self) -> Path:
        if self.managed_artifact_root is None:
            raise ValueError(
                "managed artifact root is not configured; use reference evidence or configure "
                f"{ARTIFACT_ROOT_ENV}/storage.yaml"
            )
        return self.managed_artifact_root

    def policy_payload(self) -> dict[str, Any]:
        return {
            "schema_version": STORAGE_POLICY_SCHEMA_VERSION,
            "default_evidence_mode": "reference",
            "managed_artifact_root": (
                str(self.managed_artifact_root)
                if self.managed_artifact_root is not None
                else None
            ),
            "managed_writes_enabled": self.managed_writes_enabled,
            "legacy_artifact_root": str(self.legacy_artifact_root),
            "legacy_new_writes_allowed": False,
            "source": self.source,
            "project_id": self.project_id,
        }


def _profile(root: Path) -> dict[str, Any]:
    path = root / "storage.yaml"
    if not path.exists():
        return {}
    payload = load_yaml(path)
    if not isinstance(payload, dict):
        raise ValueError("storage.yaml must contain a mapping")
    unknown = set(payload) - _PROFILE_FIELDS
    if unknown:
        raise ValueError(f"storage.yaml contains unknown fields: {sorted(unknown)}")
    if payload.get("schema_version") != STORAGE_LAYOUT_SCHEMA_VERSION:
        raise ValueError(
            f"storage.yaml schema_version must be {STORAGE_LAYOUT_SCHEMA_VERSION}"
        )
    artifact_root = payload.get("artifact_root")
    if not isinstance(artifact_root, str) or not artifact_root.strip():
        raise ValueError("storage.yaml artifact_root must be a non-empty string")
    project_id = payload.get("project_id")
    if project_id is not None and (
        not isinstance(project_id, str) or not project_id.strip()
    ):
        raise ValueError("storage.yaml project_id must be a non-empty string or null")
    return payload


def _classify_path_syntax(value: Path | str) -> str:
    text = str(value)
    windows = PureWindowsPath(text)
    if windows.is_absolute():
        return "windows_absolute"
    if windows.drive:
        return "windows_drive_relative"
    if PurePosixPath(text).is_absolute():
        return "posix_absolute"
    if "\\" in text:
        return "windows_relative"
    return "relative"


def _ensure_native_path_syntax(value: Path | str) -> None:
    syntax = _classify_path_syntax(value)
    if syntax == "windows_drive_relative":
        raise ValueError(
            "managed artifact root must not use Windows drive-relative syntax"
        )
    foreign = (
        syntax == "posix_absolute"
        if os.name == "nt"
        else syntax.startswith("windows")
    )
    if foreign:
        raise ValueError(
            f"managed artifact root uses foreign path syntax {syntax!r}; "
            "configure a native path on this host"
        )


def _resolve_path(value: Path | str, *, state_root: Path) -> Path:
    _ensure_native_path_syntax(value)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = state_root / path
    return path.resolve()


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_separation(state_root: Path, artifact_root: Path) -> None:
    if _contains(state_root, artifact_root) or _contains(artifact_root, state_root):
        raise ValueError(
            "managed artifact root must not overlap state root; configure a separate path"
        )


def resolve_storage_layout(
    state_root: Path | str,
    *,
    explicit_artifact_root: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> StorageLayout:
    root = Path(state_root).expanduser().resolve()
    profile = _profile(root)
    environment = os.environ if environ is None else environ
    env_value = str(environment.get(ARTIFACT_ROOT_ENV, "")).strip()

    selected: Path | str | None
    if explicit_artifact_root is not None and str(explicit_artifact_root).strip():
        selected = explicit_artifact_root
        source = "explicit"
    elif env_value:
        selected = env_value
        source = "environment"
    elif profile:
        selected = str(profile["artifact_root"])
        source = "profile"
    else:
        selected = None
        source = "unconfigured"

    managed_root = (
        _resolve_path(selected, state_root=root) if selected is not None else None
    )
    if managed_root is not None:
        _validate_separation(root, managed_root)

    project_id = profile.get("project_id") if profile else None
    return StorageLayout(
        state_root=root,
        managed_artifact_root=managed_root,
        legacy_artifact_root=(root / "artifacts").resolve(),
        source=source,
        project_id=str(project_id).strip() if project_id is not None else None,
    )
