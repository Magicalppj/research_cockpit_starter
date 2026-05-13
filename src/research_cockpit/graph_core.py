from __future__ import annotations

from pathlib import Path
from typing import Any
import networkx as nx

from research_cockpit.storage import load_yaml
from research_cockpit.types import (
    DEFAULT_FOCUS_MODE,
    DEFAULT_STATUS_BY_TYPE,
    STATUS_COLORS,
    TYPE_SHAPES,
    VALID_STATUSES_BY_TYPE,
    ResearchNode,
    ValidationError,
)
from research_cockpit.resources import node_link_entries


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


def node_id_by_type_in_path(
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
        "current_stage": node_id_by_type_in_path(nodes, path, "stage") or current.get("current_stage"),
        "current_problem": node_id_by_type_in_path(nodes, path, "problem", nearest=True)
        or current.get("current_problem"),
        "current_option": node_id_by_type_in_path(nodes, path, "option", nearest=True)
        or current.get("current_option"),
        "current_focus_node": focus_node_id,
        "current_focus_path": path,
    }


def child_ids(nodes: dict[str, ResearchNode], node: ResearchNode) -> list[str]:
    ids = [child_id for child_id in node.children if child_id in nodes]
    for candidate in sorted(nodes.values(), key=lambda item: item.id):
        if candidate.parent == node.id and candidate.id not in ids:
            ids.append(candidate.id)
    return ids


def graph_child_ids(nodes: dict[str, ResearchNode], node_id: str) -> list[str]:
    return child_ids(nodes, nodes[node_id])


def graph_descendant_ids(nodes: dict[str, ResearchNode], node_id: str) -> set[str]:
    descendants: set[str] = set()
    stack = list(graph_child_ids(nodes, node_id)) if node_id in nodes else []
    while stack:
        child_id = stack.pop()
        if child_id in descendants or child_id not in nodes:
            continue
        descendants.add(child_id)
        stack.extend(graph_child_ids(nodes, child_id))
    return descendants


def safe_node_path(nodes: dict[str, ResearchNode], node_id: str) -> list[str]:
    try:
        return derive_focus_path(nodes, node_id)
    except ValueError:
        return [node_id] if node_id in nodes else []


def node_has_evidence(node: ResearchNode) -> bool:
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


def unique_strings(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in (None, ""):
            continue
        text = str(value)
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def node_context(node: ResearchNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "type": node.type,
        "title": node.title,
        "status": node.status,
        "priority": node.priority,
        "order": node.raw.get("order"),
        "rank": node.raw.get("rank"),
        "summary": node.summary,
        "question": node.raw.get("question"),
        "hypothesis": node.raw.get("hypothesis"),
        "evidence_strength": node.raw.get("evidence_strength"),
        "evidence_summary": node.raw.get("evidence_summary"),
        "current_best_option": node.raw.get("current_best_option"),
        "baseline": node.raw.get("baseline"),
        "decision_state": node.raw.get("decision_state"),
        "outcome": node.raw.get("outcome"),
        "result_summary": node.raw.get("result_summary"),
        "supporting_experiments": node.raw.get("supporting_experiments", []),
        "contradicting_experiments": node.raw.get("contradicting_experiments", []),
        "supporting_decisions": node.raw.get("supporting_decisions", []),
        "linked_artifacts": node.raw.get("linked_artifacts", []),
        "links": node_link_entries(node),
        "findings": node.raw.get("findings", []),
        "implementation_steps": node.raw.get("implementation_steps", []),
        "success_criteria": node.raw.get("success_criteria", []),
        "agent_workstream": node.raw.get("agent_workstream"),
        "workstream_report": node.raw.get("workstream_report"),
        "owner": node.raw.get("owner"),
        "ready_for_agent": node.raw.get("ready_for_agent"),
        "depends_on": node.raw.get("depends_on", []),
        "blocked_by": node.raw.get("blocked_by", []),
        "handoff_context": node.raw.get("handoff_context"),
        "agent_context": node.raw.get("agent_context"),
        "next_actions": node.raw.get("next_actions", []),
        "blockers": node.raw.get("blockers", []),
    }


def ordered_node_contexts(nodes: dict[str, ResearchNode], node_ids: list[str]) -> list[dict[str, Any]]:
    return [node_context(nodes[node_id]) for node_id in node_ids if node_id in nodes]


def node_title(nodes: dict[str, ResearchNode], node_id: str | None) -> str | None:
    if node_id and node_id in nodes:
        return nodes[node_id].title
    return None


def focus_related_ids(nodes: dict[str, ResearchNode], current: dict[str, Any]) -> set[str]:
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
        related.update(child_id for child_id in child_ids(nodes, node) if child_id in nodes)
    return related


def _graph_interaction_metadata(
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    current = current or {}
    focus_node_id = focus_node_id_from_current(current, nodes) if current else None
    current_branch_ids = set(str(node_id) for node_id in current.get("current_focus_path", []) or [])
    if focus_node_id and focus_node_id in nodes:
        current_branch_ids.add(focus_node_id)
        current_branch_ids.update(graph_descendant_ids(nodes, focus_node_id))

    metadata: dict[str, dict[str, Any]] = {}
    for node in nodes.values():
        path = safe_node_path(nodes, node.id)
        stage_id = node_id_by_type_in_path(nodes, path, "stage")
        problem_id = node_id_by_type_in_path(nodes, path, "problem", nearest=True)
        option_id = node_id_by_type_in_path(nodes, path, "option", nearest=True)
        upstream_problem_id = None
        if option_id and option_id in nodes:
            option_path = safe_node_path(nodes, option_id)
            upstream_problem_id = node_id_by_type_in_path(nodes, option_path, "problem", nearest=True)

        metadata[node.id] = {
            "stage_id": stage_id,
            "problem_id": problem_id,
            "option_workstream_id": option_id,
            "option_workstream_upstream_problem_id": upstream_problem_id,
            "in_current_branch": node.id in current_branch_ids,
            "has_blockers": bool(node.raw.get("blockers")),
            "has_next_actions": bool(node.raw.get("next_actions")),
            "has_evidence": node_has_evidence(node),
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
                for sibling_id in graph_child_ids(nodes, str(focus_node.parent)):
                    if sibling_id != focus_node_id:
                        include(sibling_id, 1, "sibling")

        focus_index = focus_path.index(focus_node_id) if focus_node_id in focus_path else -1
        for index, node_id in enumerate(focus_path):
            if node_id == focus_node_id:
                continue
            role = "parent" if focus_index == -1 or index < focus_index else "child"
            include(node_id, 1, role)

        child_ids_for_focus = graph_child_ids(nodes, focus_node_id)
        for child_id in child_ids_for_focus:
            include(child_id, 1, "child")

        current_best_option = focus_node.raw.get("current_best_option") or current.get("current_option")
        option_ids = [
            node_id
            for node_id in unique_strings([current_best_option, current.get("current_option"), focus_node_id])
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
    from research_cockpit.baselines import build_graph_baseline_metadata

    current_focus_path = current_focus_path or []
    focus_set = set(current_focus_path)
    focus_metadata = _focus_graph_metadata(nodes, current_focus_path, current)
    interaction_metadata = _graph_interaction_metadata(nodes, current)
    baseline_metadata = build_graph_baseline_metadata(nodes, current)
    current_focus_node = focus_node_id_from_current(current or {}, nodes) if current else None

    out_nodes = []
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
            **baseline_metadata.get(node.id, {}),
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
