from __future__ import annotations

from pathlib import Path
from typing import Any

from research_cockpit.command_registry import cli_command_for_script
from research_cockpit.graph_core import (
    child_ids,
    derive_focus_path,
    node_context,
    node_id_by_type_in_path,
    node_title,
    ordered_node_contexts,
    unique_strings,
)
from research_cockpit.types import ACTIVE_WORKSTREAM_STATUSES, ResearchNode


def _workflow_command(script_name: str, *parts: str) -> str:
    return cli_command_for_script(script_name, *parts)


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
        for child_id in child_ids(nodes, nodes[node_id]):
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


def experiment_ids_for_option(nodes: dict[str, ResearchNode], option_id: str) -> list[str]:
    if option_id not in nodes or nodes[option_id].type != "option":
        return []
    return build_option_subtree(nodes, option_id)["experiment_ids"]


def latest_experiment_result(nodes: dict[str, ResearchNode], experiment_ids: list[str]) -> str | None:
    latest: str | None = None
    for experiment_id in experiment_ids:
        experiment = nodes[experiment_id]
        latest = experiment.raw.get("result_summary") or experiment.raw.get("outcome") or experiment.summary or latest
    return latest


def upstream_problem_id(nodes: dict[str, ResearchNode], option_id: str) -> str | None:
    try:
        path = derive_focus_path(nodes, option_id)
    except ValueError:
        return None
    return node_id_by_type_in_path(nodes, path, "problem", nearest=True)


def build_option_workstream_context(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    option_id: str,
    locale: str | None = None,
) -> dict[str, Any]:
    from research_cockpit.decisions import build_decision_evidence_bundle, normalize_locale

    subtree = build_option_subtree(nodes, option_id)
    option = nodes[option_id]
    problem_id = upstream_problem_id(nodes, option_id)
    evidence = build_decision_evidence_bundle(nodes, option_id, locale=normalize_locale(locale, current))
    next_actions: list[str] = []
    blockers: list[str] = []
    for node_id in subtree["node_ids"]:
        node = nodes[node_id]
        next_actions.extend(node.raw.get("next_actions", []) or [])
        blockers.extend(node.raw.get("blockers", []) or [])

    return {
        "option": node_context(option),
        "upstream_problem": node_context(nodes[problem_id]) if problem_id else None,
        "focus_path": ordered_node_contexts(nodes, derive_focus_path(nodes, option_id)),
        "subtree": subtree,
        "subtree_nodes": ordered_node_contexts(nodes, subtree["node_ids"]),
        "problems": ordered_node_contexts(nodes, subtree["problem_ids"]),
        "options": ordered_node_contexts(nodes, subtree["option_ids"]),
        "experiments": ordered_node_contexts(nodes, subtree["experiment_ids"]),
        "decisions": ordered_node_contexts(nodes, subtree["decision_ids"]),
        "evidence_summary": {
            **evidence,
            "experiment_count": len(subtree["experiment_ids"]),
        },
        "focus_next_actions": current.get("next_actions", []) or [],
        "open_next_actions": unique_strings(next_actions),
        "blockers": unique_strings(blockers),
        "suggested_commands": {
            "claim": _workflow_command(
                "claim_option.py",
                "--option",
                option_id,
                "--agent",
                "<agent_id>",
                '--objective "Describe objective"',
            ),
            "context": _workflow_command("option_workstream_context.py", "--id", option_id, "--compact", "--json"),
            "context_option_alias": _workflow_command("option_workstream_context.py", "--option", option_id, "--json"),
            "finalize": _workflow_command(
                "finalize_workstream.py",
                "--file",
                "finalize.yaml",
                "--json",
                "--compact",
            ),
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


def build_option_workstream_rows(
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from research_cockpit.decisions import build_decision_evidence_bundle

    current = current or {}
    agent_focuses = current.get("agent_focuses") if isinstance(current.get("agent_focuses"), dict) else {}
    rows: list[dict[str, Any]] = []

    def option_sort_key(node: ResearchNode) -> tuple[int, str]:
        try:
            return (len(derive_focus_path(nodes, node.id)), node.id)
        except ValueError:
            return (999, node.id)

    for option in sorted((node for node in nodes.values() if node.type == "option"), key=option_sort_key):
        subtree = build_option_subtree(nodes, option.id)
        evidence = build_decision_evidence_bundle(nodes, option.id)
        problem_id = upstream_problem_id(nodes, option.id)
        workstream = option.raw.get("agent_workstream") if isinstance(option.raw.get("agent_workstream"), dict) else {}
        report = option.raw.get("workstream_report") if isinstance(option.raw.get("workstream_report"), dict) else {}
        owner = workstream.get("owner")
        focus = agent_focuses.get(str(owner)) if owner and isinstance(agent_focuses.get(str(owner)), dict) else {}
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
            "agent_focus_node": focus.get("current_focus_node"),
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
) -> list[dict[str, Any]]:
    current = current or {}
    problem_id = problem_id or current.get("current_problem")
    if not problem_id or problem_id not in nodes or nodes[problem_id].type != "problem":
        return []

    problem = nodes[problem_id]
    option_ids = [
        child_id
        for child_id in child_ids(nodes, problem)
        if child_id in nodes and nodes[child_id].type == "option"
    ]
    current_best_option = problem.raw.get("current_best_option") or current.get("current_option")
    rows: list[dict[str, Any]] = []
    for option_id in option_ids:
        option = nodes[option_id]
        experiment_ids = experiment_ids_for_option(nodes, option_id)
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
) -> list[dict[str, Any]]:
    return [
        row for row in build_option_workstream_rows(nodes, current) if row.get("workstream_status") in ACTIVE_WORKSTREAM_STATUSES
    ]
