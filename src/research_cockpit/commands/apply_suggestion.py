from __future__ import annotations

import argparse
import copy
import json
from datetime import date
from pathlib import Path

from research_cockpit.paths import default_data_root
from typing import Any

ROOT = default_data_root()

from research_cockpit.model import (
    ResearchNode,
    ValidationError,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    script_command,
    validate_cockpit,
)
from research_cockpit.resources import build_link_rows
from research_cockpit.suggestions import build_action_suggestions
from research_cockpit.commands._runtime import dry_run_preflight_result, finish_mutation
from research_cockpit.commands.record_finding import find_node_file


VALID_TARGETS = {"current", "node"}


def _find_suggestion(suggestions: list[dict[str, Any]], suggestion_id: str) -> dict[str, Any]:
    for suggestion in suggestions:
        if suggestion_id in {
            suggestion.get("id"),
            suggestion.get("key"),
            suggestion.get("suggestion_id"),
            suggestion.get("display_id"),
        }:
            return suggestion
    raise ValueError(f"Suggestion does not exist: {suggestion_id}")


def _append_action(data: dict[str, Any], action: str, owner: str) -> bool:
    actions = data.get("next_actions", []) or []
    if not isinstance(actions, list):
        raise ValueError(f"{owner}: next_actions must be a list")
    if action in actions:
        data["next_actions"] = actions
        return False
    actions.append(action)
    data["next_actions"] = actions
    data["updated_at"] = str(date.today())
    return True


def apply_suggestion(
    root: Path,
    *,
    suggestion_id: str,
    target: str = "current",
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    if target not in VALID_TARGETS:
        allowed = ", ".join(sorted(VALID_TARGETS))
        raise ValueError(f"Invalid target {target!r}; allowed: {allowed}")

    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
    suggestions = build_action_suggestions(root, nodes, current, build_link_rows(root, nodes))
    suggestion = _find_suggestion(suggestions, suggestion_id)
    action = str(suggestion.get("action") or "")
    source_node_id = str(suggestion.get("source_node_id") or "")

    if target == "current":
        target_path = root / "current_state.yaml"
        target_data = current
        before_target_data = copy.deepcopy(current)
        before_actions = list(current.get("next_actions", []) or [])
        changed = _append_action(current, action, "current_state")
        validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
        after_actions = list(current.get("next_actions", []) or [])
    else:
        node_id = source_node_id
        target_path = find_node_file(root, node_id)
        node_data = load_yaml(target_path)
        target_data = node_data
        before_target_data = copy.deepcopy(node_data)
        before_actions = list(node_data.get("next_actions", []) or [])
        changed = _append_action(node_data, action, node_id)
        candidate = dict(nodes)
        candidate[node_id] = ResearchNode.from_dict(node_data)
        validate_cockpit(root, candidate, current, explicit_edges, raise_on_error=True)
        after_actions = list(node_data.get("next_actions", []) or [])

    result = {
        "suggestion": suggestion,
        "suggestion_id": suggestion_id,
        "target": target,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "source_node_id": source_node_id,
        "action": action,
        "before": {"target": target, "next_actions": before_actions},
        "after": {"target": target, "next_actions": after_actions},
    }
    if dry_run:
        return dry_run_preflight_result(root, result)

    finish_mutation(
        root,
        [(target_path, before_target_data, target_data)],
        interaction={
            "kind": "apply_suggestion",
            "actor": "researcher",
            "node_id": source_node_id or None,
            "command": f"{script_command('apply_suggestion.py')} --id {suggestion_id} --target {target}",
            "before": {"target": target, "next_actions": before_actions},
            "after": {"target": target, "next_actions": after_actions},
            "extra": {
                "suggestion_id": suggestion_id,
                "target": target,
                "changed": changed,
                "source_node_id": source_node_id,
                "action": action,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="suggestion_id")
    parser.add_argument("--target", choices=sorted(VALID_TARGETS), default="current")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = apply_suggestion(
            args.root,
            suggestion_id=args.suggestion_id,
            target=args.target,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
        )
    except (ValidationError, ValueError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    action = result["suggestion"]["action"]
    if args.dry_run:
        if result["would_change"]:
            print(f"Would queue suggestion {args.suggestion_id} to {args.target}: {action}")
        else:
            print(f"Suggestion {args.suggestion_id} is already queued in {args.target}: {action}")
        return

    if result["changed"]:
        print(f"Queued suggestion {args.suggestion_id} to {args.target}: {action}")
    else:
        print(f"Suggestion {args.suggestion_id} is already queued in {args.target}: {action}")
    if not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
