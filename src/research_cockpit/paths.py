from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any

from research_cockpit.storage import load_yaml


PROJECT_LOCATOR_FILENAME = ".research-cockpit.yaml"
PROJECT_LOCATOR_SCHEMA_VERSION = "research_cockpit_locator_v1"
LOCAL_STATE_HOME_POLICY = "local_state_home_v1"
_PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[2]


def validate_project_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("research cockpit project_id must be a string")
    project_id = value.strip()
    if not _PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError(
            "research cockpit project_id must use 1-64 lowercase letters, digits, _ or -"
        )
    return project_id


def local_state_home() -> Path:
    configured = os.environ.get("RESEARCH_COCKPIT_STATE_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".research-cockpit" / "state").resolve()


def local_state_root(project_id: str, *, state_home: Path | None = None) -> Path:
    return (state_home or local_state_home()).expanduser().resolve() / validate_project_id(project_id)


def project_locator_payload(project_id: str) -> dict[str, str]:
    return {
        "schema_version": PROJECT_LOCATOR_SCHEMA_VERSION,
        "project_id": validate_project_id(project_id),
        "state_root_policy": LOCAL_STATE_HOME_POLICY,
    }


def load_project_locator(path: Path) -> dict[str, str]:
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"project locator must be a mapping: {path}")
    if data.get("schema_version") != PROJECT_LOCATOR_SCHEMA_VERSION:
        raise ValueError(f"unsupported research cockpit project locator: {path}")
    if data.get("state_root_policy") != LOCAL_STATE_HOME_POLICY:
        raise ValueError(f"unsupported state-root policy in project locator: {path}")
    return project_locator_payload(validate_project_id(data.get("project_id")))


def find_project_locator(start: Path | None = None) -> tuple[Path, dict[str, str]] | None:
    current = (start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate_parent in [current, *current.parents]:
        locator_path = candidate_parent / PROJECT_LOCATOR_FILENAME
        if not locator_path.is_file():
            continue
        try:
            return locator_path, load_project_locator(locator_path)
        except ValueError:
            continue
    return None


def default_data_root(start: Path | None = None) -> Path:
    env_root = os.environ.get("RESEARCH_COCKPIT_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()

    current = (start or Path.cwd()).resolve()
    locator = find_project_locator(current)
    if locator is not None:
        _locator_path, data = locator
        return local_state_root(data["project_id"])
    for candidate_parent in [current, *current.parents]:
        candidate = candidate_parent / "research_cockpit"
        if candidate.exists():
            return candidate

    demo_root = plugin_root() / "examples" / "demo_research_cockpit"
    if demo_root.exists():
        return demo_root

    return current / "research_cockpit"


def resolve_data_root(root: Path | str | None = None) -> Path:
    if root is None:
        return default_data_root()
    return Path(root).expanduser().resolve()


def display_path(path: Path, *, base: Path | None = None) -> str:
    path = path.resolve()
    base = (base or Path.cwd()).resolve()
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()
