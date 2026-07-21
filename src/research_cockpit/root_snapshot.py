from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_cockpit.commands.validate_cockpit import validation_payload
from research_cockpit.model import (
    ResearchNode,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
    validate_current_state,
)
from research_cockpit.validation_index import (
    file_signature,
    is_index_schema_compatible,
    load_validation_index,
)


@dataclass(frozen=True)
class RootSnapshot:
    nodes: dict[str, ResearchNode]
    current: dict[str, Any]
    explicit_edges: list[dict[str, Any]]
    validation_errors: list[str]
    node_count: int
    fast_path: bool
    index_fresh: bool
    fallback_reason: str
    run_records: list[dict[str, Any]] | None = None
    gate_records: list[dict[str, Any]] | None = None
    loaded_node_ids: frozenset[str] = frozenset()

    def status_payload(self) -> dict[str, Any]:
        return {
            "fast_path": self.fast_path,
            "index_fresh": self.index_fresh,
            "fallback_reason": self.fallback_reason,
            "node_count": self.node_count,
        }


def _stub_node(node_id: str, row: dict[str, Any]) -> ResearchNode:
    raw: dict[str, Any] = {
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


def _selected_node_ids(
    index: dict[str, Any],
    node_id: str,
    *,
    additional_seed_ids: list[str] | None = None,
) -> list[str]:
    rows = index.get("nodes", {}) or {}
    selected: list[str] = []
    seen: set[str] = set()

    def add(node_id_to_add: str) -> None:
        if node_id_to_add in rows and node_id_to_add not in seen:
            selected.append(node_id_to_add)
            seen.add(node_id_to_add)

    def add_chain(seed_id: str) -> None:
        current_id = seed_id
        while current_id in rows and current_id not in seen:
            add(current_id)
            parent_id = str(rows[current_id].get("parent") or "")
            if not parent_id:
                break
            current_id = parent_id

    add_chain(node_id)
    for seed_id in additional_seed_ids or []:
        add_chain(str(seed_id))

    target = rows.get(node_id, {})
    candidate_ids = [
        *list(target.get("children", []) or [])[:10],
        *list(target.get("references", []) or [])[:30],
    ]
    parent_id = str(target.get("parent") or "")
    if parent_id in rows:
        candidate_ids.extend(list(rows[parent_id].get("children", []) or [])[:10])

    option_id = ""
    for candidate_id in selected:
        if str(rows.get(candidate_id, {}).get("type") or "") == "option":
            option_id = candidate_id
            break
    if option_id:
        option_experiments = [
            str(child_id)
            for child_id in rows[option_id].get("children", []) or []
            if str(rows.get(str(child_id), {}).get("type") or "") == "experiment"
        ]
        candidate_ids.extend(option_experiments[:10])

    for candidate_id in candidate_ids:
        add(str(candidate_id))
    return selected


def indexed_root_snapshot_source(
    root: Path,
    *,
    node_id: str,
    validation_index: dict[str, Any],
) -> dict[str, Any]:
    """Return bounded source signatures without parsing indexed graph YAML."""

    rows = validation_index.get("nodes", {}) or {}
    if node_id not in rows:
        raise ValueError(f"Node does not exist: {node_id}")
    current_path = root / "current_state.yaml"
    current_data = load_yaml(current_path)
    current = current_data if isinstance(current_data, dict) else {}
    current_seed_ids = [
        str(current.get(field) or "")
        for field in (
            "current_focus_node",
            "current_option",
            "current_problem",
            "current_stage",
        )
        if current.get(field)
    ]
    selected = _selected_node_ids(
        validation_index,
        node_id,
        additional_seed_ids=current_seed_ids,
    )

    def path_signature(relative_path: str) -> dict[str, Any] | None:
        path = root / relative_path
        return file_signature(path) if path.is_file() else None

    node_sources: dict[str, Any] = {}
    for selected_id in selected:
        row = rows.get(selected_id, {}) or {}
        relative_path = str(row.get("file") or "")
        node_sources[selected_id] = {
            "index_row": row,
            "actual_file_signature": path_signature(relative_path) if relative_path else None,
        }

    target_type = str(rows.get(node_id, {}).get("type") or "")
    sidecar_sources: dict[str, Any] = {}
    if target_type == "experiment":
        for rows_key, by_experiment_key, file_key in (
            ("runs", "runs_by_experiment", "file"),
            ("gate_results", "gate_results_by_experiment", "record_file"),
        ):
            indexed_rows = validation_index.get(rows_key, {}) or {}
            sources: dict[str, Any] = {}
            for record_id in (
                validation_index.get(by_experiment_key, {}) or {}
            ).get(node_id, []) or []:
                row = indexed_rows.get(str(record_id), {}) or {}
                relative_path = str(row.get(file_key) or "")
                sources[str(record_id)] = {
                    "index_row": row,
                    "actual_file_signature": (
                        path_signature(relative_path) if relative_path else None
                    ),
                }
            sidecar_sources[rows_key] = sources

    edges = validation_index.get("explicit_edges", {}) or {}
    edges_path = str(edges.get("file") or "graph/edges.yaml")
    return {
        "current_state": {
            "value": current,
            "actual_file_signature": (
                file_signature(current_path) if current_path.is_file() else None
            ),
        },
        "nodes": node_sources,
        "explicit_edges": {
            "index_row": edges,
            "actual_file_signature": path_signature(edges_path),
        },
        "sidecars": sidecar_sources,
    }


def _indexed_sidecar_records(
    root: Path,
    index: dict[str, Any],
    *,
    experiment_id: str,
    rows_key: str,
    by_experiment_key: str,
    file_key: str,
) -> list[dict[str, Any]]:
    rows = index.get(rows_key, {}) or {}
    records: list[dict[str, Any]] = []
    for record_id in (index.get(by_experiment_key, {}) or {}).get(experiment_id, []) or []:
        row = rows.get(str(record_id), {})
        rel_path = str(row.get(file_key) or "")
        if not rel_path:
            continue
        data = load_yaml(root / rel_path)
        if isinstance(data, dict):
            records.append(dict(data))
    return records

def _indexed_snapshot(root: Path, node_id: str, index: dict[str, Any]) -> RootSnapshot:
    rows = index.get("nodes", {}) or {}
    if node_id not in rows:
        raise ValueError(f"Node does not exist: {node_id}")

    validation = validation_payload(root, changed_nodes=[node_id], validation_index=index)
    if validation.get("fallback", {}).get("used_full_validation"):
        raise RuntimeError(str(validation.get("fallback", {}).get("reason") or "validation_index_fallback"))

    current = load_yaml(root / "current_state.yaml")
    current_seed_ids = [
        str(current.get(field) or "")
        for field in (
            "current_focus_node",
            "current_option",
            "current_problem",
            "current_stage",
        )
        if current.get(field)
    ]
    loaded_node_ids = _selected_node_ids(
        index,
        node_id,
        additional_seed_ids=current_seed_ids,
    )
    nodes = {
        str(current_id): _stub_node(str(current_id), row)
        for current_id, row in rows.items()
        if isinstance(row, dict)
    }
    for current_id in loaded_node_ids:
        row = rows.get(current_id, {})
        rel_path = str(row.get("file") or "")
        if not rel_path:
            continue
        data = load_yaml(root / rel_path)
        if isinstance(data, dict):
            nodes[current_id] = ResearchNode.from_dict(data)

    explicit_edges = load_explicit_edges(root)
    current_errors = validate_current_state(current, nodes, explicit_edges)

    target_type = str(rows.get(node_id, {}).get("type") or "")
    run_records = []
    gate_records = []
    if target_type == "experiment":
        run_records = _indexed_sidecar_records(
            root,
            index,
            experiment_id=node_id,
            rows_key="runs",
            by_experiment_key="runs_by_experiment",
            file_key="file",
        )
        gate_records = _indexed_sidecar_records(
            root,
            index,
            experiment_id=node_id,
            rows_key="gate_results",
            by_experiment_key="gate_results_by_experiment",
            file_key="record_file",
        )

    return RootSnapshot(
        nodes=nodes,
        current=current,
        explicit_edges=explicit_edges,
        validation_errors=[
            *list(validation.get("errors", []) or []),
            *current_errors,
        ],
        node_count=len(rows),
        fast_path=True,
        index_fresh=True,
        fallback_reason="",
        run_records=run_records,
        gate_records=gate_records,
        loaded_node_ids=frozenset(loaded_node_ids),
    )


def load_indexed_root_snapshot(
    root: Path,
    *,
    node_id: str,
    validation_index: dict[str, Any] | None = None,
) -> RootSnapshot:
    index = validation_index if validation_index is not None else load_validation_index(root)
    if not is_index_schema_compatible(index):
        raise RuntimeError("validation_index_missing_or_incompatible")
    assert index is not None
    return _indexed_snapshot(root, node_id, index)


def load_root_snapshot(root: Path, *, node_id: str, compact: bool) -> RootSnapshot:
    if compact:
        index = load_validation_index(root)
        if isinstance(index, dict) and index.get("load_error"):
            fallback_reason = "validation_index_unreadable"
        elif is_index_schema_compatible(index):
            try:
                return load_indexed_root_snapshot(
                    root,
                    node_id=node_id,
                    validation_index=index,
                )
            except RuntimeError as exc:
                fallback_reason = str(exc)
        else:
            fallback_reason = "validation_index_missing_or_incompatible"
    else:
        fallback_reason = "compact_fast_path_not_requested"

    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    errors = validate_cockpit(root, nodes, current, explicit_edges)
    return RootSnapshot(
        nodes=nodes,
        current=current,
        explicit_edges=explicit_edges,
        validation_errors=errors,
        node_count=len(nodes),
        fast_path=False,
        index_fresh=False,
        fallback_reason=fallback_reason,
    )
