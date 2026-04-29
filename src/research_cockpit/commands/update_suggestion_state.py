from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from research_cockpit.paths import default_data_root
from typing import Any

ROOT = default_data_root()

from research_cockpit.model import (
    VALID_SUGGESTION_LIFECYCLE_STATES,
    ValidationError,
    build_action_suggestions,
    build_link_rows,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    save_yaml,
    validate_cockpit,
)
from research_cockpit.commands.build_dashboard import build_dashboard


def _find_suggestion(suggestions: list[dict[str, Any]], suggestion_id: str) -> dict[str, Any]:
    for suggestion in suggestions:
        if suggestion.get("id") == suggestion_id or suggestion.get("key") == suggestion_id:
            return suggestion
    raise ValueError(f"Suggestion does not exist: {suggestion_id}")


def update_suggestion_state(
    root: Path,
    *,
    suggestion_id: str,
    state: str,
    reason: str = "",
    rebuild_dashboard: bool = True,
) -> dict[str, Any]:
    if state not in VALID_SUGGESTION_LIFECYCLE_STATES:
        allowed = ", ".join(sorted(VALID_SUGGESTION_LIFECYCLE_STATES))
        raise ValueError(f"Invalid suggestion state {state!r}; allowed: {allowed}")

    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
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
        save_yaml(root / "current_state.yaml", current)
        if rebuild_dashboard:
            build_dashboard(root)
    return {
        "suggestion": suggestion,
        "state": state,
        "changed": changed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="suggestion_id")
    parser.add_argument("--state", required=True, choices=sorted(VALID_SUGGESTION_LIFECYCLE_STATES))
    parser.add_argument("--reason", default="")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = update_suggestion_state(
            args.root,
            suggestion_id=args.suggestion_id,
            state=args.state,
            reason=args.reason,
            rebuild_dashboard=not args.no_build,
        )
    except (ValidationError, ValueError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    key = result["suggestion"]["key"]
    if result["changed"]:
        print(f"Updated suggestion {key} to {result['state']}")
    else:
        print(f"Suggestion {key} was already {result['state']}")
    if not args.no_build and result["changed"]:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
