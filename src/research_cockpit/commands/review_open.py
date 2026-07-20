from __future__ import annotations

import argparse
from pathlib import Path

from research_cockpit.assignment_reviews import open_review_assignment
from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.paths import default_data_root
from research_cockpit.types import ValidationError


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit review open", allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--assignment", required=True, dest="assignment_id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    try:
        payload = open_review_assignment(args.root, assignment_id=args.assignment_id)
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    if args.json:
        emit_json(payload, compact=args.compact)
    else:
        safe_print(
            f"review open: {args.assignment_id} "
            f"({payload['producer']['result_revision']})"
        )


if __name__ == "__main__":
    main()
