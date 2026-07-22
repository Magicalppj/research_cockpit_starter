from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
import networkx as nx
import yaml

from research_cockpit.agent_state import (
    AgentRecord,
    AssignmentRecord,
    CoordinatorState,
    assignment_contract_errors,
    load_agents,
    load_assignment,
    load_assignments,
    load_coordinator_state,
)
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
from research_cockpit.types import ValidationError
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
VALID_AGENT_STATUSES = {"active", "idle", "completed", "retired"}
VALID_ASSIGNMENT_STATUSES = {"queued", "active", "blocked", "completed", "cancelled", "retired"}
ACTIVE_ASSIGNMENT_STATUSES = {"queued", "active", "blocked"}
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
    resources: dict[str, Any] | None = None
    output_retention: dict[str, Any] | None = None
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
            resources=data.get("resources") if isinstance(data.get("resources"), dict) else None,
            output_retention=data.get("output_retention") if isinstance(data.get("output_retention"), dict) else None,
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


def graph_to_json(
    nodes: dict[str, ResearchNode],
    current_focus_path: list[str] | None = None,
    current: dict[str, Any] | None = None,
    explicit_edges: list[dict[str, Any]] | None = None,
    *,
    topology: GraphTopology | None = None,
    include_raw: bool = True,
) -> dict[str, Any]:
    from research_cockpit.graph_core import graph_to_json as _graph_to_json

    return _graph_to_json(
        nodes,
        current_focus_path=current_focus_path,
        current=current,
        explicit_edges=explicit_edges,
        topology=topology,
        include_raw=include_raw,
    )


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
    from research_cockpit.retention import validate_retention

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

    def validate_artifact_metadata(node: ResearchNode) -> None:
        if node.type != "artifact":
            if node.raw.get("retention") is not None:
                errors.append(f"{node.id}: retention is only supported on artifact nodes")
            if node.raw.get("artifact_kind") is not None:
                errors.append(f"{node.id}: artifact_kind is only supported on artifact nodes")
            return
        artifact_kind = node.raw.get("artifact_kind")
        if artifact_kind is not None and not str(artifact_kind).strip():
            errors.append(f"{node.id}: artifact_kind cannot be empty")
        if node.raw.get("retention") is not None:
            try:
                validate_retention(node.raw.get("retention"), "retention")
            except ValueError as exc:
                errors.append(f"{node.id}: {exc}")

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
        validate_artifact_metadata(node)
    return errors


def validate_runs(runs: dict[str, RunRecord], nodes: dict[str, ResearchNode]) -> list[str]:
    from research_cockpit.retention import validate_retention

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
        if "resources" in run.raw and not isinstance(run.raw.get("resources"), dict):
            errors.append(f"{run.run_id}: resources must be a mapping")
        if "output_retention" in run.raw:
            try:
                validate_retention(run.raw.get("output_retention"), "output_retention")
            except ValueError as exc:
                errors.append(f"{run.run_id}: {exc}")
    return errors


def validate_agents(agents: dict[str, AgentRecord], assignments: dict[str, AssignmentRecord]) -> list[str]:
    errors: list[str] = []
    for agent in agents.values():
        if not agent.agent_id:
            errors.append("agent has empty agent_id")
        if agent.status not in VALID_AGENT_STATUSES:
            allowed = ", ".join(sorted(VALID_AGENT_STATUSES))
            errors.append(f"{agent.agent_id}: invalid agent status {agent.status!r}; allowed: {allowed}")
        if not isinstance(agent.raw.get("active_assignment_ids", []), list):
            errors.append(f"{agent.agent_id}: active_assignment_ids must be a list")
        for assignment_id in agent.active_assignment_ids:
            if assignment_id not in assignments:
                errors.append(
                    f"{agent.agent_id}: active_assignment_ids references missing assignment {assignment_id!r}"
                )
            elif assignments[assignment_id].agent_id != agent.agent_id:
                errors.append(
                    f"{agent.agent_id}: active_assignment_ids contains assignment {assignment_id!r} "
                    f"owned by {assignments[assignment_id].agent_id!r}"
                )
    return errors


def _assignment_allowed_root(assignment: AssignmentRecord) -> str:
    value = assignment.allowed_subtree.get("root")
    return str(value) if value not in (None, "") else assignment.root_node


def _assignment_policy(assignment: AssignmentRecord) -> str:
    value = assignment.allowed_subtree.get("policy")
    return str(value) if value not in (None, "") else "descendants_only"


def _node_inside_assignment_scope(
    nodes: dict[str, ResearchNode],
    assignment: AssignmentRecord,
    node_id: str,
    *,
    topology: GraphTopology,
) -> bool:
    root_node = _assignment_allowed_root(assignment)
    if root_node not in nodes or node_id not in nodes:
        return False
    if node_id == root_node:
        return True
    return node_id in topology.descendant_ids(root_node)


def _assignment_dependency_errors(
    assignments: dict[str, AssignmentRecord],
) -> list[str]:
    errors: list[str] = []
    adjacency: dict[str, list[str]] = {}
    for assignment_id, assignment in sorted(assignments.items()):
        dependency_ids: list[str] = []
        for dependency in assignment.dependencies:
            dependency_id = str(dependency.get("assignment_id") or "")
            if not dependency_id:
                continue
            if dependency_id not in assignments:
                errors.append(
                    f"{assignment_id}: dependency references missing assignment {dependency_id!r}"
                )
                continue
            dependency_ids.append(dependency_id)
        adjacency[assignment_id] = dependency_ids

    state: dict[str, int] = {}
    stack: list[str] = []
    cycles: set[tuple[str, ...]] = set()

    def visit(assignment_id: str) -> None:
        state[assignment_id] = 1
        stack.append(assignment_id)
        for dependency_id in adjacency.get(assignment_id, []):
            if state.get(dependency_id, 0) == 0:
                visit(dependency_id)
            elif state.get(dependency_id) == 1:
                start = stack.index(dependency_id)
                members = stack[start:]
                first = min(range(len(members)), key=lambda index: members[index])
                canonical = tuple([*members[first:], *members[:first]])
                cycles.add(canonical)
        stack.pop()
        state[assignment_id] = 2

    for assignment_id in sorted(adjacency):
        if state.get(assignment_id, 0) == 0:
            visit(assignment_id)
    for cycle in sorted(cycles):
        errors.append(f"dependency cycle detected: {' -> '.join([*cycle, cycle[0]])}")
    return errors


def validate_assignments(
    assignments: dict[str, AssignmentRecord],
    agents: dict[str, AgentRecord],
    nodes: dict[str, ResearchNode],
) -> list[str]:
    errors: list[str] = []
    if not assignments:
        return errors
    topology = GraphTopology.from_nodes(nodes)
    active_by_root: dict[str, list[str]] = {}

    for assignment in assignments.values():
        errors.extend(assignment_contract_errors(assignment))
        if not assignment.assignment_id:
            errors.append("assignment has empty assignment_id")
        if assignment.status not in VALID_ASSIGNMENT_STATUSES:
            allowed = ", ".join(sorted(VALID_ASSIGNMENT_STATUSES))
            errors.append(
                f"{assignment.assignment_id}: invalid assignment status {assignment.status!r}; allowed: {allowed}"
            )
        if not assignment.agent_id and assignment.status in {"active", "blocked"}:
            errors.append(f"{assignment.assignment_id}: agent_id is required")
        elif assignment.agent_id and assignment.agent_id not in agents:
            errors.append(
                f"{assignment.assignment_id}: agent_id references missing agent {assignment.agent_id!r}"
            )
        if not assignment.root_node:
            errors.append(f"{assignment.assignment_id}: root_node is required")
        elif assignment.root_node not in nodes:
            errors.append(
                f"{assignment.assignment_id}: root_node references missing node {assignment.root_node!r}"
            )
        if not assignment.current_node:
            errors.append(f"{assignment.assignment_id}: current_node is required")
        elif assignment.current_node not in nodes:
            errors.append(
                f"{assignment.assignment_id}: current_node references missing node {assignment.current_node!r}"
            )

        raw_allowed_subtree = assignment.raw.get("allowed_subtree")
        if not isinstance(raw_allowed_subtree, dict):
            errors.append(f"{assignment.assignment_id}: allowed_subtree must be a mapping")
        allowed_root = _assignment_allowed_root(assignment)
        if not isinstance(raw_allowed_subtree, dict) or raw_allowed_subtree.get("root") in (None, ""):
            errors.append(f"{assignment.assignment_id}: allowed_subtree.root is required")
        if allowed_root != assignment.root_node:
            errors.append(
                f"{assignment.assignment_id}: allowed_subtree.root {allowed_root!r} "
                f"must match root_node {assignment.root_node!r}"
            )
        if allowed_root not in nodes:
            errors.append(
                f"{assignment.assignment_id}: allowed_subtree.root references missing node {allowed_root!r}"
            )
        policy = _assignment_policy(assignment)
        if policy != "descendants_only":
            errors.append(f"{assignment.assignment_id}: unsupported allowed_subtree.policy {policy!r}")
        if (
            assignment.current_node
            and assignment.current_node in nodes
            and allowed_root in nodes
            and not _node_inside_assignment_scope(nodes, assignment, assignment.current_node, topology=topology)
        ):
            errors.append(
                f"{assignment.assignment_id}: current_node {assignment.current_node!r} "
                f"is outside allowed_subtree root {allowed_root!r}"
            )
        if assignment.next_actions is not None and not isinstance(assignment.raw.get("next_actions", []), list):
            errors.append(f"{assignment.assignment_id}: next_actions must be a list")
        if assignment.raw.get("worktree") is not None and not isinstance(assignment.raw.get("worktree"), dict):
            errors.append(f"{assignment.assignment_id}: worktree must be a mapping")

        if assignment.status in ACTIVE_ASSIGNMENT_STATUSES and assignment.root_node in nodes:
            root = nodes[assignment.root_node]
            if root.status not in {"planned", "queued", "running", "open", "active", "promising", "blocked"}:
                if assignment.raw.get("allow_terminal_root") is not True:
                    errors.append(
                        f"{assignment.assignment_id}: active assignment root_node {assignment.root_node!r} "
                        f"has terminal status {root.status!r}"
                    )
            active_by_root.setdefault(assignment.root_node, []).append(assignment.assignment_id)

    for root_node, assignment_ids in sorted(active_by_root.items()):
        if len(assignment_ids) <= 1:
            continue
        if all(assignments[assignment_id].raw.get("allow_parallel_assignments") is True for assignment_id in assignment_ids):
            continue
        errors.append(
            f"multiple active assignments claim root_node {root_node!r}: {', '.join(sorted(assignment_ids))}"
        )
    errors.extend(_assignment_dependency_errors(assignments))
    return errors


def validate_coordinator_state(
    coordinator_state: CoordinatorState,
    nodes: dict[str, ResearchNode],
    assignments: dict[str, AssignmentRecord],
) -> list[str]:
    errors: list[str] = []
    if coordinator_state.selected_node and coordinator_state.selected_node not in nodes:
        errors.append(
            f"coordinator_state.selected_node references missing node {coordinator_state.selected_node!r}"
        )
    if coordinator_state.selected_assignment and coordinator_state.selected_assignment not in assignments:
        errors.append(
            "coordinator_state.selected_assignment references missing assignment "
            f"{coordinator_state.selected_assignment!r}"
        )
    if not isinstance(coordinator_state.raw.get("global_next_actions", []), list):
        errors.append("coordinator_state.global_next_actions must be a list")
    if coordinator_state.raw.get("dashboard_filters") is not None and not isinstance(
        coordinator_state.raw.get("dashboard_filters"),
        dict,
    ):
        errors.append("coordinator_state.dashboard_filters must be a mapping")
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


def validate_artifact_records(
    root: Path,
    nodes: dict[str, ResearchNode],
    records: list[dict[str, Any]] | None = None,
) -> list[str]:
    from research_cockpit.artifact_records import (
        AVAILABILITY_STATUSES,
        INTEGRITY_LEVELS,
        STORAGE_MODES,
        list_artifact_records,
    )
    from research_cockpit.retention import validate_retention

    errors: list[str] = []
    if records is None:
        try:
            records = list_artifact_records(root)
        except (ValueError, FileNotFoundError) as exc:
            return [str(exc)]

    record_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for record in records:
        record_id = str(record.get("record_id") or "").strip()
        if not record_id:
            errors.append("artifact record missing record_id")
            continue
        if record_id in record_ids:
            duplicate_ids.add(record_id)
        record_ids.add(record_id)
        experiment_id = str(record.get("experiment_id") or "").strip()
        if not experiment_id:
            errors.append(f"artifact record {record_id!r}: experiment_id is required")
        elif experiment_id not in nodes:
            errors.append(f"artifact record {record_id!r}: experiment_id references missing node {experiment_id!r}")
        elif nodes[experiment_id].type != "experiment":
            errors.append(
                f"artifact record {record_id!r}: experiment_id {experiment_id!r} has type "
                f"{nodes[experiment_id].type!r}; expected 'experiment'"
            )
        links = record.get("links")
        if links is not None and not isinstance(links, dict):
            errors.append(f"artifact record {record_id!r}: links must be a mapping")
        storage = record.get("storage")
        if storage is not None:
            if not isinstance(storage, dict):
                errors.append(f"artifact record {record_id!r}: storage must be a mapping")
            else:
                mode = str(storage.get("mode") or "").strip()
                ownership = str(storage.get("ownership") or "").strip()
                uri = str(storage.get("uri") or "").strip()
                managed_key = storage.get("managed_key")
                if mode not in STORAGE_MODES:
                    errors.append(
                        f"artifact record {record_id!r}: storage.mode is invalid"
                    )
                expected_ownership = {
                    "reference": "external",
                    "managed": "cockpit_managed",
                    "legacy": "cockpit_managed",
                }.get(mode)
                if expected_ownership is not None and ownership != expected_ownership:
                    errors.append(
                        f"artifact record {record_id!r}: storage.ownership must be "
                        f"{expected_ownership!r} for mode {mode!r}"
                    )
                raw_availability = record.get("availability")
                availability_status = (
                    raw_availability.get("status")
                    if isinstance(raw_availability, dict)
                    else None
                )
                if not uri and (mode == "managed" or availability_status == "available"):
                    errors.append(
                        f"artifact record {record_id!r}: storage.uri is required"
                    )
                if mode == "managed":
                    key_text = str(managed_key or "").strip().replace("\\", "/")
                    key_path = PurePosixPath(key_text)
                    if (
                        not key_text
                        or key_path.is_absolute()
                        or ".." in key_path.parts
                    ):
                        errors.append(
                            f"artifact record {record_id!r}: "
                            "storage.managed_key must be a safe relative path"
                        )
                elif managed_key not in (None, ""):
                    errors.append(
                        f"artifact record {record_id!r}: storage.managed_key "
                        "is only valid for managed storage"
                    )

        integrity = record.get("integrity")
        if integrity is not None:
            if not isinstance(integrity, dict):
                errors.append(
                    f"artifact record {record_id!r}: integrity must be a mapping"
                )
            else:
                level = str(integrity.get("level") or "").strip()
                algorithm = integrity.get("algorithm")
                digest = integrity.get("digest")
                if level not in INTEGRITY_LEVELS:
                    errors.append(
                        f"artifact record {record_id!r}: integrity.level is invalid"
                    )
                if level == "unverified":
                    if algorithm not in (None, ""):
                        errors.append(
                            f"artifact record {record_id!r}: "
                            "integrity.algorithm must be null when unverified"
                        )
                    if digest not in (None, ""):
                        errors.append(
                            f"artifact record {record_id!r}: "
                            "integrity.digest must be null when unverified"
                        )
                elif level in INTEGRITY_LEVELS:
                    if algorithm != "sha256":
                        errors.append(
                            f"artifact record {record_id!r}: "
                            "integrity.algorithm must be 'sha256'"
                        )
                    digest_text = str(digest or "").strip().lower()
                    prefix, separator, hex_digest = digest_text.partition(":")
                    allowed_prefixes = {
                        "content": {"sha256"},
                        "manifest": {"sha256", "manifest-sha256"},
                        "inventory": {"inventory-sha256"},
                    }.get(level, set())
                    if (
                        not separator
                        or prefix not in allowed_prefixes
                        or len(hex_digest) != 64
                        or any(character not in "0123456789abcdef" for character in hex_digest)
                    ):
                        errors.append(
                            f"artifact record {record_id!r}: "
                            "integrity.digest does not match integrity.level"
                        )

        inventory = record.get("inventory")
        if inventory is not None:
            if not isinstance(inventory, dict):
                errors.append(
                    f"artifact record {record_id!r}: inventory must be a mapping"
                )
            else:
                for field_name in ("size_bytes", "file_count", "entries_scanned"):
                    value = inventory.get(field_name)
                    if value is not None and (
                        isinstance(value, bool)
                        or not isinstance(value, int)
                        or value < 0
                    ):
                        errors.append(
                            f"artifact record {record_id!r}: "
                            f"inventory.{field_name} must be a non-negative integer or null"
                        )
                if not isinstance(inventory.get("complete"), bool):
                    errors.append(
                        f"artifact record {record_id!r}: inventory.complete must be a boolean"
                    )

        availability = record.get("availability")
        if availability is not None:
            if not isinstance(availability, dict):
                errors.append(
                    f"artifact record {record_id!r}: availability must be a mapping"
                )
            elif availability.get("status") not in AVAILABILITY_STATUSES:
                errors.append(
                    f"artifact record {record_id!r}: availability.status is invalid"
                )

        lifecycle = record.get("lifecycle")
        if lifecycle is not None:
            if not isinstance(lifecycle, dict):
                errors.append(
                    f"artifact record {record_id!r}: lifecycle must be a mapping"
                )
            else:
                supersedes = lifecycle.get("supersedes")
                if not isinstance(supersedes, list) or not all(
                    isinstance(value, str) and value for value in supersedes
                ):
                    errors.append(
                        f"artifact record {record_id!r}: "
                        "lifecycle.supersedes must be a list of record ids"
                    )
                superseded_by = lifecycle.get("superseded_by")
                if superseded_by is not None and (
                    not isinstance(superseded_by, str) or not superseded_by
                ):
                    errors.append(
                        f"artifact record {record_id!r}: "
                        "lifecycle.superseded_by must be a record id or null"
                    )
        retention = record.get("retention")
        if retention is not None:
            try:
                validate_retention(retention, "retention")
            except ValueError as exc:
                errors.append(f"artifact record {record_id!r}: {exc}")
    for record_id in sorted(duplicate_ids):
        errors.append(f"artifact record id {record_id!r} is duplicated")

    def validate_record_refs(owner_id: str, field_name: str, value: Any) -> None:
        if value is None:
            return
        if not isinstance(value, list):
            errors.append(f"{owner_id}: {field_name} must be a list")
            return
        for record_id in value:
            ref_id = str(record_id)
            if ref_id not in record_ids:
                errors.append(f"{owner_id}: {field_name} references missing artifact record {ref_id!r}")

    for node in nodes.values():
        validate_record_refs(node.id, "linked_artifact_records", node.raw.get("linked_artifact_records"))
        findings = node.raw.get("findings")
        if not isinstance(findings, list):
            continue
        for index, finding in enumerate(findings, start=1):
            if not isinstance(finding, dict):
                continue
            validate_record_refs(
                node.id,
                f"findings[{index}].linked_artifact_records",
                finding.get("linked_artifact_records"),
            )
    return errors

def validate_cockpit(
    root: Path,
    nodes: dict[str, ResearchNode] | None = None,
    current: dict[str, Any] | None = None,
    explicit_edges: list[dict[str, Any]] | None = None,
    *,
    runs: dict[str, RunRecord] | None = None,
    agents: dict[str, AgentRecord] | None = None,
    assignments: dict[str, AssignmentRecord] | None = None,
    coordinator_state: CoordinatorState | None = None,
    artifact_records: list[dict[str, Any]] | None = None,
    include_interaction_log: bool = False,
    include_gate_results: bool = False,
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
    agent_load_errors: list[str] = []
    if agents is None:
        try:
            agents = load_agents(root)
        except ValidationError as exc:
            agents = {}
            agent_load_errors = exc.errors
    assignment_load_errors: list[str] = []
    if assignments is None:
        try:
            assignments = load_assignments(root)
        except ValidationError as exc:
            assignments = {}
            assignment_load_errors = exc.errors
    coordinator_load_errors: list[str] = []
    if coordinator_state is None:
        try:
            coordinator_state = load_coordinator_state(root)
        except ValidationError as exc:
            coordinator_state = CoordinatorState()
            coordinator_load_errors = exc.errors
    errors = validate_nodes(nodes)
    errors.extend(validate_artifact_records(root, nodes, artifact_records))
    errors.extend(validate_explicit_edges(nodes, explicit_edges))
    errors.extend(validate_current_state(current, nodes, explicit_edges))
    errors.extend(run_load_errors)
    errors.extend(validate_runs(runs, nodes))
    if include_gate_results:
        from research_cockpit.gate_result_records import validate_gate_result_records
        errors.extend(validate_gate_result_records(root, nodes, runs))
    errors.extend(agent_load_errors)
    errors.extend(assignment_load_errors)
    errors.extend(coordinator_load_errors)
    errors.extend(validate_agents(agents, assignments))
    errors.extend(validate_assignments(assignments, agents, nodes))
    errors.extend(validate_coordinator_state(coordinator_state, nodes, assignments))
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
    resource_scan_settings: Any | None = None,
    sources: set[str] | list[str] | None = None,
) -> list[dict[str, Any]]:
    from research_cockpit.search_index import build_search_index as _build_search_index

    return _build_search_index(
        root,
        nodes,
        current,
        link_rows=link_rows,
        topology=topology,
        include_resource_text=include_resource_text,
        resource_scan_settings=resource_scan_settings,
        sources=sources,
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
        "artifact_kind": node.raw.get("artifact_kind"),
        "retention": node.raw.get("retention"),
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
