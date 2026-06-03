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
from research_cockpit.model import ResearchNode, ValidationError, derive_focus_fields, load_yaml, script_command, validate_cockpit
from research_cockpit.paths import default_data_root

ROOT = default_data_root()


def create_followup_experiment(
    root: Path,
    *,
    from_experiment: str,
    parent: str | None = None,
    node_id: str,
    title: str,
    summary: str = "",
    priority: str | None = None,
    success_criteria: list[str] | None = None,
    next_action: str | None = None,
    set_focus_to_created: bool = False,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    assignment_id: str | None = None,
    coordinator: bool = False,
) -> dict[str, Any]:
    state = load_validated_state(root)
    scope = resolve_assignment_scope(root, state.nodes, assignment_id=assignment_id, coordinator=coordinator)
    scope.check_nodes(state.nodes, [from_experiment])
    if from_experiment not in state.nodes:
        raise ValueError(f"Source experiment does not exist: {from_experiment}")
    if set_focus_to_created:
        scope.forbid_set_focus(from_experiment)
    if parent is not None:
        scope.check_nodes(state.nodes, [parent])
    source = state.nodes[from_experiment]
    if source.type != "experiment":
        raise ValueError(f"Source node {from_experiment} must be experiment")
    if source.status not in {"done", "running"}:
        raise ValueError(f"Source experiment {from_experiment} must be done or running")
    resolved_parent = parent or source.raw.get("parent")
    scope.check_nodes(state.nodes, [resolved_parent])
    if not resolved_parent or resolved_parent not in state.nodes:
        raise ValueError(f"Parent option does not exist: {resolved_parent}")
    if state.nodes[str(resolved_parent)].type != "option":
        raise ValueError(f"Parent {resolved_parent} must be option")
    if node_id in state.nodes:
        raise FileExistsError(root / "graph" / "nodes" / f"{node_id}.yaml")

    today = str(date.today())
    criteria = list(success_criteria or [])
    default_criterion = f"Validate follow-up against {from_experiment}."
    if default_criterion not in criteria:
        criteria.insert(0, default_criterion)
    data: dict[str, Any] = {
        "id": node_id,
        "type": "experiment",
        "title": title,
        "status": "queued",
        "parent": str(resolved_parent),
        "summary": summary,
        "derived_from": [from_experiment],
        "success_criteria": criteria,
        "created_at": today,
        "updated_at": today,
    }
    if priority:
        data["priority"] = priority
    if next_action:
        data["next_actions"] = [next_action]

    candidate = dict(state.nodes)
    candidate[node_id] = ResearchNode.from_dict(data)
    scope.check_nodes(candidate, [node_id])
    changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any] | None]] = [
        (root / "graph" / "nodes" / f"{node_id}.yaml", None, data)
    ]
    current = copy.deepcopy(state.current)
    before_current = copy.deepcopy(current)
    if set_focus_to_created:
        current.update(derive_focus_fields(candidate, node_id, current))
        if next_action:
            current["next_actions"] = [next_action]
        current["updated_at"] = today
        changes.append((root / "current_state.yaml", before_current, current))
    validate_cockpit(root, candidate, current, state.explicit_edges, raise_on_error=True)

    result: dict[str, Any] = {
        "node_id": node_id,
        "from_experiment": from_experiment,
        "parent": str(resolved_parent),
        "dry_run": dry_run,
        "changed": False if dry_run else True,
        "would_change": True,
        "path": str(changes[0][0]),
        "created_nodes": [node_id],
        "updated_nodes": ["current_state"] if set_focus_to_created else [],
        "after": data,
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        return dry_run_preflight_result(root, result)

    finish_mutation(
        root,
        changes,
        interaction={
            "kind": "create_followup_experiment",
            "actor": "researcher",
            "node_id": node_id,
            "command": f"{script_command('create_followup_experiment.py')} --from {from_experiment} --id {node_id}",
            "after": {"id": node_id, "parent": str(resolved_parent), "derived_from": [from_experiment]},
            "extra": {"set_focus": set_focus_to_created},
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit create-followup-experiment")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--from", required=True, dest="from_experiment")
    parser.add_argument("--parent")
    parser.add_argument("--id", required=True, dest="node_id")
    parser.add_argument("--title", required=True)
    parser.add_argument("--summary", default="")
    parser.add_argument("--priority")
    parser.add_argument("--success-criterion", action="append", dest="success_criteria")
    parser.add_argument(
        "--next-action",
        help="Initial node-local next action; with --set-focus it also becomes current_state next_actions.",
    )
    parser.add_argument(
        "--set-focus",
        action="store_true",
        dest="set_focus_to_created",
        help="Move global focus to the new experiment.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    add_assignment_scope_args(parser)
    args = parser.parse_args()

    try:
        result = create_followup_experiment(
            args.root,
            from_experiment=args.from_experiment,
            parent=args.parent,
            node_id=args.node_id,
            title=args.title,
            summary=args.summary,
            priority=args.priority,
            success_criteria=args.success_criteria,
            next_action=args.next_action,
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
            command="create-followup-experiment",
            target=args.node_id,
            root=args.root,
            created=[args.node_id],
            updated=result.get("updated_nodes", []),
        ) if args.compact else result
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    verb = "Would create" if args.dry_run else "Created"
    print(f"{verb} follow-up experiment {args.node_id}")


if __name__ == "__main__":
    main()
