from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands.agent_bootstrap import agent_bootstrap_payload
from research_cockpit.commands.node_context import node_context_payload
from research_cockpit.graph_core import derive_focus_path, node_context, node_id_by_type_in_path, ordered_node_contexts
from research_cockpit.model import (
    ValidationError,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
)
from research_cockpit.option_workstreams import experiment_ids_for_option, upstream_problem_id
from research_cockpit.resources import build_link_rows, node_artifact_ids


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


def _compact_focus_payload(current: dict[str, Any]) -> dict[str, Any]:
    return {
        "current_stage": current.get("current_stage"),
        "current_problem": current.get("current_problem"),
        "current_option": current.get("current_option"),
        "current_focus_node": current.get("current_focus_node") or current.get("current_problem"),
        "current_focus_path": current.get("current_focus_path", []) or [],
        "next_actions": current.get("next_actions", []) or [],
    }


def _target_context_payload(nodes: dict[str, Any], node_id: str, global_focus: dict[str, Any]) -> dict[str, Any]:
    node = nodes[node_id]
    return {
        "node_id": node.id,
        "node_type": node.type,
        "node_status": node.status,
        "is_current_global_focus": node.id == global_focus.get("current_focus_node"),
    }


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

    node_payload = node_context_payload(root, node_id=node_id, compact=compact, command_style=command_style)
    option_id = _related_option_id(nodes, node_id)
    problem_id = upstream_problem_id(nodes, option_id) if option_id else None
    related_experiment_ids = _related_experiment_ids(nodes, node_id)
    related_ids = [node_id, *related_experiment_ids]
    artifact_ids = _artifact_ids_for(nodes, related_ids)
    global_focus = _compact_focus_payload(current)
    target_context = _target_context_payload(nodes, node_id, global_focus)
    target_differs_from_global_focus = not target_context["is_current_global_focus"]

    payload: dict[str, Any] = {
        "root": str(root),
        "warnings": list(node_payload.get("warnings", [])),
        "node": node_payload["node"],
        "node_context": node_payload,
        "validation": {
            "ok": True,
            "errors": [],
            "node_count": len(nodes),
        },
        "focus": global_focus,
        "current_global_focus": global_focus,
        "target_context": target_context,
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
            "complete_experiment": "research-cockpit complete-experiment --root <root> --id <experiment_id> --finding \"...\" --confidence medium --evidence-path outputs/run_x --evidence-link metrics=outputs/run_x/metrics.json --dry-run --json --show-diff",
            "complete_experiments": "research-cockpit complete-experiments --root <root> --file findings.yaml --dry-run --json --show-diff",
            "create_artifact": "research-cockpit create-artifact --root <root> --id <artifact_id> --title \"...\" --path <path> --link-to <node_id> --no-build",
            "finalize_workstream": "research-cockpit finalize-workstream --root <root> --file finalize.yaml --dry-run --json --compact",
        },
    }
    if with_bootstrap:
        bootstrap = agent_bootstrap_payload(root, build=False)
        payload["bootstrap"] = {
            "validation": bootstrap.get("validation"),
            "focus": bootstrap.get("focus"),
            "mutation_guidance": bootstrap.get("mutation_guidance"),
            "top_suggestions": bootstrap.get("top_suggestions", [])[:3],
        } if compact else bootstrap
    if with_artifacts:
        payload["artifacts"] = {
            "artifact_ids": artifact_ids,
            "nodes": ordered_node_contexts(nodes, artifact_ids),
            "resource_rows": [
                row
                for row in build_link_rows(root, nodes)
                if row.get("node_id") in set([node_id, *related_experiment_ids, *artifact_ids])
            ],
        }
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
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    _print_human(payload)


if __name__ == "__main__":
    main()
