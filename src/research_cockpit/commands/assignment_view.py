from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_cockpit.assignment_view import (
    DEFAULT_ASSIGNMENT_PRIORITIES,
    DEFAULT_ASSIGNMENT_STATUSES,
    build_assignment_view,
)
from research_cockpit.model import ValidationError, load_explicit_edges, load_nodes, load_yaml, validate_cockpit
from research_cockpit.paths import default_data_root

ROOT = default_data_root()


def assignment_view_payload(
    root: Path,
    *,
    statuses: list[str] | None = None,
    priorities: list[str] | None = None,
) -> dict:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
    return build_assignment_view(nodes, statuses=statuses, priorities=priorities)


def _print_human(payload: dict) -> None:
    print(f"Assignments: {payload['count']}")
    for row in payload.get("assignments", []):
        owner = row.get("owner") or "unassigned"
        next_action = row.get("next_action") or ""
        print(f"- {row['id']} [{row['status']}/{row.get('priority') or 'none'}] owner={owner}: {next_action}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit assignment-view")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--status", action="append", dest="statuses", help="Filter experiment statuses.")
    parser.add_argument("--priority", action="append", dest="priorities", help="Filter priorities.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        payload = assignment_view_payload(
            args.root,
            statuses=args.statuses or list(DEFAULT_ASSIGNMENT_STATUSES),
            priorities=args.priorities or list(DEFAULT_ASSIGNMENT_PRIORITIES),
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    _print_human(payload)


if __name__ == "__main__":
    main()
