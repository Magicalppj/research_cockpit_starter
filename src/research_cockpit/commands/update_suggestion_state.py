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
    VALID_SUGGESTION_LIFECYCLE_STATES,
    ValidationError,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    script_command,
    validate_cockpit,
)
from research_cockpit.resources import build_link_rows
from research_cockpit.suggestions import build_action_suggestions
from research_cockpit.commands._runtime import finish_mutation, yaml_change_diff


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


def update_suggestion_state(
    root: Path,
    *,
    suggestion_id: str,
    state: str,
    reason: str = "",
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    if state not in VALID_SUGGESTION_LIFECYCLE_STATES:
        allowed = ", ".join(sorted(VALID_SUGGESTION_LIFECYCLE_STATES))
        raise ValueError(f"Invalid suggestion state {state!r}; allowed: {allowed}")

    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    before_current = copy.deepcopy(current)
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)

    suggestions = build_action_suggestions(
        root,
        nodes,
        current,
        build_link_rows(root, nodes),
        include_inactive=True,
    )
    suggestion = _find_suggestion(suggestions, suggestion_id)
    key = str(suggestion["key"])
    lifecycle = dict(current.get("suggestion_lifecycle") or {})

    if state == "active":
        changed = key in lifecycle
        lifecycle.pop(key, None)
    else:
        record = {
            "state": state,
            "reason": reason,
            "updated_at": str(date.today()),
            "action": str(suggestion.get("action") or ""),
            "kind": str(suggestion.get("kind") or ""),
            "source_node_id": str(suggestion.get("source_node_id") or ""),
        }
        changed = lifecycle.get(key) != record
        lifecycle[key] = record

    if changed:
        if lifecycle:
            current["suggestion_lifecycle"] = lifecycle
        else:
            current.pop("suggestion_lifecycle", None)
        current["updated_at"] = str(date.today())
        validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
        if not dry_run:
            finish_mutation(
                root,
                [(root / "current_state.yaml", current)],
                interaction={
                    "kind": "update_suggestion_state",
                    "actor": "researcher",
                    "command": f"{script_command('update_suggestion_state.py')} --id {suggestion_id} --state {state}",
                    "before": {
                        "suggestion_id": suggestion_id,
                        "state": suggestion.get("lifecycle_state"),
                    },
                    "after": {
                        "suggestion_id": suggestion_id,
                        "state": state,
                    },
                    "extra": {
                        "key": key,
                        "reason": reason,
                        "changed": changed,
                    },
                },
                rebuild_dashboard=rebuild_dashboard,
            )
    result: dict[str, Any] = {
        "suggestion": suggestion,
        "state": state,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "path": str(root / "current_state.yaml"),
        "before": {
            "suggestion_lifecycle": before_current.get("suggestion_lifecycle"),
        },
        "after": {
            "suggestion_lifecycle": current.get("suggestion_lifecycle"),
        },
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(root / "current_state.yaml", before_current, current)])
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="suggestion_id")
    parser.add_argument("--state", required=True, choices=sorted(VALID_SUGGESTION_LIFECYCLE_STATES))
    parser.add_argument("--reason", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = update_suggestion_state(
            args.root,
            suggestion_id=args.suggestion_id,
            state=args.state,
            reason=args.reason,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValidationError, ValueError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    key = result["suggestion"]["key"]
    if args.dry_run:
        if result["would_change"]:
            print(f"Would update suggestion {key} to {result['state']}")
        else:
            print(f"Suggestion {key} was already {result['state']}")
        if args.show_diff and result.get("diff"):
            print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
        return
    if result["changed"]:
        print(f"Updated suggestion {key} to {result['state']}")
    else:
        print(f"Suggestion {key} was already {result['state']}")
    if not args.no_build and result["changed"]:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
