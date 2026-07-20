from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.coordination import build_coordination_snapshot
from research_cockpit.paths import default_data_root


ROOT = default_data_root()
_ASSIGNMENT_STATUSES = ("queued", "active", "blocked", "completed", "cancelled", "retired")
_ASSIGNMENT_KINDS = ("experiment", "review", "synthesis", "coordination", "maintenance")
_REVIEW_STATUSES = ("not_required", "pending", "approved", "changes_requested")


def coord_overview_payload(
    root: Path,
    *,
    limit: int = 20,
    page: str | None = None,
    since_revision: str | None = None,
    statuses: set[str] | None = None,
    kinds: set[str] | None = None,
    agent_id: str | None = None,
    root_node: str | None = None,
    review_status: str | None = None,
) -> dict[str, Any]:
    return build_coordination_snapshot(
        root,
        limit=limit,
        page=page,
        since_revision=since_revision,
        statuses=statuses,
        kinds=kinds,
        agent_id=agent_id,
        root_node=root_node,
        review_status=review_status,
    )


def _print_human(payload: dict[str, Any]) -> None:
    if payload.get("changed") is False:
        safe_print(f"Coordination state is unchanged at {payload['revision']}.")
        return
    counts = payload["counts"]
    safe_print(
        "Coordination: "
        f"{counts['ready']} ready, {counts['waiting']} waiting, "
        f"{counts['blocked']} blocked, {counts['stale_inputs']} stale"
    )
    for row in payload["assignments"]["items"]:
        safe_print(
            f"- {row['assignment_id']}: {row['status']} / {row['readiness']} "
            f"({row['agent_id'] or 'unassigned'})"
        )
    if payload.get("next_page"):
        safe_print("More assignments are available; use --page with the returned JSON token.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit coord overview")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--status", action="append", choices=_ASSIGNMENT_STATUSES, dest="statuses")
    parser.add_argument("--kind", action="append", choices=_ASSIGNMENT_KINDS, dest="kinds")
    parser.add_argument("--agent", dest="agent_id")
    parser.add_argument("--root-node")
    parser.add_argument("--review-status", choices=_REVIEW_STATUSES)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--page")
    parser.add_argument("--since", dest="since_revision")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    try:
        payload = coord_overview_payload(
            args.root,
            limit=args.limit,
            page=args.page,
            since_revision=args.since_revision,
            statuses=set(args.statuses or []),
            kinds=set(args.kinds or []),
            agent_id=args.agent_id,
            root_node=args.root_node,
            review_status=args.review_status,
        )
    except ValueError as exc:
        if args.json:
            emit_json(
                {
                    "ok": False,
                    "error": {"code": "invalid_coordination_query", "message": str(exc)},
                },
                compact=args.compact,
            )
        else:
            safe_print(str(exc))
        raise SystemExit(2) from None

    if args.json:
        emit_json(payload, compact=args.compact)
        return
    _print_human(payload)


if __name__ == "__main__":
    main()
