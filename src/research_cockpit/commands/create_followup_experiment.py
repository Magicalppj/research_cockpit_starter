from __future__ import annotations

import argparse
import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.commands._runtime import compact_mutation_result, dry_run_preflight_result, finish_mutation, load_validated_state, yaml_change_diff
from research_cockpit.model import ResearchNode, ValidationError, derive_focus_fields, load_yaml, script_command, validate_cockpit
from research_cockpit.paths import default_data_root

ROOT = default_data_root()


def create_followup_experiment(
    root: Path,
    *,
    from_experiment: str,
    parent: str,
    node_id: str,
    title: str,
    summary: str = "",
    success_criteria: list[str] | None = None,
    next_action: str | None = None,
    set_focus_to_created: bool = False,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    state = load_validated_state(root)
    if from_experiment not in state.nodes:
        raise ValueError(f"Source experiment does not exist: {from_experiment}")
    if state.nodes[from_experiment].type != "experiment":
        raise ValueError(f"Source node {from_experiment} must be experiment")
    if parent not in state.nodes:
        raise ValueError(f"Parent option does not exist: {parent}")
    if state.nodes[parent].type != "option":
        raise ValueError(f"Parent {parent} must be option")
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
        "status": "planned",
        "parent": parent,
        "summary": summary,
        "derived_from": [from_experiment],
        "success_criteria": criteria,
        "created_at": today,
        "updated_at": today,
    }
    if next_action:
        data["next_actions"] = [next_action]

    candidate = dict(state.nodes)
    candidate[node_id] = ResearchNode.from_dict(data)
    changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any] | None]] = [
        (root / "graph" / "nodes" / f"{node_id}.yaml", None, data)
    ]
    current = copy.deepcopy(state.current)
    before_current = copy.deepcopy(current)
    if set_focus_to_created:
        current.update(derive_focus_fields(candidate, node_id, current))
        current["updated_at"] = today
        changes.append((root / "current_state.yaml", before_current, current))
    validate_cockpit(root, candidate, current, state.explicit_edges, raise_on_error=True)

    result: dict[str, Any] = {
        "node_id": node_id,
        "from_experiment": from_experiment,
        "parent": parent,
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
            "after": {"id": node_id, "parent": parent, "derived_from": [from_experiment]},
            "extra": {"set_focus": set_focus_to_created},
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit create-followup-experiment")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--from", required=True, dest="from_experiment")
    parser.add_argument("--parent", required=True)
    parser.add_argument("--id", required=True, dest="node_id")
    parser.add_argument("--title", required=True)
    parser.add_argument("--summary", default="")
    parser.add_argument("--success-criterion", action="append", dest="success_criteria")
    parser.add_argument("--next-action")
    parser.add_argument("--set-focus", action="store_true", dest="set_focus_to_created")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = create_followup_experiment(
            args.root,
            from_experiment=args.from_experiment,
            parent=args.parent,
            node_id=args.node_id,
            title=args.title,
            summary=args.summary,
            success_criteria=args.success_criteria,
            next_action=args.next_action,
            set_focus_to_created=args.set_focus_to_created,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
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
