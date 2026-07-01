from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.commands._runtime import emit_json
from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands.agent_bootstrap import agent_bootstrap_payload
from research_cockpit.commands.lint_semantic import semantic_lint
from research_cockpit.commands.node_context import node_context_payload
from research_cockpit.baselines import baseline_artifact_ids, resolve_effective_baseline
from research_cockpit.context_packs import build_next_action_scopes
from research_cockpit.graph_core import (
    child_ids,
    derive_focus_path,
    node_context,
    node_id_by_type_in_path,
    ordered_node_contexts,
    unique_strings,
)
from research_cockpit.model import (
    ValidationError,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
)
from research_cockpit.option_workstreams import experiment_ids_for_option, upstream_problem_id
from research_cockpit.artifact_records import list_artifact_records
from research_cockpit.resources import build_link_rows, node_artifact_ids, node_artifact_record_ids


def _related_option_id(nodes: dict[str, Any], node_id: str) -> str | None:
    node = nodes[node_id]
    if node.type == "option":
        return node.id
    try:
        path = derive_focus_path(nodes, node_id)
    except ValueError:
        return None
    return node_id_by_type_in_path(nodes, path, "option", nearest=True)


def _related_experiment_ids(nodes: dict[str, Any], node_id: str) -> list[str]:
    if nodes[node_id].type == "experiment":
        option_id = _related_option_id(nodes, node_id)
        if option_id:
            experiment_ids = experiment_ids_for_option(nodes, option_id)
            return experiment_ids or [node_id]
        return [node_id]
    option_id = _related_option_id(nodes, node_id)
    if option_id:
        return experiment_ids_for_option(nodes, option_id)
    return []


def _artifact_ids_for(nodes: dict[str, Any], node_ids: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for node_id in node_ids:
        if node_id not in nodes:
            continue
        for artifact_id in node_artifact_ids(nodes[node_id]):
            artifact_id = str(artifact_id)
            if artifact_id in nodes and nodes[artifact_id].type == "artifact" and artifact_id not in seen:
                out.append(artifact_id)
                seen.add(artifact_id)
    return out


def _artifact_record_ids_for(nodes: dict[str, Any], node_ids: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for node_id in node_ids:
        if node_id not in nodes:
            continue
        for record_id in node_artifact_record_ids(nodes[node_id]):
            record_id = str(record_id)
            if record_id not in seen:
                out.append(record_id)
                seen.add(record_id)
    return out


def _artifact_record_experiment_ids_for(nodes: dict[str, Any], node_ids: list[str]) -> list[str]:
    return unique_strings([
        node_id
        for node_id in node_ids
        if node_id in nodes and nodes[node_id].type == "experiment"
    ])


def _artifact_records_for(
    root: Path,
    nodes: dict[str, Any],
    node_ids: list[str],
    artifact_record_ids: list[str],
    *,
    record_cache: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, dict[str, Any]]:
    wanted = set(artifact_record_ids)
    if not wanted:
        return {}
    records: dict[str, dict[str, Any]] = {}
    cache = record_cache if record_cache is not None else {}
    for experiment_id in _artifact_record_experiment_ids_for(nodes, node_ids):
        if experiment_id not in cache:
            cache[experiment_id] = list_artifact_records(root, experiment_id=experiment_id)
        for record in cache[experiment_id]:
            record_id = str(record.get("record_id") or "")
            if record_id in wanted and record_id not in records:
                records[record_id] = record
    missing = wanted - set(records)
    if missing:
        all_key = "__all__"
        if all_key not in cache:
            cache[all_key] = list_artifact_records(root)
        for record in cache[all_key]:
            record_id = str(record.get("record_id") or "")
            if record_id in missing and record_id not in records:
                records[record_id] = record
    return records


def _node_context_resource_node_ids(nodes: dict[str, Any], node_id: str) -> list[str]:
    node = nodes[node_id]
    path_ids = derive_focus_path(nodes, node_id)
    child_ids_for_node = child_ids(nodes, node)
    sibling_ids: list[str] = []
    if node.parent and node.parent in nodes:
        sibling_ids = [
            child_id
            for child_id in child_ids(nodes, nodes[str(node.parent)])
            if child_id != node.id
        ]
    return unique_strings([*path_ids, *child_ids_for_node, *sibling_ids])


def _compact_focus_payload(nodes: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    focus_node_id = current.get("current_focus_node") or current.get("current_problem")
    focus_path_ids = current.get("current_focus_path", []) or []
    return {
        "current_stage": current.get("current_stage"),
        "current_problem": current.get("current_problem"),
        "current_option": current.get("current_option"),
        "current_focus_node": focus_node_id,
        "current_focus_path": focus_path_ids,
        "next_actions": current.get("next_actions", []) or [],
        "next_action_scopes": build_next_action_scopes(
            nodes,
            current,
            focus_node_id=focus_node_id,
            focus_path_ids=focus_path_ids,
        ),
    }


def _target_context_payload(nodes: dict[str, Any], node_id: str, global_focus: dict[str, Any]) -> dict[str, Any]:
    node = nodes[node_id]
    return {
        "node_id": node.id,
        "node_type": node.type,
        "node_status": node.status,
        "is_current_global_focus": node.id == global_focus.get("current_focus_node"),
    }


def _compact_nested_node_context(node_payload: dict[str, Any]) -> dict[str, Any]:
    out = {
        "schema_version": "node_context_nested_compact_v1",
        "compact": True,
        "node": node_payload.get("node", {}) or {},
        "parent_path": node_payload.get("parent_path", []) or [],
        "core_problem": node_payload.get("core_problem", {}) or {},
        "effective_baseline": node_payload.get("effective_baseline", {}) or {},
        "blockers": node_payload.get("blockers", []) or [],
        "next_actions": node_payload.get("next_actions", []) or [],
        "next_action_scopes": node_payload.get("next_action_scopes", {}) or {},
        "evidence_summary": node_payload.get("evidence_summary", {}) or {},
        "recommended_next_step": node_payload.get("recommended_next_step"),
        "worker_verify_commands": node_payload.get("worker_verify_commands", []) or [],
        "final_handoff_commands": node_payload.get("final_handoff_commands", []) or [],
        "verification_note": node_payload.get("verification_note", ""),
        "command_drafts": node_payload.get("command_drafts", {}) or {},
        "context_freshness": node_payload.get("context_freshness", {}) or {},
        "warnings": node_payload.get("warnings", []) or [],
        "omitted_fields": ["recommended_next_steps"],
    }
    for key in ("run_summary", "gate_summary"):
        if key in node_payload:
            out[key] = node_payload[key]
    return out


def _limited_items(values: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], int]:
    return values[:limit], max(0, len(values) - limit)


_ARTIFACT_RECORD_COMPACT_KEYS = {
    "record_id",
    "experiment_id",
    "run_id",
    "agent_id",
    "title",
    "summary",
    "stable_path",
    "status",
    "promoted_artifact_id",
    "created_at",
}
_RESOURCE_ROW_COMPACT_KEYS = {
    "kind",
    "node_id",
    "target",
    "path",
    "exists",
    "artifact_record_id",
    "resolution_base",
    "source_file",
}


def _compact_mapping(row: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {key: row[key] for key in keys if key in row}


def context_payload(
    root: Path,
    *,
    node_id: str,
    with_bootstrap: bool = False,
    with_artifacts: bool = False,
    compact: bool = False,
    command_style: str = "console",
) -> dict[str, Any]:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validation_errors = validate_cockpit(root, nodes, current, explicit_edges)
    if validation_errors:
        raise ValidationError(validation_errors)
    if node_id not in nodes:
        raise ValueError(f"Node does not exist: {node_id}")

    option_id = _related_option_id(nodes, node_id)
    problem_id = upstream_problem_id(nodes, option_id) if option_id else None
    related_experiment_ids = _related_experiment_ids(nodes, node_id)
    related_ids = [node_id, *related_experiment_ids]
    effective_baseline = resolve_effective_baseline(nodes, node_id, current)
    artifact_ids = _artifact_ids_for(nodes, related_ids)
    artifact_ids = unique_strings([*artifact_ids, *baseline_artifact_ids(effective_baseline)])
    artifact_record_ids = _artifact_record_ids_for(nodes, related_ids)
    artifact_record_cache: dict[str, list[dict[str, Any]]] = {}
    node_context_resource_ids = _node_context_resource_node_ids(nodes, node_id)
    node_context_record_ids = _artifact_record_ids_for(nodes, node_context_resource_ids)
    node_context_record_map = _artifact_records_for(
        root,
        nodes,
        node_context_resource_ids,
        node_context_record_ids,
        record_cache=artifact_record_cache,
    )
    node_context_nodes = {
        current_id: nodes[current_id]
        for current_id in node_context_resource_ids
        if current_id in nodes
    }
    node_context_link_rows = build_link_rows(
        root,
        node_context_nodes,
        artifact_records=node_context_record_map,
    )
    node_payload = node_context_payload(
        root,
        node_id=node_id,
        compact=compact,
        command_style=command_style,
        nodes=nodes,
        current=current,
        explicit_edges=explicit_edges,
        link_rows=node_context_link_rows,
        run_validation=False,
    )
    global_focus = _compact_focus_payload(nodes, current)
    target_context = _target_context_payload(nodes, node_id, global_focus)
    target_differs_from_global_focus = not target_context["is_current_global_focus"]
    semantic = semantic_lint(root)

    semantic_warnings = semantic["warnings"]
    if compact:
        semantic_warnings, semantic_omitted_count = _limited_items(semantic_warnings, 10)
    else:
        semantic_omitted_count = 0
    payload: dict[str, Any] = {
        "root": str(root),
        "warnings": list(node_payload.get("warnings", [])),
        "semantic_warnings": semantic_warnings,
        "node": node_payload["node"],
        "node_context": _compact_nested_node_context(node_payload) if compact else node_payload,
        "validation": {
            "ok": True,
            "errors": [],
            "node_count": len(nodes),
        },
        "focus": global_focus,
        "current_global_focus": global_focus,
        "target_context": target_context,
        "effective_baseline": effective_baseline,
        "context_boundary": {
            "target_node_id": node_id,
            "global_focus_node_id": global_focus.get("current_focus_node"),
            "target_differs_from_global_focus": target_differs_from_global_focus,
            "warning": (
                "This payload is for the target node, while current_global_focus points elsewhere."
                if target_differs_from_global_focus
                else ""
            ),
        },
        "related": {
            "problem": node_context(nodes[problem_id]) if problem_id else None,
            "option": node_context(nodes[option_id]) if option_id else None,
            "experiments": ordered_node_contexts(nodes, related_experiment_ids),
        },
        "recommended_commands": {
            "ingest_artifact": "research-cockpit ingest-artifact --root <root> --node <experiment_id> --from <worktree_output_dir> --run-id <run_id> --agent <agent_id> --record-only --json --compact --no-build",
            "complete_experiment": "research-cockpit complete-experiment --root <root> --id <experiment_id> --finding \"...\" --confidence medium --artifact-id <artifact_id> --dry-run --json --show-diff",
            "complete_experiments": "research-cockpit complete-experiments --root <root> --file findings.yaml --dry-run --json --show-diff",
            "create_artifact": "research-cockpit create-artifact --root <root> --id <artifact_id> --title \"...\" --path artifacts/<node_id>/<run_id> --link-to <node_id> --no-build",
            "finalize_workstream": "research-cockpit finalize-workstream --root <root> --file finalize.yaml --dry-run --json --compact",
        },
    }
    if semantic_omitted_count:
        payload["semantic_warnings_omitted_count"] = semantic_omitted_count
    if with_bootstrap:
        bootstrap = agent_bootstrap_payload(root, build=False)
        payload["bootstrap"] = {
            "validation": bootstrap.get("validation"),
            "focus": bootstrap.get("focus"),
            "mutation_guidance": bootstrap.get("mutation_guidance"),
            "top_suggestions": bootstrap.get("top_suggestions", [])[:3],
            "semantic_warnings": bootstrap.get("semantic_warnings", [])[:10],
        } if compact else bootstrap
    if with_artifacts:
        artifact_record_map = _artifact_records_for(
            root,
            nodes,
            related_ids,
            artifact_record_ids,
            record_cache=artifact_record_cache,
        )
        artifact_record_id_set = set(artifact_record_ids)
        resource_node_ids = unique_strings([node_id, *related_experiment_ids, *artifact_ids])
        resource_node_id_set = set(resource_node_ids)
        resource_nodes = {current_id: nodes[current_id] for current_id in resource_node_ids if current_id in nodes}
        resource_rows = build_link_rows(root, resource_nodes, artifact_records=artifact_record_map)
        artifact_records = [
            artifact_record_map[record_id]
            for record_id in artifact_record_ids
            if record_id in artifact_record_map
        ]
        scoped_resource_rows = [
            row
            for row in resource_rows
            if row.get("node_id") in resource_node_id_set
            or row.get("artifact_record_id") in artifact_record_id_set
        ]
        if compact:
            artifact_records, artifact_records_omitted = _limited_items(artifact_records, 20)
            scoped_resource_rows, resource_rows_omitted = _limited_items(scoped_resource_rows, 40)
            artifact_records = [
                _compact_mapping(record, _ARTIFACT_RECORD_COMPACT_KEYS)
                for record in artifact_records
            ]
            scoped_resource_rows = [
                _compact_mapping(row, _RESOURCE_ROW_COMPACT_KEYS)
                for row in scoped_resource_rows
            ]
        else:
            artifact_records_omitted = 0
            resource_rows_omitted = 0
        payload["artifacts"] = {
            "artifact_ids": artifact_ids,
            "artifact_record_ids": artifact_record_ids,
            "nodes": ordered_node_contexts(nodes, artifact_ids),
            "artifact_records": artifact_records,
            "resource_rows": scoped_resource_rows,
        }
        if compact:
            payload["artifacts"]["artifact_records_omitted_count"] = artifact_records_omitted
            payload["artifacts"]["resource_rows_omitted_count"] = resource_rows_omitted
    return payload


def _print_human(payload: dict[str, Any]) -> None:
    node = payload["node"]
    print(f"Node: {node['id']} - {node['title']} ({node['type']}/{node['status']})")
    related = payload.get("related", {})
    if related.get("option"):
        print(f"Option: {related['option']['id']}")
    if related.get("problem"):
        print(f"Problem: {related['problem']['id']}")
    print(f"Related experiments: {len(related.get('experiments', []))}")
    if payload.get("artifacts"):
        print(f"Artifacts: {len(payload['artifacts'].get('artifact_ids', []))}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--node", "--id", required=True, dest="node_id")
    parser.add_argument("--with-bootstrap", action="store_true")
    parser.add_argument("--with-artifacts", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--command-style",
        choices=["console", "python"],
        default="console",
        help="Command draft style to emit in nested node context.",
    )
    args = parser.parse_args()

    try:
        payload = context_payload(
            args.root,
            node_id=args.node_id,
            with_bootstrap=args.with_bootstrap,
            with_artifacts=args.with_artifacts,
            compact=args.compact,
            command_style=args.command_style,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(payload)
        return
    _print_human(payload)


if __name__ == "__main__":
    main()
