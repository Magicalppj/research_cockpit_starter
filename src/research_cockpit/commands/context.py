from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.cli_progress import progress_traced
from research_cockpit.commands._runtime import emit_json
from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands.agent_bootstrap import agent_bootstrap_payload
from research_cockpit.commands.lint_semantic import TERMINAL_STATUSES, semantic_lint
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
from research_cockpit.root_snapshot import load_root_snapshot
from research_cockpit.execution_context import execution_context_payload
from research_cockpit.decisions import build_decision_acceptance_checklist


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
    allow_global_fallback: bool = True,
) -> dict[str, dict[str, Any]]:
    wanted = set(artifact_record_ids)
    if not wanted:
        return {}
    records: dict[str, dict[str, Any]] = {}
    cache = record_cache if record_cache is not None else {}
    experiment_ids = _artifact_record_experiment_ids_for(nodes, node_ids)
    for experiment_id in experiment_ids:
        if experiment_id not in cache:
            cache[experiment_id] = list_artifact_records(root, experiment_id=experiment_id)
        for record in cache[experiment_id]:
            record_id = str(record.get("record_id") or "")
            if record_id in wanted and record_id not in records:
                records[record_id] = record
    missing = wanted - set(records)
    if missing and (allow_global_fallback or not experiment_ids):
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


def _bounded_values(values: Any, limit: int) -> tuple[list[Any], int, int]:
    items = list(values) if isinstance(values, list) else []
    selected = items[:limit]
    return selected, len(items), max(0, len(items) - len(selected))


def _compact_action_scopes(scopes: Any) -> dict[str, Any]:
    if not isinstance(scopes, dict):
        return {}
    out: dict[str, Any] = {}
    omitted_counts: dict[str, int] = {}
    for key, value in scopes.items():
        if isinstance(value, list):
            limit = 8 if key == "focus_path_ids" else 3
            selected, _, omitted = _bounded_values(value, limit)
            out[key] = [
                {
                    field: item[field]
                    for field in ("scope", "node_id", "action", "stale")
                    if field in item
                }
                if isinstance(item, dict)
                else item
                for item in selected
            ]
            omitted_counts[key] = omitted
        elif key != "counts":
            out[key] = value
    out["counts"] = dict(scopes.get("counts", {})) if isinstance(scopes.get("counts"), dict) else {}
    out["omitted_counts"] = omitted_counts
    return out


def _compact_focus_payload(
    nodes: dict[str, Any],
    current: dict[str, Any],
    *,
    compact: bool = False,
) -> dict[str, Any]:
    focus_node_id = current.get("current_focus_node") or current.get("current_problem")
    raw_focus_path = current.get("current_focus_path", []) or []
    raw_next_actions = current.get("next_actions", []) or []
    focus_path, focus_path_count, focus_path_omitted = _bounded_values(raw_focus_path, 8) if compact else (
        list(raw_focus_path),
        len(raw_focus_path),
        0,
    )
    next_actions, next_actions_count, next_actions_omitted = _bounded_values(raw_next_actions, 5) if compact else (
        list(raw_next_actions),
        len(raw_next_actions),
        0,
    )
    scopes = build_next_action_scopes(
        nodes,
        current,
        focus_node_id=focus_node_id,
        focus_path_ids=raw_focus_path,
    )
    payload = {
        "current_stage": current.get("current_stage"),
        "current_problem": current.get("current_problem"),
        "current_option": current.get("current_option"),
        "current_focus_node": focus_node_id,
        "current_focus_path": focus_path,
        "next_actions": next_actions,
        "next_action_scopes": _compact_action_scopes(scopes) if compact else scopes,
    }
    if compact:
        payload.update({
            "current_focus_path_count": focus_path_count,
            "current_focus_path_omitted_count": focus_path_omitted,
            "next_actions_count": next_actions_count,
            "next_actions_omitted_count": next_actions_omitted,
        })
    return payload

def _target_context_payload(nodes: dict[str, Any], node_id: str, global_focus: dict[str, Any]) -> dict[str, Any]:
    node = nodes[node_id]
    return {
        "node_id": node.id,
        "node_type": node.type,
        "node_status": node.status,
        "is_current_global_focus": node.id == global_focus.get("current_focus_node"),
    }


def _compact_nested_node_context(node_payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "blockers", "blockers_count",
        "blockers_omitted_count", "next_actions", "next_actions_count",
        "next_actions_omitted_count", "evidence_summary",
        "recommended_next_step",
        "success_criteria_summary", "metrics_summary", "latest_findings",
        "key_artifacts", "assignment_cursor", "run_summary", "gate_summary",
    )
    out = {
        "schema_version": "node_context_nested_compact_v3",
        "compact": True,
        "omitted_fields": [
            "node", "parent_path", "core_problem", "effective_baseline",
            "next_action_scopes", "command_drafts", "verification_commands",
        ],
    }
    for key in keys:
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


_COMPACT_NODE_REF_KEYS = {"id", "type", "title", "status", "summary", "result_summary", "outcome"}


def _compact_node_ref(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    return {key: row[key] for key in _COMPACT_NODE_REF_KEYS if row.get(key) not in (None, "", [])}


def _compact_bootstrap_payload(bootstrap: dict[str, Any], focus: dict[str, Any]) -> dict[str, Any]:
    guidance = bootstrap.get("mutation_guidance") if isinstance(bootstrap.get("mutation_guidance"), dict) else {}
    batch_mode = guidance.get("multi_agent_batch_mode") if isinstance(guidance.get("multi_agent_batch_mode"), dict) else {}
    top_suggestions = list(bootstrap.get("top_suggestions", []) or [])
    semantic_warnings = list(bootstrap.get("semantic_warnings", []) or [])
    compact_suggestions = [
        {
            key: suggestion[key]
            for key in ("id", "suggestion_id", "kind", "priority", "action", "reason", "source_node_id", "suggested_command")
            if suggestion.get(key) not in (None, "", [])
        }
        for suggestion in top_suggestions[:3]
        if isinstance(suggestion, dict)
    ]
    return {
        "validation": bootstrap.get("validation"),
        "focus": focus,
        "mutation_guidance": {
            "current_focus_node": guidance.get("current_focus_node"),
            "current_best_option": guidance.get("current_best_option"),
            "batching": guidance.get("batching"),
            "multi_agent_batch_mode": {
                "rules": list(batch_mode.get("rules", []) or [])[:4],
                "worker_verify_commands": list(batch_mode.get("worker_verify_commands", []) or [])[:3],
                "final_handoff_commands": list(batch_mode.get("final_handoff_commands", []) or [])[:1],
            },
        },
        "top_suggestions": compact_suggestions,
        "top_suggestions_count": len(top_suggestions),
        "top_suggestions_omitted_count": max(0, len(top_suggestions) - len(compact_suggestions)),
        "semantic_warnings": semantic_warnings[:10],
        "semantic_warnings_count": len(semantic_warnings),
        "semantic_warnings_omitted_count": max(0, len(semantic_warnings) - 10),
    }

def _compact_semantic_warnings(
    root: Path,
    nodes: dict[str, Any],
    current: dict[str, Any],
) -> list[dict[str, Any]]:
    focus_node_id = str(current.get("current_focus_node") or "")
    focus = nodes.get(focus_node_id)
    if focus is None or focus.status not in TERMINAL_STATUSES:
        return []
    return [{
        "id": "current_focus_terminal",
        "severity": "warning",
        "message": (
            f"current_focus_node {focus.id!r} has terminal status {focus.status!r}."
        ),
        "node_id": focus.id,
        "command": f"research-cockpit coord assign --root {root} --file <coord_assign.yaml> --json --compact",
    }]

@progress_traced("context_snapshot")
def context_payload(
    root: Path,
    *,
    node_id: str,
    with_bootstrap: bool = False,
    with_artifacts: bool = False,
    compact: bool = False,
    command_style: str = "console",
    view: str = "default",
    since_revision: str | None = None,
) -> dict[str, Any]:
    if view == "execution":
        if with_bootstrap or with_artifacts:
            raise ValueError(
                "--view execution cannot be combined with --with-bootstrap or --with-artifacts"
            )
        return execution_context_payload(
            root,
            node_id=node_id,
            since_revision=since_revision,
        )
    if view != "default":
        raise ValueError(f"Unsupported context view: {view}")
    if since_revision:
        raise ValueError("--since requires --view execution")
    snapshot = load_root_snapshot(root, node_id=node_id, compact=compact)
    nodes = snapshot.nodes
    current = snapshot.current
    explicit_edges = snapshot.explicit_edges
    validation_errors = snapshot.validation_errors
    if validation_errors:
        raise ValidationError(validation_errors)
    if node_id not in nodes:
        raise ValueError(f"Node does not exist: {node_id}")

    option_id = _related_option_id(nodes, node_id)
    problem_id = upstream_problem_id(nodes, option_id) if option_id else None
    all_related_experiment_ids = _related_experiment_ids(nodes, node_id)
    related_experiment_count = len(all_related_experiment_ids)
    related_experiment_ids = all_related_experiment_ids[:10] if compact else all_related_experiment_ids
    related_ids = [node_id, *related_experiment_ids]
    effective_baseline = resolve_effective_baseline(nodes, node_id, current)
    artifact_ids = _artifact_ids_for(nodes, related_ids)
    artifact_ids = unique_strings([*artifact_ids, *baseline_artifact_ids(effective_baseline)])
    if compact:
        artifact_ids = artifact_ids[:20]
    artifact_record_ids = _artifact_record_ids_for(nodes, related_ids)
    if compact:
        artifact_record_ids = artifact_record_ids[:20]
    artifact_record_cache: dict[str, list[dict[str, Any]]] = {}
    node_context_resource_ids = _node_context_resource_node_ids(nodes, node_id)
    if compact:
        node_context_resource_ids = node_context_resource_ids[:30]
    node_context_record_ids = _artifact_record_ids_for(nodes, node_context_resource_ids)
    if compact:
        node_context_record_ids = node_context_record_ids[:20]
    node_context_record_map = _artifact_records_for(
        root,
        nodes,
        node_context_resource_ids,
        node_context_record_ids,
        record_cache=artifact_record_cache,
    allow_global_fallback=not compact,
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
        suggestions=[] if compact else None,
        run_records=snapshot.run_records,
        gate_records=snapshot.gate_records,
    )
    global_focus = _compact_focus_payload(nodes, current, compact=compact)
    target_context = _target_context_payload(nodes, node_id, global_focus)
    target_differs_from_global_focus = not target_context["is_current_global_focus"]
    semantic = {
        "warnings": _compact_semantic_warnings(root, nodes, current)
    } if compact else semantic_lint(root)
    related_experiments = ordered_node_contexts(nodes, related_experiment_ids)
    if compact:
        related_experiments = [
            item
            for item in (_compact_node_ref(row) for row in related_experiments)
            if item is not None
        ]
    if compact:
        related_experiments, _, related_experiments_omitted = _bounded_values(related_experiments, 10)
        related_experiments_omitted = max(0, related_experiment_count - len(related_experiments))
    else:
        related_experiments_omitted = 0

    semantic_warnings = semantic["warnings"]
    if compact:
        semantic_warnings, semantic_omitted_count = _limited_items(semantic_warnings, 10)
    else:
        semantic_omitted_count = 0
    payload: dict[str, Any] = {
        "root": str(root),
        "warnings": list(node_payload.get("warnings", []))[:10] if compact else list(node_payload.get("warnings", [])),
        "semantic_warnings": semantic_warnings,
        "node": node_payload["node"],
        "node_context": _compact_nested_node_context(node_payload) if compact else node_payload,
        "validation": {
            "ok": True,
            "errors": [],
            "node_count": snapshot.node_count,
        },
        "snapshot": snapshot.status_payload(),
        "focus": global_focus,

        "target_context": target_context,
        "effective_baseline": node_payload.get("effective_baseline", effective_baseline) if compact else effective_baseline,
        "context_boundary": {
            "target_node_id": node_id,
            "global_focus_node_id": global_focus.get("current_focus_node"),
            "target_differs_from_global_focus": target_differs_from_global_focus,
            "warning": (
                "This payload is for the target node, while global focus points elsewhere."
                if target_differs_from_global_focus
                else ""
            ),
        },
        "related": {
            "problem": _compact_node_ref(node_context(nodes[problem_id])) if compact and problem_id else (node_context(nodes[problem_id]) if problem_id else None),
            "option": _compact_node_ref(node_context(nodes[option_id])) if compact and option_id else (node_context(nodes[option_id]) if option_id else None),
            "experiments": related_experiments,
            "experiments_count": related_experiment_count,
            "experiments_omitted_count": related_experiments_omitted,
        },
        "recommended_commands": {
            "open_assignment": "research-cockpit work open --root <root> --assignment <assignment_id> --json --compact",
            "record_evidence": "research-cockpit work record --root <root> --assignment <assignment_id> --file <record.yaml> --json --compact",
            "close_assignment": "research-cockpit work close --root <root> --assignment <assignment_id> --file <closeout.yaml> --json --compact",
            "coordinate_graph": "research-cockpit coord assign --root <root> --file <coord_assign.yaml> --json --compact",
            "coordinate_decision": "research-cockpit coord decide --root <root> --file <coord_decide.yaml> --json --compact",
        },
    }
    if nodes[node_id].type == "decision":
        payload["decision_acceptance"] = build_decision_acceptance_checklist(nodes, node_id)
    if compact:
        payload["schema_version"] = "context_compact_v3"
        payload["compact"] = True
        payload["warnings_count"] = len(node_payload.get("warnings", []))
        payload["warnings_omitted_count"] = max(0, len(node_payload.get("warnings", [])) - 10)
        payload["deprecated_fields"] = ["current_global_focus"]
    if not compact:
        payload["current_global_focus"] = global_focus
    if semantic_omitted_count:
        payload["semantic_warnings_omitted_count"] = semantic_omitted_count
    if with_bootstrap:
        bootstrap = agent_bootstrap_payload(
            root,
            build=False,
            nodes=nodes,
            current=current,
            validation_errors=validation_errors,
            link_rows=node_context_link_rows,
            semantic_warnings=list(semantic["warnings"]),
            compact_runtime=compact,
        )
        payload["bootstrap"] = _compact_bootstrap_payload(bootstrap, global_focus) if compact else bootstrap
    if with_artifacts:
        artifact_record_map = _artifact_records_for(
            root,
            nodes,
            related_ids,
            artifact_record_ids,
            record_cache=artifact_record_cache,
        allow_global_fallback=not compact,
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
        output_artifact_ids = artifact_ids[:20] if compact else artifact_ids
        output_record_ids = artifact_record_ids[:20] if compact else artifact_record_ids
        artifact_nodes = ordered_node_contexts(nodes, output_artifact_ids)
        if compact:
            artifact_nodes = [
                item
                for item in (_compact_node_ref(row) for row in artifact_nodes)
                if item is not None
            ]
        payload["artifacts"] = {
            "artifact_ids": output_artifact_ids,
            "artifact_record_ids": output_record_ids,
            "nodes": artifact_nodes,
            "artifact_records": artifact_records,
            "resource_rows": scoped_resource_rows,
        }
        if compact:
            payload["artifacts"].update({
                "artifact_ids_count": len(artifact_ids),
                "artifact_ids_omitted_count": max(0, len(artifact_ids) - len(output_artifact_ids)),
                "artifact_record_ids_count": len(artifact_record_ids),
                "artifact_record_ids_omitted_count": max(0, len(artifact_record_ids) - len(output_record_ids)),
                "artifact_records_count": len(artifact_records) + artifact_records_omitted,
                "artifact_records_omitted_count": artifact_records_omitted,
                "resource_rows_count": len(scoped_resource_rows) + resource_rows_omitted,
                "resource_rows_omitted_count": resource_rows_omitted,
            })
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
    parser.add_argument("--view", choices=["default", "execution"], default="default")
    parser.add_argument("--since", dest="since_revision")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--command-style",
        choices=["console", "python"],
        default="console",
        help="Command draft style to emit in nested node context.",
    )
    parser.add_argument("--progress", action="store_true", help="Print phase progress to stderr.")
    args = parser.parse_args()
    if args.since_revision and args.view != "execution":
        parser.error("--since requires --view execution")
    if args.view == "execution" and (args.with_bootstrap or args.with_artifacts):
        parser.error(
            "--view execution cannot be combined with --with-bootstrap or --with-artifacts"
        )

    try:
        payload = context_payload(
            args.root,
            node_id=args.node_id,
            with_bootstrap=args.with_bootstrap,
            with_artifacts=args.with_artifacts,
            compact=args.compact,
            command_style=args.command_style,
            view=args.view,
            since_revision=args.since_revision,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(payload, compact=args.compact)
        return
    _print_human(payload)


if __name__ == "__main__":
    main()
