from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_cockpit.agent_state import AssignmentRecord, load_assignment
from research_cockpit.commands._runtime import stable_payload_revision
from research_cockpit.gate_result_records import build_gate_summaries
from research_cockpit.public_contracts import parse_public_contract
from research_cockpit.storage import load_yaml
from research_cockpit.validation_index import (
    is_index_schema_compatible,
    load_validation_index,
)


SYNTHESIS_SCHEMA_VERSION = "synthesis_packet_v1"
SYNTHESIS_COLLECTION_LIMIT = 20
SYNTHESIS_MAX_BYTES = 4 * 1024
_TEXT_LIMIT = 200


def _text(value: Any, *, limit: int = _TEXT_LIMIT) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)] + "..."


def _bounded(values: list[Any], *, limit: int = SYNTHESIS_COLLECTION_LIMIT) -> dict[str, Any]:
    actual_limit = max(0, min(limit, SYNTHESIS_COLLECTION_LIMIT))
    items = values[:actual_limit]
    return {
        "items": items,
        "limit": actual_limit,
        "total": len(values),
        "omitted": len(values) - len(items),
    }


def _review_status(assignment: AssignmentRecord) -> str:
    return str(
        assignment.review.get("status")
        or ("pending" if assignment.review.get("required") else "not_required")
    )


def _dependency_satisfied(
    dependency: AssignmentRecord,
    specification: dict[str, Any],
) -> bool:
    required_status = specification.get("required_status")
    required_review = specification.get("required_review_status")
    if required_status is None and required_review is None:
        required_status = "completed"
    return (
        (required_status is None or dependency.status == str(required_status))
        and (required_review is None or _review_status(dependency) == str(required_review))
    )


def _result_revision(assignment: AssignmentRecord) -> str | None:
    revision = str(assignment.result.get("revision") or "").strip()
    return revision or None


def _finding(
    root: Path,
    experiment_ids: list[str],
    finding_id: str,
) -> dict[str, Any] | None:
    for experiment_id in experiment_ids:
        path = root / "graph" / "nodes" / f"{experiment_id}.yaml"
        data = load_yaml(path) if path.is_file() else None
        if not isinstance(data, dict):
            continue
        for finding in data.get("findings", []) or []:
            if isinstance(finding, dict) and str(finding.get("id") or "") == finding_id:
                return dict(finding)
    return None


def _confidence(findings: list[dict[str, Any]]) -> str:
    levels = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    aliases = {
        "weak": "low",
        "low": "low",
        "medium": "medium",
        "moderate": "medium",
        "strong": "high",
        "high": "high",
    }
    normalized = [aliases.get(str(item.get("confidence") or "").lower(), "unknown") for item in findings]
    return max(normalized or ["unknown"], key=lambda value: levels[value])


def _metric(item: Any, *, source_revision: str) -> dict[str, Any] | None:
    if isinstance(item, dict):
        name = _text(item.get("name"), limit=100)
        value = item.get("value")
        unit = item.get("unit")
    else:
        name, separator, raw_value = str(item or "").partition("=")
        if not separator:
            return None
        name = _text(name, limit=100)
        raw_value = raw_value.strip()
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        unit = None
    if not name or isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str):
        value = _text(value, limit=100)
        if not value:
            return None
    return {
        "name": name,
        "value": value,
        "unit": _text(unit, limit=40) or None,
        "source_result_revision": source_revision,
    }


def _gate_records_for_runs(root: Path, run_ids: set[str]) -> tuple[list[dict[str, Any]], bool]:
    if not run_ids:
        return [], True
    index = load_validation_index(root)
    records: list[dict[str, Any]] = []
    if is_index_schema_compatible(index):
        assert index is not None
        gate_ids = {
            str(gate_id)
            for run_id in run_ids
            for gate_id in (index.get("gate_results_by_run", {}) or {}).get(run_id, []) or []
        }
        rows = index.get("gate_results", {}) or {}
        for gate_id in sorted(gate_ids):
            row = rows.get(gate_id, {}) or {}
            record_file = str(row.get("record_file") or "")
            data = load_yaml(root / record_file) if record_file else None
            if isinstance(data, dict):
                records.append(dict(data))
        return records, True
    return [], False


def _gate_rows(
    root: Path,
    run_revisions: dict[str, str],
) -> tuple[list[dict[str, Any]], bool]:
    records, index_available = _gate_records_for_runs(root, set(run_revisions))
    summaries, _warnings = build_gate_summaries(
        root,
        records=records,
    )
    rows: list[dict[str, Any]] = []
    for gate in summaries:
        run_id = str(gate.get("run_id") or "")
        source_revision = run_revisions.get(run_id)
        gate_id = str(gate.get("gate_id") or "")
        if not source_revision or not gate_id:
            continue
        passed = gate.get("passed")
        if passed is True:
            status = "passed"
        elif passed is False:
            status = "failed"
        elif gate.get("blocks_next_action"):
            status = "blocked"
        else:
            status = "not_run"
        summary = _text(
            gate.get("next_allowed_action")
            or f"{gate.get('gate_type') or 'Gate'} is {status}."
        )
        rows.append(
            {
                "gate_id": gate_id,
                "status": status,
                "summary": summary,
                "source_result_revision": source_revision,
            }
        )
    return rows, index_available


def _encoded_size(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _fit_budget(payload: dict[str, Any]) -> None:
    collection_names = (
        "unresolved_questions",
        "missing_evidence",
        "stale_input_warnings",
        "contradictions",
        "gate_summaries",
        "metrics",
        "artifact_links",
        "evidence_pairs",
        "candidate_options",
        "decision_criteria",
    )
    while _encoded_size(payload) >= SYNTHESIS_MAX_BYTES:
        for name in collection_names:
            if name == "evidence_pairs":
                evidence = payload["evidence_bundles"]
                outcomes = payload["outcome_summaries"]
                if len(evidence["items"]) != len(outcomes["items"]):
                    raise ValueError("Synthesis evidence and outcome projections are not aligned")
                if evidence["items"]:
                    evidence["items"].pop()
                    outcomes["items"].pop()
                    evidence["omitted"] = evidence["total"] - len(evidence["items"])
                    outcomes["omitted"] = outcomes["total"] - len(outcomes["items"])
                    break
                continue
            collection = payload[name]
            if collection["items"]:
                collection["items"].pop()
                collection["omitted"] = collection["total"] - len(collection["items"])
                break
        else:
            if len(payload["research_question"]) > 80:
                payload["research_question"] = _text(payload["research_question"], limit=80)
                continue
            raise ValueError("Synthesis Packet cannot fit the 4 KiB output budget")


def build_synthesis_packet_for_assignment(
    root: Path,
    assignment: AssignmentRecord,
) -> dict[str, Any]:
    if assignment.kind != "synthesis":
        raise ValueError(f"{assignment.assignment_id}: assignment kind must be synthesis")
    metadata = assignment.raw.get("synthesis")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    expected_revisions = assignment.inputs.get("dependency_revisions", {})
    if not isinstance(expected_revisions, dict):
        expected_revisions = {}

    evidence_revisions: list[str] = []
    outcomes: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    artifact_links: list[str] = []
    missing: list[str] = []
    stale: list[str] = []
    run_revisions: dict[str, str] = {}
    dependency_root_nodes: list[str] = []

    for specification in assignment.dependencies[:SYNTHESIS_COLLECTION_LIMIT]:
        dependency_id = str(specification.get("assignment_id") or "")
        expected = expected_revisions.get(dependency_id)
        try:
            dependency = load_assignment(root, dependency_id)
        except FileNotFoundError:
            missing.append(f"{dependency_id}: dependency assignment is missing.")
            if expected:
                stale.append(f"{dependency_id}: expected {expected}, current result is missing.")
            continue
        if dependency.root_node and dependency.root_node not in dependency_root_nodes:
            dependency_root_nodes.append(dependency.root_node)

        current_revision = _result_revision(dependency)
        if not _dependency_satisfied(dependency, specification):
            missing.append(
                f"{dependency_id}: dependency requirements are not satisfied "
                f"(status={dependency.status}, review={_review_status(dependency)})."
            )
        if not isinstance(expected, str) or not expected:
            stale.append(f"{dependency_id}: no captured dependency revision.")
            continue
        if current_revision != expected:
            stale.append(
                f"{dependency_id}: expected {expected}, current result is "
                f"{current_revision or 'missing'}."
            )
            continue
        if not _dependency_satisfied(dependency, specification):
            continue
        try:
            bundle = parse_public_contract(dependency.result)
        except ValueError as exc:
            missing.append(f"{dependency_id}: result is not a valid Evidence Bundle ({exc}).")
            continue
        if bundle.get("schema_version") != "evidence_bundle_v1":
            missing.append(f"{dependency_id}: result is not an Evidence Bundle.")
            continue

        evidence_revisions.append(expected)
        evidence_experiments = [dependency.current_node]
        for run_id in bundle["runs"]["items"]:
            run_path = root / "runs" / f"{run_id}.yaml"
            run = load_yaml(run_path) if run_path.is_file() else None
            experiment_id = str(run.get("experiment_id") or "") if isinstance(run, dict) else ""
            if experiment_id and experiment_id not in evidence_experiments:
                evidence_experiments.append(experiment_id)
        finding_rows = [
            row
            for row in (
                _finding(
                    root,
                    evidence_experiments,
                    str(finding_id),
                )
                for finding_id in bundle["findings"]["items"]
            )
            if row is not None
        ]
        outcomes.append(
            {
                "assignment_id": dependency_id,
                "result_revision": expected,
                "outcome": str(bundle["outcome"]),
                "confidence": _confidence(finding_rows),
                "summary": _text(bundle["summary"]),
            }
        )
        for finding in finding_rows:
            for item in finding.get("metrics", []) or []:
                projected = _metric(item, source_revision=expected)
                if projected is not None:
                    metrics.append(projected)
        artifact_links.extend(str(item) for item in bundle["artifact_records"]["items"])
        for run_id in bundle["runs"]["items"]:
            run_revisions.setdefault(str(run_id), expected)

    gate_rows, gate_index_available = _gate_rows(root, run_revisions)
    if run_revisions and not gate_index_available:
        missing.append(
            "Selected gate summaries were omitted because the validation index is unavailable."
        )
    outcome_values = {str(item["outcome"]) for item in outcomes}
    contradictions = []
    if "positive" in outcome_values and "negative" in outcome_values:
        contradictions.append("Selected evidence contains both positive and negative outcomes.")

    candidate_options = [
        _text(item)
        for item in metadata.get("candidate_options", []) or []
        if _text(item)
    ]
    if not candidate_options:
        candidate_options = sorted(dependency_root_nodes)
    decision_criteria = [
        _text(item)
        for item in (metadata.get("decision_criteria") or assignment.success_criteria or [])
        if _text(item)
    ]
    unresolved_questions = [
        _text(item)
        for item in metadata.get("unresolved_questions", []) or []
        if _text(item)
    ]
    packet: dict[str, Any] = {
        "schema_version": SYNTHESIS_SCHEMA_VERSION,
        "revision": "",
        "research_question": _text(
            metadata.get("research_question") or assignment.objective or assignment.assignment_id,
            limit=400,
        ),
        "candidate_options": _bounded(candidate_options),
        "evidence_bundles": _bounded(evidence_revisions),
        "outcome_summaries": _bounded(outcomes),
        "metrics": _bounded(metrics),
        "gate_summaries": _bounded(gate_rows),
        "artifact_links": _bounded(sorted(set(artifact_links))),
        "contradictions": _bounded(contradictions),
        "missing_evidence": _bounded([_text(item) for item in missing]),
        "stale_input_warnings": _bounded([_text(item) for item in stale]),
        "decision_criteria": _bounded(decision_criteria),
        "unresolved_questions": _bounded(unresolved_questions),
    }
    packet["revision"] = "synthesis-v1:" + "0" * 64
    _fit_budget(packet)
    packet["revision"] = stable_payload_revision(
        {key: value for key, value in packet.items() if key != "revision"},
        prefix="synthesis-v1",
    )
    parse_public_contract(packet)
    return packet


def build_synthesis_packet(root: Path, assignment_id: str) -> dict[str, Any]:
    return build_synthesis_packet_for_assignment(root, load_assignment(root, assignment_id))
