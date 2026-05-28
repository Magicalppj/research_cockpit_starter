from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import networkx as nx
import yaml

from research_cockpit.command_registry import cli_command_for_script
from research_cockpit.context_packs import (
    build_agent_context,
    build_context_metadata,
    build_current_state_payload,
    build_focus_context,
    write_dashboard_markdown,
)
from research_cockpit.decisions import (
    build_decision_acceptance_checklist,
    build_decision_acceptance_checklists,
    build_decision_evidence_bundle,
    build_decision_evidence_summary,
    build_decision_rows,
    build_decision_trace,
    decision_acceptance_failure_message,
)
from research_cockpit.graph_views import graph_view_id_from_title, load_graph_views, upsert_graph_view
from research_cockpit.graph_core import GraphTopology, unique_strings
from research_cockpit.interaction_log import append_interaction_log, load_interaction_log, recent_interactions, validate_interaction_log
from research_cockpit.option_workstreams import (
    build_branch_comparison,
    build_option_subtree,
    build_option_workstream_context,
    build_option_workstream_rows,
)
from research_cockpit.resources import build_link_rows, node_link_entries as _node_link_entries
from research_cockpit.storage import load_yaml, save_yaml
from research_cockpit.suggestions import (
    build_action_suggestions,
    build_suggestion_lifecycle_rows,
    build_suggestion_lifecycle_summary,
    suggestion_key,
)


VALID_COMMAND_STYLES = {"console", "python"}
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
VALID_RUN_STATUSES = {"queued", "running", "completed", "failed", "cancelled"}
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
    "owner",
    "handoff_context",
    "depends_on",
    "blocked_by",
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
    "collapsed_branch_roots",
    "revealed_child_roots",
)

GRAPH_VIEW_FILTER_BOOL_KEYS = (
    "only_blocking",
    "only_next_actions",
    "only_missing_evidence",
    "show_baseline_lens",
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


@dataclass
class RunRecord:
    run_id: str
    status: str
    experiment_id: str
    started_at: str | None = None
    finished_at: str | None = None
    launcher: str | None = None
    command: str | None = None
    tmux_session: str | None = None
    pid: int | str | None = None
    log_root: str | None = None
    output_root: str | None = None
    monitor_command: str | None = None
    stop_command: str | None = None
    progress_file: str | None = None
    config_file: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRecord":
        def optional_str(key: str) -> str | None:
            value = data.get(key)
            return None if value is None else str(value)

        return cls(
            run_id=str(data["run_id"]),
            status=str(data.get("status", "")),
            experiment_id=str(data.get("experiment_id", "")),
            started_at=optional_str("started_at"),
            finished_at=optional_str("finished_at"),
            launcher=optional_str("launcher"),
            command=optional_str("command"),
            tmux_session=optional_str("tmux_session"),
            pid=data.get("pid"),
            log_root=optional_str("log_root"),
            output_root=optional_str("output_root"),
            monitor_command=optional_str("monitor_command"),
            stop_command=optional_str("stop_command"),
            progress_file=optional_str("progress_file"),
            config_file=optional_str("config_file"),
            raw=data,
        )


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


def load_runs(root: Path) -> dict[str, RunRecord]:
    run_dir = root / "runs"
    runs: dict[str, RunRecord] = {}
    if not run_dir.exists():
        return runs
    for path in sorted(run_dir.glob("*.yaml")):
        rel_path = f"runs/{path.name}"
        try:
            data = load_yaml(path)
        except (OSError, yaml.YAMLError) as exc:
            raise ValidationError([f"{rel_path}: YAML parse error: {exc}"]) from exc
        if not data:
            continue
        if not isinstance(data, dict):
            raise ValidationError([f"{rel_path}: run record must be a mapping"])
        if data.get("run_id") in (None, ""):
            raise ValidationError([f"{rel_path}: missing required field 'run_id'"])
        try:
            run = RunRecord.from_dict(data)
        except KeyError as exc:
            raise ValidationError([f"{rel_path}: missing required field {exc.args[0]!r}"]) from exc
        if run.run_id in runs:
            raise ValidationError([f"{rel_path}: duplicate run id {run.run_id!r}"])
        runs[run.run_id] = run
    return runs


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


def _graph_child_ids(
    nodes: dict[str, ResearchNode],
    node_id: str,
    *,
    topology: GraphTopology | None = None,
) -> list[str]:
    if topology is not None:
        return topology.child_ids(node_id)
    node = nodes[node_id]
    child_ids = [child for child in node.children if child in nodes]
    implicit_children = sorted(
        candidate.id
        for candidate in nodes.values()
        if candidate.parent == node_id and candidate.id not in child_ids
    )
    return child_ids + implicit_children


def _graph_descendant_ids(
    nodes: dict[str, ResearchNode],
    node_id: str,
    *,
    topology: GraphTopology | None = None,
) -> set[str]:
    if topology is not None:
        return topology.descendant_ids(node_id)
    descendants: set[str] = set()
    stack = list(_graph_child_ids(nodes, node_id)) if node_id in nodes else []
    while stack:
        child_id = stack.pop()
        if child_id in descendants or child_id not in nodes:
            continue
        descendants.add(child_id)
        stack.extend(_graph_child_ids(nodes, child_id))
    return descendants


def _safe_node_path(
    nodes: dict[str, ResearchNode],
    node_id: str,
    *,
    topology: GraphTopology | None = None,
) -> list[str]:
    if topology is not None:
        return topology.safe_path(node_id)
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
    *,
    topology: GraphTopology,
) -> dict[str, dict[str, Any]]:
    current = current or {}
    focus_node_id = focus_node_id_from_current(current, nodes) if current else None
    current_branch_ids = set(str(node_id) for node_id in current.get("current_focus_path", []) or [])
    if focus_node_id and focus_node_id in nodes:
        current_branch_ids.add(focus_node_id)
        current_branch_ids.update(_graph_descendant_ids(nodes, focus_node_id, topology=topology))

    metadata: dict[str, dict[str, Any]] = {}
    for node in nodes.values():
        path = _safe_node_path(nodes, node.id, topology=topology)
        stage_id = _node_id_by_type_in_path(nodes, path, "stage")
        problem_id = _node_id_by_type_in_path(nodes, path, "problem", nearest=True)
        option_id = _node_id_by_type_in_path(nodes, path, "option", nearest=True)
        upstream_problem_id = None
        agent_owner = None
        agent_session_id = None
        if option_id and option_id in nodes:
            option_path = _safe_node_path(nodes, option_id, topology=topology)
            upstream_problem_id = _node_id_by_type_in_path(nodes, option_path, "problem", nearest=True)
            workstream = nodes[option_id].raw.get("agent_workstream")
            if isinstance(workstream, dict):
                agent_owner = workstream.get("owner")
                agent_session_id = workstream.get("session_id")

        metadata[node.id] = {
            "stage_id": stage_id,
            "problem_id": problem_id,
            "option_workstream_id": option_id,
            "option_workstream_upstream_problem_id": upstream_problem_id,
            "agent_owner": agent_owner,
            "agent_session_id": agent_session_id,
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
        "agents": "agent_owner",
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
    *,
    topology: GraphTopology,
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
                for sibling_id in _graph_child_ids(nodes, str(focus_node.parent), topology=topology):
                    if sibling_id != focus_node_id:
                        include(sibling_id, 1, "sibling")

        focus_index = focus_path.index(focus_node_id) if focus_node_id in focus_path else -1
        for index, node_id in enumerate(focus_path):
            if node_id == focus_node_id:
                continue
            role = "parent" if focus_index == -1 or index < focus_index else "child"
            include(node_id, 1, role)

        child_ids = _graph_child_ids(nodes, focus_node_id, topology=topology)
        for child_id in child_ids:
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
    *,
    topology: GraphTopology | None = None,
    include_raw: bool = True,
) -> dict[str, Any]:
    from research_cockpit.baselines import build_graph_baseline_metadata

    current_focus_path = current_focus_path or []
    topology = topology or GraphTopology.from_nodes(nodes)
    focus_set = set(current_focus_path)
    focus_metadata = _focus_graph_metadata(nodes, current_focus_path, current, topology=topology)
    interaction_metadata = _graph_interaction_metadata(nodes, current, topology=topology)
    baseline_metadata = build_graph_baseline_metadata(nodes, current)
    current_focus_node = focus_node_id_from_current(current or {}, nodes) if current else None

    out_nodes = []
    out_edges = []
    for node in nodes.values():
        color = STATUS_COLORS.get(node.status, "#EEEEEE")
        row = {
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
        }
        if include_raw:
            row["raw"] = node.raw
        out_nodes.append(row)
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
    from research_cockpit.baselines import validate_baseline_for_node

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

    def validate_experiment_assignment(node: ResearchNode) -> None:
        assignment_fields = {"owner", "blocked_by", "depends_on", "ready_for_agent", "handoff_context"}
        present_fields = sorted(field_name for field_name in assignment_fields if field_name in node.raw)
        if not present_fields:
            return
        if node.type != "experiment":
            fields_text = ", ".join(present_fields)
            errors.append(f"{node.id}: assignment fields are only supported on experiment nodes: {fields_text}")
            return
        for field_name in ("owner", "handoff_context"):
            value = node.raw.get(field_name)
            if value is not None and not isinstance(value, str):
                errors.append(f"{node.id}: {field_name} must be a string")
        ready_for_agent = node.raw.get("ready_for_agent")
        if ready_for_agent is not None and type(ready_for_agent) is not bool:
            errors.append(f"{node.id}: ready_for_agent must be a boolean")
        validate_list_refs(node.id, "depends_on")
        validate_list_refs(node.id, "blocked_by")

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
        errors.extend(validate_baseline_for_node(nodes, node, node.raw.get("baseline")))
        validate_findings(node)
        validate_option_workstream(node)
        validate_experiment_assignment(node)
    return errors


def validate_runs(runs: dict[str, RunRecord], nodes: dict[str, ResearchNode]) -> list[str]:
    errors: list[str] = []
    for run in runs.values():
        if not run.run_id:
            errors.append("run has empty run_id")
        if run.status not in VALID_RUN_STATUSES:
            allowed = ", ".join(sorted(VALID_RUN_STATUSES))
            errors.append(f"{run.run_id}: invalid run status {run.status!r}; allowed: {allowed}")
        if not run.experiment_id:
            errors.append(f"{run.run_id}: experiment_id is required")
        elif run.experiment_id not in nodes:
            errors.append(f"{run.run_id}: experiment_id references missing node {run.experiment_id!r}")
        elif nodes[run.experiment_id].type != "experiment":
            errors.append(
                f"{run.run_id}: experiment_id references {run.experiment_id!r} "
                f"with type {nodes[run.experiment_id].type!r}; expected 'experiment'"
            )
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

    agent_focuses = current.get("agent_focuses")
    if agent_focuses is not None:
        if not isinstance(agent_focuses, dict):
            errors.append("current_state.agent_focuses must be a mapping")
        else:
            for agent_id, focus in agent_focuses.items():
                prefix = f"current_state.agent_focuses[{agent_id!r}]"
                if not str(agent_id).strip():
                    errors.append("current_state.agent_focuses contains an empty agent id")
                    continue
                if not isinstance(focus, dict):
                    errors.append(f"{prefix} must be a mapping")
                    continue
                focus_node = focus.get("current_focus_node")
                if focus_node and str(focus_node) not in nodes:
                    errors.append(f"{prefix}.current_focus_node references missing node {focus_node!r}")
                current_option = focus.get("current_option")
                if current_option:
                    if str(current_option) not in nodes:
                        errors.append(f"{prefix}.current_option references missing node {current_option!r}")
                    elif nodes[str(current_option)].type != "option":
                        errors.append(f"{prefix}.current_option must reference an option node")
                focus_path_value = focus.get("current_focus_path", []) or []
                if not isinstance(focus_path_value, list):
                    errors.append(f"{prefix}.current_focus_path must be a list")
                else:
                    for node_id in focus_path_value:
                        if node_id not in nodes:
                            errors.append(f"{prefix}.current_focus_path references missing node {node_id!r}")
                next_actions = focus.get("next_actions")
                if next_actions is not None and not isinstance(next_actions, list):
                    errors.append(f"{prefix}.next_actions must be a list")
                updated_at = focus.get("updated_at")
                if updated_at is not None and not isinstance(updated_at, str):
                    errors.append(f"{prefix}.updated_at must be a string")

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
    if isinstance(agent_focuses, dict):
        for agent_id, focus in agent_focuses.items():
            if not isinstance(focus, dict):
                continue
            focus_path_value = focus.get("current_focus_path", []) or []
            if not isinstance(focus_path_value, list):
                continue
            for parent, child in zip(focus_path_value, focus_path_value[1:]):
                if parent in nodes and child in nodes and (parent, child) not in edge_pairs:
                    errors.append(
                        f"current_state.agent_focuses[{agent_id!r}].current_focus_path "
                        f"has disconnected step {parent!r} -> {child!r}"
                    )
    return errors


def validate_cockpit(
    root: Path,
    nodes: dict[str, ResearchNode] | None = None,
    current: dict[str, Any] | None = None,
    explicit_edges: list[dict[str, Any]] | None = None,
    *,
    runs: dict[str, RunRecord] | None = None,
    include_interaction_log: bool = False,
    raise_on_error: bool = False,
) -> list[str]:
    nodes = nodes if nodes is not None else load_nodes(root)
    current = current if current is not None else load_yaml(root / "current_state.yaml")
    explicit_edges = explicit_edges if explicit_edges is not None else load_explicit_edges(root)
    run_load_errors: list[str] = []
    if runs is None:
        try:
            runs = load_runs(root)
        except ValidationError as exc:
            runs = {}
            run_load_errors = exc.errors
    errors = validate_nodes(nodes)
    errors.extend(validate_explicit_edges(nodes, explicit_edges))
    errors.extend(validate_current_state(current, nodes, explicit_edges))
    errors.extend(run_load_errors)
    errors.extend(validate_runs(runs, nodes))
    if include_interaction_log:
        errors.extend(validate_interaction_log(root))
    if errors and raise_on_error:
        raise ValidationError(errors)
    return errors


def script_command(script_name: str, *parts: str) -> str:
    return cli_command_for_script(script_name, *parts)


def _workflow_command(script_name: str, *parts: str) -> str:
    return script_command(script_name, *parts)


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


def build_search_index(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None = None,
    *,
    link_rows: list[dict[str, Any]] | None = None,
    topology: GraphTopology | None = None,
    include_resource_text: bool = True,
) -> list[dict[str, Any]]:
    from research_cockpit.search_index import build_search_index as _build_search_index

    return _build_search_index(
        root,
        nodes,
        current,
        link_rows=link_rows,
        topology=topology,
        include_resource_text=include_resource_text,
    )


def make_search_snippet(text: str, query: str, width: int = 180) -> str:
    from research_cockpit.search_index import make_search_snippet as _make_search_snippet

    return _make_search_snippet(text, query, width=width)


def search_knowledge(
    index: list[dict[str, Any]],
    query: str,
    *,
    sources: set[str] | list[str] | None = None,
    node_types: set[str] | list[str] | None = None,
    limit: int | None = 20,
    focus_only: bool = False,
) -> list[dict[str, Any]]:
    from research_cockpit.search_index import search_knowledge as _search_knowledge

    return _search_knowledge(
        index,
        query,
        sources=sources,
        node_types=node_types,
        limit=limit,
        focus_only=focus_only,
    )


def build_search_index_summary(index: list[dict[str, Any]], focus_entry_limit: int = 8) -> dict[str, Any]:
    from research_cockpit.search_index import build_search_index_summary as _build_search_index_summary

    return _build_search_index_summary(index, focus_entry_limit=focus_entry_limit)

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
        "links": _node_link_entries(node),
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


def node_title(nodes: dict[str, ResearchNode], node_id: str | None) -> str | None:
    if not node_id or node_id not in nodes:
        return None
    return nodes[node_id].title


def build_node_onboarding_context(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    node_id: str,
    *,
    compact: bool = False,
    command_style: str = "console",
) -> dict[str, Any]:
    from research_cockpit.node_onboarding import build_node_onboarding_context as _build_node_onboarding_context

    return _build_node_onboarding_context(
        root,
        nodes,
        current,
        node_id,
        compact=compact,
        command_style=command_style,
    )


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
