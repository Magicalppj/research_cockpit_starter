from __future__ import annotations

from pathlib import Path
from typing import Any
import sys

from research_cockpit.graph_core import (
    child_ids,
    derive_focus_path,
    node_context,
    node_has_evidence,
    node_id_by_type_in_path,
    ordered_node_contexts,
)
from research_cockpit.storage import load_yaml, relative_to_root
from research_cockpit.types import VALID_COMMAND_STYLES, ResearchNode
from research_cockpit.model import (
    build_action_suggestions,
    build_decision_acceptance_checklist,
    build_decision_trace,
    build_link_rows,
    build_option_workstream_context,
    recent_interactions,
)


def _command_arg(value: Any) -> str:
    text = str(value)
    if not text:
        return '""'
    if any(char.isspace() for char in text):
        return f'"{text.replace(chr(34), chr(92) + chr(34))}"'
    return text


def _rooted_cli_command(root: Path, subcommand: str, *parts: Any, command_style: str = "console") -> str:
    if command_style not in VALID_COMMAND_STYLES:
        raise ValueError(f"Invalid command style: {command_style}")
    if command_style == "python":
        command = [_command_arg(sys.executable), "-m", "research_cockpit.cli", subcommand, "--root", _command_arg(root)]
    else:
        command = ["research-cockpit", subcommand, "--root", _command_arg(root)]
    command.extend(_command_arg(part) for part in parts if part not in (None, ""))
    return " ".join(command)


def _node_context_file_info(root: Path, filename: str) -> dict[str, Any]:
    path = root / "dashboards" / filename
    info: dict[str, Any] = {
        "path": relative_to_root(root, path),
        "exists": path.exists(),
    }
    if path.exists():
        data = load_yaml(path)
        metadata = data.get("metadata") if isinstance(data, dict) else {}
        if isinstance(metadata, dict):
            info["metadata_generated_at"] = metadata.get("generated_at")
            info["metadata_source_git_commit"] = metadata.get("source_git_commit")
    return info


def _node_context_freshness(root: Path) -> dict[str, Any]:
    return {
        "source": "truth_source_yaml",
        "generated_files": {
            "agent_context_pack": _node_context_file_info(root, "agent_context_pack.json"),
            "focus_context_pack": _node_context_file_info(root, "focus_context_pack.json"),
            "search_index": _node_context_file_info(root, "search_index.json"),
        },
    }


def _node_id_in_path(nodes: dict[str, ResearchNode], node_id: str, node_type: str, *, nearest: bool = False) -> str | None:
    try:
        return node_id_by_type_in_path(nodes, derive_focus_path(nodes, node_id), node_type, nearest=nearest)
    except ValueError:
        return None


def _node_blocker_rows(nodes: dict[str, ResearchNode], node_ids: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for candidate_id in node_ids:
        node = nodes.get(candidate_id)
        if not node:
            continue
        for blocker in node.raw.get("blockers", []) or []:
            if blocker:
                rows.append({"node_id": node.id, "node_title": node.title, "blocker": str(blocker)})
    return rows


def _node_next_action_rows(nodes: dict[str, ResearchNode], node_ids: list[str], current: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for action in current.get("next_actions", []) or []:
        if action:
            rows.append({"node_id": "current_state", "node_title": "current_state", "action": str(action)})
            seen.add(("current_state", str(action)))
    for candidate_id in node_ids:
        node = nodes.get(candidate_id)
        if not node:
            continue
        for action in node.raw.get("next_actions", []) or []:
            key = (node.id, str(action))
            if action and key not in seen:
                rows.append({"node_id": node.id, "node_title": node.title, "action": str(action)})
                seen.add(key)
    return rows


def _node_relevant_suggestions(
    suggestions: list[dict[str, Any]],
    node_id: str,
    related_ids: set[str],
) -> list[dict[str, Any]]:
    relevant: list[dict[str, Any]] = []
    for suggestion in suggestions:
        source_id = str(suggestion.get("source_node_id") or "")
        suggestion_related = {str(item) for item in suggestion.get("related_node_ids", []) or []}
        if source_id == node_id or node_id in suggestion_related or source_id in related_ids:
            relevant.append(suggestion)
    return relevant


def _node_recent_interactions(root: Path, node_id: str, limit: int = 5) -> list[dict[str, Any]]:
    rows = []
    for event in recent_interactions(root, limit=25):
        values = {str(event.get("node_id") or "")}
        for key in ("option_id", "experiment_id", "decision_id", "suggestion_id", "view_id"):
            if event.get(key):
                values.add(str(event[key]))
        if node_id in values:
            rows.append(event)
        if len(rows) >= limit:
            break
    return rows


def _experiment_context(
    root: Path,
    nodes: dict[str, ResearchNode],
    experiment: ResearchNode,
    *,
    command_style: str = "console",
) -> dict[str, Any]:
    option_id = _node_id_in_path(nodes, experiment.id, "option", nearest=True)
    problem_id = _node_id_in_path(nodes, experiment.id, "problem", nearest=True)
    stage_id = _node_id_in_path(nodes, experiment.id, "stage")
    decision_ids = [
        node.id
        for node in sorted(nodes.values(), key=lambda item: item.id)
        if node.type == "decision"
        and (
            experiment.id in (node.raw.get("supporting_experiments", []) or [])
            or (option_id and node.parent == option_id)
        )
    ]
    findings = experiment.raw.get("findings", []) or []
    missing_evidence = not node_has_evidence(experiment)
    return {
        "kind": "experiment",
        "parent_stage": node_context(nodes[stage_id]) if stage_id else None,
        "parent_problem": node_context(nodes[problem_id]) if problem_id else None,
        "parent_option": node_context(nodes[option_id]) if option_id else None,
        "metrics": experiment.raw.get("metrics", []) or [],
        "findings": findings,
        "result_summary": experiment.raw.get("result_summary"),
        "outcome": experiment.raw.get("outcome"),
        "missing_evidence": missing_evidence,
        "related_decisions": ordered_node_contexts(nodes, decision_ids),
        "suggested_commands": {
            "mark_running": _rooted_cli_command(
                root,
                "update-status",
                "--id",
                experiment.id,
                "--status",
                "running",
                command_style=command_style,
            ),
            "record_finding": _rooted_cli_command(
                root,
                "record-finding",
                "--experiment",
                experiment.id,
                "--statement",
                "Describe the finding",
                "--confidence",
                "medium",
                "--outcome",
                "inconclusive",
                command_style=command_style,
            ),
        },
    }


def _alternative_option_for_decision(nodes: dict[str, ResearchNode], decision: ResearchNode) -> str:
    option_id = decision.parent if decision.parent in nodes and nodes[str(decision.parent)].type == "option" else None
    problem_id = nodes[str(option_id)].parent if option_id else None
    if not problem_id or problem_id not in nodes:
        return "<option_id>"
    for child_id in child_ids(nodes, nodes[str(problem_id)]):
        if child_id != option_id and child_id in nodes and nodes[child_id].type == "option":
            return child_id
    return "<option_id>"


def _decision_repair_hints(
    root: Path,
    nodes: dict[str, ResearchNode],
    decision: ResearchNode,
    checklist: dict[str, Any],
    *,
    command_style: str = "console",
) -> list[dict[str, str]]:
    hints: list[dict[str, str]] = []
    alternative_id = _alternative_option_for_decision(nodes, decision)
    for item in checklist.get("blocking_failures", []) or []:
        check_id = str(item.get("id") or "")
        related = [str(node_id) for node_id in item.get("related_node_ids", []) or []]
        command = ""
        if check_id == "supporting_evidence":
            experiment_id = related[0] if related else "<experiment_id>"
            command = _rooted_cli_command(
                root,
                "record-finding",
                "--experiment",
                experiment_id,
                "--statement",
                "Describe the finding",
                "--confidence",
                "medium",
                "--outcome",
                "inconclusive",
                command_style=command_style,
            )
        elif check_id in {"supporting_experiments", "evidence_strength", "evidence_summary"}:
            command = _rooted_cli_command(
                root,
                "update-decision-evidence",
                "--id",
                decision.id,
                command_style=command_style,
            )
        elif check_id == "alternatives_considered":
            command = _rooted_cli_command(
                root,
                "update-decision-checklist",
                "--id",
                decision.id,
                "--alternative",
                alternative_id,
                command_style=command_style,
            )
        elif check_id == "consequences":
            command = _rooted_cli_command(
                root,
                "update-decision-checklist",
                "--id",
                decision.id,
                "--consequence",
                "Describe downstream impact",
                command_style=command_style,
            )
        elif check_id == "next_required_actions":
            command = _rooted_cli_command(
                root,
                "update-decision-checklist",
                "--id",
                decision.id,
                "--next-required-action",
                "Describe required follow-up",
                command_style=command_style,
            )
        hints.append({
            "check_id": check_id,
            "reason": str(item.get("reason") or ""),
            "command": command,
        })
    if any(item.get("check_id") == "supporting_evidence" for item in hints):
        hints.append({
            "check_id": "refresh_evidence",
            "reason": "Refresh decision evidence after recording experiment findings.",
            "command": _rooted_cli_command(
                root,
                "update-decision-evidence",
                "--id",
                decision.id,
                command_style=command_style,
            ),
        })
    return hints


def _decision_context(
    root: Path,
    nodes: dict[str, ResearchNode],
    decision: ResearchNode,
    *,
    command_style: str = "console",
) -> dict[str, Any]:
    checklist = build_decision_acceptance_checklist(nodes, decision.id)
    trace = build_decision_trace(nodes, decision.id)
    return {
        "kind": "decision",
        "trace": trace,
        "acceptance": checklist,
        "repair_hints": _decision_repair_hints(root, nodes, decision, checklist, command_style=command_style),
        "suggested_commands": {
            "check_acceptance": _rooted_cli_command(
                root,
                "check-decision-acceptance",
                "--id",
                decision.id,
                "--json",
                command_style=command_style,
            ),
            "accept_dry_run": _rooted_cli_command(
                root,
                "accept-decision",
                "--id",
                decision.id,
                "--dry-run",
                "--json",
                command_style=command_style,
            ),
        },
    }


def _option_onboarding_context(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    option: ResearchNode,
    *,
    command_style: str = "console",
) -> dict[str, Any]:
    workstream = build_option_workstream_context(root, nodes, current, option.id)
    workstream["suggested_commands"] = {
        "claim": _rooted_cli_command(
            root,
            "claim-option",
            "--option",
            option.id,
            "--agent",
            "<agent_id>",
            "--objective",
            "Describe objective",
            command_style=command_style,
        ),
        "context": _rooted_cli_command(
            root,
            "option-workstream-context",
            "--option",
            option.id,
            "--json",
            command_style=command_style,
        ),
        "report": _rooted_cli_command(
            root,
            "report-option-workstream",
            "--option",
            option.id,
            "--agent",
            "<agent_id>",
            "--recommend",
            "continue",
            "--summary",
            "Summarize evidence and recommendation",
            command_style=command_style,
        ),
    }
    return {
        "kind": "option",
        "workstream": workstream,
    }


def _node_command_drafts(
    root: Path,
    node: ResearchNode,
    type_context: dict[str, Any],
    *,
    command_style: str = "console",
) -> dict[str, str]:
    drafts = {
        "validate": _rooted_cli_command(root, "validate", "--json", command_style=command_style),
        "build": _rooted_cli_command(root, "build", command_style=command_style),
        "search_node": _rooted_cli_command(root, "search", "--query", node.id, "--json", command_style=command_style),
    }
    if node.type == "option":
        drafts.update({
            "claim_option": _rooted_cli_command(
                root,
                "claim-option",
                "--option",
                node.id,
                "--agent",
                "<agent_id>",
                "--objective",
                "Describe objective",
                "--dry-run",
                "--json",
                command_style=command_style,
            ),
            "option_workstream_context": _rooted_cli_command(
                root,
                "option-workstream-context",
                "--option",
                node.id,
                "--json",
                command_style=command_style,
            ),
            "report_option_workstream": _rooted_cli_command(
                root,
                "report-option-workstream",
                "--option",
                node.id,
                "--agent",
                "<agent_id>",
                "--recommend",
                "continue",
                "--summary",
                "Summarize evidence and recommendation",
                "--dry-run",
                "--json",
                command_style=command_style,
            ),
        })
    elif node.type == "experiment":
        drafts.update(type_context.get("suggested_commands", {}))
    elif node.type == "decision":
        drafts.update(type_context.get("suggested_commands", {}))
    return drafts


def _recommended_next_steps(node: ResearchNode, type_context: dict[str, Any], drafts: dict[str, str]) -> list[dict[str, str]]:
    if node.type == "experiment" and type_context.get("missing_evidence"):
        return [{
            "action": "Run or complete the experiment, then record one structured finding.",
            "command": drafts.get("record_finding", ""),
            "reason": "This experiment has no findings, result_summary, or outcome yet.",
        }]
    if node.type == "decision" and not type_context.get("acceptance", {}).get("ready", False):
        first_repair = next((item for item in type_context.get("repair_hints", []) if item.get("command")), {})
        return [{
            "action": "Repair blocking decision acceptance failures before accepting the decision.",
            "command": str(first_repair.get("command") or drafts.get("check_acceptance") or ""),
            "reason": "Decision acceptance checklist is not ready.",
        }]
    if node.type == "option":
        return [{
            "action": "Claim the option workstream before starting agent work.",
            "command": drafts.get("claim_option", ""),
            "reason": "Option work should be coordinated through an agent workstream.",
        }]
    for action in node.raw.get("next_actions", []) or []:
        return [{"action": str(action), "command": "", "reason": "Node lists this as its next action."}]
    return []


def _compact_node_summary(node: dict[str, Any] | None) -> dict[str, Any] | None:
    if not node:
        return None
    fields = [
        "id",
        "type",
        "title",
        "status",
        "priority",
        "summary",
        "question",
        "hypothesis",
        "current_best_option",
        "decision_state",
        "evidence_strength",
        "evidence_summary",
        "result_summary",
        "outcome",
    ]
    return {field: node.get(field) for field in fields if node.get(field) not in (None, "", [])}


def _compact_core_problem(parent_path: list[dict[str, Any]]) -> dict[str, Any] | None:
    for node in reversed(parent_path):
        if node.get("type") == "problem":
            return node
    return None


def _compact_evidence_summary(payload: dict[str, Any]) -> dict[str, Any]:
    node = payload.get("node", {})
    type_context = payload.get("type_context", {})
    kind = type_context.get("kind") or node.get("type")
    if kind == "option":
        workstream = type_context.get("workstream", {})
        evidence = workstream.get("evidence_summary", {})
        return evidence if isinstance(evidence, dict) else {}
    if kind == "decision":
        trace = type_context.get("trace", {})
        evidence = trace.get("evidence_summary", {})
        return evidence if isinstance(evidence, dict) else {}
    if kind == "experiment":
        findings = type_context.get("findings", []) or []
        return {
            "findings_count": len(findings) if isinstance(findings, list) else 0,
            "missing_evidence": bool(type_context.get("missing_evidence")),
            "result_summary": type_context.get("result_summary"),
            "outcome": type_context.get("outcome"),
        }
    return {
        "evidence_strength": node.get("evidence_strength"),
        "evidence_summary": node.get("evidence_summary"),
    }


def _compact_node_onboarding_context(payload: dict[str, Any]) -> dict[str, Any]:
    parent_path = [
        summary
        for summary in (_compact_node_summary(item) for item in payload.get("parent_chain", []))
        if summary
    ]
    recommended_next_steps = payload.get("recommended_next_steps", []) or []
    return {
        "schema_version": "node_context_compact_v1",
        "node": _compact_node_summary(payload.get("node")) or {},
        "parent_path": parent_path,
        "core_problem": _compact_core_problem(parent_path),
        "blockers": payload.get("blockers", []) or [],
        "next_actions": payload.get("next_actions", []) or [],
        "evidence_summary": _compact_evidence_summary(payload),
        "recommended_next_step": recommended_next_steps[0] if recommended_next_steps else None,
        "recommended_next_steps": recommended_next_steps,
        "command_drafts": payload.get("command_drafts", {}) or {},
        "context_freshness": payload.get("context_freshness", {}) or {},
    }


def build_node_onboarding_context(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    node_id: str,
    *,
    compact: bool = False,
    command_style: str = "console",
) -> dict[str, Any]:
    if command_style not in VALID_COMMAND_STYLES:
        raise ValueError(f"Invalid command style: {command_style}")
    if node_id not in nodes:
        raise ValueError(f"Node does not exist: {node_id}")
    node = nodes[node_id]
    path_ids = derive_focus_path(nodes, node_id)
    child_ids_for_node = child_ids(nodes, node)
    sibling_ids: list[str] = []
    if node.parent and node.parent in nodes:
        sibling_ids = [child_id for child_id in child_ids(nodes, nodes[str(node.parent)]) if child_id != node.id]
    related_ids = set(path_ids + child_ids_for_node + sibling_ids)
    link_rows = build_link_rows(root, nodes)
    suggestions = build_action_suggestions(root, nodes, current, link_rows)

    if node.type == "option":
        type_context = _option_onboarding_context(root, nodes, current, node, command_style=command_style)
    elif node.type == "experiment":
        type_context = _experiment_context(root, nodes, node, command_style=command_style)
    elif node.type == "decision":
        type_context = _decision_context(root, nodes, node, command_style=command_style)
    else:
        type_context = {"kind": node.type}

    command_drafts = _node_command_drafts(root, node, type_context, command_style=command_style)
    payload = {
        "node": node_context(node),
        "parent_chain": ordered_node_contexts(nodes, path_ids),
        "relations": {
            "parents": ordered_node_contexts(nodes, path_ids[:-1]),
            "children": ordered_node_contexts(nodes, child_ids_for_node),
            "siblings": ordered_node_contexts(nodes, sibling_ids),
        },
        "blockers": _node_blocker_rows(nodes, path_ids + child_ids_for_node),
        "next_actions": _node_next_action_rows(nodes, path_ids + child_ids_for_node, current),
        "relevant_suggestions": _node_relevant_suggestions(suggestions, node.id, related_ids),
        "resources": [row for row in link_rows if row.get("node_id") in related_ids],
        "recent_interactions": _node_recent_interactions(root, node.id),
        "context_freshness": _node_context_freshness(root),
        "type_context": type_context,
        "command_drafts": command_drafts,
        "recommended_next_steps": _recommended_next_steps(node, type_context, command_drafts),
    }
    if compact:
        return _compact_node_onboarding_context(payload)
    return payload
