from __future__ import annotations

import os
from pathlib import Path


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_data_root(start: Path | None = None) -> Path:
    env_root = os.environ.get("RESEARCH_COCKPIT_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()

    current = (start or Path.cwd()).resolve()
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
