from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import yaml

from research_cockpit.agent_state import AssignmentRecord
from research_cockpit.model import ResearchNode, RunRecord
from research_cockpit.storage import load_yaml

SCHEMA_VERSION = "validation_index_v1"

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


def _artifact_records_payload(root: Path) -> tuple[dict[str, Any], dict[str, list[str]]]:
    records: dict[str, Any] = {}
    by_experiment: dict[str, list[str]] = {}
    record_dir = root / "artifact_records"
    if not record_dir.exists():
        return records, by_experiment
    for path in sorted(record_dir.glob("*.yaml")):
        rel_path = relative_path(root, path)
        data = load_yaml(path)
        if not isinstance(data, dict):
            continue
        experiment_id = str(data.get("experiment_id") or "").strip()
        raw_records = data.get("records", {}) if isinstance(data.get("records"), dict) else {}
        for record_id, record in raw_records.items():
            record_id = str(record_id)
            run_id = ""
            if isinstance(record, dict) and record.get("run_id"):
                run_id = str(record["run_id"])
            records[record_id] = {
                "experiment_id": experiment_id,
                "run_id": run_id,
                "file": rel_path,
                "file_signature": file_signature(path),
            }
            if experiment_id:
                by_experiment.setdefault(experiment_id, []).append(record_id)
    for values in by_experiment.values():
        values.sort()
    return records, by_experiment


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
    for assignment_id, assignment in sorted(assignments.items()):
        path = root / "assignments" / f"{assignment_id}.yaml"
        rel_path = relative_path(root, path)
        allowed_root = str(assignment.allowed_subtree.get("root") or assignment.root_node or "")
        refs = [assignment.root_node, assignment.current_node, allowed_root]
        refs = sorted({str(ref) for ref in refs if ref})
        assignment_rows[assignment_id] = {
            "assignment_id": assignment_id,
            "refs": refs,
            "allowed_root": allowed_root,
            "file": rel_path,
            "file_signature": file_signature(path) if path.exists() else None,
        }
        file_to_assignment[rel_path] = assignment_id
        for ref_id in refs:
            assignments_by_node.setdefault(ref_id, []).append(assignment_id)

    artifact_records, artifact_records_by_node = _artifact_records_payload(root)
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
        "assignments": assignment_rows,
        "assignments_by_node": assignments_by_node,
        "artifact_records": artifact_records,
        "artifact_records_by_node": artifact_records_by_node,
        "files": {
            "nodes": file_to_node,
            "runs": file_to_run,
            "assignments": file_to_assignment,
        },
    }


def validation_index_path(root: Path) -> Path:
    return root / "dashboards" / "validation_index.json"


def load_validation_index(root: Path) -> dict[str, Any] | None:
    path = validation_index_path(root)
    if not path.exists():
        return None
    try:
        data = load_yaml(path)
    except (OSError, yaml.YAMLError) as exc:
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
    )


def signature_matches(root: Path, rel_path: str, expected: dict[str, Any] | None) -> bool:
    if not expected:
        return False
    path = root / rel_path
    if not path.exists():
        return False
    current = file_signature(path)
    return current.get("sha256") == expected.get("sha256") and current.get("size") == expected.get("size")