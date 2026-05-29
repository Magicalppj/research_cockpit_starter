from __future__ import annotations

import argparse
import copy
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import (
    compact_mutation_result,
    dry_run_preflight_result,
    emit_json,
    finish_mutation,
    load_validated_state,
    safe_print,
    yaml_change_diff,
)
from research_cockpit.commands.record_finding import find_node_file
from research_cockpit.graph_core import GraphTopology
from research_cockpit.lifecycle_guards import (
    ACTIVE_DOWNSTREAM_STATUSES,
    ordered_descendant_ids,
    active_descendant_blockers,
    terminal_parent_guard_failure,
)
from research_cockpit.model import ResearchNode, ValidationError, load_yaml, script_command, validate_cockpit


SUPPORTED_DOWNSTREAM_STATUSES = {"parked"}
BRANCH_ROOT_TYPES = {"problem", "option"}


def _status_target(node: ResearchNode, downstream_status: str, *, include_experiments: bool) -> str | None:
    if node.status not in ACTIVE_DOWNSTREAM_STATUSES.get(node.type, set()):
        return None
    if node.type in {"problem", "option"}:
        return downstream_status
    if node.type == "experiment" and include_experiments:
        return "cancelled"
    return None


def _candidate_nodes(
    nodes: dict[str, ResearchNode],
    after_data_by_id: dict[str, dict[str, Any]],
) -> dict[str, ResearchNode]:
    candidate = dict(nodes)
    for node_id, data in after_data_by_id.items():
        candidate[node_id] = ResearchNode.from_dict(data)
    return candidate


def close_branch(
    root: Path,
    *,
    node_id: str,
    downstream_status: str = "parked",
    include_experiments: bool = False,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    if downstream_status not in SUPPORTED_DOWNSTREAM_STATUSES:
        allowed = ", ".join(sorted(SUPPORTED_DOWNSTREAM_STATUSES))
        raise ValueError(f"Unsupported downstream status {downstream_status!r}; allowed: {allowed}")

    state = load_validated_state(root)
    nodes = state.nodes
    if node_id not in nodes:
        raise ValueError(f"Node does not exist: {node_id}")
    root_node = nodes[node_id]
    if root_node.type not in BRANCH_ROOT_TYPES:
        raise ValueError(f"Node {node_id} must be problem or option, got {root_node.type}")

    topology = GraphTopology.from_nodes(nodes)
    descendant_ids = ordered_descendant_ids(topology, node_id)
    proposed_status_by_id: dict[str, str] = {}
    skipped_by_id: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    for descendant_id in descendant_ids:
        descendant = nodes.get(descendant_id)
        if not descendant:
            continue
        target_status = _status_target(
            descendant,
            downstream_status,
            include_experiments=include_experiments,
        )
        if target_status:
            proposed_status_by_id[descendant_id] = target_status
        elif descendant.type == "experiment" and descendant.status in ACTIVE_DOWNSTREAM_STATUSES["experiment"]:
            skipped_by_id[descendant_id] = {
                "id": descendant.id,
                "type": descendant.type,
                "status": descendant.status,
                "reason": "requires_include_experiments",
                "path": topology.safe_path(descendant.id),
            }
            warnings.append(
                f"{descendant.id} is {descendant.status}; rerun with --include-experiments after external work is stopped."
            )

    before_data_by_id: dict[str, dict[str, Any]] = {}
    path_by_id: dict[str, Path] = {}
    for proposed_id in proposed_status_by_id:
        path = find_node_file(root, proposed_id)
        before_data_by_id[proposed_id] = load_yaml(path)
        path_by_id[proposed_id] = path

    today = str(date.today())

    def build_after_data() -> dict[str, dict[str, Any]]:
        after: dict[str, dict[str, Any]] = {}
        for proposed_id, target_status in proposed_status_by_id.items():
            data = copy.deepcopy(before_data_by_id[proposed_id])
            data["status"] = target_status
            data["updated_at"] = today
            after[proposed_id] = data
        return after

    while True:
        after_data_by_id = build_after_data()
        candidate = _candidate_nodes(nodes, after_data_by_id)
        blocked_ids: list[tuple[str, dict[str, Any]]] = []
        for proposed_id, target_status in sorted(proposed_status_by_id.items()):
            node = nodes[proposed_id]
            if node.type not in {"problem", "option"}:
                continue
            failure = terminal_parent_guard_failure(candidate, proposed_id, target_status, topology=topology)
            if failure:
                blocked_ids.append((proposed_id, failure))
        if not blocked_ids:
            break
        for blocked_id, failure in blocked_ids:
            proposed_status_by_id.pop(blocked_id, None)
            skipped_by_id[blocked_id] = {
                "id": blocked_id,
                "type": nodes[blocked_id].type,
                "status": nodes[blocked_id].status,
                "reason": "blocked_by_active_descendants",
                "path": topology.safe_path(blocked_id),
                "blocking_descendants": failure["blocking_descendants"],
            }

    after_data_by_id = build_after_data()
    candidate = _candidate_nodes(nodes, after_data_by_id)
    validate_cockpit(root, candidate, state.current, state.explicit_edges, raise_on_error=True)

    changes = [
        (path_by_id[node_id], before_data_by_id[node_id], after_data_by_id[node_id])
        for node_id in sorted(after_data_by_id)
        if before_data_by_id[node_id] != after_data_by_id[node_id]
    ]
    updates = [
        {
            "id": changed_id,
            "type": nodes[changed_id].type,
            "before_status": nodes[changed_id].status,
            "after_status": after_data_by_id[changed_id]["status"],
            "path": topology.safe_path(changed_id),
        }
        for changed_id in sorted(after_data_by_id)
        if before_data_by_id[changed_id] != after_data_by_id[changed_id]
    ]
    remaining_active = active_descendant_blockers(candidate, node_id, "parked", topology=topology)
    recommended_commands = []
    if any(item["type"] == "experiment" for item in remaining_active) and not include_experiments:
        recommended_commands.append(
            f"research-cockpit close-branch --root {root} --id {node_id} "
            f"--downstream-status {downstream_status} --include-experiments --dry-run --json --show-diff"
        )
    if not remaining_active:
        recommended_commands.append(
            f"research-cockpit update-status --root {root} --id {node_id} --status <terminal_status> --dry-run --json"
        )

    result: dict[str, Any] = {
        "id": node_id,
        "root_type": root_node.type,
        "downstream_status": downstream_status,
        "include_experiments": include_experiments,
        "dry_run": dry_run,
        "changed": False if dry_run else bool(changes),
        "would_change": bool(changes),
        "updates": updates,
        "skipped": list(skipped_by_id.values()),
        "warnings": warnings,
        "remaining_active_descendants": remaining_active,
        "parent_ready_for_terminal_status": not remaining_active,
        "recommended_commands": recommended_commands,
        "changed_files": [str(path) for path, _, _ in changes],
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        return dry_run_preflight_result(root, result)
    if not changes:
        return result

    finish_mutation(
        root,
        changes,
        interaction={
            "kind": "close_branch",
            "actor": "researcher",
            "node_id": node_id,
            "command": script_command("close_branch.py"),
            "after": {
                "updated_nodes": [item["id"] for item in updates],
                "remaining_active_descendants": [item["id"] for item in remaining_active],
            },
            "extra": {
                "downstream_status": downstream_status,
                "include_experiments": include_experiments,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def compact_close_branch_result(result: dict[str, Any], *, root: Path) -> dict[str, Any]:
    payload = compact_mutation_result(
        result,
        command="close-branch",
        target=str(result["id"]),
        root=root,
        updated=[item["id"] for item in result.get("updates", [])],
    )
    payload.update(
        {
            "parent_ready_for_terminal_status": bool(result.get("parent_ready_for_terminal_status")),
            "skipped": result.get("skipped", []),
            "remaining_active_descendants": result.get("remaining_active_descendants", []),
            "update_count": len(result.get("updates", [])),
            "skipped_count": len(result.get("skipped", [])),
            "remaining_active_count": len(result.get("remaining_active_descendants", [])),
        }
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Close active descendants before retrying a terminal parent status change."
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="Research Cockpit data root.")
    parser.add_argument("--id", dest="node_id", help="Problem or option branch root to prepare for terminal status.")
    parser.add_argument(
        "--downstream-status",
        default="parked",
        choices=sorted(SUPPORTED_DOWNSTREAM_STATUSES),
        help="Status to apply to active downstream problem/option nodes.",
    )
    parser.add_argument(
        "--include-experiments",
        action="store_true",
        help="Also mark planned, queued, and running experiments as cancelled after external work is stopped.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview eligible updates without writing files.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    parser.add_argument("--compact", action="store_true", help="Print terse JSON while retaining lifecycle safety fields.")
    parser.add_argument("--show-diff", action="store_true", help="Include the YAML diff in output.")
    parser.add_argument("--no-build", action="store_true", help="Skip dashboard rebuild after writing changes.")
    args = parser.parse_args()
    if not args.node_id:
        message = "--id is required"
        if args.json:
            emit_json({"ok": False, "error": message, "id": None, "dry_run": args.dry_run, "changed": False})
            raise SystemExit(1)
        parser.error(message)

    try:
        result = close_branch(
            args.root,
            node_id=args.node_id,
            downstream_status=args.downstream_status,
            include_experiments=args.include_experiments,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        if args.json:
            payload: dict[str, Any] = {
                "ok": False,
                "error": str(exc),
                "id": args.node_id,
                "dry_run": args.dry_run,
                "changed": False,
            }
            if isinstance(exc, ValidationError):
                payload["errors"] = exc.errors
            emit_json(payload)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(
            compact_close_branch_result(result, root=args.root) if args.compact else result
        )
        return

    verb = "Would close" if args.dry_run else "Closed"
    safe_print(f"{verb} {len(result['updates'])} downstream node(s) under {args.node_id}")
    for warning in result.get("warnings", []):
        safe_print(f"Warning: {warning}")
    if args.show_diff and result.get("diff"):
        safe_print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        safe_print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
