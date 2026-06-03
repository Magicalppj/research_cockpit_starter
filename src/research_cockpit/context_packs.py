from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import subprocess

from research_cockpit.baselines import resolve_current_effective_baseline
from research_cockpit.assignment_view import build_assignment_view
from research_cockpit.graph_core import (
    GraphTopology,
    child_ids,
    derive_focus_path,
    focus_mode_from_current,
    focus_node_id_from_current,
    node_context,
    node_id_by_type_in_path,
    node_title,
    unique_strings,
)
from research_cockpit.gate_result_records import build_gate_overview
from research_cockpit.graph_views import load_graph_views
from research_cockpit.interaction_log import interaction_log_warnings, recent_interactions
from research_cockpit.option_workstreams import build_option_workstream_context, build_option_workstream_rows
from research_cockpit.resources import build_link_rows, node_artifact_ids, node_link_entries
from research_cockpit.run_summaries import build_run_overview
from research_cockpit.search_index import build_search_index, build_search_index_summary
from research_cockpit.storage import load_yaml, save_text
from research_cockpit.suggestions import build_action_suggestions
from research_cockpit.types import ACTIVE_WORKSTREAM_STATUSES, CONTEXT_SCHEMA_VERSION, ResearchNode

TERMINAL_NEXT_ACTION_STATUSES = {
    "accepted",
    "archived",
    "cancelled",
    "deprecated",
    "done",
    "failed",
    "parked",
    "rejected",
    "resolved",
    "superseded",
}


@dataclass(frozen=True)
class DashboardReadModels:
    linked_resources: list[dict[str, Any]]
    action_suggestions: list[dict[str, Any]]
    search_index: list[dict[str, Any]]
    option_workstreams: list[dict[str, Any]]
    assignment_view: dict[str, Any]
    topology: GraphTopology | None = None


def _resource_context_models(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    *,
    read_models: DashboardReadModels | None = None,
    topology: GraphTopology | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if read_models is not None:
        return read_models.linked_resources, read_models.action_suggestions, read_models.search_index
    link_rows = build_link_rows(root, nodes)
    action_suggestions = build_action_suggestions(root, nodes, current, link_rows)
    search_index = build_search_index(root, nodes, current, link_rows=link_rows, topology=topology)
    return link_rows, action_suggestions, search_index


def build_dashboard_read_models(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    *,
    topology: GraphTopology | None = None,
    include_resource_text: bool = True,
) -> DashboardReadModels:
    from research_cockpit.model import load_assignments

    topology = topology or GraphTopology.from_nodes(nodes)
    linked_resources = build_link_rows(root, nodes)
    action_suggestions = build_action_suggestions(root, nodes, current, linked_resources)
    search_index = build_search_index(
        root,
        nodes,
        current,
        link_rows=linked_resources,
        topology=topology,
        include_resource_text=include_resource_text,
    )
    return DashboardReadModels(
        linked_resources=linked_resources,
        action_suggestions=action_suggestions,
        search_index=search_index,
        option_workstreams=build_option_workstream_rows(
            nodes,
            current,
            assignments=load_assignments(root),
            topology=topology,
        ),
        assignment_view=build_assignment_view(nodes),
        topology=topology,
    )


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


def _ordered_node_contexts(nodes: dict[str, ResearchNode], node_ids: list[str]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for node_id in node_ids:
        if node_id in seen or node_id not in nodes:
            continue
        seen.add(node_id)
        out.append(node_context(nodes[node_id]))
    return out


def _knowledge_index(nodes: dict[str, ResearchNode], node_ids: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    for node_id in node_ids:
        if node_id in seen_nodes or node_id not in nodes:
            continue
        seen_nodes.add(node_id)
        node = nodes[node_id]
        agent_context = node.raw.get("agent_context")
        linked_resources = node_link_entries(node)
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


def _action_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _node_action_entries(
    node: ResearchNode,
    *,
    scope: str,
    stale: bool = False,
    migration_candidate: bool = False,
    reason: str = "",
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    is_terminal = node.status in TERMINAL_NEXT_ACTION_STATUSES
    entry_stale = stale or is_terminal
    entry_migration_candidate = migration_candidate or is_terminal
    entry_reason = reason
    if is_terminal:
        terminal_reason = "Terminal nodes should keep conclusions, not live follow-up work."
        entry_reason = reason if terminal_reason in reason else f"{reason} {terminal_reason}".strip()
    for action in _action_list(node.raw.get("next_actions")):
        entries.append({
            "scope": scope,
            "source": "node",
            "node_id": node.id,
            "node_title": node.title,
            "node_type": node.type,
            "node_status": node.status,
            "action": action,
            "is_terminal": is_terminal,
            "stale": entry_stale,
            "migration_candidate": entry_migration_candidate,
            "reason": entry_reason,
        })
    return entries


def _current_state_action_entries(current: dict[str, Any], focus_node_id: str | None) -> list[dict[str, Any]]:
    return [
        {
            "scope": "global_coordinator_next_actions",
            "source": "coordinator_state",
            "node_id": "coordinator_state",
            "node_title": "coordinator_state",
            "node_type": "coordinator_state",
            "node_status": "",
            "related_focus_node": focus_node_id,
            "action": action,
            "is_terminal": False,
            "stale": False,
            "migration_candidate": False,
            "reason": "Stored as coordinator-level handoff state.",
        }
        for action in _action_list(current.get("next_actions"))
    ]


def _coordinator_state_payload(root: Path) -> dict[str, Any]:
    from research_cockpit.model import load_coordinator_state

    coordinator = load_coordinator_state(root)
    return {
        "coordinator_only": True,
        "selected_node": coordinator.selected_node,
        "selected_assignment": coordinator.selected_assignment,
        "global_next_actions": list(coordinator.global_next_actions),
        "dashboard_filters": dict(coordinator.dashboard_filters),
    }


def _assignment_record_overview(
    root: Path,
    nodes: dict[str, ResearchNode],
    *,
    topology: GraphTopology | None = None,
) -> dict[str, Any]:
    from research_cockpit.model import ACTIVE_ASSIGNMENT_STATUSES, load_assignments

    topology = topology or GraphTopology.from_nodes(nodes)
    rows: list[dict[str, Any]] = []
    for assignment in sorted(load_assignments(root).values(), key=lambda item: item.assignment_id):
        current_path: list[str] = []
        current_option = None
        if assignment.current_node in nodes:
            try:
                current_path = derive_focus_path(nodes, assignment.current_node, topology=topology)
                current_option = node_id_by_type_in_path(nodes, current_path, "option", nearest=True)
            except ValueError:
                current_path = [assignment.current_node]
        rows.append({
            "assignment_id": assignment.assignment_id,
            "agent_id": assignment.agent_id,
            "status": assignment.status,
            "root_node": assignment.root_node,
            "root_node_title": node_title(nodes, assignment.root_node),
            "current_node": assignment.current_node,
            "current_node_title": node_title(nodes, assignment.current_node),
            "current_path": current_path,
            "current_option": current_option,
            "next_actions": list(assignment.next_actions),
            "objective": assignment.objective,
            "updated_at": assignment.updated_at,
        })
    active_rows = [row for row in rows if row["status"] in ACTIVE_ASSIGNMENT_STATUSES]
    return {
        "schema_version": "assignment_overview_v1",
        "primary_context": "assignment_scope",
        "count": len(rows),
        "active_count": len(active_rows),
        "assignments": rows,
        "active_assignments": active_rows,
    }


def _nearest_parent_id(
    nodes: dict[str, ResearchNode],
    path_ids: list[str],
    focus_node_id: str | None,
    node_type: str,
) -> str | None:
    for node_id in reversed(path_ids):
        if node_id == focus_node_id:
            continue
        node = nodes.get(node_id)
        if node and node.type == node_type:
            return node.id
    return None


def build_next_action_scopes(
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    *,
    focus_node_id: str | None = None,
    focus_path_ids: list[str] | None = None,
    topology: GraphTopology | None = None,
) -> dict[str, Any]:
    focus_node_id = focus_node_id or focus_node_id_from_current(current, nodes)
    path_ids = [
        str(node_id)
        for node_id in (focus_path_ids if focus_path_ids is not None else current.get("current_focus_path", []) or [])
        if str(node_id) in nodes
    ]
    if focus_node_id and focus_node_id in nodes and focus_node_id not in path_ids:
        try:
            path_ids = derive_focus_path(nodes, focus_node_id, topology=topology)
        except ValueError:
            path_ids = [focus_node_id]

    focus_node = nodes.get(focus_node_id) if focus_node_id else None
    parent_option_id = _nearest_parent_id(nodes, path_ids, focus_node_id, "option")
    parent_problem_id = _nearest_parent_id(nodes, path_ids, focus_node_id, "problem")

    focus_next_actions = (
        _node_action_entries(
            focus_node,
            scope="focus_next_actions",
            reason="Stored on the current focus node.",
        )
        if focus_node
        else []
    )
    parent_option_next_actions = (
        _node_action_entries(
            nodes[parent_option_id],
            scope="parent_option_next_actions",
            reason="Stored on the nearest parent option in the focus path.",
        )
        if parent_option_id
        else []
    )
    parent_problem_next_actions = (
        _node_action_entries(
            nodes[parent_problem_id],
            scope="parent_problem_next_actions",
            reason="Stored on the nearest parent problem in the focus path.",
        )
        if parent_problem_id
        else []
    )
    global_coordinator_next_actions = _current_state_action_entries(current, focus_node_id)
    stale_terminal_node_next_actions: list[dict[str, Any]] = []
    for node in sorted(nodes.values(), key=lambda item: item.id):
        if node.status not in TERMINAL_NEXT_ACTION_STATUSES:
            continue
        stale_terminal_node_next_actions.extend(_node_action_entries(
            node,
            scope="stale_terminal_node_next_actions",
            stale=True,
            migration_candidate=True,
            reason="Terminal nodes should keep conclusions, not live follow-up work.",
        ))

    buckets = {
        "focus_next_actions": focus_next_actions,
        "parent_option_next_actions": parent_option_next_actions,
        "parent_problem_next_actions": parent_problem_next_actions,
        "global_coordinator_next_actions": global_coordinator_next_actions,
        "stale_terminal_node_next_actions": stale_terminal_node_next_actions,
    }
    return {
        "focus_node_id": focus_node_id,
        "focus_path_ids": path_ids,
        **buckets,
        "counts": {key: len(value) for key, value in buckets.items()},
    }


def build_agent_context(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None = None,
    read_models: DashboardReadModels | None = None,
) -> dict[str, Any]:
    current = current if current is not None else load_yaml(root / "current_state.yaml")
    path = current.get("current_focus_path", []) or []
    linked_nodes = [node_context(nodes[node_id]) for node_id in path if node_id in nodes]
    current_focus_node = focus_node_id_from_current(current, nodes)
    effective_baseline = resolve_current_effective_baseline(nodes, current)
    next_action_scopes = build_next_action_scopes(
        nodes,
        current,
        focus_node_id=current_focus_node,
        focus_path_ids=path,
        topology=read_models.topology if read_models else None,
    )
    topology = read_models.topology if read_models else None
    assignment_overview = _assignment_record_overview(root, nodes, topology=topology)
    coordinator_state = _coordinator_state_payload(root)
    current_global_focus = {
        "coordinator_only": True,
        "current_stage": current.get("current_stage"),
        "current_problem": current.get("current_problem"),
        "current_option": current.get("current_option"),
        "current_focus_node": current_focus_node,
        "current_focus_path": path,
        "next_actions": current.get("next_actions", []),
        "next_action_scopes": next_action_scopes,
    }

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
        if n.type == "decision" and n.status == "proposed"
    ]
    link_rows, action_suggestions, search_index = _resource_context_models(
        root,
        nodes,
        current,
        read_models=read_models,
        topology=read_models.topology if read_models else None,
    )
    if read_models is not None:
        option_workstreams = read_models.option_workstreams
        assignment_view = read_models.assignment_view
    else:
        from research_cockpit.model import load_assignments

        option_workstreams = build_option_workstream_rows(nodes, current, assignments=load_assignments(root))
        assignment_view = build_assignment_view(nodes)

    return {
        "metadata": build_context_metadata(root, current),
        "primary_context": "assignment_overview" if assignment_overview["active_count"] else "coordinator_global",
        "legacy_global_fields_are_coordinator_only": True,
        "current_focus_is_coordinator_only": True,
        "assignment_overview": assignment_overview,
        "coordinator_state": coordinator_state,
        "current_global_focus": current_global_focus,
        "project_name": "Research Cockpit Demo",
        "current_stage": current.get("current_stage"),
        "current_stage_title": node_title(nodes, current.get("current_stage")),
        "current_problem": current.get("current_problem"),
        "current_problem_title": node_title(nodes, current.get("current_problem")),
        "current_option": current.get("current_option"),
        "current_option_title": node_title(nodes, current.get("current_option")),
        "current_focus_node": current_focus_node,
        "current_focus_node_title": node_title(nodes, current_focus_node),
        "effective_baseline": effective_baseline,
        "focus_mode": focus_mode_from_current(current),
        "current_hypothesis": current.get("current_hypothesis"),
        "current_focus_path": path,
        "open_risks": current.get("open_risks", []),
        "next_actions": current.get("next_actions", []),
        "next_action_scopes": next_action_scopes,
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
        "assignment_view": assignment_view,
        "run_overview": build_run_overview(root, nodes),
        "gate_overview": build_gate_overview(root),
        "saved_graph_views": load_graph_views(root),
        "recent_interactions": recent_interactions(root),
        "warnings": interaction_log_warnings(root),
        "suggested_next_actions": action_suggestions,
        "search_index_summary": build_search_index_summary(search_index),
    }


def build_focus_context(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None = None,
    read_models: DashboardReadModels | None = None,
) -> dict[str, Any]:
    current = current if current is not None else load_yaml(root / "current_state.yaml")
    topology = read_models.topology if read_models else None
    focus_node_id = focus_node_id_from_current(current, nodes)
    focus_node = nodes.get(focus_node_id) if focus_node_id else None
    effective_baseline = resolve_current_effective_baseline(nodes, current)
    path_ids = [
        str(node_id)
        for node_id in current.get("current_focus_path", []) or []
        if str(node_id) in nodes
    ]

    parent_ids: list[str] = []
    child_node_ids: list[str] = []
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
            sibling_ids.extend(
                child
                for child in child_ids(nodes, parent, topology=topology)
                if child != focus_node.id
            )

        child_node_ids.extend(child_ids(nodes, focus_node, topology=topology))
        focus_related_ids.extend(parent_ids)
        focus_related_ids.extend(child_node_ids)
        focus_related_ids.extend(sibling_ids)

        current_best_option = focus_node.raw.get("current_best_option") or current.get("current_option")
        option_ids = [
            node_id
            for node_id in unique_strings([current_best_option, current.get("current_option")])
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
            for artifact_id in node_artifact_ids(node):
                if artifact_id in nodes and nodes[artifact_id].type == "artifact":
                    artifact_ids.append(str(artifact_id))
            dataset_id = node.raw.get("dataset")
            if dataset_id in nodes and nodes[dataset_id].type == "artifact":
                artifact_ids.append(str(dataset_id))

        blockers = unique_strings(focus_node.raw.get("blockers", []) or [])
    else:
        current_best_option = current.get("current_option")

    next_actions = unique_strings(
        (current.get("next_actions", []) or [])
        + (focus_node.raw.get("next_actions", []) if focus_node else [])
    )
    next_action_scopes = build_next_action_scopes(
        nodes,
        current,
        focus_node_id=focus_node_id,
        focus_path_ids=path_ids,
        topology=topology,
    )
    knowledge_node_ids = (
        [focus_node_id or ""]
        + path_ids
        + parent_ids
        + child_node_ids
        + experiment_ids
        + decision_ids
        + artifact_ids
    )

    link_rows, action_suggestions, search_index = _resource_context_models(
        root,
        nodes,
        current,
        read_models=read_models,
        topology=topology,
    )
    suggested_next_actions = [
        suggestion
        for suggestion in action_suggestions
        if suggestion.get("is_focus_related")
    ]
    option_context_id: str | None = None
    if focus_node:
        try:
            option_context_id = node_id_by_type_in_path(
                nodes,
                derive_focus_path(nodes, focus_node.id, topology=topology),
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
        build_option_workstream_context(root, nodes, current, option_context_id, topology=topology)
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
            "children": _ordered_node_contexts(nodes, child_node_ids),
            "siblings": _ordered_node_contexts(nodes, sibling_ids),
            "experiments": _ordered_node_contexts(nodes, experiment_ids),
            "decisions": _ordered_node_contexts(nodes, decision_ids),
            "artifacts": _ordered_node_contexts(nodes, artifact_ids),
            "blockers": blockers,
        },
        "current_best_option": current_best_option,
        "effective_baseline": effective_baseline,
        "blockers": blockers,
        "next_actions": next_actions,
        "next_action_scopes": next_action_scopes,
        "knowledge_index": _knowledge_index(nodes, knowledge_node_ids),
        "suggested_next_actions": suggested_next_actions,
        "search_index_summary": build_search_index_summary(search_index),
        "option_workstream_context": option_workstream_context,
        "saved_graph_views": load_graph_views(root),
        "recent_interactions": recent_interactions(root),
        "warnings": interaction_log_warnings(root),
    }


def build_current_state_payload(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = current if current is not None else load_yaml(root / "current_state.yaml")
    focus_node_id = focus_node_id_from_current(current, nodes)
    effective_baseline = resolve_current_effective_baseline(nodes, current)
    focus_path = current.get("current_focus_path", []) or []
    return {
        "current_stage": current.get("current_stage"),
        "current_stage_title": node_title(nodes, current.get("current_stage")),
        "current_problem": current.get("current_problem"),
        "current_problem_title": node_title(nodes, current.get("current_problem")),
        "current_option": current.get("current_option"),
        "current_option_title": node_title(nodes, current.get("current_option")),
        "current_focus_node": focus_node_id,
        "current_focus_node_title": node_title(nodes, focus_node_id),
        "effective_baseline": effective_baseline,
        "focus_mode": focus_mode_from_current(current),
        "current_focus_path": focus_path,
        "current_hypothesis": current.get("current_hypothesis"),
        "open_risks": current.get("open_risks", []),
        "next_actions": current.get("next_actions", []),
        "next_action_scopes": build_next_action_scopes(
            nodes,
            current,
            focus_node_id=focus_node_id,
            focus_path_ids=focus_path,
        ),
        "updated_at": current.get("updated_at"),
        "saved_graph_views": load_graph_views(root),
        "recent_interactions": recent_interactions(root),
        "warnings": interaction_log_warnings(root),
        "linked_nodes": [
            node_context(nodes[node_id])
            for node_id in current.get("current_focus_path", []) or []
            if node_id in nodes
        ],
    }


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
    baseline = context.get("effective_baseline") or {}
    option = baseline.get("option") or {}
    decision = baseline.get("decision") or {}
    lines.append("## Effective Baseline\n")
    lines.append(f"- **Option:** `{option.get('id') or ''}`")
    lines.append(f"- **Decision:** `{decision.get('id') or ''}`")
    lines.append(f"- **Source:** `{baseline.get('source_kind') or ''}` from `{baseline.get('source_node_id') or ''}`")
    lines.append("")
    lines.append("## Open Risks\n")
    for item in context.get("open_risks", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Next Actions\n")
    for item in context.get("next_actions", []):
        lines.append(f"- {item}")
    lines.append("")
    action_scopes = context.get("next_action_scopes") or {}
    scope_titles = [
        ("focus_next_actions", "Focus Node"),
        ("parent_option_next_actions", "Parent Option"),
        ("parent_problem_next_actions", "Parent Problem"),
        ("global_coordinator_next_actions", "Global Coordinator"),
        ("stale_terminal_node_next_actions", "Stale Terminal Nodes"),
    ]
    if any(action_scopes.get(key) for key, _title in scope_titles):
        lines.append("## Next Actions By Scope\n")
        for key, title in scope_titles:
            entries = action_scopes.get(key) or []
            if not entries:
                continue
            lines.append(f"### {title}")
            for entry in entries:
                source_id = entry.get("node_id") or entry.get("source")
                lines.append(f"- `{source_id}`: {entry.get('action')}")
        lines.append("")
    lines.append("## Active Problems\n")
    for problem in context.get("active_problems", []):
        lines.append(f"- **{problem['title']}** (`{problem['id']}`): {problem.get('summary','')}")
    lines.append("")
    lines.append("## Recent Decisions\n")
    for decision in context.get("recent_decisions", []):
        lines.append(f"- **{decision['title']}** (`{decision['status']}`): {decision.get('summary','')}")
    (root / "dashboards").mkdir(parents=True, exist_ok=True)
    save_text(root / "dashboards" / "current_state.md", "\n".join(lines))
