from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import yaml

from research_cockpit.agent_state import AssignmentRecord
from research_cockpit.baselines import (
    compact_effective_baseline,
    resolve_effective_baseline,
)
from research_cockpit.model import ResearchNode, RunRecord
from research_cockpit.storage import load_yaml, save_text

SCHEMA_VERSION = "validation_index_v2"
ASSIGNMENT_PROJECTION_VERSION = 1

REFERENCE_FIELDS = (
    "current_best_option",
    "resolved_by",
    "supporting_experiments",
    "contradicting_experiments",
    "supporting_decisions",
    "linked_artifacts",
    "linked_artifact_records",
    "alternatives_considered",
    "derived_from",
)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError:
        return path.as_posix()


def file_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest}


def _node_path(root: Path, node_id: str) -> Path:
    return root / "graph" / "nodes" / f"{node_id}.yaml"


def assignment_index_row(
    root: Path,
    path: Path,
    assignment: AssignmentRecord,
    *,
    current_baseline_revision: str | None = None,
    baseline_projection_fresh: bool = False,
) -> dict[str, Any]:
    rel_path = relative_path(root, path)
    allowed_root = str(assignment.allowed_subtree.get("root") or assignment.root_node or "")
    refs = sorted(
        {
            str(ref)
            for ref in (assignment.root_node, assignment.current_node, allowed_root)
            if ref
        }
    )
    return {
        "assignment_id": assignment.assignment_id,
        "agent_id": assignment.agent_id,
        "status": assignment.status,
        "kind": assignment.kind,
        "root_node": assignment.root_node,
        "current_node": assignment.current_node,
        "refs": refs,
        "allowed_root": allowed_root,
        "scope": deepcopy(assignment.scope),
        "dependencies": deepcopy(assignment.dependencies),
        "inputs": deepcopy(assignment.inputs),
        "has_inputs": "inputs" in assignment.raw,
        "input_revision": assignment.input_revision,
        "lease": deepcopy(assignment.lease),
        "review": deepcopy(assignment.review),
        "result_revision": str(assignment.result.get("revision") or "") or None,
        "current_baseline_revision": current_baseline_revision,
        "baseline_projection_fresh": baseline_projection_fresh,
        "file": rel_path,
        "file_signature": file_signature(path) if path.exists() else None,
    }


def _assignment_baseline_revision(
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    assignment: AssignmentRecord,
) -> tuple[str | None, bool]:
    if not assignment.current_node or assignment.current_node not in nodes:
        return None, False
    try:
        effective = resolve_effective_baseline(
            nodes,
            assignment.current_node,
            current,
        )
    except ValueError:
        return None, False
    compact = compact_effective_baseline(effective)
    if compact["source_kind"] == "none":
        return None, True
    canonical = json.dumps(
        compact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"exec-v1:{hashlib.sha256(canonical).hexdigest()}", True


def _value_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            refs.extend(_value_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_value_refs(item))
    elif value not in (None, ""):
        refs.append(str(value))
    return refs


def node_reference_ids(node: ResearchNode) -> list[str]:
    refs: list[str] = []
    if node.parent:
        refs.append(str(node.parent))
    refs.extend(str(child_id) for child_id in node.children)
    for field_name in REFERENCE_FIELDS:
        if field_name in node.raw:
            refs.extend(_value_refs(node.raw.get(field_name)))
    for finding in node.raw.get("findings", []) or []:
        if isinstance(finding, dict):
            refs.extend(_value_refs(finding.get("linked_artifacts", [])))
            refs.extend(_value_refs(finding.get("linked_artifact_records", [])))
    baseline = node.raw.get("baseline")
    if isinstance(baseline, dict):
        refs.extend(_value_refs(baseline))
    workstream = node.raw.get("agent_workstream")
    if isinstance(workstream, dict):
        refs.extend(_value_refs(workstream.get("report_to_problem")))
    return sorted({ref for ref in refs if ref})


def _artifact_records_payload(
    root: Path,
) -> tuple[dict[str, Any], dict[str, list[str]], dict[str, Any]]:
    records: dict[str, Any] = {}
    by_experiment: dict[str, list[str]] = {}
    files: dict[str, Any] = {}
    record_dir = root / "artifact_records"
    if not record_dir.exists():
        return records, by_experiment, files
    for path in sorted(record_dir.glob("*.yaml")):
        rel_path = relative_path(root, path)
        signature = file_signature(path)
        data = load_yaml(path)
        if not isinstance(data, dict):
            files[rel_path] = {
                "experiment_id": path.stem,
                "record_ids": [],
                "run_ids": [],
                "file_signature": signature,
            }
            continue
        experiment_id = str(data.get("experiment_id") or path.stem).strip()
        raw_records = data.get("records", {}) if isinstance(data.get("records"), dict) else {}
        file_record_ids: list[str] = []
        file_run_ids: list[str] = []
        for record_id, record in raw_records.items():
            record_id = str(record_id)
            run_id = ""
            if isinstance(record, dict) and record.get("run_id"):
                run_id = str(record["run_id"])
                file_run_ids.append(run_id)
            file_record_ids.append(record_id)
            records[record_id] = {
                "experiment_id": experiment_id,
                "run_id": run_id,
                "file": rel_path,
                "file_signature": signature,
            }
            if experiment_id:
                by_experiment.setdefault(experiment_id, []).append(record_id)
        files[rel_path] = {
            "experiment_id": experiment_id,
            "record_ids": sorted(file_record_ids),
            "run_ids": sorted(set(file_run_ids)),
            "file_signature": signature,
        }
    for values in by_experiment.values():
        values.sort()
    return records, by_experiment, files


def _gate_results_payload(
    root: Path,
) -> tuple[dict[str, Any], dict[str, list[str]], dict[str, list[str]], dict[str, str], dict[str, str]]:
    gates: dict[str, Any] = {}
    by_experiment: dict[str, list[str]] = {}
    by_run: dict[str, list[str]] = {}
    record_files: dict[str, str] = {}
    payload_files: dict[str, str] = {}
    record_dir = root / "gate_results"
    if not record_dir.exists():
        return gates, by_experiment, by_run, record_files, payload_files

    for path in sorted(record_dir.glob("*.yaml")):
        rel_path = relative_path(root, path)
        try:
            data = load_yaml(path)
        except (OSError, yaml.YAMLError):
            data = None
        gate_id = str(data.get("gate_id") or path.stem).strip() if isinstance(data, dict) else path.stem
        experiment_id = str(data.get("experiment_id") or "").strip() if isinstance(data, dict) else ""
        run_id = str(data.get("run_id") or "").strip() if isinstance(data, dict) else ""
        payload_file = str(data.get("gate_result_file") or "").replace("\\", "/").strip() if isinstance(data, dict) else ""
        payload_path = root / payload_file if payload_file else None
        gates[gate_id] = {
            "gate_id": gate_id,
            "experiment_id": experiment_id,
            "run_id": run_id,
            "artifact_id": (
                str(data.get("artifact_id") or "").strip()
                if isinstance(data, dict)
                else ""
            ),
            "record_file": rel_path,
            "record_file_signature": file_signature(path),
            "payload_file": payload_file,
            "payload_file_signature": (
                file_signature(payload_path)
                if payload_path is not None and payload_path.is_file()
                else None
            ),
        }
        record_files[rel_path] = gate_id
        if payload_file:
            payload_files[payload_file] = gate_id
        if experiment_id:
            by_experiment.setdefault(experiment_id, []).append(gate_id)
        if run_id:
            by_run.setdefault(run_id, []).append(gate_id)

    for values in (*by_experiment.values(), *by_run.values()):
        values.sort()
    return gates, by_experiment, by_run, record_files, payload_files


def build_validation_index(
    root: Path,
    nodes: dict[str, ResearchNode],
    explicit_edges: list[dict[str, Any]],
    runs: dict[str, RunRecord] | None = None,
    assignments: dict[str, AssignmentRecord] | None = None,
) -> dict[str, Any]:
    runs = runs or {}
    assignments = assignments or {}
    node_rows: dict[str, Any] = {}
    reverse_refs: dict[str, list[str]] = {}
    file_to_node: dict[str, str] = {}
    for node_id, node in sorted(nodes.items()):
        path = _node_path(root, node_id)
        rel_path = relative_path(root, path)
        refs = node_reference_ids(node)
        node_rows[node_id] = {
            "id": node.id,
            "type": node.type,
            "title": node.title,
            "status": node.status,
            "parent": node.parent,
            "children": list(node.children),
            "file": rel_path,
            "file_signature": file_signature(path) if path.exists() else None,
            "references": refs,
        }
        file_to_node[rel_path] = node_id
        for ref_id in refs:
            reverse_refs.setdefault(ref_id, []).append(node_id)

    edges_path = root / "graph" / "edges.yaml"
    explicit_edges_row = {
        "file": "graph/edges.yaml",
        "exists": edges_path.exists(),
        "file_signature": file_signature(edges_path) if edges_path.exists() else None,
    }
    edge_neighbors: dict[str, list[str]] = {}
    for edge in explicit_edges:
        source = str(edge.get("from") or edge.get("source") or "")
        target = str(edge.get("to") or edge.get("target") or "")
        if source and target:
            edge_neighbors.setdefault(source, []).append(target)
            edge_neighbors.setdefault(target, []).append(source)

    run_rows: dict[str, Any] = {}
    runs_by_experiment: dict[str, list[str]] = {}
    file_to_run: dict[str, str] = {}
    for run_id, run in sorted(runs.items()):
        path = root / "runs" / f"{run_id}.yaml"
        rel_path = relative_path(root, path)
        run_rows[run_id] = {
            "run_id": run_id,
            "status": run.status,
            "experiment_id": run.experiment_id,
            "file": rel_path,
            "file_signature": file_signature(path) if path.exists() else None,
        }
        file_to_run[rel_path] = run_id
        if run.experiment_id:
            runs_by_experiment.setdefault(run.experiment_id, []).append(run_id)

    assignment_rows: dict[str, Any] = {}
    assignments_by_node: dict[str, list[str]] = {}
    file_to_assignment: dict[str, str] = {}
    current_data = load_yaml(root / "current_state.yaml")
    current = current_data if isinstance(current_data, dict) else {}
    for assignment_id, assignment in sorted(assignments.items()):
        path = root / "assignments" / f"{assignment_id}.yaml"
        baseline_revision, baseline_fresh = _assignment_baseline_revision(
            nodes,
            current,
            assignment,
        )
        assignment_rows[assignment_id] = assignment_index_row(
            root,
            path,
            assignment,
            current_baseline_revision=baseline_revision,
            baseline_projection_fresh=baseline_fresh,
        )
        rel_path = str(assignment_rows[assignment_id]["file"])
        file_to_assignment[rel_path] = assignment_id
        for ref_id in assignment_rows[assignment_id]["refs"]:
            assignments_by_node.setdefault(ref_id, []).append(assignment_id)

    artifact_records, artifact_records_by_node, artifact_record_files = _artifact_records_payload(root)
    (
        gate_results,
        gate_results_by_experiment,
        gate_results_by_run,
        gate_record_files,
        gate_payload_files,
    ) = _gate_results_payload(root)
    for values in reverse_refs.values():
        values.sort()
    for values in edge_neighbors.values():
        values.sort()
    for values in runs_by_experiment.values():
        values.sort()
    for values in assignments_by_node.values():
        values.sort()

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_timestamp(),
        "root": str(root),
        "nodes": node_rows,
        "reverse_refs": reverse_refs,
        "edge_neighbors": edge_neighbors,
        "explicit_edges": explicit_edges_row,
        "runs": run_rows,
        "runs_by_experiment": runs_by_experiment,
        "assignment_projection_version": ASSIGNMENT_PROJECTION_VERSION,
        "assignments": assignment_rows,
        "assignments_by_node": assignments_by_node,
        "artifact_records": artifact_records,
        "artifact_records_by_node": artifact_records_by_node,
        "artifact_record_files": artifact_record_files,
        "gate_results": gate_results,
        "gate_results_by_experiment": gate_results_by_experiment,
        "gate_results_by_run": gate_results_by_run,
        "files": {
            "nodes": file_to_node,
            "runs": file_to_run,
            "assignments": file_to_assignment,
            "artifact_records": {path: path for path in artifact_record_files},
            "gate_results": gate_record_files,
            "gate_payloads": gate_payload_files,
        },
    }


def validation_index_path(root: Path) -> Path:
    return root / "dashboards" / "validation_index.json"


def load_validation_index(root: Path) -> dict[str, Any] | None:
    path = validation_index_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "schema_version": "",
            "load_error": str(exc),
            "load_error_kind": exc.__class__.__name__,
            "path": str(path),
        }
    return data if isinstance(data, dict) else None


def is_index_schema_compatible(index: dict[str, Any] | None) -> bool:
    return bool(
        index
        and index.get("schema_version") == SCHEMA_VERSION
        and isinstance(index.get("nodes"), dict)
        and isinstance(index.get("explicit_edges"), dict)
        and not index.get("stale")
    )


def ensure_validation_index(root: Path) -> dict[str, Any]:
    """Create the generated validation index only when no usable projection exists."""
    from research_cockpit.agent_state import load_assignments
    from research_cockpit.model import load_explicit_edges, load_nodes, load_runs
    from research_cockpit.mutation_lock import mutation_lock

    with mutation_lock(root):
        existing = load_validation_index(root)
        if is_index_schema_compatible(existing):
            return {"status": "current", "rebuilt": False}
        nodes = load_nodes(root)
        explicit_edges = load_explicit_edges(root)
        runs = load_runs(root)
        assignments = load_assignments(root)
        index = build_validation_index(
            root,
            nodes,
            explicit_edges,
            runs,
            assignments,
        )
        save_text(
            validation_index_path(root),
            json.dumps(index, indent=2, ensure_ascii=False),
        )
        return {
            "status": "rebuilt",
            "rebuilt": True,
            "nodes": len(nodes),
            "runs": len(runs),
            "assignments": len(assignments),
        }


def _mark_validation_index_stale_unlocked(root: Path, *, reason: str, detail: str = "") -> None:
    index = load_validation_index(root)
    if not isinstance(index, dict):
        return
    index["stale"] = {
        "reason": reason,
        "detail": detail,
        "marked_at": utc_timestamp(),
    }
    save_text(validation_index_path(root), json.dumps(index, indent=2, ensure_ascii=False))


def mark_validation_index_stale(root: Path, *, reason: str, detail: str = "") -> None:
    from research_cockpit.mutation_lock import mutation_lock

    with mutation_lock(root, lock_name=".validation-index.lock"):
        _mark_validation_index_stale_unlocked(root, reason=reason, detail=detail)

def _changed_relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError:
        return path.as_posix()


def _refresh_index_derived_maps(index: dict[str, Any]) -> None:
    nodes = index.get("nodes", {}) or {}
    reverse_refs: dict[str, list[str]] = {}
    file_nodes: dict[str, str] = {}
    for node_id, row in nodes.items():
        if not isinstance(row, dict):
            continue
        if row.get("file"):
            file_nodes[str(row["file"])] = str(node_id)
        for ref_id in row.get("references", []) or []:
            reverse_refs.setdefault(str(ref_id), []).append(str(node_id))
    for values in reverse_refs.values():
        values.sort()
    index["reverse_refs"] = reverse_refs

    runs = index.get("runs", {}) or {}
    runs_by_experiment: dict[str, list[str]] = {}
    file_runs: dict[str, str] = {}
    for run_id, row in runs.items():
        if not isinstance(row, dict):
            continue
        if row.get("file"):
            file_runs[str(row["file"])] = str(run_id)
        experiment_id = str(row.get("experiment_id") or "")
        if experiment_id:
            runs_by_experiment.setdefault(experiment_id, []).append(str(run_id))
    for values in runs_by_experiment.values():
        values.sort()
    index["runs_by_experiment"] = runs_by_experiment

    assignments = index.get("assignments", {}) or {}
    assignments_by_node: dict[str, list[str]] = {}
    file_assignments: dict[str, str] = {}
    for assignment_id, row in assignments.items():
        if not isinstance(row, dict):
            continue
        if row.get("file"):
            file_assignments[str(row["file"])] = str(assignment_id)
        for ref_id in row.get("refs", []) or []:
            assignments_by_node.setdefault(str(ref_id), []).append(str(assignment_id))
    for values in assignments_by_node.values():
        values.sort()
    index["assignments_by_node"] = assignments_by_node

    artifact_records = index.get("artifact_records", {}) or {}
    artifact_record_files = index.get("artifact_record_files", {}) or {}
    artifact_by_node: dict[str, list[str]] = {}
    for record_id, row in artifact_records.items():
        if not isinstance(row, dict):
            continue
        experiment_id = str(row.get("experiment_id") or "")
        if experiment_id:
            artifact_by_node.setdefault(experiment_id, []).append(str(record_id))
    for values in artifact_by_node.values():
        values.sort()
    index["artifact_records_by_node"] = artifact_by_node

    gates = index.get("gate_results", {}) or {}
    gates_by_experiment: dict[str, list[str]] = {}
    gates_by_run: dict[str, list[str]] = {}
    gate_record_files: dict[str, str] = {}
    gate_payload_files: dict[str, str] = {}
    for gate_id, row in gates.items():
        if not isinstance(row, dict):
            continue
        if row.get("record_file"):
            gate_record_files[str(row["record_file"])] = str(gate_id)
        if row.get("payload_file"):
            gate_payload_files[str(row["payload_file"])] = str(gate_id)
        experiment_id = str(row.get("experiment_id") or "")
        run_id = str(row.get("run_id") or "")
        if experiment_id:
            gates_by_experiment.setdefault(experiment_id, []).append(str(gate_id))
        if run_id:
            gates_by_run.setdefault(run_id, []).append(str(gate_id))
    for values in (*gates_by_experiment.values(), *gates_by_run.values()):
        values.sort()
    index["gate_results_by_experiment"] = gates_by_experiment
    index["gate_results_by_run"] = gates_by_run

    files = index.setdefault("files", {})
    files["nodes"] = file_nodes
    files["runs"] = file_runs
    files["assignments"] = file_assignments
    files["artifact_records"] = {str(path): str(path) for path in artifact_record_files}
    files["gate_results"] = gate_record_files
    files["gate_payloads"] = gate_payload_files


def _patch_node(index: dict[str, Any], root: Path, rel_path: str) -> None:
    rows = index.setdefault("nodes", {})
    old_id = (index.get("files", {}).get("nodes", {}) or {}).get(rel_path)
    if old_id:
        rows.pop(str(old_id), None)
    path = root / rel_path
    if not path.exists():
        return
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"{rel_path}: node file must be a mapping")
    node = ResearchNode.from_dict(data)
    rows[node.id] = {
        "id": node.id,
        "type": node.type,
        "title": node.title,
        "status": node.status,
        "parent": node.parent,
        "children": list(node.children),
        "file": rel_path,
        "file_signature": file_signature(path),
        "references": node_reference_ids(node),
    }


def _patch_run(index: dict[str, Any], root: Path, rel_path: str) -> None:
    rows = index.setdefault("runs", {})
    old_id = (index.get("files", {}).get("runs", {}) or {}).get(rel_path)
    if old_id:
        rows.pop(str(old_id), None)
    path = root / rel_path
    if not path.exists():
        return
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"{rel_path}: run record must be a mapping")
    run = RunRecord.from_dict(data)
    rows[run.run_id] = {
        "run_id": run.run_id,
        "status": run.status,
        "experiment_id": run.experiment_id,
        "file": rel_path,
        "file_signature": file_signature(path),
    }


def _patch_assignment(index: dict[str, Any], root: Path, rel_path: str) -> None:
    rows = index.setdefault("assignments", {})
    old_id = (index.get("files", {}).get("assignments", {}) or {}).get(rel_path)
    old_row = rows.get(str(old_id), {}) if old_id else {}
    if not isinstance(old_row, dict):
        old_row = {}
    if old_id:
        rows.pop(str(old_id), None)
    path = root / rel_path
    if not path.exists():
        return
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"{rel_path}: assignment record must be a mapping")
    assignment = AssignmentRecord.from_dict(data)
    preserve_baseline = (
        old_row.get("current_node") == assignment.current_node
        and old_row.get("baseline_projection_fresh") is True
    )
    rows[assignment.assignment_id] = assignment_index_row(
        root,
        path,
        assignment,
        current_baseline_revision=(
            old_row.get("current_baseline_revision") if preserve_baseline else None
        ),
        baseline_projection_fresh=preserve_baseline,
    )


def _invalidate_assignment_baseline_projections(index: dict[str, Any]) -> None:
    for row in (index.get("assignments", {}) or {}).values():
        if not isinstance(row, dict):
            continue
        row["baseline_projection_fresh"] = False


def _patch_artifact_records(index: dict[str, Any], root: Path, rel_path: str) -> None:
    rows = index.setdefault("artifact_records", {})
    file_rows = index.setdefault("artifact_record_files", {})
    for record_id, row in list(rows.items()):
        if isinstance(row, dict) and row.get("file") == rel_path:
            rows.pop(record_id, None)
    file_rows.pop(rel_path, None)
    path = root / rel_path
    if not path.exists():
        return
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"{rel_path}: artifact record file must be a mapping")
    raw_records = data.get("records", {})
    if not isinstance(raw_records, dict):
        raise ValueError(f"{rel_path}: records must be a mapping")
    experiment_id = str(data.get("experiment_id") or path.stem)
    signature = file_signature(path)
    record_ids: list[str] = []
    run_ids: list[str] = []
    for record_id, record in raw_records.items():
        normalized_id = str(record_id)
        run_id = str(record.get("run_id") or "") if isinstance(record, dict) else ""
        record_ids.append(normalized_id)
        if run_id:
            run_ids.append(run_id)
        rows[normalized_id] = {
            "experiment_id": experiment_id,
            "run_id": run_id,
            "file": rel_path,
            "file_signature": signature,
        }
    file_rows[rel_path] = {
        "experiment_id": experiment_id,
        "record_ids": sorted(record_ids),
        "run_ids": sorted(set(run_ids)),
        "file_signature": signature,
    }


def _patch_gate_record(index: dict[str, Any], root: Path, rel_path: str) -> None:
    rows = index.setdefault("gate_results", {})
    old_id = (index.get("files", {}).get("gate_results", {}) or {}).get(rel_path)
    if old_id:
        rows.pop(str(old_id), None)
    path = root / rel_path
    if not path.exists():
        return
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"{rel_path}: gate result record must be a mapping")
    gate_id = str(data.get("gate_id") or path.stem)
    payload_file = str(data.get("gate_result_file") or "").replace("\\", "/").strip()
    payload_path = root / payload_file if payload_file else None
    rows[gate_id] = {
        "gate_id": gate_id,
        "experiment_id": str(data.get("experiment_id") or ""),
        "run_id": str(data.get("run_id") or ""),
        "artifact_id": str(data.get("artifact_id") or ""),
        "record_file": rel_path,
        "record_file_signature": file_signature(path),
        "payload_file": payload_file,
        "payload_file_signature": (
            file_signature(payload_path)
            if payload_path is not None and payload_path.is_file()
            else None
        ),
    }


def _patch_explicit_edges(index: dict[str, Any], root: Path) -> None:
    path = root / "graph" / "edges.yaml"
    neighbors: dict[str, list[str]] = {}
    if path.exists():
        data = load_yaml(path)
        edges = data.get("edges", []) if isinstance(data, dict) else data
        if not isinstance(edges, list):
            raise ValueError("graph/edges.yaml: edges must be a list")
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("from") or edge.get("source") or "")
            target = str(edge.get("to") or edge.get("target") or "")
            if source and target:
                neighbors.setdefault(source, []).append(target)
                neighbors.setdefault(target, []).append(source)
    for values in neighbors.values():
        values.sort()
    index["edge_neighbors"] = neighbors
    index["explicit_edges"] = {
        "file": "graph/edges.yaml",
        "exists": path.exists(),
        "file_signature": file_signature(path) if path.exists() else None,
    }


def _patch_validation_index_unlocked(root: Path, changed_paths: list[Path]) -> dict[str, Any]:
    index = load_validation_index(root)
    if index is None:
        return {"status": "missing", "updated": False}
    if not is_index_schema_compatible(index):
        return {"status": "unavailable", "updated": False}

    rel_paths = sorted({_changed_relative_path(root, Path(path)) for path in changed_paths})
    baseline_sources_changed = False
    for rel_path in rel_paths:
        parts = Path(rel_path).parts
        suffix = Path(rel_path).suffix.lower()
        if len(parts) >= 3 and parts[-3:-1] == ("graph", "nodes") and suffix in {".yaml", ".yml"}:
            _patch_node(index, root, rel_path)
            baseline_sources_changed = True
        elif len(parts) >= 2 and parts[-2] == "runs" and suffix in {".yaml", ".yml"}:
            _patch_run(index, root, rel_path)
        elif len(parts) >= 2 and parts[-2] == "assignments" and suffix in {".yaml", ".yml"}:
            _patch_assignment(index, root, rel_path)
        elif len(parts) >= 2 and parts[-2] == "artifact_records" and suffix in {".yaml", ".yml"}:
            _patch_artifact_records(index, root, rel_path)
        elif len(parts) >= 2 and parts[-2] == "gate_results" and suffix in {".yaml", ".yml"}:
            _patch_gate_record(index, root, rel_path)
        elif rel_path == "graph/edges.yaml":
            _patch_explicit_edges(index, root)
        elif rel_path == "current_state.yaml":
            baseline_sources_changed = True

    _refresh_index_derived_maps(index)
    if baseline_sources_changed:
        _invalidate_assignment_baseline_projections(index)
    for rel_path in rel_paths:
        gate_id = (index.get("files", {}).get("gate_payloads", {}) or {}).get(rel_path)
        if not gate_id:
            continue
        row = (index.get("gate_results", {}) or {}).get(str(gate_id))
        if not isinstance(row, dict):
            continue
        payload_path = root / rel_path
        row["payload_file_signature"] = file_signature(payload_path) if payload_path.is_file() else None

    index["generated_at"] = utc_timestamp()
    index.pop("stale", None)
    save_text(validation_index_path(root), json.dumps(index, indent=2, ensure_ascii=False))
    return {"status": "updated", "updated": True, "changed_files": rel_paths}

def patch_validation_index(root: Path, changed_paths: list[Path]) -> dict[str, Any]:
    from research_cockpit.mutation_lock import mutation_lock

    with mutation_lock(root, lock_name=".validation-index.lock"):
        return _patch_validation_index_unlocked(root, changed_paths)

def signature_matches(root: Path, rel_path: str, expected: dict[str, Any] | None) -> bool:
    if not expected:
        return False
    path = root / rel_path
    if not path.exists():
        return False
    current = file_signature(path)
    return current.get("sha256") == expected.get("sha256") and current.get("size") == expected.get("size")
