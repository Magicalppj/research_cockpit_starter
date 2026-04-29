from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit.components.v1 as components


COMPONENT_DIR = Path(__file__).resolve().parent
FRONTEND_BUILD_DIR = COMPONENT_DIR / "frontend" / "build"
_COMPONENT = None


def graph_component_build_available(build_dir: Path = FRONTEND_BUILD_DIR) -> bool:
    build_dir = Path(build_dir)
    if not (build_dir / "index.html").is_file():
        return False
    return any(path.suffix == ".js" for path in build_dir.rglob("*.js"))


def _component_renderer():
    global _COMPONENT
    if not graph_component_build_available():
        return None
    if _COMPONENT is None:
        _COMPONENT = components.declare_component(
            "research_cockpit_graph_component",
            path=str(FRONTEND_BUILD_DIR),
        )
    return _COMPONENT


def render_research_graph_component(
    payload: dict[str, Any],
    *,
    selected_node_id: str | None = None,
    key: str = "research_graph_component",
) -> Any:
    component = _component_renderer()
    if component is None:
        return None
    return component(
        payload=payload,
        selected_node_id=selected_node_id,
        default=None,
        key=key,
    )
