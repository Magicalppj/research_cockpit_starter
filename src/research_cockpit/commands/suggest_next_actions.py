from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.model import (
    ValidationError,
    build_link_rows,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
)
from research_cockpit.suggestions import build_action_suggestions


def select_suggestions(
    suggestions: list[dict],
    *,
    kinds: list[str] | None = None,
    limit: int | None = None,
    focus_only: bool = False,
    state: str | None = None,
) -> list[dict]:
    selected = suggestions
    if kinds:
        allowed = set(kinds)
        selected = [item for item in selected if item.get("kind") in allowed]
    if focus_only:
        selected = [item for item in selected if item.get("is_focus_related")]
    if state and state != "all":
        selected = [item for item in selected if item.get("lifecycle_state", "active") == state]
    if limit is not None:
        selected = selected[:limit]
    return selected


def _print_human(suggestions: list[dict]) -> None:
    if not suggestions:
        print("No action suggestions.")
        return
    for suggestion in suggestions:
        print(
            f"[{suggestion.get('priority')}] {suggestion.get('kind')}: "
            f"{suggestion.get('action')} ({suggestion.get('source_node_id')})"
        )
        reason = suggestion.get("reason")
        if reason:
            print(f"  reason: {reason}")
        command = suggestion.get("suggested_command")
        if command:
            print(f"  command: {command}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--kind", action="append", dest="kinds")
    parser.add_argument("--focus-only", action="store_true")
    parser.add_argument("--include-inactive", action="store_true")
    parser.add_argument("--state", choices=["active", "dismissed", "completed", "all"])
    args = parser.parse_args()
    state_filter = args.state or ("all" if args.include_inactive else "active")
    include_inactive = args.include_inactive or state_filter != "active"

    try:
        nodes = load_nodes(args.root)
        current = load_yaml(args.root / "current_state.yaml")
        explicit_edges = load_explicit_edges(args.root)
        validate_cockpit(args.root, nodes, current, explicit_edges, raise_on_error=True)
        link_rows = build_link_rows(args.root, nodes)
        suggestions = build_action_suggestions(
            args.root,
            nodes,
            current,
            link_rows,
            include_inactive=include_inactive,
        )
        selected = select_suggestions(
            suggestions,
            kinds=args.kinds,
            limit=args.limit,
            focus_only=args.focus_only,
            state=state_filter,
        )
    except ValidationError as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.as_json:
        print(json.dumps(selected, indent=2, ensure_ascii=False))
    else:
        _print_human(selected)


if __name__ == "__main__":
    main()
