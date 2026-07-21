from __future__ import annotations

from pathlib import Path
from typing import Any

from research_cockpit.graph_core import (
    GraphTopology,
    child_ids,
    derive_focus_path,
    node_context,
    node_id_by_type_in_path,
    node_title,
    ordered_node_contexts,
    unique_strings,
)
from research_cockpit.hierarchy_policy import hierarchy_policy
from research_cockpit.types import ACTIVE_WORKSTREAM_STATUSES, ResearchNode
from research_cockpit.gate_result_records import build_gate_summaries_by_experiment
from research_cockpit.run_summaries import build_run_summaries_by_experiment


def _canonical_command(route: str, *parts: str) -> str:
    command = ["research-cockpit", *route.split(), *parts]
    return " ".join(str(part) for part in command if part not in ("", None))


def build_option_subtree(
    nodes: dict[str, ResearchNode],
    option_id: str,
    *,
    topology: GraphTopology | None = None,
) -> dict[str, Any]:
    if option_id not in nodes:
        raise ValueError(f"Option node does not exist: {option_id}")
    if nodes[option_id].type != "option":
        raise ValueError(f"Node {option_id} must be option, got {nodes[option_id].type}")

    node_ids: list[str] = []
    seen: set[str] = set()

    stack = [option_id]
    while stack:
        node_id = stack.pop()
        if node_id in seen or node_id not in nodes:
            continue
        seen.add(node_id)
        node_ids.append(node_id)
        children = topology.child_ids(node_id) if topology is not None else child_ids(nodes, nodes[node_id])
        stack.extend(reversed(children))
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


def experiment_ids_for_option(
    nodes: dict[str, ResearchNode],
    option_id: str,
    *,
    topology: GraphTopology | None = None,
) -> list[str]:
    if option_id not in nodes or nodes[option_id].type != "option":
        return []
    return build_option_subtree(nodes, option_id, topology=topology)["experiment_ids"]


def latest_experiment_result(nodes: dict[str, ResearchNode], experiment_ids: list[str]) -> str | None:
    latest: str | None = None
    for experiment_id in experiment_ids:
        experiment = nodes[experiment_id]
        latest = experiment.raw.get("result_summary") or experiment.raw.get("outcome") or experiment.summary or latest
    return latest


def upstream_problem_id(
    nodes: dict[str, ResearchNode],
    option_id: str,
    *,
    topology: GraphTopology | None = None,
) -> str | None:
    try:
        path = derive_focus_path(nodes, option_id, topology=topology)
    except ValueError:
        return None
    return node_id_by_type_in_path(nodes, path, "problem", nearest=True)


def build_option_workstream_context(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    option_id: str,
    locale: str | None = None,
    *,
    topology: GraphTopology | None = None,
) -> dict[str, Any]:
    from research_cockpit.decisions import build_decision_evidence_bundle, normalize_locale

    topology = topology or GraphTopology.from_nodes(nodes)
    subtree = build_option_subtree(nodes, option_id, topology=topology)
    option = nodes[option_id]
    problem_id = upstream_problem_id(nodes, option_id, topology=topology)
    evidence = build_decision_evidence_bundle(
        nodes,
        option_id,
        locale=normalize_locale(locale, current),
        topology=topology,
    )
    gate_summaries = build_gate_summaries_by_experiment(root, subtree["experiment_ids"])
    next_actions: list[str] = []
    blockers: list[str] = []
    for node_id in subtree["node_ids"]:
        node = nodes[node_id]
        next_actions.extend(node.raw.get("next_actions", []) or [])
        blockers.extend(node.raw.get("blockers", []) or [])

    return {
        "option": node_context(option),
        "upstream_problem": node_context(nodes[problem_id]) if problem_id else None,
        "focus_path": ordered_node_contexts(nodes, derive_focus_path(nodes, option_id, topology=topology)),
        "subtree": subtree,
        "subtree_nodes": ordered_node_contexts(nodes, subtree["node_ids"]),
        "problems": ordered_node_contexts(nodes, subtree["problem_ids"]),
        "options": ordered_node_contexts(nodes, subtree["option_ids"]),
        "experiments": ordered_node_contexts(nodes, subtree["experiment_ids"]),
        "run_summaries_by_experiment": build_run_summaries_by_experiment(root, nodes, subtree["experiment_ids"]),
        "gate_summaries_by_experiment": gate_summaries,
        "decisions": ordered_node_contexts(nodes, subtree["decision_ids"]),
        "evidence_summary": {
            **evidence,
            "experiment_count": len(subtree["experiment_ids"]),
        },
        "focus_next_actions": current.get("next_actions", []) or [],
        "open_next_actions": unique_strings(next_actions),
        "blockers": unique_strings(blockers),
        "hierarchy_policy": hierarchy_policy(parent_option_id=option_id),
        "suggested_commands": {
            "claim": _canonical_command(
                "work claim",
                "--assignment",
                "<assignment_id>",
                "--agent",
                "<agent_id>",
                "--operation-id",
                "<operation_id>",
                "--return-packet",
                "--json",
                "--compact",
            ),
            "context": _canonical_command("context", "--id", option_id, "--view", "execution", "--compact", "--json"),
            "context_option_alias": _canonical_command(
                "context", "--id", option_id, "--view", "execution", "--compact", "--json"
            ),
            "create_child_workstream": _canonical_command(
                "coord assign",
                "--file",
                "<coord_assign.yaml>",
                "--json",
                "--compact",
            ),
            "finalize": _canonical_command(
                "work close",
                "--assignment",
                "<assignment_id>",
                "--file",
                "<closeout.yaml>",
                "--json",
                "--compact",
            ),
            "report": _canonical_command(
                "work close",
                "--assignment",
                "<assignment_id>",
                "--file",
                "<closeout.yaml>",
                "--json",
                "--compact",
            ),
        },
        "current_focus_related": option_id in set(current.get("current_focus_path", []) or []),
    }


def build_option_workstream_rows(
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None = None,
    *,
    assignments: dict[str, Any] | None = None,
    topology: GraphTopology | None = None,
) -> list[dict[str, Any]]:
    from research_cockpit.decisions import build_decision_evidence_bundle

    current = current or {}
    topology = topology or GraphTopology.from_nodes(nodes)
    agent_focuses = current.get("agent_focuses") if isinstance(current.get("agent_focuses"), dict) else {}
    assignments_by_root: dict[str, Any] = {}
    for assignment in sorted((assignments or {}).values(), key=lambda item: str(item.assignment_id)):
        assignments_by_root.setdefault(str(assignment.root_node), assignment)
    rows: list[dict[str, Any]] = []

    def option_sort_key(node: ResearchNode) -> tuple[int, str]:
        try:
            return (len(derive_focus_path(nodes, node.id, topology=topology)), node.id)
        except ValueError:
            return (999, node.id)

    for option in sorted((node for node in nodes.values() if node.type == "option"), key=option_sort_key):
        subtree = build_option_subtree(nodes, option.id, topology=topology)
        evidence = build_decision_evidence_bundle(nodes, option.id, topology=topology)
        problem_id = upstream_problem_id(nodes, option.id, topology=topology)
        workstream = option.raw.get("agent_workstream") if isinstance(option.raw.get("agent_workstream"), dict) else {}
        report = option.raw.get("workstream_report") if isinstance(option.raw.get("workstream_report"), dict) else {}
        owner = workstream.get("owner")
        focus = agent_focuses.get(str(owner)) if owner and isinstance(agent_focuses.get(str(owner)), dict) else {}
        assignment = assignments_by_root.get(option.id)
        assignment_current_node = str(assignment.current_node) if assignment else None
        assignment_next_actions = list(assignment.next_actions) if assignment else []
        agent_focus_node = assignment_current_node or focus.get("current_focus_node")
        agent_focus_source = "assignment" if assignment_current_node else ("current_state" if focus else None)
        last_update = workstream.get("updated_at") or report.get("reported_at")
        rows.append({
            "option_id": option.id,
            "option_title": option.title,
            "option_status": option.status,
            "upstream_problem_id": problem_id,
            "upstream_problem_title": node_title(nodes, problem_id),
            "owner": owner,
            "session_id": workstream.get("session_id"),
            "git_branch": workstream.get("git_branch"),
            "worktree_label": workstream.get("worktree_label"),
            "agent_focus_node": agent_focus_node,
            "agent_focus_source": agent_focus_source,
            "assignment_id": str(assignment.assignment_id) if assignment else None,
            "assignment_status": str(assignment.status) if assignment else None,
            "assignment_current_node": assignment_current_node,
            "assignment_next_actions": assignment_next_actions,
            "workstream_status": workstream.get("status"),
            "objective": workstream.get("objective"),
            "report_to_problem": workstream.get("report_to_problem") or problem_id,
            "started_at": workstream.get("started_at"),
            "updated_at": workstream.get("updated_at"),
            "last_update": last_update,
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
    *,
    topology: GraphTopology | None = None,
) -> list[dict[str, Any]]:
    current = current or {}
    topology = topology or GraphTopology.from_nodes(nodes)
    problem_id = problem_id or current.get("current_problem")
    if not problem_id or problem_id not in nodes or nodes[problem_id].type != "problem":
        return []

    problem = nodes[problem_id]
    option_ids = [
        child_id
        for child_id in child_ids(nodes, problem, topology=topology)
        if child_id in nodes and nodes[child_id].type == "option"
    ]
    current_best_option = problem.raw.get("current_best_option") or current.get("current_option")
    rows: list[dict[str, Any]] = []
    for option_id in option_ids:
        option = nodes[option_id]
        experiment_ids = experiment_ids_for_option(nodes, option_id, topology=topology)
        rows.append({
            "id": option.id,
            "title": option.title,
            "status": option.status,
            "decision_state": option.raw.get("decision_state"),
            "evidence_strength": option.raw.get("evidence_strength"),
            "pros": option.raw.get("pros", []) or [],
            "cons": option.raw.get("cons", []) or [],
            "experiment_count": len(experiment_ids),
            "latest_result": latest_experiment_result(nodes, experiment_ids),
            "rejection_reason": option.raw.get("rejection_reason") or option.raw.get("current_conclusion"),
            "is_current_best": option.id == current_best_option,
        })
    return rows


def active_option_workstream_rows(
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None = None,
    *,
    topology: GraphTopology | None = None,
) -> list[dict[str, Any]]:
    return [
        row
        for row in build_option_workstream_rows(nodes, current, topology=topology)
        if row.get("workstream_status") in ACTIVE_WORKSTREAM_STATUSES
    ]
