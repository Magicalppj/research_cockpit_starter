from __future__ import annotations

from typing import Any

from research_cockpit.graph_core import (
    GraphTopology,
    derive_focus_path,
    node_context,
    node_id_by_type_in_path,
    ordered_node_contexts,
    unique_strings,
)
from research_cockpit.option_workstreams import experiment_ids_for_option
from research_cockpit.types import ResearchNode

VALID_LOCALES = {"en", "zh"}


def normalize_locale(locale: str | None = None, current: dict[str, Any] | None = None) -> str:
    raw = str(locale or (current or {}).get("language") or "en").strip().lower()
    if raw.startswith("zh"):
        return "zh"
    return "en"


def has_experiment_evidence(experiment: ResearchNode) -> bool:
    return bool(
        experiment.raw.get("findings")
        or experiment.raw.get("result_summary")
        or experiment.raw.get("outcome")
    )


def evidence_experiment_ids(
    nodes: dict[str, ResearchNode],
    node_id: str,
    *,
    topology: GraphTopology | None = None,
) -> list[str]:
    if node_id not in nodes:
        return []
    node = nodes[node_id]
    if node.type == "experiment":
        return [node.id]
    if node.type == "option":
        return experiment_ids_for_option(nodes, node.id, topology=topology)
    if node.type != "decision":
        return []

    experiment_ids = unique_strings(node.raw.get("supporting_experiments", []) or [])
    option_id = node.parent if node.parent in nodes and nodes[node.parent].type == "option" else None
    if option_id:
        experiment_ids = unique_strings(experiment_ids + experiment_ids_for_option(nodes, str(option_id), topology=topology))
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

    supporting_experiments = unique_strings(decision.raw.get("supporting_experiments", []) or [])
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
    evidence_ok = any(has_experiment_evidence(nodes[experiment_id]) for experiment_id in valid_experiment_ids)
    checks.append(_acceptance_check(
        "supporting_evidence",
        "Supporting experiments contain evidence",
        evidence_ok,
        "At least one supporting experiment has findings, result_summary, or outcome."
        if evidence_ok else "At least one supporting experiment must contain findings, result_summary, or outcome.",
        related_node_ids=valid_experiment_ids,
    ))

    strength = str(decision.raw.get("evidence_strength") or "none")
    strength_ok = strength in {"weak", "medium", "strong"} and strength != "none"
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

    alternative_ids = unique_strings(decision.raw.get("alternatives_considered", []) or [])
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
    locale: str | None = None,
    *,
    topology: GraphTopology | None = None,
) -> dict[str, Any]:
    if option_id not in nodes:
        raise ValueError(f"Option node does not exist: {option_id}")
    if nodes[option_id].type != "option":
        raise ValueError(f"Node {option_id} must be option, got {nodes[option_id].type}")

    manual_ids = unique_strings(supporting_experiments or [])
    _validate_experiment_refs(nodes, manual_ids, "supporting_experiments")
    automatic_ids = [
        experiment_id
        for experiment_id in experiment_ids_for_option(nodes, option_id, topology=topology)
        if has_experiment_evidence(nodes[experiment_id])
    ]
    experiment_ids = unique_strings(manual_ids + automatic_ids)

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
    locale = normalize_locale(locale)
    if locale == "zh":
        summary_parts = [
            f"{len(experiment_ids)} 个实验",
            f"{len(findings)} 条 finding",
        ]
    else:
        summary_parts = [
            f"{len(experiment_ids)} experiment(s)",
            f"{len(findings)} finding(s)",
        ]
    if outcome_counts:
        label = "结果: " if locale == "zh" else "outcomes: "
        summary_parts.append(label + ", ".join(f"{key}={value}" for key, value in sorted(outcome_counts.items())))
    if latest_finding:
        label = "最新 finding: " if locale == "zh" else "latest finding: "
        summary_parts.append(f"{label}{latest_finding}")
    return {
        "supporting_experiments": experiment_ids,
        "evidence_strength": strength,
        "evidence_summary": "; ".join(summary_parts),
        "findings_count": len(findings),
        "outcome_counts": outcome_counts,
        "latest_finding": latest_finding,
    }


def build_decision_evidence_summary(
    nodes: dict[str, ResearchNode],
    node_id: str,
    *,
    topology: GraphTopology | None = None,
) -> dict[str, Any]:
    experiment_ids = evidence_experiment_ids(nodes, node_id, topology=topology)
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


def build_decision_trace(nodes: dict[str, ResearchNode], decision_id: str) -> dict[str, Any]:
    if decision_id not in nodes:
        raise ValueError(f"Decision node does not exist: {decision_id}")
    decision = nodes[decision_id]
    if decision.type != "decision":
        raise ValueError(f"Node {decision_id} must be decision, got {decision.type}")

    path = derive_focus_path(nodes, decision_id)
    stage_id = node_id_by_type_in_path(nodes, path, "stage")
    problem_id = node_id_by_type_in_path(nodes, path, "problem", nearest=True)
    option_id = node_id_by_type_in_path(nodes, path, "option", nearest=True)

    supporting_experiment_ids = unique_strings(decision.raw.get("supporting_experiments", []) or [])
    if option_id:
        supporting_experiment_ids = unique_strings(
            supporting_experiment_ids + experiment_ids_for_option(nodes, option_id)
        )
    alternative_ids = unique_strings(decision.raw.get("alternatives_considered", []) or [])

    return {
        "decision": node_context(decision),
        "stage": node_context(nodes[stage_id]) if stage_id else None,
        "problem": node_context(nodes[problem_id]) if problem_id else None,
        "option": node_context(nodes[option_id]) if option_id else None,
        "focus_path": ordered_node_contexts(nodes, path),
        "supporting_experiments": ordered_node_contexts(nodes, supporting_experiment_ids),
        "alternatives_considered": ordered_node_contexts(nodes, alternative_ids),
        "consequences": decision.raw.get("consequences", []) or [],
        "evidence_summary": {
            **build_decision_evidence_summary(nodes, decision_id),
            "summary_text": decision.raw.get("evidence_summary"),
        },
    }


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
