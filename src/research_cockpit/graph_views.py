from __future__ import annotations

from pathlib import Path
from typing import Any
import re

from research_cockpit.interaction_log import utc_timestamp
from research_cockpit.mutation_runtime import finish_mutation
from research_cockpit.storage import load_yaml
from research_cockpit.types import (
    GRAPH_VIEW_FILTER_BOOL_KEYS,
    GRAPH_VIEW_FILTER_LIST_KEYS,
    VALID_GRAPH_VIEW_SCOPES,
)


def graph_view_id_from_title(title: str, fallback_timestamp: str | None = None) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(title or "").strip()).strip("_").lower()
    if slug:
        return slug
    timestamp = fallback_timestamp or utc_timestamp()
    fallback = re.sub(r"[^A-Za-z0-9_.-]+", "_", timestamp).strip("_")
    return f"graph_view_{fallback}"


def _normal_string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (str, int, float)):
        raw_values = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        if item in (None, ""):
            continue
        text = str(item)
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _normal_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _normal_graph_view_filters(data: Any) -> dict[str, Any]:
    raw = data if isinstance(data, dict) else {}
    filters: dict[str, Any] = {}
    for key in GRAPH_VIEW_FILTER_LIST_KEYS:
        filters[key] = _normal_string_list(raw.get(key))
    for key in GRAPH_VIEW_FILTER_BOOL_KEYS:
        filters[key] = _normal_bool(raw.get(key, False))
    return filters


def _normal_graph_view(raw: Any, *, timestamp: str | None = None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    title = str(raw.get("title") or raw.get("id") or "Untitled graph view").strip() or "Untitled graph view"
    view_id = str(raw.get("id") or graph_view_id_from_title(title, timestamp)).strip()
    view_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", view_id).strip("_").lower()
    if not view_id:
        view_id = graph_view_id_from_title(title, timestamp)

    scope = str(raw.get("scope") or "focus_depth_2")
    if scope not in VALID_GRAPH_VIEW_SCOPES:
        scope = "focus_depth_2"

    saved_focus_node_id = raw.get("saved_focus_node_id")
    return {
        "id": view_id,
        "title": title,
        "scope": scope,
        "filters": _normal_graph_view_filters(raw.get("filters")),
        "saved_focus_node_id": None if saved_focus_node_id in (None, "") else str(saved_focus_node_id),
        "saved_focus_path": _normal_string_list(raw.get("saved_focus_path")),
        "created_at": str(raw.get("created_at") or timestamp or ""),
        "updated_at": str(raw.get("updated_at") or timestamp or ""),
    }


def load_graph_views(root: Path) -> list[dict[str, Any]]:
    data = load_yaml(root / "graph" / "graph_views.yaml")
    raw_views = data.get("views", []) if isinstance(data, dict) else []
    if not isinstance(raw_views, list):
        return []

    views: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_views:
        view = _normal_graph_view(raw)
        if not view or view["id"] in seen:
            continue
        seen.add(view["id"])
        views.append(view)
    return views


def upsert_graph_view(root: Path, view: dict[str, Any]) -> dict[str, Any]:
    timestamp = utc_timestamp()
    normalized = _normal_graph_view(view, timestamp=timestamp)
    if not normalized:
        raise ValueError("graph view must be a mapping")

    view_path = root / "graph" / "graph_views.yaml"
    before_data = load_yaml(view_path) if view_path.exists() else None
    existing_views = load_graph_views(root)
    next_views: list[dict[str, Any]] = []
    replaced = False
    before: dict[str, Any] | None = None
    for existing in existing_views:
        if existing["id"] == normalized["id"]:
            before = existing
            normalized["created_at"] = existing.get("created_at") or timestamp
            normalized["updated_at"] = timestamp
            next_views.append(normalized)
            replaced = True
        else:
            next_views.append(existing)

    if not replaced:
        normalized["created_at"] = normalized.get("created_at") or timestamp
        normalized["updated_at"] = timestamp
        next_views.append(normalized)

    finish_mutation(
        root,
        [(view_path, before_data, {"version": 1, "views": next_views})],
        interaction={
            "kind": "save_graph_view",
            "before": before,
            "after": normalized,
            "extra": {
                "view_id": normalized["id"],
                "title": normalized["title"],
                "scope": normalized["scope"],
                "filters": normalized["filters"],
            },
        },
        rebuild_dashboard=False,
    )
    return normalized
