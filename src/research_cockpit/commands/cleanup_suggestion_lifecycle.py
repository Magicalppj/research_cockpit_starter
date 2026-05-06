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
    ValidationError,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    script_command,
    validate_cockpit,
)
from research_cockpit.resources import build_link_rows
from research_cockpit.suggestions import build_action_suggestions, build_suggestion_lifecycle_rows
from research_cockpit.commands._runtime import dry_run_preflight_result, finish_mutation, yaml_change_diff


VALID_CLEANUP_STATES = {"dismissed", "completed", "all"}


def _matches_state(row: dict[str, Any], state: str) -> bool:
    return state == "all" or row.get("state") == state


def _matches_age(row: dict[str, Any], older_than_days: int | None) -> bool:
    if older_than_days is None:
        return True
    age_days = row.get("age_days")
    return isinstance(age_days, int) and age_days >= older_than_days


def cleanup_suggestion_lifecycle(
    root: Path,
    *,
    state: str = "all",
    older_than_days: int | None = None,
    dry_run: bool = False,
    rebuild_dashboard: bool = True,
    show_diff: bool = False,
) -> dict[str, Any]:
    if state not in VALID_CLEANUP_STATES:
        allowed = ", ".join(sorted(VALID_CLEANUP_STATES))
        raise ValueError(f"Invalid lifecycle cleanup state {state!r}; allowed: {allowed}")
    if older_than_days is not None and older_than_days < 0:
        raise ValueError("--older-than-days must be >= 0")

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
    rows = build_suggestion_lifecycle_rows(current, suggestions)
    candidates = [
        row
        for row in rows
        if row.get("orphan") and _matches_state(row, state) and _matches_age(row, older_than_days)
    ]

    changed = False
    removed_count = 0
    if candidates:
        lifecycle = dict(current.get("suggestion_lifecycle") or {})
        for row in candidates:
            if lifecycle.pop(str(row["key"]), None) is not None:
                removed_count += 1
        if removed_count:
            if lifecycle:
                current["suggestion_lifecycle"] = lifecycle
            else:
                current.pop("suggestion_lifecycle", None)
            current["updated_at"] = str(date.today())
            validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
            changed = True
            if not dry_run:
                finish_mutation(
                    root,
                    [(root / "current_state.yaml", before_current, current)],
                    interaction={
                        "kind": "cleanup_suggestion_lifecycle",
                        "actor": "researcher",
                        "command": f"{script_command('cleanup_suggestion_lifecycle.py')} --state {state}",
                        "before": {
                            "candidate_count": len(candidates),
                        },
                        "after": {
                            "removed_count": removed_count,
                        },
                        "extra": {
                            "state": state,
                            "older_than_days": older_than_days,
                        },
                    },
                    rebuild_dashboard=rebuild_dashboard,
                )

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "state": state,
        "older_than_days": older_than_days,
        "candidate_count": len(candidates),
        "removed_count": 0 if dry_run else removed_count,
        "would_remove_count": removed_count,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "candidates": candidates,
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(root / "current_state.yaml", before_current, current)])
    if dry_run:
        return dry_run_preflight_result(root, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--state", choices=sorted(VALID_CLEANUP_STATES), default="all")
    parser.add_argument("--older-than-days", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = cleanup_suggestion_lifecycle(
            args.root,
            state=args.state,
            older_than_days=args.older_than_days,
            dry_run=args.dry_run,
            rebuild_dashboard=not args.no_build,
            show_diff=args.show_diff,
        )
    except (ValidationError, ValueError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if result["candidate_count"] == 0:
        print("No orphan suggestion lifecycle records matched cleanup filters.")
    elif args.dry_run:
        print(f"Would remove {result['candidate_count']} orphan suggestion lifecycle record(s).")
    else:
        print(f"Removed {result['removed_count']} orphan suggestion lifecycle record(s).")
        if not args.no_build and result["changed"]:
            print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
