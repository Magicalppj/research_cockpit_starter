from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import finish_mutation, load_validated_state, yaml_change_diff
from research_cockpit.graph_core import unique_strings
from research_cockpit.model import ValidationError, load_yaml, script_command, validate_cockpit


VALID_MODES = {"replace", "append"}


def _actions(value: Any, owner: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{owner}: next_actions must be a list")
    return [str(item) for item in value if str(item).strip()]


def sync_focus_actions(
    root: Path,
    *,
    from_node: str,
    mode: str = "replace",
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    if mode not in VALID_MODES:
        allowed = ", ".join(sorted(VALID_MODES))
        raise ValueError(f"Invalid mode {mode!r}; allowed: {allowed}")

    state = load_validated_state(root)
    if from_node not in state.nodes:
        raise ValueError(f"Node does not exist: {from_node}")
    source_actions = _actions(state.nodes[from_node].raw.get("next_actions"), from_node)
    current_path = root / "current_state.yaml"
    current = load_yaml(current_path)
    before_data = dict(current)
    before_actions = _actions(current.get("next_actions"), "current_state")
    if mode == "replace":
        after_actions = unique_strings(source_actions)
    else:
        after_actions = unique_strings([*before_actions, *source_actions])
    current["next_actions"] = after_actions
    current["updated_at"] = str(date.today())
    validate_cockpit(root, state.nodes, current, state.explicit_edges, raise_on_error=True)

    changed = before_actions != after_actions
    result: dict[str, Any] = {
        "from_node": from_node,
        "mode": mode,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "path": str(current_path),
        "before": {"next_actions": before_actions},
        "after": {"next_actions": after_actions},
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(current_path, before_data, current)]) if changed else ""
    if dry_run or not changed:
        return result

    finish_mutation(
        root,
        [(current_path, current)],
        interaction={
            "kind": "sync_focus_actions",
            "actor": "researcher",
            "node_id": from_node,
            "command": f"{script_command('sync_focus_actions.py')} --from-node {from_node} --mode {mode}",
            "before": result["before"],
            "after": result["after"],
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--from-node", required=True)
    parser.add_argument("--mode", choices=sorted(VALID_MODES), default="replace")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = sync_focus_actions(
            args.root,
            from_node=args.from_node,
            mode=args.mode,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    verb = "Would sync" if args.dry_run else "Synced"
    print(f"{verb} current_state next_actions from {args.from_node}")
    if args.show_diff and result.get("diff"):
        print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
