from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import finish_mutation, load_validated_state
from research_cockpit.commands.record_finding import find_node_file
from research_cockpit.model import ResearchNode, ValidationError, load_yaml, script_command, validate_cockpit


def _validate_current_best_option(nodes: dict[str, ResearchNode], node: ResearchNode, option_id: str) -> None:
    if node.type != "problem":
        raise ValueError(f"--current-best-option can only be used with problem nodes; {node.id} is {node.type}")
    option = nodes.get(option_id)
    if not option:
        raise ValueError(f"Current best option does not exist: {option_id}")
    if option.type != "option":
        raise ValueError(f"Current best option {option_id} must be option, got {option.type}")
    if option.parent != node.id and option_id not in node.children:
        raise ValueError(f"Current best option {option_id} must be a child option of problem {node.id}")


def update_node_fields(
    root: Path,
    *,
    node_id: str,
    current_best_option: str | None = None,
    replace_next_actions: list[str] | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    if current_best_option is None and replace_next_actions is None:
        raise ValueError("At least one field update is required")

    state = load_validated_state(root)
    nodes = state.nodes
    if node_id not in nodes:
        raise ValueError(f"Node does not exist: {node_id}")
    node = nodes[node_id]
    if current_best_option is not None:
        _validate_current_best_option(nodes, node, current_best_option)

    path = find_node_file(root, node_id)
    data = load_yaml(path)
    before = {
        "current_best_option": data.get("current_best_option"),
        "next_actions": list(data.get("next_actions", []) or []),
    }
    if current_best_option is not None:
        data["current_best_option"] = current_best_option
    if replace_next_actions is not None:
        data["next_actions"] = [str(action) for action in replace_next_actions]
    data["updated_at"] = str(date.today())

    candidate = dict(nodes)
    candidate[node_id] = ResearchNode.from_dict(data)
    validate_cockpit(root, candidate, state.current, state.explicit_edges, raise_on_error=True)

    after = {
        "current_best_option": data.get("current_best_option"),
        "next_actions": list(data.get("next_actions", []) or []),
    }
    changed = before != after
    result: dict[str, Any] = {
        "node_id": node_id,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "path": str(path),
        "before": before,
        "after": after,
    }
    if dry_run or not changed:
        return result

    finish_mutation(
        root,
        [(path, data)],
        interaction={
            "kind": "update_node_fields",
            "actor": "researcher",
            "node_id": node_id,
            "command": f"{script_command('update_node_fields.py')} --id {node_id}",
            "before": before,
            "after": after,
            "extra": {
                "current_best_option": current_best_option,
                "replaced_next_actions": replace_next_actions is not None,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="node_id")
    parser.add_argument("--current-best-option")
    parser.add_argument("--replace-next-actions", action="append", dest="replace_next_actions")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = update_node_fields(
            args.root,
            node_id=args.node_id,
            current_best_option=args.current_best_option,
            replace_next_actions=args.replace_next_actions,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.dry_run:
        print(f"Would update node fields for {args.node_id}")
        return
    if result["changed"]:
        print(f"Updated node fields for {args.node_id}: {result['path']}")
        if not args.no_build:
            print(f"Rebuilt dashboards under {args.root / 'dashboards'}")
    else:
        print(f"No node field changes for {args.node_id}")


if __name__ == "__main__":
    main()
