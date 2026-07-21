from __future__ import annotations

import argparse
import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.assignment_scope import AssignmentScopeError, resolve_assignment_scope
from research_cockpit.commands._assignment_scope_cli import add_assignment_scope_args, emit_assignment_scope_error
from research_cockpit.commands._runtime import compact_mutation_result, dry_run_preflight_result, emit_json, finish_mutation, load_validated_state, yaml_change_diff
from research_cockpit.commands.record_finding import find_node_file
from research_cockpit.model import ResearchNode, ValidationError, derive_focus_fields, load_yaml, validate_cockpit
from research_cockpit.paths import default_data_root

ROOT = default_data_root()

TERMINAL_STATUSES = {
    "accepted",
    "archived",
    "cancelled",
    "deprecated",
    "done",
    "failed",
    "parked",
    "rejected",
    "resolved",
    "superseded",
}


def _actions(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _workstream_guidance(
    root: Path,
    node_id: str,
    *,
    assignment_id: str | None = None,
) -> dict[str, str]:
    commands = {
        "plan_graph": (
            f"research-cockpit coord assign --root {root} "
            "--file <coord_assign.yaml> --json --compact"
        ),
        "inspect_node": (
            f"research-cockpit context --root {root} --id {node_id} "
            "--view execution --json --compact"
        ),
    }
    if assignment_id:
        commands["close_assignment"] = (
            f"research-cockpit work close --root {root} "
            f"--assignment {assignment_id} --file <closeout.yaml> --json --compact"
        )
    return commands


def _base_result(
    root: Path,
    *,
    node: ResearchNode,
    actions: list[str],
    dry_run: bool,
    assignment_id: str | None = None,
) -> dict[str, Any]:
    return {
        "node_id": node.id,
        "node_type": node.type,
        "node_status": node.status,
        "dry_run": dry_run,
        "changed": False,
        "would_change": False,
        "moved_next_actions": actions,
        "action_count": len(actions),
        "created_nodes": [],
        "updated_nodes": [],
        "changed_files": [],
        "recommended_commands": _workstream_guidance(root, node.id, assignment_id=assignment_id),
    }


def _guidance_result(
    root: Path,
    *,
    node: ResearchNode,
    actions: list[str],
    dry_run: bool,
    strategy: str,
    guidance: str,
    assignment_id: str | None = None,
) -> dict[str, Any]:
    result = _base_result(root, node=node, actions=actions, dry_run=dry_run, assignment_id=assignment_id)
    result.update({
        "strategy": strategy,
        "guidance": guidance,
    })
    if dry_run:
        return dry_run_preflight_result(root, result)
    return result


def migrate_terminal_next_actions(
    root: Path,
    *,
    node_id: str,
    followup_id: str | None = None,
    title: str | None = None,
    parent: str | None = None,
    priority: str | None = None,
    set_focus_to_created: bool = False,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    assignment_id: str | None = None,
    coordinator: bool = False,
) -> dict[str, Any]:
    state = load_validated_state(root)
    scope = resolve_assignment_scope(root, state.nodes, assignment_id=assignment_id, coordinator=coordinator)
    scope.check_nodes(state.nodes, [node_id])
    if node_id not in state.nodes:
        raise ValueError(f"Node does not exist: {node_id}")
    if scope.active and set_focus_to_created:
        scope.forbid_set_focus(node_id)
    if parent is not None:
        scope.check_nodes(state.nodes, [parent])
    node = state.nodes[node_id]
    if node.status not in TERMINAL_STATUSES:
        raise ValueError(f"Node {node_id} is not terminal: {node.status}")

    actions = _actions(node.raw.get("next_actions"))
    if not actions:
        return _guidance_result(
            root,
            node=node,
            actions=[],
            dry_run=dry_run,
            strategy="no_terminal_next_actions",
            guidance="No node-local next_actions to migrate.",
            assignment_id=scope.assignment_id,
        )

    if len(actions) != 1 or node.type != "experiment" or node.status != "done":
        return _guidance_result(
            root,
            node=node,
            actions=actions,
            dry_run=dry_run,
            strategy="coord_assign_guidance",
            guidance=(
                "Use a coordinator graph plan for multi-step work, non-experiment "
                "nodes, or terminal experiments that are not done."
            ),
            assignment_id=scope.assignment_id,
        )

    if bool(followup_id) != bool(title):
        raise ValueError("--followup-id and --title must be provided together")
    if not followup_id:
        result = _guidance_result(
            root,
            node=node,
            actions=actions,
            dry_run=dry_run,
            strategy="single_followup_experiment",
            guidance="Provide --followup-id and --title to create a queued follow-up experiment.",
            assignment_id=scope.assignment_id,
        )
        result["recommended_commands"]["migrate_single_followup"] = (
            f"research-cockpit maintenance migrate --root {root} "
            "--file <terminal_next_actions.yaml> --json --compact"
        )
        return result

    if followup_id in state.nodes:
        raise FileExistsError(root / "graph" / "nodes" / f"{followup_id}.yaml")
    resolved_parent = parent or node.raw.get("parent")
    scope.check_nodes(state.nodes, [resolved_parent])
    if not resolved_parent or resolved_parent not in state.nodes:
        raise ValueError(f"Parent option does not exist: {resolved_parent}")
    if state.nodes[str(resolved_parent)].type != "option":
        raise ValueError(f"Parent {resolved_parent} must be option")

    today = str(date.today())
    source_path = find_node_file(root, node.id)
    source_data = load_yaml(source_path)
    before_source = copy.deepcopy(source_data)
    source_data.pop("next_actions", None)
    source_data["updated_at"] = today

    default_criterion = f"Validate follow-up against {node.id}."
    followup_data: dict[str, Any] = {
        "id": followup_id,
        "type": "experiment",
        "title": title,
        "status": "queued",
        "parent": str(resolved_parent),
        "derived_from": [node.id],
        "success_criteria": [default_criterion],
        "next_actions": actions,
        "created_at": today,
        "updated_at": today,
    }
    if priority:
        followup_data["priority"] = priority

    candidate = dict(state.nodes)
    candidate[node.id] = ResearchNode.from_dict(source_data)
    candidate[followup_id] = ResearchNode.from_dict(followup_data)
    scope.check_nodes(candidate, [node.id, resolved_parent, followup_id])
    current = copy.deepcopy(state.current)
    changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any] | None]] = [
        (source_path, before_source, source_data),
        (root / "graph" / "nodes" / f"{followup_id}.yaml", None, followup_data),
    ]
    updated_nodes = [node.id]
    if set_focus_to_created:
        before_current = copy.deepcopy(current)
        current.update(derive_focus_fields(candidate, followup_id, current))
        current["next_actions"] = actions
        current["updated_at"] = today
        changes.append((root / "current_state.yaml", before_current, current))
        updated_nodes.append("current_state")

    validate_cockpit(root, candidate, current, state.explicit_edges, raise_on_error=True)

    result: dict[str, Any] = {
        "node_id": node.id,
        "node_type": node.type,
        "node_status": node.status,
        "strategy": "single_followup_experiment",
        "dry_run": dry_run,
        "changed": False if dry_run else True,
        "would_change": True,
        "moved_next_actions": actions,
        "action_count": len(actions),
        "created_nodes": [followup_id],
        "updated_nodes": updated_nodes,
        "changed_files": [str(path) for path, _before, _after in changes],
        "after": {
            "source": {"id": node.id, "next_actions": []},
            "followup": followup_data,
        },
        "recommended_commands": _workstream_guidance(root, node.id, assignment_id=scope.assignment_id),
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        return dry_run_preflight_result(root, result)

    finish_mutation(
        root,
        changes,
        interaction={
            "kind": "migrate_terminal_next_actions",
            "actor": "researcher",
            "node_id": node.id,
            "command": "research-cockpit maintenance migrate",
            "before": {"next_actions": actions},
            "after": {"followup_id": followup_id, "source_next_actions": []},
            "extra": {"set_focus": set_focus_to_created},
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit migrate-terminal-next-actions")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="node_id")
    parser.add_argument("--followup-id")
    parser.add_argument("--title")
    parser.add_argument("--parent")
    parser.add_argument("--priority")
    parser.add_argument("--set-focus", action="store_true", dest="set_focus_to_created")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    add_assignment_scope_args(parser)
    args = parser.parse_args()

    try:
        result = migrate_terminal_next_actions(
            args.root,
            node_id=args.node_id,
            followup_id=args.followup_id,
            title=args.title,
            parent=args.parent,
            priority=args.priority,
            set_focus_to_created=args.set_focus_to_created,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
            assignment_id=args.assignment,
            coordinator=args.coordinator,
        )
    except AssignmentScopeError as exc:
        emit_assignment_scope_error(args, exc)
        raise SystemExit(1) from exc
    except (ValidationError, ValueError, FileExistsError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        payload = compact_mutation_result(
            result,
            command="migrate-terminal-next-actions",
            target=args.node_id,
            root=args.root,
            created=result.get("created_nodes", []),
            updated=result.get("updated_nodes", []),
        ) if args.compact else result
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if result.get("would_change"):
        verb = "Would migrate" if args.dry_run else "Migrated"
        print(f"{verb} next_actions from {args.node_id}")
    else:
        print(result.get("guidance") or "No terminal next_actions migrated.")


if __name__ == "__main__":
    main()
