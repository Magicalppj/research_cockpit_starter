from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from research_cockpit.agent_state import AssignmentRecord
from research_cockpit.artifact_records import SCHEMA_VERSION as ARTIFACT_RECORD_SCHEMA_VERSION, list_artifact_records
from research_cockpit.assignment_scope import ensure_assignment_scope
from research_cockpit.commands._evidence import append_unique, validate_artifact_ids
from research_cockpit.commands._runs import RUN_OPTIONAL_FIELDS
from research_cockpit.commands.record_finding import _next_finding_id, find_node_file
from research_cockpit.gate_result_records import (
    build_gate_record_data,
    gate_record_path,
    validate_attached_gate_artifact,
    validate_gate_result_relative_path,
)
from research_cockpit.gate_results import normalize_gate_result
from research_cockpit.model import (
    ResearchNode,
    RunRecord,
    VALID_FINDING_CONFIDENCES,
    VALID_FINDING_OUTCOMES,
    load_yaml,
)
from research_cockpit.mutation_runtime import (
    execute_mutation_transaction,
    indexed_artifact_record_stubs,
    load_targeted_state,
    validate_mutation_candidate,
)


RUN_CLOSEOUT_SCHEMA_VERSION = "run_closeout_v1"
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}


def load_run_closeout(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Run closeout file does not exist: {path}")
    try:
        data = load_yaml(path)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Run closeout file contains invalid YAML: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Run closeout file could not be read: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Run closeout file must contain a mapping")
    return data


def _mapping(value: Any, field_name: str, *, required: bool = False) -> dict[str, Any]:
    if value is None and not required:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return dict(value)


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [str(item) for item in value if str(item).strip()]


def _artifact_record_plan(
    root: Path,
    state: Any,
    spec: dict[str, Any],
    *,
    experiment_id: str,
    run_id: str,
) -> tuple[tuple[Path, dict[str, Any] | None, dict[str, Any]], dict[str, Any]]:
    record_id = str(spec.get("record_id") or f"artifact_{experiment_id}_{run_id}").strip()
    if not record_id:
        raise ValueError("artifact_record.record_id is required")
    known_ids = {
        str(record.get("record_id"))
        for record in indexed_artifact_record_stubs(state)
        if record.get("record_id")
    }
    if record_id in known_ids:
        raise FileExistsError(f"Artifact record already exists: {record_id}")

    path = root / "artifact_records" / f"{experiment_id}.yaml"
    before = load_yaml(path) if path.exists() else None
    current = dict(before or {})
    records = current.get("records", {})
    if not isinstance(records, dict):
        raise ValueError(f"{path}: records must be a mapping")
    if record_id in records:
        raise FileExistsError(f"Artifact record already exists: {record_id}")
    links = spec.get("links", {})
    if not isinstance(links, dict):
        raise ValueError("artifact_record.links must be a mapping")
    record = {
        "record_id": record_id,
        "experiment_id": experiment_id,
        "run_id": run_id,
        "artifact_kind": str(spec.get("artifact_kind") or "run_output"),
        "status": str(spec.get("status") or "recorded"),
        "title": str(spec.get("title") or f"Closeout record for {run_id}"),
        "summary": str(spec.get("summary") or ""),
        "links": {str(key): str(value) for key, value in links.items()},
        "created_at": str(spec.get("created_at") or date.today()),
        "updated_at": str(spec.get("updated_at") or date.today()),
    }
    for field_name in ("stable_path", "manifest_path", "retention", "agent"):
        if spec.get(field_name) is not None:
            record[field_name] = spec[field_name]
    after = {
        **current,
        "schema_version": ARTIFACT_RECORD_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "records": {**records, record_id: record},
    }
    return (path, before, after), record


def _existing_artifact_record(
    root: Path,
    spec: dict[str, Any],
    *,
    experiment_id: str,
    run_id: str,
) -> tuple[dict[str, Any], tuple[Path, bytes]]:
    allowed_fields = {"existing_record_id"}
    unexpected = sorted(set(spec) - allowed_fields)
    if unexpected:
        raise ValueError(
            "artifact_record with existing_record_id cannot also define: " + ", ".join(unexpected)
        )
    record_id = str(spec.get("existing_record_id") or "").strip()
    if not record_id:
        raise ValueError("artifact_record.existing_record_id must be non-empty")

    path = root / "artifact_records" / f"{experiment_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Artifact record does not exist: {record_id}")
    before_bytes = path.read_bytes()
    try:
        data = yaml.safe_load(before_bytes.decode("utf-8")) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"{path}: invalid artifact record YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: artifact record file must contain a mapping")
    records = data.get("records", {})
    if not isinstance(records, dict):
        raise ValueError(f"{path}: records must be a mapping")
    raw_record = records.get(record_id)
    if not isinstance(raw_record, dict):
        raise FileNotFoundError(f"Artifact record does not exist: {record_id}")
    record = dict(raw_record)
    record.setdefault("record_id", record_id)
    record.setdefault("experiment_id", str(data.get("experiment_id") or experiment_id))
    if str(record.get("experiment_id") or "") != experiment_id:
        raise ValueError(f"Artifact record {record_id!r} does not belong to experiment {experiment_id!r}")
    if str(record.get("run_id") or "") != run_id:
        raise ValueError(f"Artifact record {record_id!r} does not belong to run {run_id!r}")
    return record, (path, before_bytes)


def complete_run_closeout(
    root: Path,
    *,
    plan: dict[str, Any],
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    assignment_id: str | None = None,
    coordinator: bool = False,
) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("Run closeout plan must be a mapping")
    schema_version = plan.get("schema_version")
    if schema_version not in (None, RUN_CLOSEOUT_SCHEMA_VERSION):
        raise ValueError(f"Unsupported run closeout schema {schema_version!r}")
    run_spec = _mapping(plan.get("run"), "run", required=True)
    run_id = str(run_spec.get("id") or run_spec.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run.id is required")
    status = str(run_spec.get("status") or "completed")
    if status not in TERMINAL_RUN_STATUSES:
        allowed = ", ".join(sorted(TERMINAL_RUN_STATUSES))
        raise ValueError(f"Invalid terminal run status {status!r}; allowed: {allowed}")

    state = load_targeted_state(
        root,
        run_ids=[run_id],
        include_artifact_records=True,
    )
    runs = dict(state.runs) if state.targeted and state.runs is not None else {}
    if not state.targeted:
        from research_cockpit.model import load_runs

        runs = load_runs(root)
    if run_id not in runs:
        raise FileNotFoundError(root / "runs" / f"{run_id}.yaml")
    run = runs[run_id]
    experiment_id = run.experiment_id
    if experiment_id not in state.nodes or state.nodes[experiment_id].type != "experiment":
        raise ValueError(f"Run {run_id} does not reference an experiment node")

    resolved_assignment_id = str(plan.get("assignment_id") or assignment_id or "").strip() or None
    ensure_assignment_scope(
        root,
        state.nodes,
        assignment_id=resolved_assignment_id,
        coordinator=coordinator,
        target_node_ids=[experiment_id],
    )

    run_path = root / "runs" / f"{run_id}.yaml"
    run_before = load_yaml(run_path)
    run_after = copy.deepcopy(run_before)
    run_after["status"] = status
    for field_name in RUN_OPTIONAL_FIELDS:
        if field_name in run_spec:
            run_after[field_name] = run_spec[field_name]
    run_after["run_id"] = run_id
    run_after["experiment_id"] = experiment_id
    candidate_runs = dict(runs)
    candidate_runs[run_id] = RunRecord.from_dict(run_after)

    experiment_path = find_node_file(root, experiment_id)
    experiment_before = load_yaml(experiment_path)
    experiment_after = copy.deepcopy(experiment_before)
    yaml_changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]] = [
        (run_path, run_before, run_after)
    ]
    read_dependencies: list[tuple[Path, bytes | None]] = []
    created_record: dict[str, Any] | None = None
    linked_record: dict[str, Any] | None = None
    artifact_spec = _mapping(plan.get("artifact_record"), "artifact_record")
    if artifact_spec:
        if "existing_record_id" in artifact_spec:
            linked_record, record_dependency = _existing_artifact_record(
                root,
                artifact_spec,
                experiment_id=experiment_id,
                run_id=run_id,
            )
            read_dependencies.append(record_dependency)
        else:
            record_change, created_record = _artifact_record_plan(
                root,
                state,
                artifact_spec,
                experiment_id=experiment_id,
                run_id=run_id,
            )
            linked_record = created_record
            yaml_changes.append(record_change)
        linked_records, _ = append_unique(
            experiment_after.get("linked_artifact_records"),
            [linked_record["record_id"]],
            "linked_artifact_records",
        )
        experiment_after["linked_artifact_records"] = linked_records

    gate_ids: list[str] = []
    gate_id_keys: set[str] = set()
    gates = plan.get("gates", []) or []
    if not isinstance(gates, list):
        raise ValueError("gates must be a list")
    for index, gate_spec in enumerate(gates, start=1):
        if not isinstance(gate_spec, dict):
            raise ValueError(f"gates[{index}] must be a mapping")
        gate_id = str(gate_spec.get("id") or gate_spec.get("gate_id") or "").strip()
        gate_file = str(gate_spec.get("file") or gate_spec.get("gate_result_file") or "").strip()
        if not gate_id or not gate_file:
            raise ValueError(f"gates[{index}].id and .file are required")
        gate_id_key = gate_id.casefold()
        if gate_id_key in gate_id_keys:
            raise ValueError(f"Duplicate gate id in closeout: {gate_id}")
        gate_id_keys.add(gate_id_key)
        gate_ids.append(gate_id)
        gate_path = validate_gate_result_relative_path(root, gate_file)
        if not gate_path.exists():
            raise FileNotFoundError(gate_path)
        gate_bytes = gate_path.read_bytes()
        try:
            gate_data = json.loads(gate_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            gate = {
                "valid": False,
                "schema_warnings": [f"gate result file JSON parse error: {exc}"],
            }
        else:
            gate = normalize_gate_result(
                gate_data,
                path=gate_file,
                experiment_id=experiment_id,
                run_id=run_id,
            )
        if not gate.get("valid"):
            warnings = "; ".join((gate or {}).get("schema_warnings", []) or [])
            raise ValueError(f"Gate {gate_id} is invalid: {warnings or 'missing payload'}")
        read_dependencies.append((gate_path, gate_bytes))
        record_path = gate_record_path(root, gate_id)
        if record_path.exists():
            raise FileExistsError(record_path)
        artifact_id = str(gate_spec.get("artifact_id") or "").strip() or None
        if artifact_id:
            validate_attached_gate_artifact(
                state.nodes,
                experiment_id=experiment_id,
                artifact_id=artifact_id,
                gate_result_file=gate_file,
            )
        gate_record = build_gate_record_data(
            gate_id=gate_id,
            experiment_id=experiment_id,
            run_id=run_id,
            artifact_id=artifact_id,
            gate_result_file=gate_file,
            recorded_at=str(gate_spec.get("recorded_at") or "").strip() or None,
        )
        yaml_changes.append((record_path, None, gate_record))

    finding_spec = _mapping(plan.get("finding"), "finding")
    finding_id: str | None = None
    if finding_spec:
        statement = str(finding_spec.get("statement") or "").strip()
        confidence = str(finding_spec.get("confidence") or "medium").strip()
        outcome = str(finding_spec.get("outcome") or "").strip() or None
        if not statement:
            raise ValueError("finding.statement is required")
        if confidence not in VALID_FINDING_CONFIDENCES:
            raise ValueError(f"Invalid finding confidence {confidence!r}")
        if outcome is not None and outcome not in VALID_FINDING_OUTCOMES:
            raise ValueError(f"Invalid finding outcome {outcome!r}")
        artifact_ids = _string_list(finding_spec.get("artifact_ids"), "finding.artifact_ids")
        validate_artifact_ids(state.nodes, artifact_ids)
        findings = experiment_after.get("findings", []) or []
        if not isinstance(findings, list):
            raise ValueError(f"{experiment_id}: findings must be a list")
        finding_id = _next_finding_id(experiment_id, findings)
        finding = {
            "id": finding_id,
            "statement": statement,
            "confidence": confidence,
            "outcome": outcome,
            "metrics": _string_list(finding_spec.get("metrics"), "finding.metrics"),
            "evidence": [experiment_id],
            "linked_artifacts": artifact_ids,
            "linked_artifact_records": [linked_record["record_id"]] if linked_record else [],
            "created_at": str(date.today()),
        }
        findings.append(finding)
        experiment_after["findings"] = findings

    next_actions = _mapping(plan.get("next_actions"), "next_actions")
    if "experiment" in next_actions:
        experiment_after["next_actions"] = _string_list(
            next_actions.get("experiment"),
            "next_actions.experiment",
        )
    assignment_actions = _string_list(next_actions.get("assignment"), "next_actions.assignment")
    if assignment_actions:
        if not resolved_assignment_id:
            raise ValueError("next_actions.assignment requires assignment_id")
        assignment_path = root / "assignments" / f"{resolved_assignment_id}.yaml"
        assignment_before = load_yaml(assignment_path)
        if not assignment_before:
            raise FileNotFoundError(assignment_path)
        assignment = AssignmentRecord.from_dict(assignment_before)
        if assignment.assignment_id != resolved_assignment_id:
            raise ValueError("assignment_id does not match assignment file")
        assignment_after = copy.deepcopy(assignment_before)
        assignment_after["next_actions"] = assignment_actions
        yaml_changes.append((assignment_path, assignment_before, assignment_after))

    experiment_after["updated_at"] = str(date.today())
    if experiment_after != experiment_before:
        yaml_changes.append((experiment_path, experiment_before, experiment_after))
    candidate_nodes = dict(state.nodes)
    candidate_nodes[experiment_id] = ResearchNode.from_dict(experiment_after)
    artifact_records = (
        indexed_artifact_record_stubs(state)
        if state.targeted
        else list_artifact_records(root)
    )
    if linked_record:
        linked_record_id = str(linked_record.get("record_id") or "")
        artifact_records = [
            record
            for record in artifact_records
            if str(record.get("record_id") or "") != linked_record_id
        ]
        artifact_records.append(linked_record)
    validate_mutation_candidate(
        root,
        state,
        nodes=candidate_nodes,
        runs=candidate_runs,
        artifact_records=artifact_records if linked_record else None,
    )

    result: dict[str, Any] = {
        "schema_version": RUN_CLOSEOUT_SCHEMA_VERSION,
        "run_id": run_id,
        "experiment_id": experiment_id,
        "status": status,
        "gate_ids": gate_ids,
        "record_id": linked_record.get("record_id") if linked_record else None,
        "finding_id": finding_id,
        "dry_run": dry_run,
        "changed": not dry_run,
        "would_change": True,
        "changed_files": [str(path) for path, _, _ in yaml_changes],
    }
    if show_diff:
        from research_cockpit.commands._runtime import yaml_change_diff

        result["diff"] = yaml_change_diff(yaml_changes)
    if dry_run:
        result["changed"] = False
        result["transaction"] = {
            "status": "planned",
            "partial_success": False,
            "rolled_back": False,
        }
        return result

    result["transaction"] = execute_mutation_transaction(
        root,
        yaml_changes,
        interactions=[
            {
                "kind": "complete_run_closeout",
                "actor": "researcher",
                "node_id": experiment_id,
                "command": f"research-cockpit complete-run --file <closeout.yaml>",
                "after": {
                    "run_id": run_id,
                    "status": status,
                    "gate_ids": gate_ids,
                    "record_id": result["record_id"],
                    "finding_id": finding_id,
                },
            }
        ],
        rebuild_dashboard=rebuild_dashboard,
        read_dependencies=read_dependencies,
    )
    return result
