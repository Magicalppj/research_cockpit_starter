from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
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

DEFAULT_FOCUS_MODE = {
    "default_depth": 2,
    "hide_statuses": ["rejected", "parked", "archived"],
    "show_resolved": False,
    "show_rejected": False,
    "show_parked": False,
}

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
            "raw": node.raw,
        })
    out_edges = iter_graph_edges(nodes, explicit_edges)
    return {
        "nodes": out_nodes,
        "edges": out_edges,
        "current_focus_path": current_focus_path,
        "current_focus_node": current_focus_node,
        "focus_mode": focus_mode_from_current(current or {}),
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


def _workflow_command(script_name: str, *parts: str) -> str:
    command = [r"D:\Tools\miniconda3\envs\aigc\python.exe", fr"scripts\{script_name}"]
    command.extend(parts)
    return " ".join(str(part) for part in command if part not in ("", None))


def _priority_rank(priority: str | None) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(priority or "").lower(), 2)


def _suggestion_priority(node: ResearchNode | None, default: str = "medium") -> str:
    if node and str(node.priority or "").lower() in {"critical", "high", "medium", "low"}:
        return str(node.priority).lower()
    return default


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
    for index, suggestion in enumerate(deduped, start=1):
        suggestion["id"] = f"next_action_{index:03d}"
    return deduped


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

    return _mark_queued_suggestions(_finalize_suggestions(suggestions), nodes, current)


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

    return {
        "project_name": "Audio Edit Research Cockpit",
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
        "suggested_next_actions": build_action_suggestions(root, nodes, current),
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

    return {
        "focus_node": node_context(focus_node) if focus_node else None,
        "focus_path": _ordered_node_contexts(nodes, path_ids),
        "focus_path_ids": path_ids,
        "overview": {
            "project_name": "Audio Edit Research Cockpit",
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
        "linked_nodes": [
            node_context(nodes[node_id])
            for node_id in current.get("current_focus_path", []) or []
            if node_id in nodes
        ],
    }


def _experiment_ids_for_option(nodes: dict[str, ResearchNode], option_id: str) -> list[str]:
    child_ids = _child_ids(nodes, nodes[option_id]) if option_id in nodes else []
    experiment_ids = [
        child_id
        for child_id in child_ids
        if child_id in nodes and nodes[child_id].type == "experiment"
    ]
    for node in sorted(nodes.values(), key=lambda item: item.id):
        if node.type == "experiment" and node.parent == option_id and node.id not in experiment_ids:
            experiment_ids.append(node.id)
    return experiment_ids


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
