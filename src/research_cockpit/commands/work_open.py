from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.paths import default_data_root
from research_cockpit.types import ValidationError
from research_cockpit.work_packets import build_work_packet


ROOT = default_data_root()


def work_open_payload(
    root: Path,
    *,
    assignment_id: str,
    since_revision: str | None = None,
) -> dict[str, Any]:
    return build_work_packet(
        root,
        assignment_id,
        since_revision=since_revision,
    )


def _error_payload(
    *,
    code: str,
    assignment_id: str,
    messages: list[str],
) -> dict[str, Any]:
    return {
        "ok": False,
        "assignment_id": assignment_id,
        "error": {
            "code": code,
            "messages": messages,
        },
    }


def _print_human(payload: dict[str, Any]) -> None:
    if payload.get("changed") is False:
        safe_print(f"Assignment {payload['assignment_id']} is unchanged at {payload['revision']}.")
        return
    safe_print(
        f"{payload['assignment_id']}: {payload['status']} / {payload['readiness']} "
        f"({payload['revision']})"
    )
    for action in payload.get("allowed_operations", {}).get("items", []):
        safe_print(f"- {action}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit work open")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--assignment", required=True, dest="assignment_id")
    parser.add_argument("--since", dest="since_revision")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    try:
        payload = work_open_payload(
            args.root,
            assignment_id=args.assignment_id,
            since_revision=args.since_revision,
        )
    except ValidationError as exc:
        error = _error_payload(
            code="assignment_validation_error",
            assignment_id=args.assignment_id,
            messages=list(exc.errors),
        )
        if args.json:
            emit_json(error, compact=args.compact)
        else:
            for message in exc.errors:
                safe_print(message)
        raise SystemExit(1) from None
    except FileNotFoundError as exc:
        error = _error_payload(
            code="assignment_not_found",
            assignment_id=args.assignment_id,
            messages=[str(exc)],
        )
        if args.json:
            emit_json(error, compact=args.compact)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from None

    if args.json:
        emit_json(payload, compact=args.compact)
        return
    _print_human(payload)


if __name__ == "__main__":
    main()
