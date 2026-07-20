from __future__ import annotations

import shlex
from typing import Any

from research_cockpit.graph_core import (
    derive_focus_path,
    focus_node_id_from_current,
    node_id_by_type_in_path,
    node_title,
    unique_strings,
)
from research_cockpit.option_workstreams import experiment_ids_for_option, upstream_problem_id
from research_cockpit.resources import node_artifact_ids
from research_cockpit.types import ResearchNode


def _node_ref(nodes: dict[str, ResearchNode], node_id: str | None) -> dict[str, Any] | None:
    if not node_id or node_id not in nodes:
        return None
    node = nodes[node_id]
    return {
        "id": node.id,
        "title": node.title,
        "type": node.type,
        "status": node.status,
        "summary": node.summary,
    }


def _baseline_mapping(node: ResearchNode) -> dict[str, Any] | None:
    value = node.raw.get("baseline")
    return value if isinstance(value, dict) and value.get("option") else None


def _problem_for_node(nodes: dict[str, ResearchNode], node_id: str) -> str | None:
    if node_id not in nodes:
        return None
    try:
        path = derive_focus_path(nodes, node_id)
    except ValueError:
        return None
    return node_id_by_type_in_path(nodes, path, "problem", nearest=True)


def _stage_for_node(nodes: dict[str, ResearchNode], node_id: str) -> str | None:
    if node_id not in nodes:
        return None
    try:
        path = derive_focus_path(nodes, node_id)
    except ValueError:
        return None
    return node_id_by_type_in_path(nodes, path, "stage")


def _option_for_node(nodes: dict[str, ResearchNode], node_id: str) -> str | None:
    if node_id not in nodes:
        return None
    try:
        path = derive_focus_path(nodes, node_id)
    except ValueError:
        return None
    return node_id_by_type_in_path(nodes, path, "option", nearest=True)


def _effective_from_mapping(
    nodes: dict[str, ResearchNode],
    *,
    source_node_id: str,
    source_kind: str,
    baseline: dict[str, Any],
) -> dict[str, Any]:
    option_id = str(baseline.get("option") or "")
    decision_id = str(baseline.get("decision") or "")
    raw_artifacts = baseline.get("artifacts", [])
    if not isinstance(raw_artifacts, list):
        raw_artifacts = []
    artifacts = unique_strings([str(item) for item in raw_artifacts])
    return {
        "source_node_id": source_node_id,
        "source_kind": source_kind,
        "option": _node_ref(nodes, option_id),
        "decision": _node_ref(nodes, decision_id),
        "artifacts": [
            artifact
            for artifact in (_node_ref(nodes, artifact_id) for artifact_id in artifacts)
            if artifact
        ],
        "reason": str(baseline.get("reason") or ""),
    }


def empty_effective_baseline() -> dict[str, Any]:
    return {
        "source_node_id": "",
        "source_kind": "none",
        "option": None,
        "decision": None,
        "artifacts": [],
        "reason": "",
    }


def compact_effective_baseline(
    effective: dict[str, Any],
    *,
    text_limit: int = 200,
) -> dict[str, Any]:
    """Return the stable baseline projection shared by indexes and Work Packets."""

    def ref(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or not value.get("id"):
            return None
        return {
            key: value[key]
            for key in ("id", "type", "status")
            if value.get(key) not in (None, "")
        }

    reason = str(effective.get("reason") or "").strip()
    if len(reason) > text_limit:
        reason = reason[: max(1, text_limit - 3)] + "..."
    return {
        "source_node_id": str(effective.get("source_node_id") or ""),
        "source_kind": str(effective.get("source_kind") or "none"),
        "option": ref(effective.get("option")),
        "decision": ref(effective.get("decision")),
        "artifacts": [
            item
            for item in (ref(value) for value in effective.get("artifacts", []) or [])
            if item is not None
        ][:20],
        "reason": reason,
    }


def resolve_effective_baseline(
    nodes: dict[str, ResearchNode],
    node_id: str,
    current: dict[str, Any] | None = None,
    *,
    allow_current_state_fallback: bool = True,
) -> dict[str, Any]:
    current = current or {}
    if node_id not in nodes:
        raise ValueError(f"Node does not exist: {node_id}")

    try:
        path = derive_focus_path(nodes, node_id)
    except ValueError:
        path = [node_id]

    for source_node_id in reversed(path):
        baseline = _baseline_mapping(nodes[source_node_id])
        if baseline:
            return _effective_from_mapping(
                nodes,
                source_node_id=source_node_id,
                source_kind="explicit" if source_node_id == node_id else "inherited",
                baseline=baseline,
            )

    problem_id = node_id_by_type_in_path(nodes, path, "problem", nearest=True)
    problem = nodes.get(str(problem_id)) if problem_id else None
    if problem and problem.type == "problem":
        option_id = problem.raw.get("current_best_option")
        decision_id = problem.raw.get("resolved_by")
        if option_id and str(option_id) in nodes and nodes[str(option_id)].type == "option":
            return _effective_from_mapping(
                nodes,
                source_node_id=problem.id,
                source_kind="problem_fallback",
                baseline={"option": str(option_id), "decision": decision_id, "artifacts": [], "reason": ""},
            )

    current_option = current.get("current_option")
    if (
        allow_current_state_fallback
        and current_option
        and str(current_option) in nodes
        and nodes[str(current_option)].type == "option"
    ):
        return _effective_from_mapping(
            nodes,
            source_node_id="current_state",
            source_kind="current_state_fallback",
            baseline={"option": str(current_option), "artifacts": [], "reason": ""},
        )
    return empty_effective_baseline()


def resolve_current_effective_baseline(
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
) -> dict[str, Any]:
    focus_node_id = focus_node_id_from_current(current, nodes)
    target_id = focus_node_id or current.get("current_problem") or current.get("current_option")
    if target_id and str(target_id) in nodes:
        return resolve_effective_baseline(nodes, str(target_id), current)
    return empty_effective_baseline()


def _effective_baseline_option_id(effective: dict[str, Any]) -> str:
    option = effective.get("option")
    if isinstance(option, dict) and option.get("id"):
        return str(option["id"])
    return ""


def _empty_graph_baseline_metadata() -> dict[str, Any]:
    return {
        "effective_baseline_option_id": "",
        "baseline_source_id": "",
        "baseline_source_kind": "none",
        "is_baseline_source": False,
        "is_effective_baseline_option": False,
        "is_current_effective_baseline_option": False,
    }


def build_graph_baseline_metadata(
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    current = current or {}
    metadata = {node_id: _empty_graph_baseline_metadata() for node_id in nodes}

    for node_id in sorted(nodes):
        try:
            effective = resolve_effective_baseline(
                nodes,
                node_id,
                current,
                allow_current_state_fallback=False,
            )
        except ValueError:
            continue
        option_id = _effective_baseline_option_id(effective)
        source_id = str(effective.get("source_node_id") or "")
        source_kind = str(effective.get("source_kind") or "none")
        metadata[node_id].update({
            "effective_baseline_option_id": option_id,
            "baseline_source_id": source_id if source_id in nodes else "",
            "baseline_source_kind": source_kind,
        })
        if source_id in metadata:
            metadata[source_id]["is_baseline_source"] = True
        if option_id in metadata:
            metadata[option_id]["is_effective_baseline_option"] = True

    current_effective = resolve_current_effective_baseline(nodes, current)
    current_option_id = _effective_baseline_option_id(current_effective)
    if current_option_id in metadata:
        metadata[current_option_id]["is_current_effective_baseline_option"] = True
        metadata[current_option_id]["is_effective_baseline_option"] = True
    return metadata


def validate_baseline_for_node(
    nodes: dict[str, ResearchNode],
    node: ResearchNode,
    baseline: Any,
) -> list[str]:
    if baseline is None:
        return []
    errors: list[str] = []
    if node.type not in {"stage", "problem", "option", "experiment"}:
        errors.append(f"{node.id}: baseline is only supported on stage, problem, option, or experiment nodes")
        return errors
    if not isinstance(baseline, dict):
        return [f"{node.id}: baseline must be a mapping"]

    option_id = str(baseline.get("option") or "")
    if not option_id:
        errors.append(f"{node.id}: baseline.option is required")
    elif option_id not in nodes:
        errors.append(f"{node.id}: baseline.option references missing node {option_id!r}")
    elif nodes[option_id].type != "option":
        errors.append(
            f"{node.id}: baseline.option must be option; references {option_id!r} "
            f"with type {nodes[option_id].type!r}"
        )

    decision_id = str(baseline.get("decision") or "")
    if decision_id:
        if decision_id not in nodes:
            errors.append(f"{node.id}: baseline.decision references missing node {decision_id!r}")
        elif nodes[decision_id].type != "decision":
            errors.append(
                f"{node.id}: baseline.decision references {decision_id!r} with type "
                f"{nodes[decision_id].type!r}; expected 'decision'"
            )
        elif option_id in nodes and nodes[option_id].type == "option":
            decision_option = _option_for_node(nodes, decision_id)
            if decision_option != option_id:
                errors.append(
                    f"{node.id}: baseline.decision must belong to baseline.option "
                    f"{option_id!r}; got {decision_id!r}"
                )

    artifacts = baseline.get("artifacts", [])
    if artifacts is None:
        artifacts = []
    if not isinstance(artifacts, list):
        errors.append(f"{node.id}: baseline.artifacts must be a list")
    else:
        for artifact_id in artifacts:
            ref_id = str(artifact_id)
            if ref_id not in nodes:
                errors.append(f"{node.id}: baseline.artifacts references missing node {ref_id!r}")
            elif nodes[ref_id].type != "artifact":
                errors.append(
                    f"{node.id}: baseline.artifacts references {ref_id!r} with type "
                    f"{nodes[ref_id].type!r}; expected 'artifact'"
                )

    if option_id in nodes and nodes[option_id].type == "option":
        if node.type == "stage":
            node_stage = node.id
            option_stage = _stage_for_node(nodes, option_id)
            if option_stage != node_stage:
                errors.append(f"{node.id}: baseline.option must belong to stage {node.id}")
        else:
            node_problem = _problem_for_node(nodes, node.id)
            option_problem = upstream_problem_id(nodes, option_id)
            if node_problem and option_problem != node_problem:
                errors.append(
                    f"{node.id}: baseline.option must belong to the same problem "
                    f"({node_problem}), got {option_problem}"
                )
    return errors


def baseline_artifact_ids(effective_baseline: dict[str, Any]) -> list[str]:
    return unique_strings(
        [
            str(item.get("id"))
            for item in effective_baseline.get("artifacts", []) or []
            if isinstance(item, dict) and item.get("id")
        ]
    )


def build_baseline_overview_rows(
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    current = current or {}
    rows: list[dict[str, Any]] = []
    for problem in sorted((node for node in nodes.values() if node.type == "problem"), key=lambda item: item.id):
        effective = resolve_effective_baseline(
            nodes,
            problem.id,
            current,
            allow_current_state_fallback=False,
        )
        option = effective.get("option") or {}
        decision = effective.get("decision") or {}
        rows.append({
            "problem_id": problem.id,
            "problem_title": problem.title,
            "problem_status": problem.status,
            "baseline_option_id": option.get("id", ""),
            "baseline_option_title": option.get("title", ""),
            "baseline_decision_id": decision.get("id", ""),
            "baseline_decision_title": decision.get("title", ""),
            "artifact_count": len(effective.get("artifacts", []) or []),
            "source_node_id": effective.get("source_node_id", ""),
            "source_kind": effective.get("source_kind", ""),
            "reason": effective.get("reason", ""),
            "is_current_best": bool(option.get("id") and option.get("id") == problem.raw.get("current_best_option")),
        })
    return rows


def _finding_count(nodes: dict[str, ResearchNode], experiment_ids: list[str]) -> int:
    count = 0
    for experiment_id in experiment_ids:
        findings = nodes[experiment_id].raw.get("findings", []) or []
        if isinstance(findings, list):
            count += len([finding for finding in findings if isinstance(finding, dict)])
    return count


def _artifact_count_for_nodes(nodes: dict[str, ResearchNode], node_ids: list[str]) -> int:
    artifact_ids: list[str] = []
    for node_id in node_ids:
        if node_id in nodes:
            artifact_ids.extend(node_artifact_ids(nodes[node_id]))
    return len(unique_strings(artifact_ids))


def build_accepted_option_rows(
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    current = current or {}
    rows: list[dict[str, Any]] = []
    for option in sorted((node for node in nodes.values() if node.type == "option" and node.status == "accepted"), key=lambda item: item.id):
        problem_id = upstream_problem_id(nodes, option.id)
        problem = nodes.get(str(problem_id)) if problem_id else None
        experiment_ids = experiment_ids_for_option(nodes, option.id)
        rows.append({
            "id": option.id,
            "title": option.title,
            "status": option.status,
            "problem_id": problem_id or "",
            "problem_title": problem.title if problem else "",
            "decision_state": option.raw.get("decision_state") or "",
            "is_current_best": bool(problem and problem.raw.get("current_best_option") == option.id),
            "is_current_option": current.get("current_option") == option.id,
            "finding_count": _finding_count(nodes, experiment_ids),
            "artifact_count": _artifact_count_for_nodes(nodes, [option.id, *experiment_ids]),
            "summary": option.summary,
        })
    return rows


def build_accepted_decision_rows(nodes: dict[str, ResearchNode]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for decision in sorted((node for node in nodes.values() if node.type == "decision" and node.status == "accepted"), key=lambda item: item.id):
        try:
            path = derive_focus_path(nodes, decision.id)
        except ValueError:
            path = [decision.id]
        option_id = node_id_by_type_in_path(nodes, path, "option", nearest=True)
        problem_id = node_id_by_type_in_path(nodes, path, "problem", nearest=True)
        supporting = decision.raw.get("supporting_experiments", []) or []
        rows.append({
            "id": decision.id,
            "title": decision.title,
            "status": decision.status,
            "option_id": option_id or "",
            "option_title": node_title(nodes, option_id) or "",
            "problem_id": problem_id or "",
            "problem_title": node_title(nodes, problem_id) or "",
            "evidence_strength": decision.raw.get("evidence_strength") or "",
            "supporting_experiment_count": len(supporting) if isinstance(supporting, list) else 0,
            "summary": decision.summary,
        })
    return rows


def _quote_command_value(value: str) -> str:
    return shlex.quote(str(value))


def build_set_baseline_command(
    node_id: str,
    option_id: str = "",
    *,
    decision_id: str = "",
    artifacts: list[str] | None = None,
    reason: str = "",
    clear: bool = False,
) -> str:
    parts = ["research-cockpit", "set-baseline", "--node", _quote_command_value(node_id)]
    if clear:
        parts.append("--clear")
        return " ".join(parts)
    if option_id:
        parts.extend(["--option", _quote_command_value(option_id)])
    if decision_id:
        parts.extend(["--decision", _quote_command_value(decision_id)])
    for artifact_id in artifacts or []:
        parts.extend(["--artifact", _quote_command_value(artifact_id)])
    if reason:
        parts.extend(["--reason", _quote_command_value(reason)])
    return " ".join(parts)
