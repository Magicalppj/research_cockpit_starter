from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.milestone_handoffs import execute_milestone_handoff
from research_cockpit.paths import default_data_root
from research_cockpit.storage import load_yaml


HANDOFF_SCHEMA_EXAMPLE = {
    "schema_version": "coord_handoff_v1",
    "operation_id": "handoff_release_001",
    "kind": "release",
    "summary": "Release candidate handoff",
    "strict_lifecycle": True,
    "allow": {
        "pending_reviews": False,
        "stale_inputs": False,
        "active_leases": False,
        "unresolved_blockers": False,
    },
}


def _print_human(payload: dict) -> None:
    state = "OK" if payload.get("ok") else str(payload.get("status") or "failed").upper()
    safe_print(f"Milestone handoff: {state}")
    safe_print(f"Target revision: {payload.get('target_revision') or 'not captured'}")
    if payload.get("report_file"):
        safe_print(f"Report: {payload['report_file']}")
    error = payload.get("error")
    if isinstance(error, dict) and error.get("message"):
        safe_print(str(error["message"]))


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit coord handoff", allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--file", type=Path)
    parser.add_argument("--print-schema", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()

    if args.print_schema:
        safe_print(yaml.safe_dump(HANDOFF_SCHEMA_EXAMPLE, sort_keys=False).rstrip())
        return
    if args.file is None:
        parser.error("--file is required unless --print-schema is used")
    if not args.file.is_file():
        parser.error(f"handoff input does not exist: {args.file}")
    try:
        payload = execute_milestone_handoff(args.root, load_yaml(args.file))
    except ValueError as exc:
        if args.json:
            emit_json(
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_handoff_input",
                        "message": str(exc),
                    },
                },
                compact=args.compact,
            )
            raise SystemExit(2) from None
        parser.error(str(exc))

    if args.json:
        emit_json(payload, compact=args.compact)
    else:
        _print_human(payload)
    if payload.get("ok"):
        return
    raise SystemExit(1)


if __name__ == "__main__":
    main()
