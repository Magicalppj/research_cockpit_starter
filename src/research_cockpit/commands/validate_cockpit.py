from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.agent_state import load_assignments
from research_cockpit.lifecycle_guards import terminal_parent_guard_failures
from research_cockpit.model import ResearchNode, load_explicit_edges, load_nodes, load_runs, load_yaml, validate_cockpit, validate_nodes
from research_cockpit.validation_index import is_index_schema_compatible, load_validation_index, node_reference_ids, signature_matches


def _lifecycle_error_message(error: dict) -> str:
    blocker_ids = ", ".join(str(item["id"]) for item in error.get("blocking_descendants", []))
    return (
        f"{error['node_id']}: terminal_parent_has_active_descendants for status "
        f"{error['target_status']!r}; active descendants: {blocker_ids}"
    )


def _unique_strings(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _flatten_changed_files(changed_files: list[str] | None, changed_file_groups: list[list[str]] | None) -> list[str]:
    files = [str(item) for item in changed_files or []]
    for group in changed_file_groups or []:
        files.extend(str(item) for item in group)
    return files


def _normalize_changed_file(root: Path, value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute():
        try:
            return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
        except ValueError:
            return path.as_posix()
    normalized = text.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def _path_for_changed_file(root: Path, normalized: str) -> Path:
    path = Path(normalized)
    return path if path.is_absolute() else root / path


def _node_id_from_changed_file(root: Path, normalized: str) -> str | None:
    normalized = normalized.replace("\\", "/")
    parts = normalized.split("/")
    if len(parts) < 3 or parts[-3:-1] != ["graph", "nodes"]:
        return None
    path = _path_for_changed_file(root, normalized)
    if path.suffix not in {".yaml", ".yml"}:
        return None
    data = load_yaml(path)
    node_id = data.get("id") if isinstance(data, dict) else None
    return str(node_id) if node_id else path.stem


def _changed_node_ids_from_files(root: Path, changed_files: list[str]) -> list[str]:
    node_ids: list[str] = []
    for normalized in changed_files:
        node_id = _node_id_from_changed_file(root, normalized)
        if node_id:
            node_ids.append(node_id)
    return _unique_strings(node_ids)


def _value_references(value: Any, target_id: str) -> bool:
    if isinstance(value, dict):
        return any(_value_references(item, target_id) for item in value.values())
    if isinstance(value, list):
        return any(_value_references(item, target_id) for item in value)
    return str(value) == target_id if value not in (None, "") else False


def _add_ancestors(out: list[str], nodes: dict[str, Any], node_id: str) -> None:
    seen: set[str] = set()
    current = nodes.get(node_id)
    while current and current.parent:
        parent_id = str(current.parent)
        if parent_id in seen or parent_id not in nodes:
            return
        out.append(parent_id)
        seen.add(parent_id)
        current = nodes[parent_id]


def _affected_node_ids(nodes: dict[str, Any], changed_node_ids: list[str], explicit_edges: list[dict[str, Any]]) -> list[str]:
    affected: list[str] = []
    for node_id in changed_node_ids:
        if node_id not in nodes:
            continue
        affected.append(node_id)
        _add_ancestors(affected, nodes, node_id)
        node = nodes[node_id]
        affected.extend(str(child_id) for child_id in node.children if str(child_id) in nodes)
        affected.extend(other.id for other in nodes.values() if other.parent == node_id)
        for other in nodes.values():
            if other.id != node_id and _value_references(other.raw, node_id):
                affected.append(other.id)
        for edge in explicit_edges:
            source = str(edge.get("from") or edge.get("source") or "")
            target = str(edge.get("to") or edge.get("target") or "")
            if source == node_id and target in nodes:
                affected.append(target)
            if target == node_id and source in nodes:
                affected.append(source)
    return _unique_strings(affected)


def _affected_run_ids(root: Path, affected_node_ids: list[str], changed_run_ids: list[str] | None = None) -> list[str]:
    affected_set = set(affected_node_ids)
    changed_run_ids = changed_run_ids or []
    try:
        runs = load_runs(root)
    except Exception:
        return _unique_strings(changed_run_ids)
    out = list(changed_run_ids)
    out.extend(
        run.run_id for run in runs.values()
        if run.experiment_id in affected_set or run.run_id in changed_run_ids
    )
    return _unique_strings(out)


def _is_descendant_or_self(nodes: dict[str, Any], node_id: str, root_id: str) -> bool:
    current_id = node_id
    seen: set[str] = set()
    while current_id and current_id in nodes and current_id not in seen:
        if current_id == root_id:
            return True
        seen.add(current_id)
        parent = nodes[current_id].parent
        current_id = str(parent) if parent else ""
    return False


def _affected_assignment_ids(
    root: Path,
    nodes: dict[str, Any],
    affected_node_ids: list[str],
    changed_assignment_ids: list[str],
) -> list[str]:
    try:
        assignments = load_assignments(root)
    except Exception:
        return _unique_strings(changed_assignment_ids)
    affected_set = set(affected_node_ids)
    out = list(changed_assignment_ids)
    for assignment in assignments.values():
        allowed_root = str(assignment.allowed_subtree.get("root") or assignment.root_node or "")
        direct_refs = {assignment.root_node, assignment.current_node, allowed_root}
        if affected_set.intersection(direct_refs):
            out.append(assignment.assignment_id)
            continue
        if allowed_root in nodes and any(_is_descendant_or_self(nodes, node_id, allowed_root) for node_id in affected_node_ids):
            out.append(assignment.assignment_id)
    return _unique_strings(out)



def _run_context_from_changed_file(root: Path, normalized: str) -> tuple[list[str], list[str]]:
    parts = normalized.replace("\\", "/").split("/")
    if len(parts) < 2 or parts[-2] != "runs" or Path(parts[-1]).suffix not in {".yaml", ".yml"}:
        return [], []
    data = load_yaml(_path_for_changed_file(root, normalized))
    if not isinstance(data, dict):
        return [], []
    run_id = str(data.get("run_id") or Path(parts[-1]).stem)
    experiment_id = str(data.get("experiment_id") or "").strip()
    return _unique_strings([run_id]), _unique_strings([experiment_id] if experiment_id else [])


def _assignment_context_from_changed_file(root: Path, normalized: str) -> tuple[list[str], list[str]]:
    parts = normalized.replace("\\", "/").split("/")
    if len(parts) < 2 or parts[-2] != "assignments" or Path(parts[-1]).suffix not in {".yaml", ".yml"}:
        return [], []
    data = load_yaml(_path_for_changed_file(root, normalized))
    if not isinstance(data, dict):
        return [], []
    assignment_id = str(data.get("assignment_id") or Path(parts[-1]).stem)
    allowed_subtree = data.get("allowed_subtree") if isinstance(data.get("allowed_subtree"), dict) else {}
    node_ids = [
        str(data.get("root_node") or ""),
        str(data.get("current_node") or ""),
        str(allowed_subtree.get("root") or ""),
    ]
    return _unique_strings([assignment_id]), _unique_strings(node_ids)


def _artifact_record_file_context(root: Path, normalized: str) -> tuple[list[str], list[str], list[str]]:
    parts = normalized.replace("\\", "/").split("/")
    if len(parts) < 2 or parts[-2] != "artifact_records" or Path(parts[-1]).suffix not in {".yaml", ".yml"}:
        return [], [], []
    data = load_yaml(_path_for_changed_file(root, normalized))
    if not isinstance(data, dict):
        return [], [], []
    experiment_id = str(data.get("experiment_id") or "").strip()
    records = data.get("records", {}) if isinstance(data.get("records"), dict) else {}
    record_ids: list[str] = []
    run_ids: list[str] = []
    for record_id, record in records.items():
        record_ids.append(str(record_id))
        if isinstance(record, dict) and record.get("run_id"):
            run_ids.append(str(record["run_id"]))
    return _unique_strings(record_ids), _unique_strings([experiment_id] if experiment_id else []), _unique_strings(run_ids)


def _artifact_record_errors(root: Path, changed_records: list[str]) -> tuple[list[str], list[str], list[str], list[str]]:
    record_ids: list[str] = []
    errors: list[str] = []
    record_to_node: dict[str, str] = {}
    record_to_run: dict[str, str] = {}
    artifact_record_dir = root / "artifact_records"
    if artifact_record_dir.exists():
        for path in sorted(artifact_record_dir.glob("*.yaml")):
            data = load_yaml(path)
            experiment_id = str(data.get("experiment_id") or "").strip() if isinstance(data, dict) else ""
            records = data.get("records", {}) if isinstance(data, dict) else {}
            if isinstance(records, dict):
                for known_record_id, record in records.items():
                    record_id = str(known_record_id)
                    if experiment_id:
                        record_to_node[record_id] = experiment_id
                    if isinstance(record, dict) and record.get("run_id"):
                        record_to_run[record_id] = str(record["run_id"])

    for raw_record in changed_records:
        kind, separator, record_id = str(raw_record).partition(":")
        if separator != ":" or not kind or not record_id:
            errors.append(f"changed record must use <kind>:<id>: {raw_record!r}")
            continue
        if kind != "artifact":
            errors.append(f"unsupported changed record kind {kind!r}; supported: 'artifact'")
            continue
        record_ids.append(record_id)
        if record_id not in record_to_node and record_id not in record_to_run:
            errors.append(f"changed artifact record does not exist: {record_id!r}")
    affected_nodes = [record_to_node[record_id] for record_id in record_ids if record_id in record_to_node]
    affected_runs = [record_to_run[record_id] for record_id in record_ids if record_id in record_to_run]
    return _unique_strings(record_ids), errors, _unique_strings(affected_nodes), _unique_strings(affected_runs)



def _index_node_id_from_file(index: dict[str, Any], normalized: str) -> str | None:
    return (index.get("files", {}).get("nodes", {}) or {}).get(normalized)


def _index_run_context_from_file(index: dict[str, Any], normalized: str) -> tuple[list[str], list[str]]:
    run_id = (index.get("files", {}).get("runs", {}) or {}).get(normalized)
    if not run_id:
        return [], []
    run = (index.get("runs", {}) or {}).get(run_id, {})
    experiment_id = str(run.get("experiment_id") or "")
    return [str(run_id)], _unique_strings([experiment_id] if experiment_id else [])


def _index_assignment_context_from_file(index: dict[str, Any], normalized: str) -> tuple[list[str], list[str]]:
    assignment_id = (index.get("files", {}).get("assignments", {}) or {}).get(normalized)
    if not assignment_id:
        return [], []
    assignment = (index.get("assignments", {}) or {}).get(assignment_id, {})
    refs = [str(ref) for ref in assignment.get("refs", []) or []]
    return [str(assignment_id)], _unique_strings(refs)


def _index_artifact_record_context_from_file(index: dict[str, Any], normalized: str) -> tuple[list[str], list[str], list[str]]:
    record_ids: list[str] = []
    node_ids: list[str] = []
    run_ids: list[str] = []
    for record_id, record in (index.get("artifact_records", {}) or {}).items():
        if record.get("file") != normalized:
            continue
        record_ids.append(str(record_id))
        if record.get("experiment_id"):
            node_ids.append(str(record["experiment_id"]))
        if record.get("run_id"):
            run_ids.append(str(record["run_id"]))
    return _unique_strings(record_ids), _unique_strings(node_ids), _unique_strings(run_ids)


def _index_changed_records(
    index: dict[str, Any],
    changed_records: list[str],
) -> tuple[list[str], list[str], list[str], list[str]]:
    records = index.get("artifact_records", {}) or {}
    record_ids: list[str] = []
    errors: list[str] = []
    node_ids: list[str] = []
    run_ids: list[str] = []
    for raw_record in changed_records:
        kind, separator, record_id = str(raw_record).partition(":")
        if separator != ":" or not kind or not record_id:
            errors.append(f"changed record must use <kind>:<id>: {raw_record!r}")
            continue
        if kind != "artifact":
            errors.append(f"unsupported changed record kind {kind!r}; supported: 'artifact'")
            continue
        record_ids.append(record_id)
        record = records.get(record_id)
        if not isinstance(record, dict):
            errors.append(f"changed artifact record does not exist: {record_id!r}")
            continue
        if record.get("experiment_id"):
            node_ids.append(str(record["experiment_id"]))
        if record.get("run_id"):
            run_ids.append(str(record["run_id"]))
    return _unique_strings(record_ids), errors, _unique_strings(node_ids), _unique_strings(run_ids)


def _index_ancestors(index: dict[str, Any], node_id: str) -> list[str]:
    nodes = index.get("nodes", {}) or {}
    out: list[str] = []
    seen: set[str] = set()
    current_id = node_id
    while current_id in nodes and current_id not in seen:
        seen.add(current_id)
        parent_id = str(nodes[current_id].get("parent") or "")
        if not parent_id or parent_id not in nodes:
            break
        out.append(parent_id)
        current_id = parent_id
    return out


def _index_is_descendant_or_self(index: dict[str, Any], node_id: str, root_id: str) -> bool:
    return node_id == root_id or root_id in _index_ancestors(index, node_id)


def _index_affected_nodes(index: dict[str, Any], seeds: list[str]) -> list[str]:
    nodes = index.get("nodes", {}) or {}
    reverse_refs = index.get("reverse_refs", {}) or {}
    edge_neighbors = index.get("edge_neighbors", {}) or {}
    affected: list[str] = []
    for node_id in seeds:
        if node_id not in nodes:
            continue
        row = nodes[node_id]
        affected.append(node_id)
        affected.extend(_index_ancestors(index, node_id))
        affected.extend(str(child_id) for child_id in row.get("children", []) or [] if str(child_id) in nodes)
        affected.extend(str(referrer) for referrer in reverse_refs.get(node_id, []) or [] if str(referrer) in nodes)
        affected.extend(str(neighbor) for neighbor in edge_neighbors.get(node_id, []) or [] if str(neighbor) in nodes)
    return _unique_strings(affected)


def _index_affected_runs(index: dict[str, Any], affected_nodes: list[str], changed_run_ids: list[str]) -> list[str]:
    runs_by_experiment = index.get("runs_by_experiment", {}) or {}
    out = list(changed_run_ids)
    for node_id in affected_nodes:
        out.extend(str(run_id) for run_id in runs_by_experiment.get(node_id, []) or [])
    return _unique_strings(out)


def _index_affected_assignments(
    index: dict[str, Any],
    affected_nodes: list[str],
    changed_assignment_ids: list[str],
) -> list[str]:
    assignments = index.get("assignments", {}) or {}
    by_node = index.get("assignments_by_node", {}) or {}
    out = list(changed_assignment_ids)
    for node_id in affected_nodes:
        out.extend(str(assignment_id) for assignment_id in by_node.get(node_id, []) or [])
    for assignment_id, assignment in assignments.items():
        allowed_root = str(assignment.get("allowed_root") or "")
        if allowed_root and any(_index_is_descendant_or_self(index, node_id, allowed_root) for node_id in affected_nodes):
            out.append(str(assignment_id))
    return _unique_strings(out)


def _index_required_files(
    index: dict[str, Any],
    affected_nodes: list[str],
    affected_runs: list[str],
    affected_assignments: list[str],
    artifact_record_ids: list[str],
) -> list[tuple[str, dict[str, Any] | None]]:
    required: list[tuple[str, dict[str, Any] | None]] = []
    for node_id in affected_nodes:
        row = (index.get("nodes", {}) or {}).get(node_id, {})
        if row.get("file"):
            required.append((str(row["file"]), row.get("file_signature")))
    for run_id in affected_runs:
        row = (index.get("runs", {}) or {}).get(run_id, {})
        if row.get("file"):
            required.append((str(row["file"]), row.get("file_signature")))
    for assignment_id in affected_assignments:
        row = (index.get("assignments", {}) or {}).get(assignment_id, {})
        if row.get("file"):
            required.append((str(row["file"]), row.get("file_signature")))
    for record_id in artifact_record_ids:
        row = (index.get("artifact_records", {}) or {}).get(record_id, {})
        if row.get("file"):
            required.append((str(row["file"]), row.get("file_signature")))
    out: list[tuple[str, dict[str, Any] | None]] = []
    seen: set[str] = set()
    for rel_path, signature in required:
        if rel_path not in seen:
            out.append((rel_path, signature))
            seen.add(rel_path)
    return out


def _index_stub_node(node_id: str, row: dict[str, Any]) -> ResearchNode:
    raw = {
        "id": node_id,
        "type": row.get("type") or "node",
        "title": row.get("title") or node_id,
        "status": row.get("status") or "open",
    }
    if row.get("parent"):
        raw["parent"] = row["parent"]
    if row.get("children"):
        raw["children"] = list(row.get("children") or [])
    return ResearchNode.from_dict(raw)


def _index_local_errors(
    root: Path,
    index: dict[str, Any],
    affected_nodes: list[str],
    artifact_record_ids: list[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    indexed_nodes = index.get("nodes", {}) or {}
    artifact_records = set(str(record_id) for record_id in (index.get("artifact_records", {}) or {}).keys())
    artifact_records.update(str(record_id) for record_id in artifact_record_ids or [])
    nodes: dict[str, ResearchNode] = {}
    affected_set = set(affected_nodes)
    for node_id, row in indexed_nodes.items():
        if not isinstance(row, dict):
            continue
        if node_id not in affected_set:
            nodes[str(node_id)] = _index_stub_node(str(node_id), row)
            continue
        if not row.get("file"):
            errors.append(f"changed affected node does not exist in validation index: {node_id!r}")
            continue
        data = load_yaml(root / str(row["file"]))
        if not isinstance(data, dict):
            errors.append(f"{node_id}: node file must be a mapping")
            continue
        try:
            node = ResearchNode.from_dict(data)
        except KeyError as exc:
            errors.append(f"{node_id}: missing required field {exc.args[0]!r}")
            continue
        if node.id != str(node_id):
            errors.append(f"{node_id}: node file id mismatch {node.id!r}")
            nodes[str(node_id)] = _index_stub_node(str(node_id), row)
            continue
        nodes[str(node_id)] = node
    for node_id in affected_nodes:
        if node_id not in nodes and node_id not in indexed_nodes:
            errors.append(f"changed affected node does not exist in validation index: {node_id!r}")
    def validate_record_refs(owner_id: str, field_name: str, value: Any) -> None:
        if value is None:
            return
        if not isinstance(value, list):
            errors.append(f"{owner_id}: {field_name} must be a list")
            return
        for record_id in value:
            if str(record_id) not in artifact_records:
                errors.append(f"{owner_id}: {field_name} references missing artifact record {str(record_id)!r}")

    errors.extend(validate_nodes(nodes))
    for node_id in affected_nodes:
        node = nodes.get(node_id)
        if node is None:
            continue
        validate_record_refs(node.id, "linked_artifact_records", node.raw.get("linked_artifact_records"))
        for finding_index, finding in enumerate(node.raw.get("findings", []) or [], start=1):
            if not isinstance(finding, dict):
                continue
            validate_record_refs(
                node.id,
                f"findings[{finding_index}].linked_artifact_records",
                finding.get("linked_artifact_records"),
            )
    return errors


def _index_current_changed_node_refs(root: Path, index: dict[str, Any], changed_node_ids: list[str]) -> list[str]:
    nodes = index.get("nodes", {}) or {}
    refs: list[str] = []
    for node_id in changed_node_ids:
        row = nodes.get(node_id)
        if not isinstance(row, dict) or not row.get("file"):
            continue
        data = load_yaml(root / str(row["file"]))
        if not isinstance(data, dict):
            continue
        try:
            node = ResearchNode.from_dict(data)
        except KeyError:
            continue
        if node.id != node_id:
            continue
        refs.extend(ref_id for ref_id in node_reference_ids(node) if ref_id in nodes)
    return _unique_strings(refs)


def _index_artifact_records_for_nodes(index: dict[str, Any], node_ids: list[str]) -> list[str]:
    by_node = index.get("artifact_records_by_node", {}) or {}
    out: list[str] = []
    for node_id in node_ids:
        out.extend(str(record_id) for record_id in by_node.get(node_id, []) or [])
    return _unique_strings(out)


def _index_changed_node_type_changed(root: Path, index: dict[str, Any], changed_node_ids: list[str]) -> bool:
    nodes = index.get("nodes", {}) or {}
    for node_id in changed_node_ids:
        row = nodes.get(node_id)
        if not isinstance(row, dict) or not row.get("file"):
            continue
        data = load_yaml(root / str(row["file"]))
        if not isinstance(data, dict):
            continue
        if str(data.get("type") or "") != str(row.get("type") or ""):
            return True
    return False


def _index_declared_changed_files(
    index: dict[str, Any],
    *,
    changed_node_ids: list[str],
    normalized_files: list[str],
    changed_run_ids: list[str],
    changed_assignment_ids: list[str],
) -> set[str]:
    files = list(normalized_files)
    nodes = index.get("nodes", {}) or {}
    runs = index.get("runs", {}) or {}
    assignments = index.get("assignments", {}) or {}

    for node_id in changed_node_ids:
        row = nodes.get(node_id, {})
        if isinstance(row, dict) and row.get("file"):
            files.append(str(row["file"]))
    for run_id in changed_run_ids:
        row = runs.get(run_id, {})
        if isinstance(row, dict) and row.get("file"):
            files.append(str(row["file"]))
    for assignment_id in changed_assignment_ids:
        row = assignments.get(assignment_id, {})
        if isinstance(row, dict) and row.get("file"):
            files.append(str(row["file"]))

    return set(_unique_strings(files))


def _index_explicit_edges_fresh(root: Path, index: dict[str, Any]) -> bool:
    row = index.get("explicit_edges", {}) or {}
    rel_path = str(row.get("file") or "graph/edges.yaml")
    expected_exists = bool(row.get("exists"))
    path = root / rel_path
    if not expected_exists:
        return not path.exists()
    return signature_matches(root, rel_path, row.get("file_signature"))


def _indexed_validation_payload_or_reason(
    root: Path,
    *,
    strict_lifecycle: bool,
    changed_nodes: list[str],
    normalized_files: list[str],
    changed_records: list[str],
) -> tuple[dict[str, Any] | None, str]:
    if strict_lifecycle:
        return None, "strict_lifecycle_requires_full_validation"
    index = load_validation_index(root)
    if isinstance(index, dict) and index.get("load_error"):
        return None, "validation_index_unreadable"
    if not is_index_schema_compatible(index):
        return None, "validation_index_missing_or_incompatible"
    assert index is not None
    if not _index_explicit_edges_fresh(root, index):
        return None, "validation_index_explicit_edges_hash_mismatch"
    file_node_ids = _unique_strings([
        node_id for node_id in (_index_node_id_from_file(index, path) for path in normalized_files) if node_id
    ])
    changed_run_ids: list[str] = []
    changed_assignment_ids: list[str] = []
    file_context_node_ids: list[str] = []
    file_record_ids: list[str] = []
    for normalized_file in normalized_files:
        run_ids, run_node_ids = _index_run_context_from_file(index, normalized_file)
        changed_run_ids.extend(run_ids)
        file_context_node_ids.extend(run_node_ids)
        assignment_ids, assignment_node_ids = _index_assignment_context_from_file(index, normalized_file)
        changed_assignment_ids.extend(assignment_ids)
        file_context_node_ids.extend(assignment_node_ids)
        record_ids, record_node_ids, record_run_ids = _index_artifact_record_context_from_file(index, normalized_file)
        file_record_ids.extend(record_ids)
        file_context_node_ids.extend(record_node_ids)
        changed_run_ids.extend(record_run_ids)
    known_index_files = set((index.get("files", {}).get("nodes", {}) or {}).keys())
    known_index_files.update((index.get("files", {}).get("runs", {}) or {}).keys())
    known_index_files.update((index.get("files", {}).get("assignments", {}) or {}).keys())
    known_index_files.update(
        str(record.get("file"))
        for record in (index.get("artifact_records", {}) or {}).values()
        if isinstance(record, dict) and record.get("file")
    )
    unknown_changed_files = [path for path in normalized_files if path not in known_index_files]
    if unknown_changed_files:
        return None, "validation_index_unknown_changed_file"
    if changed_run_ids or changed_assignment_ids or file_record_ids:
        return None, "validation_index_non_node_changed_file"
    changed_record_values = _unique_strings([*changed_records, *[f"artifact:{record_id}" for record_id in file_record_ids]])
    changed_artifact_records, record_errors, record_node_ids, record_run_ids = _index_changed_records(index, changed_record_values)
    if record_errors:
        return None, "validation_index_unknown_changed_record"
    changed_node_ids = _unique_strings([*changed_nodes, *file_node_ids])
    indexed_nodes = index.get("nodes", {}) or {}
    if any(node_id not in indexed_nodes for node_id in changed_node_ids):
        return None, "validation_index_unknown_changed_node"
    target_errors: list[str] = []
    record_ref_node_ids = [
        node_id for node_id, row in indexed_nodes.items()
        if any(record_id in (row.get("references", []) or []) for record_id in changed_artifact_records)
    ]
    changed_node_type_changed = _index_changed_node_type_changed(root, index, changed_node_ids)
    current_changed_ref_node_ids = _index_current_changed_node_refs(root, index, changed_node_ids)
    affected_seed_node_ids = _unique_strings([
        *changed_node_ids,
        *file_context_node_ids,
        *record_node_ids,
        *record_ref_node_ids,
        *current_changed_ref_node_ids,
    ])
    affected_nodes = _index_affected_nodes(index, affected_seed_node_ids)
    affected_runs = _index_affected_runs(index, affected_nodes, _unique_strings([*changed_run_ids, *record_run_ids]))
    affected_assignments = _index_affected_assignments(index, affected_nodes, _unique_strings(changed_assignment_ids))
    changed_node_artifact_records = _index_artifact_records_for_nodes(index, changed_node_ids)
    if changed_node_type_changed and (affected_runs or affected_assignments or changed_node_artifact_records):
        return None, "validation_index_affected_records_require_full_validation"
    required_files = _index_required_files(
        index,
        affected_nodes,
        affected_runs,
        affected_assignments,
        changed_artifact_records,
    )
    declared_changed_files = _index_declared_changed_files(
        index,
        changed_node_ids=changed_node_ids,
        normalized_files=normalized_files,
        changed_run_ids=_unique_strings([*changed_run_ids, *record_run_ids]),
        changed_assignment_ids=_unique_strings(changed_assignment_ids),
    )
    stale_files = [
        rel_path for rel_path, expected in required_files
        if rel_path not in declared_changed_files and not signature_matches(root, rel_path, expected)
    ]
    if stale_files:
        return None, "validation_index_file_hash_mismatch"
    local_errors = _index_local_errors(root, index, affected_nodes, artifact_record_ids=changed_artifact_records)
    all_errors = [*target_errors, *record_errors, *local_errors]
    ok = not all_errors
    return {
        "root": str(root),
        "valid": ok,
        "ok": ok,
        "mode": "incremental",
        "strict_lifecycle": strict_lifecycle,
        "node_count": len(index.get("nodes", {}) or {}),
        "changed": {"nodes": changed_node_ids, "files": normalized_files, "records": changed_record_values},
        "affected": {
            "nodes": affected_nodes,
            "runs": affected_runs,
            "assignments": affected_assignments,
            "artifact_records": changed_artifact_records,
        },
        "checks": [
            {"name": "changed_targets", "passed": not target_errors and not record_errors},
            {"name": "index_freshness", "passed": True},
            {"name": "local_references", "passed": not local_errors},
            {"name": "reverse_references", "passed": True},
        ],
        "fallback": {"used_full_validation": False, "reason": ""},
        "index": {
            "used": True,
            "fresh": True,
            "path": str(root / "dashboards" / "validation_index.json"),
            "checked_files_count": len([rel_path for rel_path, _ in required_files if rel_path not in declared_changed_files]),
            "declared_changed_files_count": len(declared_changed_files),
        },
        "errors": all_errors,
        "warnings": [],
    }, ""


def _validation_index_fallback_guidance(
    *,
    root: Path,
    reason: str,
    changed_nodes: list[str],
) -> dict[str, Any]:
    if reason == "validation_index_unreadable":
        recommended_fix = (
            "Validation index exists but could not be parsed, so changed-scope validation used a conservative full check. "
            "Run build once to rewrite dashboards/validation_index.json before relying on fast changed-scope checks."
        )
        commands = [f"research-cockpit build --root {root} --json"]
    elif reason == "validation_index_missing_or_incompatible":
        recommended_fix = (
            "Validation index is missing or incompatible, so changed-scope validation used a conservative full check. "
            "Run build once to refresh dashboards/validation_index.json before relying on fast changed-scope checks."
        )
        commands = [f"research-cockpit build --root {root} --json"]
    elif changed_nodes:
        recommended_fix = (
            "Validation index could not prove the changed scope is fresh, so validation used a conservative full check. "
            "Refresh the affected dashboard/index slice for the changed node and retry changed-scope validation."
        )
        commands = [
            f"research-cockpit build --root {root} --affected --id {changed_nodes[0]} --json",
        ]
    else:
        recommended_fix = (
            "Validation index could not prove the changed scope is fresh, so validation used a conservative full check. "
            "Run build to refresh dashboards/validation_index.json and retry changed-scope validation."
        )
        commands = [f"research-cockpit build --root {root} --json"]
    return {"recommended_fix": recommended_fix, "recommended_commands": commands}

def _incremental_validation_payload(
    root: Path,
    *,
    strict_lifecycle: bool,
    changed_nodes: list[str],
    changed_files: list[str],
    changed_records: list[str],
) -> dict[str, Any]:
    normalized_files = _unique_strings([_normalize_changed_file(root, item) for item in changed_files])
    indexed_payload, index_fallback_reason = _indexed_validation_payload_or_reason(
        root,
        strict_lifecycle=strict_lifecycle,
        changed_nodes=changed_nodes,
        normalized_files=normalized_files,
        changed_records=changed_records,
    )
    if indexed_payload is not None:
        return indexed_payload

    nodes = load_nodes(root)
    explicit_edges = load_explicit_edges(root)
    errors = validate_cockpit(root, nodes, explicit_edges=explicit_edges, include_interaction_log=True)
    lifecycle_errors = terminal_parent_guard_failures(nodes) if strict_lifecycle else []
    if lifecycle_errors:
        errors = [*errors, *[_lifecycle_error_message(error) for error in lifecycle_errors]]

    file_node_ids = _changed_node_ids_from_files(root, normalized_files)
    changed_run_ids: list[str] = []
    file_context_node_ids: list[str] = []
    changed_assignment_ids: list[str] = []
    file_artifact_record_ids: list[str] = []
    for normalized_file in normalized_files:
        run_ids, run_node_ids = _run_context_from_changed_file(root, normalized_file)
        changed_run_ids.extend(run_ids)
        file_context_node_ids.extend(run_node_ids)
        assignment_ids, assignment_node_ids = _assignment_context_from_changed_file(root, normalized_file)
        changed_assignment_ids.extend(assignment_ids)
        file_context_node_ids.extend(assignment_node_ids)
        record_ids, record_node_ids, record_run_ids = _artifact_record_file_context(root, normalized_file)
        file_artifact_record_ids.extend(record_ids)
        file_context_node_ids.extend(record_node_ids)
        changed_run_ids.extend(record_run_ids)

    changed_record_values = _unique_strings([*changed_records, *[f"artifact:{record_id}" for record_id in file_artifact_record_ids]])
    changed_artifact_records, record_errors, record_node_ids, record_run_ids = _artifact_record_errors(root, changed_record_values)
    record_ref_node_ids = [
        node.id for node in nodes.values()
        if any(_value_references(node.raw, record_id) for record_id in changed_artifact_records)
    ]
    changed_node_ids = _unique_strings([*changed_nodes, *file_node_ids])
    affected_seed_node_ids = _unique_strings([
        *changed_node_ids,
        *file_context_node_ids,
        *record_node_ids,
        *record_ref_node_ids,
    ])
    target_errors = [
        f"changed node does not exist: {node_id!r}"
        for node_id in changed_node_ids
        if node_id not in nodes
    ]
    affected_nodes = _affected_node_ids(nodes, affected_seed_node_ids, explicit_edges)
    affected_runs = _affected_run_ids(root, affected_nodes, _unique_strings([*changed_run_ids, *record_run_ids]))
    affected_assignments = _affected_assignment_ids(root, nodes, affected_nodes, _unique_strings(changed_assignment_ids))
    all_errors = [*errors, *target_errors, *record_errors]
    ok = not all_errors

    fallback_reason = index_fallback_reason or "phase_1_conservative_full_validation"
    fallback_guidance = _validation_index_fallback_guidance(
        root=root,
        reason=fallback_reason,
        changed_nodes=changed_node_ids,
    )

    payload: dict[str, Any] = {
        "root": str(root),
        "valid": ok,
        "ok": ok,
        "mode": "incremental",
        "strict_lifecycle": strict_lifecycle,
        "node_count": len(nodes),
        "changed": {
            "nodes": changed_node_ids,
            "files": normalized_files,
            "records": changed_record_values,
        },
        "affected": {
            "nodes": affected_nodes,
            "runs": affected_runs,
            "assignments": affected_assignments,
            "artifact_records": changed_artifact_records,
        },
        "checks": [
            {"name": "changed_targets", "passed": not target_errors and not record_errors},
            {"name": "node_schema", "passed": not errors},
            {"name": "local_references", "passed": not errors},
            {"name": "reverse_references", "passed": not errors},
            {"name": "root_consistency_fallback", "passed": not errors},
        ],
        "fallback": {
            "used_full_validation": True,
            "reason": fallback_reason,
            **fallback_guidance,
        },
        "index": {
            "used": False,
            "fresh": False,
            "fallback_reason": fallback_reason,
            "path": str(root / "dashboards" / "validation_index.json"),
            "recommended_fix": fallback_guidance["recommended_fix"],
        },
        "errors": all_errors,
        "warnings": [],
    }
    if strict_lifecycle:
        payload["lifecycle_errors"] = lifecycle_errors
    return payload


def validation_payload(
    root: Path,
    *,
    strict_lifecycle: bool = False,
    changed_nodes: list[str] | None = None,
    changed_files: list[str] | None = None,
    changed_records: list[str] | None = None,
) -> dict[str, Any]:
    changed_nodes = [str(item) for item in changed_nodes or []]
    changed_files = [str(item) for item in changed_files or []]
    changed_records = [str(item) for item in changed_records or []]
    if changed_nodes or changed_files or changed_records:
        return _incremental_validation_payload(
            root,
            strict_lifecycle=strict_lifecycle,
            changed_nodes=changed_nodes,
            changed_files=changed_files,
            changed_records=changed_records,
        )

    nodes = load_nodes(root)
    errors = validate_cockpit(root, nodes, include_interaction_log=True)
    lifecycle_errors = terminal_parent_guard_failures(nodes) if strict_lifecycle else []
    if lifecycle_errors:
        errors = [*errors, *[_lifecycle_error_message(error) for error in lifecycle_errors]]
    ok = not errors
    payload = {
        "root": str(root),
        "valid": ok,
        "ok": ok,
        "strict_lifecycle": strict_lifecycle,
        "node_count": len(nodes),
        "errors": errors,
    }
    if strict_lifecycle:
        payload["lifecycle_errors"] = lifecycle_errors
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true", help="Print machine-readable validation output")
    parser.add_argument(
        "--strict-lifecycle",
        action="store_true",
        help="Fail when terminal problem/option nodes still have active downstream work.",
    )
    parser.add_argument("--changed-node", action="append", default=[], help="Validate the affected scope for one changed node id.")
    parser.add_argument("--changed-file", action="append", default=[], help="Validate the affected scope for one root-relative changed file.")
    parser.add_argument(
        "--changed-files",
        action="append",
        nargs="+",
        default=[],
        help="Validate the affected scope for several root-relative changed files.",
    )
    parser.add_argument(
        "--changed-record",
        action="append",
        default=[],
        help="Validate the affected scope for a changed record such as artifact:<record_id>.",
    )
    args = parser.parse_args()

    changed_files = _flatten_changed_files(args.changed_file, args.changed_files)
    payload = validation_payload(
        args.root,
        strict_lifecycle=args.strict_lifecycle,
        changed_nodes=args.changed_node,
        changed_files=changed_files,
        changed_records=args.changed_record,
    )
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif payload.get("mode") == "incremental":
        changed_count = len(payload.get("changed", {}).get("nodes", []))
        affected_count = len(payload.get("affected", {}).get("nodes", []))
        state = "OK" if payload["ok"] else "FAILED"
        print(f"{state}: changed validation checked {changed_count} changed node(s), {affected_count} affected node(s) under {payload['root']}")
        for error in payload["errors"]:
            print(f"- {error}")
    elif payload["valid"]:
        print(f"OK: {payload['node_count']} nodes validated under {payload['root']}")
    else:
        print(f"FAILED: {len(payload['errors'])} issue(s) under {payload['root']}")
        for error in payload["errors"]:
            print(f"- {error}")

    raise SystemExit(0 if payload["ok"] else 1)


if __name__ == "__main__":
    main()
