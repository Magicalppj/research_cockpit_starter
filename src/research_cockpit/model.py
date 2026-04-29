from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import hashlib
import os
import re
import subprocess
import yaml
import networkx as nx


VALID_NODE_TYPES = {"stage", "problem", "option", "experiment", "decision", "artifact"}

VALID_STATUSES_BY_TYPE = {
    "stage": {"planned", "active", "blocked", "done"},
    "problem": {"open", "active", "blocked", "resolved", "parked"},
    "option": {"open", "active", "promising", "rejected", "accepted", "paused", "parked"},
    "experiment": {"planned", "queued", "running", "done", "failed", "cancelled"},
    "decision": {"proposed", "accepted", "superseded", "rejected"},
    "artifact": {"draft", "planned", "active", "done", "superseded", "deprecated", "archived"},
}

DEFAULT_STATUS_BY_TYPE = {
    "stage": "planned",
    "problem": "open",
    "option": "open",
    "experiment": "planned",
    "decision": "proposed",
    "artifact": "active",
}

ALL_KNOWN_STATUSES = {status for statuses in VALID_STATUSES_BY_TYPE.values() for status in statuses}
VALID_FINDING_CONFIDENCES = {"weak", "medium", "strong"}
VALID_FINDING_OUTCOMES = {"positive", "negative", "mixed", "inconclusive"}
VALID_SUGGESTION_LIFECYCLE_STATES = {"active", "dismissed", "completed"}
VALID_WORKSTREAM_STATUSES = {"claimed", "in_progress", "blocked", "reported", "released"}
ACTIVE_WORKSTREAM_STATUSES = {"claimed", "in_progress", "blocked"}
VALID_WORKSTREAM_RECOMMENDATIONS = {"accept", "reject", "continue"}
CONTEXT_SCHEMA_VERSION = "agent_context_v1"

SEARCH_NODE_TEXT_FIELDS = (
    "question",
    "hypothesis",
    "result_summary",
    "evidence_summary",
    "findings",
    "next_actions",
    "blockers",
    "agent_workstream",
    "workstream_report",
    "pros",
    "cons",
    "rejection_reason",
    "alternatives_considered",
    "consequences",
    "next_required_actions",
    "current_conclusion",
)

RESOURCE_SEARCH_ALLOWED_SUFFIXES = {".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".csv", ".tsv"}
RESOURCE_SEARCH_MAX_BYTES = 128 * 1024

DEFAULT_FOCUS_MODE = {
    "default_depth": 2,
    "hide_statuses": ["rejected", "parked", "archived"],
    "show_resolved": False,
    "show_rejected": False,
    "show_parked": False,
}

VALID_GRAPH_VIEW_SCOPES = {
    "focus_depth_2",
    "focus_depth_1",
    "current_branch",
    "option_workstream",
    "global",
}

GRAPH_VIEW_FILTER_LIST_KEYS = (
    "node_types",
    "statuses",
    "stages",
    "focus_roles",
    "workstreams",
)

GRAPH_VIEW_FILTER_BOOL_KEYS = (
    "only_blocking",
    "only_next_actions",
    "only_missing_evidence",
)

STATUS_COLORS = {
    "draft": "#D9E8FF",
    "planned": "#D9E8FF",
    "open": "#D9E8FF",
    "queued": "#D9E8FF",
    "active": "#FFE9A8",
    "running": "#FFE9A8",
    "promising": "#E8F5D8",
    "accepted": "#CFEAD6",
    "done": "#CFEAD6",
    "resolved": "#CFEAD6",
    "rejected": "#F3D0D0",
    "failed": "#F3D0D0",
    "blocked": "#F6C6C6",
    "parked": "#E5E5E5",
    "paused": "#E5E5E5",
    "cancelled": "#E5E5E5",
    "proposed": "#E7D8F6",
    "superseded": "#E5E5E5",
    "archived": "#E5E5E5",
    "deprecated": "#E5E5E5",
}

TYPE_SHAPES = {
    "stage": "box",
    "problem": "diamond",
    "option": "box",
    "experiment": "ellipse",
    "decision": "hexagon",
    "artifact": "database",
}


class ValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass
class ResearchNode:
    id: str
    type: str
    title: str
    status: str = "open"
    summary: str = ""
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    priority: str | None = None
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchNode":
        return cls(
            id=str(data["id"]),
            type=str(data.get("type", "node")),
            title=str(data.get("title", data["id"])),
            status=str(data.get("status", "open")),
            summary=str(data.get("summary", "")),
            parent=data.get("parent"),
            children=[str(x) for x in data.get("children", []) or []],
            priority=data.get("priority"),
            tags=[str(x) for x in data.get("tags", []) or []],
            raw=data,
        )


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_interaction_log(root: Path) -> dict[str, Any]:
    data = load_yaml(root / "graph" / "interaction_log.yaml")
    events = data.get("events", [])
    if not isinstance(events, list):
        events = []
    return {"events": events}


def append_interaction_log(
    root: Path,
    *,
    kind: str,
    actor: str = "researcher",
    node_id: str | None = None,
    command: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    created_at = _utc_timestamp()
    raw_id = "_".join(str(part) for part in (created_at, kind, node_id or "event") if part)
    event_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_id)
    event: dict[str, Any] = {
        "id": event_id,
        "kind": str(kind),
        "actor": str(actor),
        "created_at": created_at,
    }
    if node_id:
        event["node_id"] = str(node_id)
    if command:
        event["command"] = str(command)
    if before:
        event["before"] = before
    if after:
        event["after"] = after
    if extra:
        event.update(extra)

    log = load_interaction_log(root)
    log["events"].append(event)
    save_yaml(root / "graph" / "interaction_log.yaml", log)
    return event


def recent_interactions(root: Path, limit: int = 5) -> list[dict[str, Any]]:
    events = load_interaction_log(root).get("events", [])
    return list(reversed(events[-limit:]))


def graph_view_id_from_title(title: str, fallback_timestamp: str | None = None) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(title or "").strip()).strip("_").lower()
    if slug:
        return slug
    timestamp = fallback_timestamp or _utc_timestamp()
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
    view = {
        "id": view_id,
        "title": title,
        "scope": scope,
        "filters": _normal_graph_view_filters(raw.get("filters")),
        "saved_focus_node_id": None if saved_focus_node_id in (None, "") else str(saved_focus_node_id),
        "saved_focus_path": _normal_string_list(raw.get("saved_focus_path")),
        "created_at": str(raw.get("created_at") or timestamp or ""),
        "updated_at": str(raw.get("updated_at") or timestamp or ""),
    }
    return view


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
    timestamp = _utc_timestamp()
    normalized = _normal_graph_view(view, timestamp=timestamp)
    if not normalized:
        raise ValueError("graph view must be a mapping")

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

    save_yaml(root / "graph" / "graph_views.yaml", {"version": 1, "views": next_views})
    append_interaction_log(
        root,
        kind="save_graph_view",
        before=before,
        after=normalized,
        extra={
            "view_id": normalized["id"],
            "title": normalized["title"],
            "scope": normalized["scope"],
            "filters": normalized["filters"],
        },
    )
    return normalized


def load_nodes(root: Path) -> dict[str, ResearchNode]:
    node_dir = root / "graph" / "nodes"
    nodes: dict[str, ResearchNode] = {}
    for path in sorted(node_dir.glob("*.yaml")):
        data = load_yaml(path)
        if not data:
            continue
        try:
            node = ResearchNode.from_dict(data)
        except KeyError as exc:
            raise ValidationError([f"{path}: missing required field {exc.args[0]!r}"]) from exc
        if node.id in nodes:
            raise ValidationError([f"{path}: duplicate node id {node.id!r}"])
        nodes[node.id] = node
    return nodes


def load_explicit_edges(root: Path) -> list[dict[str, Any]]:
    path = root / "graph" / "edges.yaml"
    if not path.exists():
        return []

    data = load_yaml(path)
    if not data:
        return []
    if isinstance(data, list):
        raw_edges = data
    elif isinstance(data, dict):
        raw_edges = data.get("edges", [])
    else:
        raise ValidationError(["graph/edges.yaml must be a mapping with an edges list"])
    if not isinstance(raw_edges, list):
        raise ValidationError(["graph/edges.yaml: edges must be a list"])

    edges: list[dict[str, Any]] = []
    for index, item in enumerate(raw_edges, start=1):
        if not isinstance(item, dict):
            raise ValidationError([f"graph/edges.yaml: edges[{index}] must be a mapping"])
        source = item.get("source", item.get("from"))
        target = item.get("target", item.get("to"))
        edge_type = item.get("type") or item.get("relation") or "related"
        edge = {
            "from": "" if source in (None, "") else str(source),
            "to": "" if target in (None, "") else str(target),
            "relation": str(edge_type),
            "type": str(edge_type),
        }
        for key in ("label", "strength"):
            if key in item and item[key] is not None:
                edge[key] = item[key]
        edges.append(edge)
    return edges


def default_status_for_type(node_type: str) -> str:
    if node_type not in DEFAULT_STATUS_BY_TYPE:
        raise ValueError(f"Unknown node type: {node_type}")
    return DEFAULT_STATUS_BY_TYPE[node_type]


def validate_status(node_type: str, status: str) -> None:
    allowed = VALID_STATUSES_BY_TYPE.get(node_type)
    if allowed is None:
        raise ValueError(f"Unknown node type: {node_type}")
    if status not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(f"Invalid status {status!r} for {node_type}; allowed: {allowed_text}")


def focus_mode_from_current(current: dict[str, Any]) -> dict[str, Any]:
    focus_mode = dict(DEFAULT_FOCUS_MODE)
    user_focus_mode = current.get("focus_mode")
    if isinstance(user_focus_mode, dict):
        focus_mode.update(user_focus_mode)
    return focus_mode


def focus_node_id_from_current(current: dict[str, Any], nodes: dict[str, ResearchNode]) -> str | None:
    focus_node_id = current.get("current_focus_node")
    if focus_node_id:
        return str(focus_node_id)

    problem_id = current.get("current_problem")
    if problem_id and str(problem_id) in nodes:
        return str(problem_id)

    focus_path = current.get("current_focus_path", []) or []
    if isinstance(focus_path, list):
        for node_id in reversed(focus_path):
            if str(node_id) in nodes:
                return str(node_id)
    return None


def derive_focus_path(nodes: dict[str, ResearchNode], focus_node_id: str) -> list[str]:
    if focus_node_id not in nodes:
        raise ValueError(f"Focus node does not exist: {focus_node_id}")

    path: list[str] = []
    seen: set[str] = set()
    node_id: str | None = focus_node_id
    while node_id:
        if node_id in seen:
            raise ValueError(f"Focus path has a parent cycle at {node_id!r}")
        if node_id not in nodes:
            raise ValueError(f"Focus path references missing parent {node_id!r}")
        seen.add(node_id)
        path.append(node_id)
        parent = nodes[node_id].parent
        node_id = str(parent) if parent else None
    return list(reversed(path))


def _node_id_by_type_in_path(
    nodes: dict[str, ResearchNode],
    path: list[str],
    node_type: str,
    *,
    nearest: bool = False,
) -> str | None:
    node_ids = reversed(path) if nearest else path
    for node_id in node_ids:
        if node_id in nodes and nodes[node_id].type == node_type:
            return node_id
    return None


def derive_focus_fields(
    nodes: dict[str, ResearchNode],
    focus_node_id: str,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = current or {}
    path = derive_focus_path(nodes, focus_node_id)
    return {
        "current_stage": _node_id_by_type_in_path(nodes, path, "stage") or current.get("current_stage"),
        "current_problem": _node_id_by_type_in_path(nodes, path, "problem", nearest=True)
        or current.get("current_problem"),
        "current_option": _node_id_by_type_in_path(nodes, path, "option", nearest=True)
        or current.get("current_option"),
        "current_focus_node": focus_node_id,
        "current_focus_path": path,
    }


def iter_graph_edges(
    nodes: dict[str, ResearchNode],
    explicit_edges: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    by_pair: dict[tuple[str, str], dict[str, Any]] = {}

    def add_edge(
        source: str,
        target: str,
        relation: str,
        edge_type: str,
        extra: dict[str, Any] | None = None,
        *,
        overwrite: bool = False,
    ) -> None:
        key = (source, target)
        next_edge: dict[str, Any] = {
            "from": source,
            "to": target,
            "relation": relation,
            "type": edge_type,
        }
        if extra:
            for extra_key, value in extra.items():
                if value is not None:
                    next_edge[extra_key] = value
        if key in by_pair:
            if overwrite:
                by_pair[key].update(next_edge)
            return
        by_pair[key] = next_edge
        edges.append(next_edge)

    for node in nodes.values():
        if node.parent and node.parent in nodes:
            add_edge(str(node.parent), node.id, "parent", "contains")
        for child in node.children:
            if child in nodes:
                add_edge(node.id, child, "child", "contains")

    for edge in explicit_edges or []:
        source = str(edge.get("from") or edge.get("source") or "")
        target = str(edge.get("to") or edge.get("target") or "")
        if not source or not target:
            continue
        edge_type = str(edge.get("type") or edge.get("relation") or "related")
        relation = str(edge.get("relation") or edge_type)
        extra = {key: edge.get(key) for key in ("label", "strength")}
        add_edge(source, target, relation, edge_type, extra, overwrite=True)
    return edges


def build_graph(nodes: dict[str, ResearchNode], explicit_edges: list[dict[str, Any]] | None = None) -> nx.DiGraph:
    g = nx.DiGraph()
    for node in nodes.values():
        g.add_node(node.id, **node.raw)
    for edge in iter_graph_edges(nodes, explicit_edges):
        g.add_edge(edge["from"], edge["to"], relation=edge["relation"])
    return g


def _graph_child_ids(nodes: dict[str, ResearchNode], node_id: str) -> list[str]:
    node = nodes[node_id]
    child_ids = [child for child in node.children if child in nodes]
    implicit_children = sorted(
        candidate.id
        for candidate in nodes.values()
        if candidate.parent == node_id and candidate.id not in child_ids
    )
    return child_ids + implicit_children


def _graph_descendant_ids(nodes: dict[str, ResearchNode], node_id: str) -> set[str]:
    descendants: set[str] = set()
    stack = list(_graph_child_ids(nodes, node_id)) if node_id in nodes else []
    while stack:
        child_id = stack.pop()
        if child_id in descendants or child_id not in nodes:
            continue
        descendants.add(child_id)
        stack.extend(_graph_child_ids(nodes, child_id))
    return descendants


def _safe_node_path(nodes: dict[str, ResearchNode], node_id: str) -> list[str]:
    try:
        return derive_focus_path(nodes, node_id)
    except ValueError:
        return [node_id] if node_id in nodes else []


def _node_has_evidence(node: ResearchNode) -> bool:
    if node.raw.get("findings"):
        return True
    for field_name in (
        "evidence_summary",
        "evidence_strength",
        "result_summary",
        "outcome",
        "current_conclusion",
    ):
        if node.raw.get(field_name):
            return True
    for field_name in (
        "supporting_experiments",
        "contradicting_experiments",
        "supporting_decisions",
        "linked_artifacts",
    ):
        if node.raw.get(field_name):
            return True
    return False


def _graph_interaction_metadata(
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    current = current or {}
    focus_node_id = focus_node_id_from_current(current, nodes) if current else None
    current_branch_ids = set(str(node_id) for node_id in current.get("current_focus_path", []) or [])
    if focus_node_id and focus_node_id in nodes:
        current_branch_ids.add(focus_node_id)
        current_branch_ids.update(_graph_descendant_ids(nodes, focus_node_id))

    metadata: dict[str, dict[str, Any]] = {}
    for node in nodes.values():
        path = _safe_node_path(nodes, node.id)
        stage_id = _node_id_by_type_in_path(nodes, path, "stage")
        problem_id = _node_id_by_type_in_path(nodes, path, "problem", nearest=True)
        option_id = _node_id_by_type_in_path(nodes, path, "option", nearest=True)
        upstream_problem_id = None
        if option_id and option_id in nodes:
            option_path = _safe_node_path(nodes, option_id)
            upstream_problem_id = _node_id_by_type_in_path(nodes, option_path, "problem", nearest=True)

        metadata[node.id] = {
            "stage_id": stage_id,
            "problem_id": problem_id,
            "option_workstream_id": option_id,
            "option_workstream_upstream_problem_id": upstream_problem_id,
            "in_current_branch": node.id in current_branch_ids,
            "has_blockers": bool(node.raw.get("blockers")),
            "has_next_actions": bool(node.raw.get("next_actions")),
            "has_evidence": _node_has_evidence(node),
        }
    return metadata


def _graph_available_filters(nodes: list[dict[str, Any]]) -> dict[str, list[str]]:
    fields = {
        "types": "type",
        "statuses": "status",
        "stages": "stage_id",
        "problems": "problem_id",
        "focus_roles": "focus_role",
        "workstreams": "option_workstream_id",
        "priorities": "priority",
    }
    out: dict[str, list[str]] = {}
    for key, field_name in fields.items():
        values = sorted(
            {
                str(node[field_name])
                for node in nodes
                if node.get(field_name) not in (None, "")
            }
        )
        out[key] = values
    return out


def _focus_graph_metadata(
    nodes: dict[str, ResearchNode],
    current_focus_path: list[str],
    current: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    current = current or {}
    focus_node_id = focus_node_id_from_current(current, nodes) if current else None
    focus_path = [str(node_id) for node_id in current_focus_path if str(node_id) in nodes]
    focus_path_set = set(focus_path)
    focus_mode = focus_mode_from_current(current)
    hide_statuses = set(focus_mode.get("hide_statuses", []))

    depths: dict[str, int] = {}
    roles: dict[str, str] = {}

    def include(node_id: str | None, depth: int, role: str) -> None:
        if not node_id or node_id not in nodes:
            return
        if node_id not in depths or depth < depths[node_id]:
            depths[node_id] = depth
        if node_id not in roles or role == "current":
            roles[node_id] = role

    if focus_node_id and focus_node_id in nodes:
        include(focus_node_id, 0, "current")
        focus_node = nodes[focus_node_id]

        if focus_node.parent:
            include(str(focus_node.parent), 1, "parent")
            if focus_node.parent in nodes:
                for sibling_id in _graph_child_ids(nodes, str(focus_node.parent)):
                    if sibling_id != focus_node_id:
                        include(sibling_id, 1, "sibling")

        focus_index = focus_path.index(focus_node_id) if focus_node_id in focus_path else -1
        for index, node_id in enumerate(focus_path):
            if node_id == focus_node_id:
                continue
            role = "parent" if focus_index == -1 or index < focus_index else "child"
            include(node_id, 1, role)

        child_ids = _graph_child_ids(nodes, focus_node_id)
        for child_id in child_ids:
            include(child_id, 1, "child")

        current_best_option = focus_node.raw.get("current_best_option") or current.get("current_option")
        option_ids = [
            node_id
            for node_id in _unique_strings([current_best_option, current.get("current_option"), focus_node_id])
            if node_id in nodes and nodes[node_id].type == "option"
        ]
        for option_id in option_ids:
            include(option_id, 1, "child")

        search_parent_ids = set([focus_node_id] + option_ids)
        for node in nodes.values():
            if node.type in {"experiment", "decision"} and node.parent in search_parent_ids:
                include(node.id, 2, "child")

        for node_id in list(depths):
            node = nodes[node_id]
            for artifact_id in node.raw.get("linked_artifacts", []) or []:
                if artifact_id in nodes and nodes[artifact_id].type == "artifact":
                    include(str(artifact_id), 2, "child")
            dataset_id = node.raw.get("dataset")
            if dataset_id in nodes and nodes[dataset_id].type == "artifact":
                include(str(dataset_id), 2, "child")

    metadata: dict[str, dict[str, Any]] = {}
    for node in nodes.values():
        focus_depth = depths.get(node.id)
        focus_role = roles.get(node.id)
        if not focus_role:
            focus_role = "historical" if node.status in hide_statuses else "unrelated"
        metadata[node.id] = {
            "current_focus_node": focus_node_id,
            "in_focus_path": node.id in focus_path_set,
            "is_current_focus": node.id == focus_node_id,
            "focus_role": focus_role,
            "focus_priority": node.raw.get("focus_priority"),
            "show_in_focus": node.raw.get("show_in_focus", "auto"),
            "focus_visible_depth": focus_depth,
            "is_focus_visible": focus_depth is not None,
            "is_hidden_by_focus": bool(focus_node_id and focus_depth is None),
        }
    return metadata


def graph_to_json(
    nodes: dict[str, ResearchNode],
    current_focus_path: list[str] | None = None,
    current: dict[str, Any] | None = None,
    explicit_edges: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current_focus_path = current_focus_path or []
    focus_set = set(current_focus_path)
    focus_metadata = _focus_graph_metadata(nodes, current_focus_path, current)
    interaction_metadata = _graph_interaction_metadata(nodes, current)
    current_focus_node = focus_node_id_from_current(current or {}, nodes) if current else None

    out_nodes = []
    out_edges = []
    for node in nodes.values():
        color = STATUS_COLORS.get(node.status, "#EEEEEE")
        out_nodes.append({
            "id": node.id,
            "label": node.title,
            "title": node.summary,
            "type": node.type,
            "status": node.status,
            "priority": node.priority,
            "color": color,
            "shape": TYPE_SHAPES.get(node.type, "box"),
            "is_focus": node.id in focus_set,
            **focus_metadata.get(node.id, {}),
            **interaction_metadata.get(node.id, {}),
            "raw": node.raw,
        })
    out_edges = iter_graph_edges(nodes, explicit_edges)
    return {
        "nodes": out_nodes,
        "edges": out_edges,
        "current_focus_path": current_focus_path,
        "current_focus_node": current_focus_node,
        "focus_mode": focus_mode_from_current(current or {}),
        "available_filters": _graph_available_filters(out_nodes),
    }


def validate_explicit_edges(nodes: dict[str, ResearchNode], explicit_edges: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for index, edge in enumerate(explicit_edges, start=1):
        source = str(edge.get("from") or edge.get("source") or "")
        target = str(edge.get("to") or edge.get("target") or "")
        prefix = f"graph.edges[{index}]"
        if not source:
            errors.append(f"{prefix}: source is required")
            continue
        if not target:
            errors.append(f"{prefix}: target is required")
            continue
        if source not in nodes:
            errors.append(f"{prefix}: source references missing node {source!r}")
        if target not in nodes:
            errors.append(f"{prefix}: target references missing node {target!r}")
        if source == target:
            errors.append(f"{prefix}: source and target cannot be the same node")
        edge_type = edge.get("type")
        if edge_type is not None and not str(edge_type).strip():
            errors.append(f"{prefix}: type cannot be empty")
    return errors


def validate_nodes(nodes: dict[str, ResearchNode]) -> list[str]:
    errors: list[str] = []

    def validate_single_ref(
        owner_id: str,
        field_name: str,
        value: Any,
        expected_type: str | None = None,
    ) -> None:
        if value in (None, ""):
            return
        ref_id = str(value)
        if ref_id not in nodes:
            errors.append(f"{owner_id}: {field_name} references missing node {ref_id!r}")
            return
        if expected_type and nodes[ref_id].type != expected_type:
            errors.append(
                f"{owner_id}: {field_name} references {ref_id!r} with type "
                f"{nodes[ref_id].type!r}; expected {expected_type!r}"
            )

    def validate_list_refs(
        owner_id: str,
        field_name: str,
        expected_type: str | None = None,
    ) -> None:
        value = nodes[owner_id].raw.get(field_name)
        if value is None:
            return
        if not isinstance(value, list):
            errors.append(f"{owner_id}: {field_name} must be a list")
            return
        for ref_id in value:
            validate_single_ref(owner_id, field_name, ref_id, expected_type)

    def validate_findings(node: ResearchNode) -> None:
        findings = node.raw.get("findings")
        if findings is None:
            return
        if node.type != "experiment":
            errors.append(f"{node.id}: findings can only be used with experiment nodes")
            return
        if not isinstance(findings, list):
            errors.append(f"{node.id}: findings must be a list")
            return
        for index, finding in enumerate(findings, start=1):
            prefix = f"{node.id}: findings[{index}]"
            if not isinstance(finding, dict):
                errors.append(f"{prefix} must be a mapping")
                continue
            if not finding.get("statement"):
                errors.append(f"{prefix}.statement is required")
            confidence = finding.get("confidence")
            if confidence not in (None, "") and str(confidence) not in VALID_FINDING_CONFIDENCES:
                allowed = ", ".join(sorted(VALID_FINDING_CONFIDENCES))
                errors.append(f"{prefix}.confidence invalid {confidence!r}; allowed: {allowed}")
            outcome = finding.get("outcome")
            if outcome not in (None, "") and str(outcome) not in VALID_FINDING_OUTCOMES:
                allowed = ", ".join(sorted(VALID_FINDING_OUTCOMES))
                errors.append(f"{prefix}.outcome invalid {outcome!r}; allowed: {allowed}")
            linked_artifacts = finding.get("linked_artifacts", []) or []
            if not isinstance(linked_artifacts, list):
                errors.append(f"{prefix}.linked_artifacts must be a list")
                continue
            for artifact_id in linked_artifacts:
                ref_id = str(artifact_id)
                if ref_id not in nodes:
                    errors.append(f"{prefix}.linked_artifacts references missing node {ref_id!r}")
                elif nodes[ref_id].type != "artifact":
                    errors.append(
                        f"{prefix}.linked_artifacts references {ref_id!r} with type "
                        f"{nodes[ref_id].type!r}; expected 'artifact'"
                    )

    def validate_option_workstream(node: ResearchNode) -> None:
        has_workstream = "agent_workstream" in node.raw
        has_report = "workstream_report" in node.raw
        if not has_workstream and not has_report:
            return
        if node.type != "option":
            if has_workstream:
                errors.append(f"{node.id}: agent_workstream is only supported on option nodes")
            if has_report:
                errors.append(f"{node.id}: workstream_report is only supported on option nodes")
            return

        workstream = node.raw.get("agent_workstream")
        if workstream is not None:
            if not isinstance(workstream, dict):
                errors.append(f"{node.id}: agent_workstream must be a mapping")
            else:
                status = workstream.get("status")
                if status not in (None, "") and str(status) not in VALID_WORKSTREAM_STATUSES:
                    allowed = ", ".join(sorted(VALID_WORKSTREAM_STATUSES))
                    errors.append(f"{node.id}: agent_workstream.status invalid {status!r}; allowed: {allowed}")
                validate_single_ref(
                    node.id,
                    "agent_workstream.report_to_problem",
                    workstream.get("report_to_problem"),
                    "problem",
                )

        report = node.raw.get("workstream_report")
        if report is not None:
            if not isinstance(report, dict):
                errors.append(f"{node.id}: workstream_report must be a mapping")
            else:
                recommendation = report.get("recommendation")
                if recommendation not in (None, "") and str(recommendation) not in VALID_WORKSTREAM_RECOMMENDATIONS:
                    allowed = ", ".join(sorted(VALID_WORKSTREAM_RECOMMENDATIONS))
                    errors.append(
                        f"{node.id}: workstream_report.recommendation invalid {recommendation!r}; allowed: {allowed}"
                    )

    for node in nodes.values():
        if not node.id:
            errors.append("node has empty id")
        if node.type not in VALID_NODE_TYPES:
            errors.append(f"{node.id}: invalid type {node.type!r}")
            continue
        allowed = VALID_STATUSES_BY_TYPE[node.type]
        if node.status not in allowed:
            allowed_text = ", ".join(sorted(allowed))
            errors.append(f"{node.id}: invalid status {node.status!r} for {node.type}; allowed: {allowed_text}")
        if not node.title:
            errors.append(f"{node.id}: missing title")
        if node.parent:
            if node.parent == node.id:
                errors.append(f"{node.id}: parent cannot reference itself")
            elif node.parent not in nodes:
                errors.append(f"{node.id}: parent references missing node {node.parent!r}")
        for child in node.children:
            if child == node.id:
                errors.append(f"{node.id}: child cannot reference itself")
            elif child not in nodes:
                errors.append(f"{node.id}: child references missing node {child!r}")

        validate_single_ref(node.id, "current_best_option", node.raw.get("current_best_option"), "option")
        validate_single_ref(node.id, "resolved_by", node.raw.get("resolved_by"), "decision")
        validate_list_refs(node.id, "supporting_experiments", "experiment")
        validate_list_refs(node.id, "contradicting_experiments", "experiment")
        validate_list_refs(node.id, "supporting_decisions", "decision")
        validate_list_refs(node.id, "linked_artifacts", "artifact")
        validate_list_refs(node.id, "alternatives_considered", "option")
        validate_list_refs(node.id, "derived_from")
        validate_findings(node)
        validate_option_workstream(node)
    return errors


def validate_current_state(
    current: dict[str, Any],
    nodes: dict[str, ResearchNode],
    explicit_edges: list[dict[str, Any]] | None = None,
) -> list[str]:
    errors: list[str] = []
    typed_refs = {
        "current_stage": "stage",
        "current_problem": "problem",
        "current_option": "option",
    }
    for field, expected_type in typed_refs.items():
        node_id = current.get(field)
        if not node_id:
            continue
        if node_id not in nodes:
            errors.append(f"current_state.{field} references missing node {node_id!r}")
            continue
        actual_type = nodes[node_id].type
        if actual_type != expected_type:
            errors.append(
                f"current_state.{field} references {node_id!r} with type {actual_type!r}; expected {expected_type!r}"
            )

    focus_node_id = current.get("current_focus_node")
    if focus_node_id and str(focus_node_id) not in nodes:
        errors.append(f"current_state.current_focus_node references missing node {focus_node_id!r}")

    focus_mode = current.get("focus_mode")
    if focus_mode is not None:
        if not isinstance(focus_mode, dict):
            errors.append("current_state.focus_mode must be a mapping")
        else:
            default_depth = focus_mode.get("default_depth")
            if default_depth is not None and (type(default_depth) is not int or not 0 <= default_depth <= 3):
                errors.append("current_state.focus_mode.default_depth must be an integer from 0 to 3")
            hide_statuses = focus_mode.get("hide_statuses")
            if hide_statuses is not None:
                if not isinstance(hide_statuses, list):
                    errors.append("current_state.focus_mode.hide_statuses must be a list")
                else:
                    for status in hide_statuses:
                        if str(status) not in ALL_KNOWN_STATUSES:
                            errors.append(
                                f"current_state.focus_mode.hide_statuses contains unknown status {status!r}"
                            )
            for field in ("show_resolved", "show_rejected", "show_parked"):
                value = focus_mode.get(field)
                if value is not None and type(value) is not bool:
                    errors.append(f"current_state.focus_mode.{field} must be a boolean")

    lifecycle = current.get("suggestion_lifecycle")
    if lifecycle is not None:
        if not isinstance(lifecycle, dict):
            errors.append("current_state.suggestion_lifecycle must be a mapping")
        else:
            for key, record in lifecycle.items():
                prefix = f"current_state.suggestion_lifecycle[{key!r}]"
                if not str(key).strip():
                    errors.append("current_state.suggestion_lifecycle contains an empty key")
                    continue
                if not isinstance(record, dict):
                    errors.append(f"{prefix} must be a mapping")
                    continue
                state = record.get("state")
                if state not in VALID_SUGGESTION_LIFECYCLE_STATES:
                    allowed = ", ".join(sorted(VALID_SUGGESTION_LIFECYCLE_STATES))
                    errors.append(f"{prefix}.state invalid {state!r}; allowed: {allowed}")
                for field in ("reason", "updated_at", "action", "kind", "source_node_id"):
                    value = record.get(field)
                    if value is not None and not isinstance(value, str):
                        errors.append(f"{prefix}.{field} must be a string")

    focus_path = current.get("current_focus_path", []) or []
    if not isinstance(focus_path, list):
        errors.append("current_state.current_focus_path must be a list")
        return errors

    edge_pairs = {(edge["from"], edge["to"]) for edge in iter_graph_edges(nodes, explicit_edges)}
    for node_id in focus_path:
        if node_id not in nodes:
            errors.append(f"current_state.current_focus_path references missing node {node_id!r}")
    for parent, child in zip(focus_path, focus_path[1:]):
        if parent in nodes and child in nodes and (parent, child) not in edge_pairs:
            errors.append(f"current_state.current_focus_path has disconnected step {parent!r} -> {child!r}")
    return errors


def validate_cockpit(
    root: Path,
    nodes: dict[str, ResearchNode] | None = None,
    current: dict[str, Any] | None = None,
    explicit_edges: list[dict[str, Any]] | None = None,
    *,
    raise_on_error: bool = False,
) -> list[str]:
    nodes = nodes if nodes is not None else load_nodes(root)
    current = current if current is not None else load_yaml(root / "current_state.yaml")
    explicit_edges = explicit_edges if explicit_edges is not None else load_explicit_edges(root)
    errors = validate_nodes(nodes)
    errors.extend(validate_explicit_edges(nodes, explicit_edges))
    errors.extend(validate_current_state(current, nodes, explicit_edges))
    if errors and raise_on_error:
        raise ValidationError(errors)
    return errors


def _node_link_entries(node: ResearchNode) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    links = node.raw.get("links")
    if isinstance(links, dict):
        for label, target in links.items():
            if target in (None, ""):
                continue
            entries.append({"kind": "link", "label": str(label), "target": str(target)})

    for field_name in ("config_path", "path", "run_id"):
        value = node.raw.get(field_name)
        if value not in (None, ""):
            entries.append({"kind": field_name, "label": field_name, "target": str(value)})
    return entries


def _is_external_target(target: str) -> bool:
    parsed = urlparse(target)
    return bool(parsed.scheme and parsed.scheme not in {"", "file"})


def _target_exists(root: Path, kind: str, target: str, nodes: dict[str, ResearchNode]) -> bool | None:
    if kind == "run_id" or _is_external_target(target):
        return None
    if kind == "linked_artifact":
        return target in nodes
    path = Path(target)
    if path.is_absolute():
        return None
    return (root / target).exists()


def build_link_rows(root: Path, nodes: dict[str, ResearchNode]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in sorted(nodes.values(), key=lambda item: item.id):
        entries = _node_link_entries(node)
        for artifact_id in node.raw.get("linked_artifacts", []) or []:
            entries.append({"kind": "linked_artifact", "label": "linked_artifact", "target": str(artifact_id)})

        for entry in entries:
            target = entry["target"]
            kind = entry["kind"]
            rows.append({
                "node_id": node.id,
                "node_title": node.title,
                "node_type": node.type,
                "kind": kind,
                "label": entry["label"],
                "target": target,
                "exists": _target_exists(root, kind, target, nodes),
            })
    return rows


def python_command() -> str:
    return os.environ.get("RESEARCH_COCKPIT_PYTHON", "").strip() or "python"


def script_command(script_name: str, *parts: str) -> str:
    command = [python_command(), fr"scripts\{script_name}"]
    command.extend(parts)
    return " ".join(str(part) for part in command if part not in ("", None))


def _workflow_command(script_name: str, *parts: str) -> str:
    return script_command(script_name, *parts)


def _git_output(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _nearest_git_root(path: Path) -> Path:
    current = path.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return path.resolve().parent


def build_context_metadata(root: Path, current: dict[str, Any]) -> dict[str, Any]:
    repo_root = _nearest_git_root(root)
    status = _git_output(repo_root, "status", "--porcelain")
    return {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_git_commit": _git_output(repo_root, "rev-parse", "--short", "HEAD"),
        "worktree_dirty": bool(status),
        "current_state_updated_at": current.get("updated_at"),
    }


def _priority_rank(priority: str | None) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(priority or "").lower(), 2)


def _suggestion_priority(node: ResearchNode | None, default: str = "medium") -> str:
    if node and str(node.priority or "").lower() in {"critical", "high", "medium", "low"}:
        return str(node.priority).lower()
    return default


def suggestion_key(kind: str, source_node_id: str, action: str) -> str:
    payload = f"{kind}\0{source_node_id}\0{action}".encode("utf-8")
    return f"sg_{hashlib.sha1(payload).hexdigest()[:16]}"


def _focus_related_ids(nodes: dict[str, ResearchNode], current: dict[str, Any]) -> set[str]:
    related = {
        str(node_id)
        for node_id in current.get("current_focus_path", []) or []
        if str(node_id) in nodes
    }
    for key in ("current_stage", "current_problem", "current_option", "current_focus_node"):
        node_id = current.get(key)
        if node_id in nodes:
            related.add(str(node_id))
    for node_id in list(related):
        node = nodes.get(node_id)
        if not node:
            continue
        if node.parent in nodes:
            related.add(str(node.parent))
        related.update(child_id for child_id in _child_ids(nodes, node) if child_id in nodes)
    return related


def _make_suggestion(
    *,
    kind: str,
    priority: str,
    action: str,
    reason: str,
    source: ResearchNode,
    related_node_ids: list[str] | None = None,
    suggested_command: str = "",
    focus_ids: set[str] | None = None,
) -> dict[str, Any]:
    related_node_ids = related_node_ids or []
    focus_ids = focus_ids or set()
    return {
        "kind": kind,
        "priority": priority,
        "action": action,
        "reason": reason,
        "source_node_id": source.id,
        "source_node_type": source.type,
        "related_node_ids": _unique_strings(related_node_ids),
        "suggested_command": suggested_command,
        "is_focus_related": source.id in focus_ids or any(node_id in focus_ids for node_id in related_node_ids),
    }


def _finalize_suggestions(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for suggestion in suggestions:
        key = (
            str(suggestion.get("kind")),
            str(suggestion.get("source_node_id")),
            str(suggestion.get("action")),
        )
        if key in seen:
            continue
        seen.add(key)
        suggestion["key"] = suggestion_key(key[0], key[1], key[2])
        deduped.append(suggestion)

    kind_rank = {
        "focus_next_action": 0,
        "resolve_blocker": 1,
        "run_experiment": 2,
        "record_finding": 3,
        "review_decision": 4,
        "fix_resource": 5,
    }
    deduped.sort(
        key=lambda item: (
            0 if item.get("is_focus_related") else 1,
            _priority_rank(item.get("priority")),
            kind_rank.get(str(item.get("kind")), 99),
            str(item.get("source_node_id")),
            str(item.get("action")),
        )
    )
    return deduped


def _apply_suggestion_lifecycle(
    suggestions: list[dict[str, Any]],
    current: dict[str, Any],
    *,
    include_inactive: bool,
) -> list[dict[str, Any]]:
    lifecycle = current.get("suggestion_lifecycle")
    lifecycle = lifecycle if isinstance(lifecycle, dict) else {}
    out: list[dict[str, Any]] = []
    for suggestion in suggestions:
        key = str(suggestion.get("key") or "")
        record = lifecycle.get(key)
        state = "active"
        reason = ""
        updated_at = ""
        if isinstance(record, dict):
            state = str(record.get("state") or "active")
            reason = str(record.get("reason") or "")
            updated_at = str(record.get("updated_at") or "")
        suggestion["lifecycle_state"] = state
        suggestion["lifecycle_reason"] = reason
        suggestion["lifecycle_updated_at"] = updated_at
        if state in {"dismissed", "completed"} and not include_inactive:
            continue
        out.append(suggestion)
    return out


def _assign_suggestion_ids(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, suggestion in enumerate(suggestions, start=1):
        suggestion["id"] = f"next_action_{index:03d}"
    return suggestions


def _mark_queued_suggestions(
    suggestions: list[dict[str, Any]],
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
) -> list[dict[str, Any]]:
    current_actions = {str(action) for action in current.get("next_actions", []) or []}
    for suggestion in suggestions:
        action = str(suggestion.get("action") or "")
        source = nodes.get(str(suggestion.get("source_node_id")))
        node_actions = set()
        if source:
            node_actions = {str(item) for item in source.raw.get("next_actions", []) or []}
        suggestion["queued_in_current"] = action in current_actions
        suggestion["queued_in_node"] = action in node_actions
    return suggestions


def build_action_suggestions(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    link_rows: list[dict[str, Any]] | None = None,
    *,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    focus_ids = _focus_related_ids(nodes, current)
    suggestions: list[dict[str, Any]] = []
    focus_node_id = focus_node_id_from_current(current, nodes)
    focus_node = nodes.get(focus_node_id) if focus_node_id else None

    action_source = focus_node or nodes.get(str(current.get("current_problem"))) or nodes.get(str(current.get("current_stage")))
    if action_source:
        for action in (current.get("next_actions", []) or []) + (action_source.raw.get("next_actions", []) or []):
            if not action:
                continue
            suggestions.append(_make_suggestion(
                kind="focus_next_action",
                priority=_suggestion_priority(action_source, "high"),
                action=str(action),
                reason="Current focus or current_state lists this as a next action.",
                source=action_source,
                related_node_ids=[node_id for node_id in current.get("current_focus_path", []) or [] if node_id in nodes],
                focus_ids=focus_ids,
            ))

    active_statuses = {"active", "open", "blocked"}
    for node in sorted(nodes.values(), key=lambda item: item.id):
        blockers = node.raw.get("blockers", []) or []
        if node.type == "problem" and node.status in active_statuses and blockers:
            for blocker in blockers:
                suggestions.append(_make_suggestion(
                    kind="resolve_blocker",
                    priority=_suggestion_priority(node, "high"),
                    action=f"Resolve blocker: {blocker}",
                    reason=f"{node.id} is active and has an explicit blocker.",
                    source=node,
                    related_node_ids=[node.parent] if node.parent else [],
                    suggested_command=_workflow_command("update_status.py", "--id", node.id, "--status", "blocked"),
                    focus_ids=focus_ids,
                ))

        if node.type == "experiment" and node.status == "planned":
            suggestions.append(_make_suggestion(
                kind="run_experiment",
                priority=_suggestion_priority(nodes.get(str(node.parent)), "medium"),
                action=f"Run planned experiment: {node.title}",
                reason=f"{node.id} is still planned.",
                source=node,
                related_node_ids=[node.parent] if node.parent else [],
                suggested_command=_workflow_command("update_status.py", "--id", node.id, "--status", "running"),
                focus_ids=focus_ids,
            ))

        if node.type == "experiment" and node.status == "done" and not (node.raw.get("findings") or []):
            suggestions.append(_make_suggestion(
                kind="record_finding",
                priority=_suggestion_priority(nodes.get(str(node.parent)), "medium"),
                action=f"Record findings for completed experiment: {node.title}",
                reason=f"{node.id} is done but has no structured findings.",
                source=node,
                related_node_ids=[node.parent] if node.parent else [],
                suggested_command=_workflow_command(
                    "record_finding.py",
                    "--experiment",
                    node.id,
                    '--statement "Describe the finding"',
                    "--confidence",
                    "medium",
                ),
                focus_ids=focus_ids,
            ))

        if node.type == "decision" and node.status == "proposed":
            suggestions.append(_make_suggestion(
                kind="review_decision",
                priority=_suggestion_priority(nodes.get(str(node.parent)), "medium"),
                action=f"Review proposed decision: {node.title}",
                reason=f"{node.id} is proposed and needs acceptance or rejection.",
                source=node,
                related_node_ids=[node.parent] if node.parent else [],
                suggested_command=_workflow_command("update_decision_evidence.py", "--id", node.id),
                focus_ids=focus_ids,
            ))

    for row in (link_rows if link_rows is not None else build_link_rows(root, nodes)):
        if row.get("exists") is not False:
            continue
        node_id = str(row.get("node_id"))
        source = nodes.get(node_id)
        if not source:
            continue
        target = str(row.get("target") or "")
        suggestions.append(_make_suggestion(
            kind="fix_resource",
            priority="low",
            action=f"Restore or update missing resource path: {target}",
            reason=f"{node_id} links to a local resource that does not exist.",
            source=source,
            related_node_ids=[],
            suggested_command="",
            focus_ids=focus_ids,
        ))

    suggestions = _finalize_suggestions(suggestions)
    suggestions = _apply_suggestion_lifecycle(suggestions, current, include_inactive=include_inactive)
    suggestions = _assign_suggestion_ids(suggestions)
    return _mark_queued_suggestions(suggestions, nodes, current)


def build_suggestion_lifecycle_summary(current: dict[str, Any], suggestions: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"active": 0, "dismissed": 0, "completed": 0, "orphan": 0}
    suggestion_keys = {str(item.get("key")) for item in suggestions if item.get("key")}
    for suggestion in suggestions:
        state = str(suggestion.get("lifecycle_state") or "active")
        if state in counts:
            counts[state] += 1

    lifecycle = current.get("suggestion_lifecycle")
    if isinstance(lifecycle, dict):
        for key in lifecycle:
            if str(key) not in suggestion_keys:
                counts["orphan"] += 1
    return counts


def _parse_lifecycle_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def build_suggestion_lifecycle_rows(
    current: dict[str, Any],
    suggestions: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    today = today or date.today()
    by_key = {str(item.get("key")): item for item in suggestions if item.get("key")}
    lifecycle = current.get("suggestion_lifecycle")
    if not isinstance(lifecycle, dict):
        return []

    rows: list[dict[str, Any]] = []
    for key, raw_record in sorted(lifecycle.items(), key=lambda item: str(item[0])):
        record = raw_record if isinstance(raw_record, dict) else {}
        key_text = str(key)
        suggestion = by_key.get(key_text)
        updated_at = str(record.get("updated_at") or "")
        updated_date = _parse_lifecycle_date(updated_at)
        rows.append({
            "key": key_text,
            "suggestion_id": str(suggestion.get("id") or "") if suggestion else "",
            "state": str(record.get("state") or ""),
            "reason": str(record.get("reason") or ""),
            "updated_at": updated_at,
            "action": str(record.get("action") or (suggestion or {}).get("action") or ""),
            "kind": str(record.get("kind") or (suggestion or {}).get("kind") or ""),
            "source_node_id": str(record.get("source_node_id") or (suggestion or {}).get("source_node_id") or ""),
            "active_match": suggestion is not None,
            "orphan": suggestion is None,
            "age_days": (today - updated_date).days if updated_date else None,
        })
    return rows


def _normalize_relative_path(value: Any) -> str:
    path = str(value or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _node_file_paths(root: Path) -> dict[str, str]:
    paths: dict[str, str] = {}
    for path in sorted((root / "graph" / "nodes").glob("*.yaml")):
        data = load_yaml(path)
        node_id = data.get("id")
        if node_id:
            paths[str(node_id)] = _relative_to_root(root, path)
    return paths


def _node_note_paths(nodes: dict[str, ResearchNode]) -> dict[str, str]:
    note_paths: dict[str, str] = {}
    for node in nodes.values():
        links = node.raw.get("links")
        if not isinstance(links, dict):
            continue
        note_path = links.get("notes")
        if note_path:
            note_paths[_normalize_relative_path(note_path)] = node.id
    return note_paths


def _first_markdown_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    return fallback


def _flatten_search_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        values: list[str] = []
        for key in sorted(value):
            values.extend(_flatten_search_values(value[key]))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_flatten_search_values(item))
        return values
    return [str(value)]


def _node_search_text(node: ResearchNode) -> str:
    parts = [
        node.id,
        node.type,
        node.title,
        node.status,
        node.priority or "",
        node.summary,
        *node.tags,
    ]
    for field_name in SEARCH_NODE_TEXT_FIELDS:
        parts.extend(_flatten_search_values(node.raw.get(field_name)))
    return "\n".join(part for part in parts if part not in (None, ""))


def _resource_entry_id(node_id: str, label: str, target: str) -> str:
    return f"resource:{node_id}:{label}:{target}"


def _resource_search_entry(
    row: dict[str, Any],
    *,
    path: str,
    text: str = "",
    truncated: bool = False,
    bytes_read: int = 0,
    skip_reason: str = "",
) -> dict[str, Any]:
    node_id = str(row.get("node_id") or "")
    label = str(row.get("label") or row.get("kind") or "")
    title = f"{row.get('node_title') or node_id} / {label}".strip(" /")
    return {
        "entry_id": _resource_entry_id(node_id, label, path or str(row.get("target") or "")),
        "source": "resource",
        "node_id": node_id or None,
        "node_type": row.get("node_type"),
        "node_title": row.get("node_title"),
        "title": title,
        "path": path,
        "text": text,
        "updated_at": "",
        "is_focus_related": False,
        "resource_kind": row.get("kind"),
        "resource_label": label,
        "target": str(row.get("target") or ""),
        "truncated": truncated,
        "bytes_read": bytes_read,
        "skip_reason": skip_reason,
    }


def _resource_path_under_root(root: Path, target: str) -> tuple[Path | None, str]:
    normalized = _normalize_relative_path(target)
    if not normalized:
        return None, ""
    path = Path(target)
    if path.is_absolute():
        return None, normalized
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None, normalized
    return candidate, normalized


def _resource_skip_reason(
    root: Path,
    row: dict[str, Any],
    normalized_note_paths: set[str],
) -> tuple[str, Path | None, str]:
    target = str(row.get("target") or "")
    normalized = _normalize_relative_path(target)
    kind = str(row.get("kind") or "")
    if normalized in normalized_note_paths and normalized.lower().endswith(".md"):
        return "indexed_as_note", None, normalized
    if kind == "run_id":
        return "run_id", None, normalized or target
    if kind == "linked_artifact":
        return "linked_artifact", None, normalized or target
    if Path(target).is_absolute():
        return "absolute_path", None, normalized or target
    if _is_external_target(target):
        return "external", None, normalized or target
    path, normalized = _resource_path_under_root(root, target)
    if path is None:
        return "outside_root", None, normalized or target
    if row.get("exists") is False:
        return "missing", path, normalized
    if row.get("exists") is not True:
        return "unknown", path, normalized
    if path.suffix.lower() not in RESOURCE_SEARCH_ALLOWED_SUFFIXES:
        return "unsupported_extension", path, normalized
    if not path.is_file():
        return "not_file", path, normalized
    return "", path, normalized


def _read_resource_text(path: Path) -> tuple[str, bool, int]:
    with path.open("rb") as handle:
        data = handle.read(RESOURCE_SEARCH_MAX_BYTES + 1)
    truncated = len(data) > RESOURCE_SEARCH_MAX_BYTES
    data = data[:RESOURCE_SEARCH_MAX_BYTES]
    return data.decode("utf-8", errors="replace"), truncated, len(data)


def _resource_search_entries(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    note_paths: dict[str, str],
) -> list[dict[str, Any]]:
    focus_ids = _focus_related_ids(nodes, current) if current else set()
    normalized_note_paths = set(note_paths)
    entries: list[dict[str, Any]] = []
    for row in build_link_rows(root, nodes):
        skip_reason, path, normalized = _resource_skip_reason(root, row, normalized_note_paths)
        if skip_reason == "indexed_as_note":
            continue
        if skip_reason:
            entry = _resource_search_entry(row, path=normalized or str(row.get("target") or ""), skip_reason=skip_reason)
        else:
            assert path is not None
            text, truncated, bytes_read = _read_resource_text(path)
            entry = _resource_search_entry(
                row,
                path=normalized,
                text=text,
                truncated=truncated,
                bytes_read=bytes_read,
            )
        entry["is_focus_related"] = bool(entry.get("node_id") in focus_ids)
        entries.append(entry)
    return entries


def build_search_index(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    current = current or {}
    focus_ids = _focus_related_ids(nodes, current) if current else set()
    node_paths = _node_file_paths(root)
    note_paths = _node_note_paths(nodes)
    entries: list[dict[str, Any]] = []

    for node in sorted(nodes.values(), key=lambda item: item.id):
        entries.append({
            "entry_id": f"node:{node.id}",
            "source": "node",
            "node_id": node.id,
            "node_type": node.type,
            "node_title": node.title,
            "title": node.title,
            "path": node_paths.get(node.id, ""),
            "text": _node_search_text(node),
            "updated_at": str(node.raw.get("updated_at") or ""),
            "is_focus_related": node.id in focus_ids,
        })

    notes_dir = root / "notes"
    if notes_dir.exists():
        for path in sorted(notes_dir.glob("**/*.md")):
            rel_path = _relative_to_root(root, path)
            normalized = _normalize_relative_path(rel_path)
            text = path.read_text(encoding="utf-8", errors="replace")
            node_id = note_paths.get(normalized)
            node = nodes.get(node_id) if node_id else None
            entries.append({
                "entry_id": f"note:{normalized}",
                "source": "note",
                "node_id": node.id if node else None,
                "node_type": node.type if node else None,
                "node_title": node.title if node else None,
                "title": _first_markdown_heading(text, path.stem),
                "path": normalized,
                "text": text,
                "updated_at": str(node.raw.get("updated_at") or "") if node else "",
                "is_focus_related": bool(node and node.id in focus_ids),
            })
    entries.extend(_resource_search_entries(root, nodes, current, note_paths))
    return entries


def _search_terms(query: str) -> list[str]:
    return [term.lower() for term in query.split() if term.strip()]


def _count_occurrences(text: str, needle: str) -> int:
    if not needle:
        return 0
    return text.count(needle)


def _search_score(entry: dict[str, Any], query: str, terms: list[str]) -> int:
    phrase = query.lower()
    title = str(entry.get("title") or "").lower()
    path = str(entry.get("path") or "").lower()
    text = str(entry.get("text") or "").lower()
    score = 0
    score += 40 * _count_occurrences(title, phrase)
    score += 12 * _count_occurrences(text, phrase)
    score += 4 * _count_occurrences(path, phrase)
    for term in terms:
        score += 10 * _count_occurrences(title, term)
        score += _count_occurrences(text, term)
        score += 2 * _count_occurrences(path, term)
    return score


def make_search_snippet(text: str, query: str, width: int = 180) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return ""
    lower = clean.lower()
    phrase = query.lower().strip()
    terms = _search_terms(query)
    positions = []
    if phrase:
        pos = lower.find(phrase)
        if pos >= 0:
            positions.append(pos)
    for term in terms:
        pos = lower.find(term)
        if pos >= 0:
            positions.append(pos)
    start_at = min(positions) if positions else 0
    start = max(0, start_at - width // 2)
    end = min(len(clean), start + width)
    snippet = clean[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(clean):
        snippet += "..."
    return snippet


def _search_result_from_entry(entry: dict[str, Any], query: str, score: int) -> dict[str, Any]:
    text = str(entry.get("text") or "")
    return {
        "entry_id": entry.get("entry_id"),
        "score": score,
        "source": entry.get("source"),
        "node_id": entry.get("node_id"),
        "node_type": entry.get("node_type"),
        "node_title": entry.get("node_title"),
        "title": entry.get("title"),
        "path": entry.get("path"),
        "snippet": make_search_snippet(text, query),
        "preview": make_search_snippet(text, query, width=700),
        "updated_at": entry.get("updated_at"),
        "is_focus_related": bool(entry.get("is_focus_related")),
        "resource_kind": entry.get("resource_kind"),
        "resource_label": entry.get("resource_label"),
        "target": entry.get("target"),
        "truncated": bool(entry.get("truncated")),
        "bytes_read": entry.get("bytes_read"),
        "skip_reason": entry.get("skip_reason"),
    }


def search_knowledge(
    index: list[dict[str, Any]],
    query: str,
    *,
    sources: set[str] | list[str] | None = None,
    node_types: set[str] | list[str] | None = None,
    limit: int | None = 20,
    focus_only: bool = False,
) -> list[dict[str, Any]]:
    query = query.strip()
    if not query or limit == 0:
        return []
    selected_sources = set(sources or [])
    selected_node_types = set(node_types or [])
    terms = _search_terms(query)
    results: list[dict[str, Any]] = []

    for entry in index:
        if entry.get("skip_reason"):
            continue
        if selected_sources and entry.get("source") not in selected_sources:
            continue
        if selected_node_types and entry.get("node_type") not in selected_node_types:
            continue
        if focus_only and not entry.get("is_focus_related"):
            continue
        score = _search_score(entry, query, terms)
        if score <= 0:
            continue
        results.append(_search_result_from_entry(entry, query, score))

    results.sort(
        key=lambda item: (
            -int(item.get("score") or 0),
            str(item.get("source") or ""),
            str(item.get("node_id") or ""),
            str(item.get("path") or ""),
            str(item.get("entry_id") or ""),
        )
    )
    if limit is None:
        return results
    return results[:max(0, limit)]


def _search_summary_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_id": entry.get("entry_id"),
        "source": entry.get("source"),
        "node_id": entry.get("node_id"),
        "node_type": entry.get("node_type"),
        "title": entry.get("title"),
        "path": entry.get("path"),
    }


def build_search_index_summary(index: list[dict[str, Any]], focus_entry_limit: int = 8) -> dict[str, Any]:
    note_count = 0
    node_count = 0
    resource_count = 0
    resource_truncated_count = 0
    resource_skipped_count = 0
    focus_resource_count = 0
    unlinked_note_count = 0
    focus_entry_count = 0
    focus_entries: list[dict[str, Any]] = []

    for entry in index:
        source = entry.get("source")
        if source == "note":
            note_count += 1
            if not entry.get("node_id"):
                unlinked_note_count += 1
        if source == "node":
            node_count += 1
        if source == "resource":
            if entry.get("skip_reason"):
                resource_skipped_count += 1
            else:
                resource_count += 1
                if entry.get("truncated"):
                    resource_truncated_count += 1
                if entry.get("is_focus_related"):
                    focus_resource_count += 1
        if entry.get("is_focus_related") and not entry.get("skip_reason"):
            focus_entry_count += 1
            if len(focus_entries) < focus_entry_limit:
                focus_entries.append(_search_summary_entry(entry))

    return {
        "entry_count": len(index),
        "note_count": note_count,
        "node_count": node_count,
        "resource_count": resource_count,
        "resource_truncated_count": resource_truncated_count,
        "resource_skipped_count": resource_skipped_count,
        "focus_resource_count": focus_resource_count,
        "unlinked_note_count": unlinked_note_count,
        "focus_entry_count": focus_entry_count,
        "focus_entries": focus_entries,
    }


def node_context(node: ResearchNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "type": node.type,
        "title": node.title,
        "status": node.status,
        "priority": node.priority,
        "summary": node.summary,
        "question": node.raw.get("question"),
        "hypothesis": node.raw.get("hypothesis"),
        "evidence_strength": node.raw.get("evidence_strength"),
        "evidence_summary": node.raw.get("evidence_summary"),
        "current_best_option": node.raw.get("current_best_option"),
        "decision_state": node.raw.get("decision_state"),
        "outcome": node.raw.get("outcome"),
        "result_summary": node.raw.get("result_summary"),
        "supporting_experiments": node.raw.get("supporting_experiments", []),
        "contradicting_experiments": node.raw.get("contradicting_experiments", []),
        "supporting_decisions": node.raw.get("supporting_decisions", []),
        "linked_artifacts": node.raw.get("linked_artifacts", []),
        "links": _node_link_entries(node),
        "findings": node.raw.get("findings", []),
        "implementation_steps": node.raw.get("implementation_steps", []),
        "success_criteria": node.raw.get("success_criteria", []),
        "agent_workstream": node.raw.get("agent_workstream"),
        "workstream_report": node.raw.get("workstream_report"),
        "agent_context": node.raw.get("agent_context"),
        "next_actions": node.raw.get("next_actions", []),
        "blockers": node.raw.get("blockers", []),
    }


def node_title(nodes: dict[str, ResearchNode], node_id: str | None) -> str | None:
    if not node_id or node_id not in nodes:
        return None
    return nodes[node_id].title


def build_agent_context(root: Path, nodes: dict[str, ResearchNode]) -> dict[str, Any]:
    current = load_yaml(root / "current_state.yaml")
    path = current.get("current_focus_path", []) or []
    linked_nodes = []
    for node_id in path:
        if node_id in nodes:
            linked_nodes.append(node_context(nodes[node_id]))

    active_problems = [
        n for n in nodes.values()
        if n.type == "problem" and n.status in {"active", "open", "blocked"}
    ]
    active_options = [
        n for n in nodes.values()
        if n.type == "option" and n.status in {"active", "promising", "open"}
    ]
    recent_decisions = [
        n for n in nodes.values()
        if n.type == "decision" and n.status in {"accepted", "proposed"}
    ]
    search_index = build_search_index(root, nodes, current)
    option_workstreams = build_option_workstream_rows(nodes)

    return {
        "metadata": build_context_metadata(root, current),
        "project_name": "Research Cockpit Demo",
        "current_stage": current.get("current_stage"),
        "current_stage_title": node_title(nodes, current.get("current_stage")),
        "current_problem": current.get("current_problem"),
        "current_problem_title": node_title(nodes, current.get("current_problem")),
        "current_option": current.get("current_option"),
        "current_option_title": node_title(nodes, current.get("current_option")),
        "current_focus_node": focus_node_id_from_current(current, nodes),
        "current_focus_node_title": node_title(nodes, focus_node_id_from_current(current, nodes)),
        "focus_mode": focus_mode_from_current(current),
        "current_hypothesis": current.get("current_hypothesis"),
        "current_focus_path": path,
        "open_risks": current.get("open_risks", []),
        "next_actions": current.get("next_actions", []),
        "linked_nodes": linked_nodes,
        "active_problems": [
            {
                "id": n.id,
                "title": n.title,
                "status": n.status,
                "priority": n.priority,
                "summary": n.summary,
                "blockers": n.raw.get("blockers", []),
                "next_actions": n.raw.get("next_actions", []),
            }
            for n in sorted(active_problems, key=lambda item: item.id)
        ],
        "active_options": [
            {
                "id": n.id,
                "title": n.title,
                "status": n.status,
                "priority": n.priority,
                "summary": n.summary,
                "decision_state": n.raw.get("decision_state"),
            }
            for n in sorted(active_options, key=lambda item: item.id)
        ],
        "recent_decisions": [
            {
                "id": n.id,
                "title": n.title,
                "status": n.status,
                "summary": n.summary,
                "supporting_experiments": n.raw.get("supporting_experiments", []),
                "consequences": n.raw.get("consequences", []),
            }
            for n in sorted(recent_decisions, key=lambda item: item.id)
        ],
        "active_option_workstreams": [
            row for row in option_workstreams if row.get("workstream_status") in ACTIVE_WORKSTREAM_STATUSES
        ],
        "saved_graph_views": load_graph_views(root),
        "recent_interactions": recent_interactions(root),
        "suggested_next_actions": build_action_suggestions(root, nodes, current),
        "search_index_summary": build_search_index_summary(search_index),
    }


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in (None, ""):
            continue
        item = str(value)
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _ordered_node_contexts(nodes: dict[str, ResearchNode], node_ids: list[str]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for node_id in node_ids:
        if node_id in seen or node_id not in nodes:
            continue
        seen.add(node_id)
        out.append(node_context(nodes[node_id]))
    return out


def _child_ids(nodes: dict[str, ResearchNode], node: ResearchNode) -> list[str]:
    child_ids = [child for child in node.children if child in nodes]
    implicit_children = sorted(
        candidate.id
        for candidate in nodes.values()
        if candidate.parent == node.id and candidate.id not in child_ids
    )
    return child_ids + implicit_children


def _knowledge_index(nodes: dict[str, ResearchNode], node_ids: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    for node_id in node_ids:
        if node_id in seen_nodes or node_id not in nodes:
            continue
        seen_nodes.add(node_id)
        node = nodes[node_id]
        agent_context = node.raw.get("agent_context")
        linked_resources = _node_link_entries(node)
        if isinstance(agent_context, dict) and agent_context.get("include") is False:
            continue
        if not isinstance(agent_context, dict) and not linked_resources:
            continue
        key_files = agent_context.get("key_files", []) if isinstance(agent_context, dict) else []
        entries.append({
            "node_id": node.id,
            "node_title": node.title,
            "node_type": node.type,
            "role": agent_context.get("role") if isinstance(agent_context, dict) else None,
            "key_files": key_files or [],
            "key_questions": agent_context.get("key_questions", []) if isinstance(agent_context, dict) else [],
            "next_action_hint": agent_context.get("next_action_hint") if isinstance(agent_context, dict) else None,
            "linked_resources": linked_resources,
        })
    return entries


def build_focus_context(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = current if current is not None else load_yaml(root / "current_state.yaml")
    focus_node_id = focus_node_id_from_current(current, nodes)
    focus_node = nodes.get(focus_node_id) if focus_node_id else None
    path_ids = [
        str(node_id)
        for node_id in current.get("current_focus_path", []) or []
        if str(node_id) in nodes
    ]

    parent_ids: list[str] = []
    child_ids: list[str] = []
    sibling_ids: list[str] = []
    experiment_ids: list[str] = []
    decision_ids: list[str] = []
    artifact_ids: list[str] = []
    blockers: list[str] = []
    focus_related_ids: list[str] = []

    if focus_node:
        focus_related_ids.append(focus_node.id)
        if focus_node.parent and focus_node.parent in nodes:
            parent_ids.append(focus_node.parent)
            parent = nodes[focus_node.parent]
            sibling_ids.extend(child for child in _child_ids(nodes, parent) if child != focus_node.id)

        child_ids.extend(_child_ids(nodes, focus_node))
        focus_related_ids.extend(parent_ids)
        focus_related_ids.extend(child_ids)
        focus_related_ids.extend(sibling_ids)

        current_best_option = focus_node.raw.get("current_best_option") or current.get("current_option")
        option_ids = [
            node_id
            for node_id in _unique_strings([current_best_option, current.get("current_option")])
            if node_id in nodes and nodes[node_id].type == "option"
        ]
        search_parent_ids = set([focus_node.id] + option_ids)

        for node in sorted(nodes.values(), key=lambda item: item.id):
            if node.type == "experiment" and node.parent in search_parent_ids:
                experiment_ids.append(node.id)
            if node.type == "decision" and node.parent in search_parent_ids:
                decision_ids.append(node.id)

        focus_related_ids.extend(experiment_ids)
        focus_related_ids.extend(decision_ids)

        for node_id in focus_related_ids:
            node = nodes.get(node_id)
            if not node:
                continue
            for artifact_id in node.raw.get("linked_artifacts", []) or []:
                if artifact_id in nodes and nodes[artifact_id].type == "artifact":
                    artifact_ids.append(str(artifact_id))
            dataset_id = node.raw.get("dataset")
            if dataset_id in nodes and nodes[dataset_id].type == "artifact":
                artifact_ids.append(str(dataset_id))

        blockers = _unique_strings(focus_node.raw.get("blockers", []) or [])
    else:
        current_best_option = current.get("current_option")

    next_actions = _unique_strings(
        (current.get("next_actions", []) or [])
        + (focus_node.raw.get("next_actions", []) if focus_node else [])
    )
    knowledge_node_ids = [focus_node_id or ""] + path_ids + parent_ids + child_ids + experiment_ids + decision_ids + artifact_ids

    suggested_next_actions = [
        suggestion
        for suggestion in build_action_suggestions(root, nodes, current)
        if suggestion.get("is_focus_related")
    ]
    search_index = build_search_index(root, nodes, current)
    option_context_id: str | None = None
    if focus_node:
        try:
            option_context_id = _node_id_by_type_in_path(
                nodes,
                derive_focus_path(nodes, focus_node.id),
                "option",
                nearest=True,
            )
        except ValueError:
            option_context_id = None
    if not option_context_id:
        candidate_option_id = str(current_best_option or "")
        if candidate_option_id in nodes and nodes[candidate_option_id].type == "option":
            option_context_id = candidate_option_id
    option_workstream_context = (
        build_option_workstream_context(root, nodes, current, option_context_id)
        if option_context_id
        else None
    )

    return {
        "metadata": build_context_metadata(root, current),
        "focus_node": node_context(focus_node) if focus_node else None,
        "focus_path": _ordered_node_contexts(nodes, path_ids),
        "focus_path_ids": path_ids,
        "overview": {
            "project_name": "Research Cockpit Demo",
            "current_stage": current.get("current_stage"),
            "current_stage_title": node_title(nodes, current.get("current_stage")),
            "current_problem": current.get("current_problem"),
            "current_problem_title": node_title(nodes, current.get("current_problem")),
            "current_option": current.get("current_option"),
            "current_option_title": node_title(nodes, current.get("current_option")),
            "current_focus_node": focus_node_id,
            "current_focus_node_title": node_title(nodes, focus_node_id),
            "current_hypothesis": current.get("current_hypothesis"),
            "open_risks": current.get("open_risks", []),
            "focus_mode": focus_mode_from_current(current),
        },
        "local_neighbors": {
            "parents": _ordered_node_contexts(nodes, parent_ids),
            "children": _ordered_node_contexts(nodes, child_ids),
            "siblings": _ordered_node_contexts(nodes, sibling_ids),
            "experiments": _ordered_node_contexts(nodes, experiment_ids),
            "decisions": _ordered_node_contexts(nodes, decision_ids),
            "artifacts": _ordered_node_contexts(nodes, artifact_ids),
            "blockers": blockers,
        },
        "current_best_option": current_best_option,
        "blockers": blockers,
        "next_actions": next_actions,
        "knowledge_index": _knowledge_index(nodes, knowledge_node_ids),
        "suggested_next_actions": suggested_next_actions,
        "search_index_summary": build_search_index_summary(search_index),
        "option_workstream_context": option_workstream_context,
        "saved_graph_views": load_graph_views(root),
        "recent_interactions": recent_interactions(root),
    }


def build_current_state_payload(root: Path, nodes: dict[str, ResearchNode], current: dict[str, Any] | None = None) -> dict[str, Any]:
    current = current if current is not None else load_yaml(root / "current_state.yaml")
    focus_node_id = focus_node_id_from_current(current, nodes)
    return {
        "current_stage": current.get("current_stage"),
        "current_stage_title": node_title(nodes, current.get("current_stage")),
        "current_problem": current.get("current_problem"),
        "current_problem_title": node_title(nodes, current.get("current_problem")),
        "current_option": current.get("current_option"),
        "current_option_title": node_title(nodes, current.get("current_option")),
        "current_focus_node": focus_node_id,
        "current_focus_node_title": node_title(nodes, focus_node_id),
        "focus_mode": focus_mode_from_current(current),
        "current_focus_path": current.get("current_focus_path", []) or [],
        "current_hypothesis": current.get("current_hypothesis"),
        "open_risks": current.get("open_risks", []),
        "next_actions": current.get("next_actions", []),
        "updated_at": current.get("updated_at"),
        "saved_graph_views": load_graph_views(root),
        "recent_interactions": recent_interactions(root),
        "linked_nodes": [
            node_context(nodes[node_id])
            for node_id in current.get("current_focus_path", []) or []
            if node_id in nodes
        ],
    }


def build_option_subtree(nodes: dict[str, ResearchNode], option_id: str) -> dict[str, Any]:
    if option_id not in nodes:
        raise ValueError(f"Option node does not exist: {option_id}")
    if nodes[option_id].type != "option":
        raise ValueError(f"Node {option_id} must be option, got {nodes[option_id].type}")

    node_ids: list[str] = []
    seen: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in seen or node_id not in nodes:
            return
        seen.add(node_id)
        node_ids.append(node_id)
        for child_id in _child_ids(nodes, nodes[node_id]):
            visit(child_id)

    visit(option_id)
    by_type = {
        "problem": [],
        "option": [],
        "experiment": [],
        "decision": [],
        "artifact": [],
    }
    for node_id in node_ids:
        node_type = nodes[node_id].type
        if node_type in by_type:
            by_type[node_type].append(node_id)

    return {
        "root_option_id": option_id,
        "node_ids": node_ids,
        "problem_ids": by_type["problem"],
        "option_ids": by_type["option"],
        "experiment_ids": by_type["experiment"],
        "decision_ids": by_type["decision"],
        "artifact_ids": by_type["artifact"],
    }


def _experiment_ids_for_option(nodes: dict[str, ResearchNode], option_id: str) -> list[str]:
    if option_id not in nodes or nodes[option_id].type != "option":
        return []
    return build_option_subtree(nodes, option_id)["experiment_ids"]


def _latest_experiment_result(nodes: dict[str, ResearchNode], experiment_ids: list[str]) -> str | None:
    latest: str | None = None
    for experiment_id in experiment_ids:
        experiment = nodes[experiment_id]
        latest = experiment.raw.get("result_summary") or experiment.raw.get("outcome") or experiment.summary or latest
    return latest


def _has_experiment_evidence(experiment: ResearchNode) -> bool:
    return bool(
        experiment.raw.get("findings")
        or experiment.raw.get("result_summary")
        or experiment.raw.get("outcome")
    )


def _evidence_experiment_ids(nodes: dict[str, ResearchNode], node_id: str) -> list[str]:
    if node_id not in nodes:
        return []
    node = nodes[node_id]
    if node.type == "experiment":
        return [node.id]
    if node.type == "option":
        return _experiment_ids_for_option(nodes, node.id)
    if node.type != "decision":
        return []

    experiment_ids = _unique_strings(node.raw.get("supporting_experiments", []) or [])
    option_id = node.parent if node.parent in nodes and nodes[node.parent].type == "option" else None
    if option_id:
        experiment_ids = _unique_strings(experiment_ids + _experiment_ids_for_option(nodes, str(option_id)))
    return [experiment_id for experiment_id in experiment_ids if experiment_id in nodes and nodes[experiment_id].type == "experiment"]


def _validate_experiment_refs(nodes: dict[str, ResearchNode], experiment_ids: list[str], field_name: str) -> None:
    for experiment_id in experiment_ids:
        if experiment_id not in nodes:
            raise ValueError(f"{field_name} references missing node {experiment_id}")
        if nodes[experiment_id].type != "experiment":
            raise ValueError(f"{field_name} reference {experiment_id} must be experiment, got {nodes[experiment_id].type}")


def _has_list_items(value: Any) -> bool:
    return isinstance(value, list) and any(str(item).strip() for item in value)


def _acceptance_check(
    check_id: str,
    label: str,
    passed: bool,
    reason: str,
    *,
    blocking: bool = True,
    related_node_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "state": "pass" if passed else "fail",
        "reason": reason,
        "blocking": blocking,
        "related_node_ids": related_node_ids or [],
    }


def _acceptance_warning(
    check_id: str,
    label: str,
    reason: str,
    *,
    related_node_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "state": "warning",
        "reason": reason,
        "blocking": False,
        "related_node_ids": related_node_ids or [],
    }


def build_decision_acceptance_checklist(nodes: dict[str, ResearchNode], decision_id: str) -> dict[str, Any]:
    if decision_id not in nodes:
        raise ValueError(f"Decision node does not exist: {decision_id}")
    decision = nodes[decision_id]
    if decision.type != "decision":
        raise ValueError(f"Node {decision_id} must be decision, got {decision.type}")

    checks: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    related_ids = [decision_id]

    option_id = decision.parent
    option = nodes.get(str(option_id)) if option_id else None
    has_option_parent = bool(option and option.type == "option")
    checks.append(_acceptance_check(
        "decision_parent",
        "Decision parent is an option",
        has_option_parent,
        "Decision parent resolves to an option node." if has_option_parent else "Decision parent must be an option node.",
        related_node_ids=[str(option_id)] if option_id else [],
    ))

    problem_id = option.parent if has_option_parent and option else None
    problem = nodes.get(str(problem_id)) if problem_id else None
    has_problem_parent = bool(problem and problem.type == "problem")
    checks.append(_acceptance_check(
        "problem_parent",
        "Option parent is a problem",
        has_problem_parent,
        "Option parent resolves to a problem node." if has_problem_parent else "Accepted decisions must resolve back to a problem node.",
        related_node_ids=[str(problem_id)] if problem_id else [],
    ))

    supporting_experiments = _unique_strings(decision.raw.get("supporting_experiments", []) or [])
    invalid_experiments = [
        experiment_id
        for experiment_id in supporting_experiments
        if experiment_id not in nodes or nodes[experiment_id].type != "experiment"
    ]
    experiments_ok = bool(supporting_experiments) and not invalid_experiments
    checks.append(_acceptance_check(
        "supporting_experiments",
        "Supporting experiments are present",
        experiments_ok,
        "Supporting experiments are present and valid." if experiments_ok else (
            f"Invalid supporting experiment reference(s): {', '.join(invalid_experiments)}"
            if invalid_experiments else "At least one supporting experiment is required."
        ),
        related_node_ids=supporting_experiments,
    ))

    valid_experiment_ids = [
        experiment_id
        for experiment_id in supporting_experiments
        if experiment_id in nodes and nodes[experiment_id].type == "experiment"
    ]
    evidence_ok = any(_has_experiment_evidence(nodes[experiment_id]) for experiment_id in valid_experiment_ids)
    checks.append(_acceptance_check(
        "supporting_evidence",
        "Supporting experiments contain evidence",
        evidence_ok,
        "At least one supporting experiment has findings, result_summary, or outcome."
        if evidence_ok else "At least one supporting experiment must contain findings, result_summary, or outcome.",
        related_node_ids=valid_experiment_ids,
    ))

    strength = str(decision.raw.get("evidence_strength") or "none")
    strength_ok = strength in VALID_FINDING_CONFIDENCES and strength != "none"
    checks.append(_acceptance_check(
        "evidence_strength",
        "Evidence strength is set",
        strength_ok,
        f"Evidence strength is {strength}." if strength_ok else "Evidence strength must be weak, medium, or strong.",
        related_node_ids=related_ids,
    ))
    if strength == "weak":
        warning = _acceptance_warning(
            "weak_evidence",
            "Evidence is weak",
            "Weak evidence is acceptable but should be reviewed before acceptance.",
            related_node_ids=related_ids,
        )
        checks.append(warning)
        warnings.append(warning)

    for check_id, label, field_name in (
        ("evidence_summary", "Evidence summary is present", "evidence_summary"),
        ("alternatives_considered", "Alternatives were considered", "alternatives_considered"),
        ("consequences", "Consequences are recorded", "consequences"),
        ("next_required_actions", "Next required actions are recorded", "next_required_actions"),
    ):
        value = decision.raw.get(field_name)
        if field_name == "evidence_summary":
            passed = bool(str(value or "").strip())
        else:
            passed = _has_list_items(value)
        checks.append(_acceptance_check(
            check_id,
            label,
            passed,
            f"{field_name} is present." if passed else f"{field_name} must be non-empty.",
            related_node_ids=related_ids,
        ))

    alternative_ids = _unique_strings(decision.raw.get("alternatives_considered", []) or [])
    invalid_alternatives = [
        option_id
        for option_id in alternative_ids
        if option_id not in nodes or nodes[option_id].type != "option"
    ]
    if invalid_alternatives:
        checks.append(_acceptance_check(
            "alternative_refs",
            "Alternative references are valid",
            False,
            f"Invalid alternative option reference(s): {', '.join(invalid_alternatives)}",
            related_node_ids=invalid_alternatives,
        ))

    blocking_failures = [
        check
        for check in checks
        if check["blocking"] and check["state"] == "fail"
    ]
    return {
        "decision_id": decision.id,
        "decision_title": decision.title,
        "status": decision.status,
        "ready": not blocking_failures,
        "checks": checks,
        "blocking_failures": blocking_failures,
        "warnings": warnings,
    }


def build_decision_acceptance_checklists(nodes: dict[str, ResearchNode]) -> list[dict[str, Any]]:
    return [
        build_decision_acceptance_checklist(nodes, node.id)
        for node in sorted(nodes.values(), key=lambda item: item.id)
        if node.type == "decision"
    ]


def decision_acceptance_failure_message(checklist: dict[str, Any]) -> str:
    failures = checklist.get("blocking_failures", []) or []
    reasons = "; ".join(str(item.get("reason", "")) for item in failures if item.get("reason"))
    return f"Decision {checklist.get('decision_id')} is not ready for acceptance: {reasons}"


def _evidence_strength_for_counts(
    findings: list[dict[str, Any]],
    outcome_counts: dict[str, int],
    has_result_summary: bool,
) -> str:
    positive_findings = [
        finding
        for finding in findings
        if finding.get("outcome") == "positive"
    ]
    if any(finding.get("confidence") == "strong" for finding in positive_findings):
        return "strong"
    if len(positive_findings) >= 2:
        return "strong"
    if positive_findings or outcome_counts.get("mixed", 0) > 0 or outcome_counts.get("positive", 0) > 0:
        return "medium"
    if findings or has_result_summary:
        return "weak"
    return "none"


def build_decision_evidence_bundle(
    nodes: dict[str, ResearchNode],
    option_id: str,
    supporting_experiments: list[str] | None = None,
) -> dict[str, Any]:
    if option_id not in nodes:
        raise ValueError(f"Option node does not exist: {option_id}")
    if nodes[option_id].type != "option":
        raise ValueError(f"Node {option_id} must be option, got {nodes[option_id].type}")

    manual_ids = _unique_strings(supporting_experiments or [])
    _validate_experiment_refs(nodes, manual_ids, "supporting_experiments")
    automatic_ids = [
        experiment_id
        for experiment_id in _experiment_ids_for_option(nodes, option_id)
        if _has_experiment_evidence(nodes[experiment_id])
    ]
    experiment_ids = _unique_strings(manual_ids + automatic_ids)

    findings: list[dict[str, Any]] = []
    outcome_counts: dict[str, int] = {}
    latest_finding: str | None = None
    has_result_summary = False
    for experiment_id in experiment_ids:
        experiment = nodes[experiment_id]
        if experiment.raw.get("result_summary"):
            has_result_summary = True
        experiment_findings = experiment.raw.get("findings", []) or []
        counted_finding_outcome = False
        if isinstance(experiment_findings, list):
            for finding in experiment_findings:
                if not isinstance(finding, dict):
                    continue
                findings.append(finding)
                if finding.get("statement"):
                    latest_finding = str(finding["statement"])
                if finding.get("outcome"):
                    outcome = str(finding["outcome"])
                    outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
                    counted_finding_outcome = True
        outcome = experiment.raw.get("outcome")
        if outcome and not counted_finding_outcome:
            outcome_counts[str(outcome)] = outcome_counts.get(str(outcome), 0) + 1

    strength = _evidence_strength_for_counts(findings, outcome_counts, has_result_summary)
    summary_parts = [
        f"{len(experiment_ids)} experiment(s)",
        f"{len(findings)} finding(s)",
    ]
    if outcome_counts:
        summary_parts.append(
            "outcomes: " + ", ".join(f"{key}={value}" for key, value in sorted(outcome_counts.items()))
        )
    if latest_finding:
        summary_parts.append(f"latest finding: {latest_finding}")
    return {
        "supporting_experiments": experiment_ids,
        "evidence_strength": strength,
        "evidence_summary": "; ".join(summary_parts),
        "findings_count": len(findings),
        "outcome_counts": outcome_counts,
        "latest_finding": latest_finding,
    }


def build_decision_evidence_summary(nodes: dict[str, ResearchNode], node_id: str) -> dict[str, Any]:
    experiment_ids = _evidence_experiment_ids(nodes, node_id)
    findings_count = 0
    latest_finding: str | None = None
    outcome_counts: dict[str, int] = {}
    for experiment_id in experiment_ids:
        experiment = nodes[experiment_id]
        findings = experiment.raw.get("findings", []) or []
        counted_finding_outcome = False
        if isinstance(findings, list):
            findings_count += len(findings)
            for finding in findings:
                if isinstance(finding, dict) and finding.get("outcome"):
                    finding_outcome = str(finding["outcome"])
                    outcome_counts[finding_outcome] = outcome_counts.get(finding_outcome, 0) + 1
                    counted_finding_outcome = True
                if isinstance(finding, dict) and finding.get("statement"):
                    latest_finding = str(finding["statement"])
        outcome = experiment.raw.get("outcome")
        if outcome and not counted_finding_outcome:
            outcome_counts[str(outcome)] = outcome_counts.get(str(outcome), 0) + 1
    return {
        "experiment_count": len(experiment_ids),
        "experiment_ids": experiment_ids,
        "findings_count": findings_count,
        "outcome_counts": outcome_counts,
        "latest_finding": latest_finding,
    }


def _upstream_problem_id(nodes: dict[str, ResearchNode], option_id: str) -> str | None:
    try:
        path = derive_focus_path(nodes, option_id)
    except ValueError:
        return None
    return _node_id_by_type_in_path(nodes, path, "problem", nearest=True)


def build_option_workstream_context(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    option_id: str,
) -> dict[str, Any]:
    subtree = build_option_subtree(nodes, option_id)
    option = nodes[option_id]
    problem_id = _upstream_problem_id(nodes, option_id)
    evidence = build_decision_evidence_bundle(nodes, option_id)
    next_actions: list[str] = []
    blockers: list[str] = []
    for node_id in subtree["node_ids"]:
        node = nodes[node_id]
        next_actions.extend(node.raw.get("next_actions", []) or [])
        blockers.extend(node.raw.get("blockers", []) or [])

    return {
        "option": node_context(option),
        "upstream_problem": node_context(nodes[problem_id]) if problem_id else None,
        "focus_path": _ordered_node_contexts(nodes, derive_focus_path(nodes, option_id)),
        "subtree": subtree,
        "subtree_nodes": _ordered_node_contexts(nodes, subtree["node_ids"]),
        "problems": _ordered_node_contexts(nodes, subtree["problem_ids"]),
        "options": _ordered_node_contexts(nodes, subtree["option_ids"]),
        "experiments": _ordered_node_contexts(nodes, subtree["experiment_ids"]),
        "decisions": _ordered_node_contexts(nodes, subtree["decision_ids"]),
        "evidence_summary": {
            **evidence,
            "experiment_count": len(subtree["experiment_ids"]),
        },
        "open_next_actions": _unique_strings(next_actions),
        "blockers": _unique_strings(blockers),
        "suggested_commands": {
            "claim": _workflow_command(
                "claim_option.py",
                "--option",
                option_id,
                "--agent",
                "<agent_id>",
                '--objective "Describe objective"',
            ),
            "context": _workflow_command("option_workstream_context.py", "--option", option_id, "--json"),
            "report": _workflow_command(
                "report_option_workstream.py",
                "--option",
                option_id,
                "--agent",
                "<agent_id>",
                "--recommend",
                "continue",
                '--summary "Summarize evidence and recommendation"',
            ),
        },
        "current_focus_related": option_id in set(current.get("current_focus_path", []) or []),
    }


def build_option_workstream_rows(nodes: dict[str, ResearchNode]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    def option_sort_key(node: ResearchNode) -> tuple[int, str]:
        try:
            return (len(derive_focus_path(nodes, node.id)), node.id)
        except ValueError:
            return (999, node.id)

    for option in sorted((node for node in nodes.values() if node.type == "option"), key=option_sort_key):
        subtree = build_option_subtree(nodes, option.id)
        evidence = build_decision_evidence_bundle(nodes, option.id)
        problem_id = _upstream_problem_id(nodes, option.id)
        workstream = option.raw.get("agent_workstream") if isinstance(option.raw.get("agent_workstream"), dict) else {}
        report = option.raw.get("workstream_report") if isinstance(option.raw.get("workstream_report"), dict) else {}
        rows.append({
            "option_id": option.id,
            "option_title": option.title,
            "option_status": option.status,
            "upstream_problem_id": problem_id,
            "upstream_problem_title": node_title(nodes, problem_id),
            "owner": workstream.get("owner"),
            "workstream_status": workstream.get("status"),
            "objective": workstream.get("objective"),
            "report_to_problem": workstream.get("report_to_problem") or problem_id,
            "started_at": workstream.get("started_at"),
            "updated_at": workstream.get("updated_at"),
            "recommendation": report.get("recommendation"),
            "report_summary": report.get("summary"),
            "evidence_summary": report.get("evidence_summary") or evidence.get("evidence_summary"),
            "experiment_count": len(subtree["experiment_ids"]),
            "finding_count": evidence.get("findings_count", 0),
            "latest_finding": evidence.get("latest_finding"),
        })
    return rows


def build_branch_comparison(
    nodes: dict[str, ResearchNode],
    problem_id: str | None = None,
    current: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    current = current or {}
    problem_id = problem_id or current.get("current_problem")
    if not problem_id or problem_id not in nodes or nodes[problem_id].type != "problem":
        return []

    problem = nodes[problem_id]
    option_ids = [
        child_id
        for child_id in _child_ids(nodes, problem)
        if child_id in nodes and nodes[child_id].type == "option"
    ]
    current_best_option = problem.raw.get("current_best_option") or current.get("current_option")
    rows: list[dict[str, Any]] = []
    for option_id in option_ids:
        option = nodes[option_id]
        experiment_ids = _experiment_ids_for_option(nodes, option_id)
        rows.append({
            "id": option.id,
            "title": option.title,
            "status": option.status,
            "decision_state": option.raw.get("decision_state"),
            "evidence_strength": option.raw.get("evidence_strength"),
            "pros": option.raw.get("pros", []) or [],
            "cons": option.raw.get("cons", []) or [],
            "experiment_count": len(experiment_ids),
            "latest_result": _latest_experiment_result(nodes, experiment_ids),
            "rejection_reason": option.raw.get("rejection_reason") or option.raw.get("current_conclusion"),
            "is_current_best": option.id == current_best_option,
        })
    return rows


def build_decision_trace(nodes: dict[str, ResearchNode], decision_id: str) -> dict[str, Any]:
    if decision_id not in nodes:
        raise ValueError(f"Decision node does not exist: {decision_id}")
    decision = nodes[decision_id]
    if decision.type != "decision":
        raise ValueError(f"Node {decision_id} must be decision, got {decision.type}")

    path = derive_focus_path(nodes, decision_id)
    stage_id = _node_id_by_type_in_path(nodes, path, "stage")
    problem_id = _node_id_by_type_in_path(nodes, path, "problem", nearest=True)
    option_id = _node_id_by_type_in_path(nodes, path, "option", nearest=True)

    supporting_experiment_ids = _unique_strings(decision.raw.get("supporting_experiments", []) or [])
    if option_id:
        supporting_experiment_ids = _unique_strings(
            supporting_experiment_ids + _experiment_ids_for_option(nodes, option_id)
        )
    alternative_ids = _unique_strings(decision.raw.get("alternatives_considered", []) or [])

    return {
        "decision": node_context(decision),
        "stage": node_context(nodes[stage_id]) if stage_id else None,
        "problem": node_context(nodes[problem_id]) if problem_id else None,
        "option": node_context(nodes[option_id]) if option_id else None,
        "focus_path": _ordered_node_contexts(nodes, path),
        "supporting_experiments": _ordered_node_contexts(nodes, supporting_experiment_ids),
        "alternatives_considered": _ordered_node_contexts(nodes, alternative_ids),
        "consequences": decision.raw.get("consequences", []) or [],
        "evidence_summary": {
            **build_decision_evidence_summary(nodes, decision_id),
            "summary_text": decision.raw.get("evidence_summary"),
        },
    }


def build_experiment_matrix(nodes: dict[str, ResearchNode]) -> list[dict[str, Any]]:
    rows = []
    for node in sorted(nodes.values(), key=lambda item: item.id):
        if node.type != "experiment":
            continue
        findings = node.raw.get("findings", []) or []
        latest_finding = findings[-1].get("statement") if findings and isinstance(findings[-1], dict) else None
        rows.append({
            "id": node.id,
            "title": node.title,
            "status": node.status,
            "dataset": node.raw.get("dataset"),
            "backbone": node.raw.get("backbone"),
            "parent": node.parent,
            "summary": node.summary,
            "result": node.raw.get("result_summary"),
            "outcome": node.raw.get("outcome"),
            "findings_count": len(findings) if isinstance(findings, list) else 0,
            "latest_finding": latest_finding,
        })
    return rows


def build_decision_rows(nodes: dict[str, ResearchNode]) -> list[dict[str, Any]]:
    rows = []
    for node in sorted(nodes.values(), key=lambda item: item.id):
        if node.type != "decision":
            continue
        rows.append({
            "id": node.id,
            "title": node.title,
            "status": node.status,
            "parent": node.parent,
            "summary": node.summary,
            "supporting_experiments": node.raw.get("supporting_experiments", []),
            "consequences": node.raw.get("consequences", []),
        })
    return rows


def write_dashboard_markdown(root: Path, context: dict[str, Any]) -> None:
    lines = []
    lines.append("# Research Dashboard\n")
    lines.append("## Current Focus\n")
    lines.append(f"- **Stage:** `{context.get('current_stage')}`")
    lines.append(f"- **Problem:** `{context.get('current_problem')}`")
    lines.append(f"- **Option:** `{context.get('current_option')}`")
    lines.append("")
    lines.append("## Current Hypothesis\n")
    lines.append(str(context.get("current_hypothesis") or ""))
    lines.append("")
    lines.append("## Open Risks\n")
    for item in context.get("open_risks", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Next Actions\n")
    for item in context.get("next_actions", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Active Problems\n")
    for p in context.get("active_problems", []):
        lines.append(f"- **{p['title']}** (`{p['id']}`): {p.get('summary','')}")
    lines.append("")
    lines.append("## Recent Decisions\n")
    for d in context.get("recent_decisions", []):
        lines.append(f"- **{d['title']}** (`{d['status']}`): {d.get('summary','')}")
    (root / "dashboards").mkdir(parents=True, exist_ok=True)
    (root / "dashboards" / "current_state.md").write_text("\n".join(lines), encoding="utf-8")
