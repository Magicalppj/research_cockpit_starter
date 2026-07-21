from __future__ import annotations

import argparse
from pathlib import Path

from research_cockpit.assignment_leases import AssignmentLeaseError
from research_cockpit.assignment_reviews import report_assignment_review
from research_cockpit.commands._work_lease_cli import (
    emit_work_result,
    handle_role_cli_input_error,
    handle_work_error,
)
from research_cockpit.paths import default_data_root
from research_cockpit.storage import load_yaml
from research_cockpit.types import ValidationError


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit review report", allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--assignment", required=True, dest="assignment_id")
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    plan = {}

    try:
        if not args.file.is_file():
            raise FileNotFoundError(args.file)
        plan = load_yaml(args.file)
        payload = report_assignment_review(
            args.root,
            assignment_id=args.assignment_id,
            plan=plan,
        )
    except AssignmentLeaseError as exc:
        handle_work_error(args, exc)
        raise SystemExit(1) from None
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        handle_role_cli_input_error(
            args,
            parser,
            exc,
            operation="review report",
            assignment_id=args.assignment_id,
            operation_id=plan.get("operation_id"),
            input_revision=plan.get("input_revision"),
            retry_command=(
                "research-cockpit commands --role reviewer --name report --json --compact"
            ),
        )
    emit_work_result(args, payload)


if __name__ == "__main__":
    main()
