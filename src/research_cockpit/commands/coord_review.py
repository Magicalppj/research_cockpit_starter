from __future__ import annotations

import argparse
from pathlib import Path

from research_cockpit.assignment_leases import AssignmentLeaseError
from research_cockpit.coordinator_reviews import apply_coord_review
from research_cockpit.commands._runtime import emit_json
from research_cockpit.commands._work_lease_cli import (
    emit_work_result,
    handle_role_cli_input_error,
    handle_work_error,
)
from research_cockpit.paths import default_data_root
from research_cockpit.storage import load_yaml
from research_cockpit.types import ValidationError


COORD_REVIEW_SCHEMAS = {
    "assignment_result": {
        "schema_version": "coord_review_v1",
        "operation_id": "op_coord_review_assignment_x",
        "action": "assignment_result",
        "review_assignment_id": "assignment_review_x",
        "review_result_revision": "result-v1-review-x",
        "producer_result_revision": "result-v1-producer-x",
    },
    "promote_artifact": {
        "schema_version": "coord_review_v1",
        "operation_id": "op_coord_promote_artifact_x",
        "action": "promote_artifact",
        "record_id": "record_x",
        "artifact_id": "artifact_x",
        "link_to": ["experiment_x"],
        "promotion_reason": "Preserve this evidence for cross-assignment reuse.",
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit coord review", allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--assignment", dest="assignment_id")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--print-schema", action="store_true")
    parser.add_argument(
        "--action",
        choices=tuple(COORD_REVIEW_SCHEMAS),
        default="assignment_result",
        help="Schema action to print with --print-schema.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    if args.print_schema:
        emit_json(COORD_REVIEW_SCHEMAS[args.action], compact=args.compact)
        return
    raw_plan = {}
    if args.file is None:
        handle_role_cli_input_error(
            args,
            parser,
            ValueError("--file is required unless --print-schema is used"),
            operation="coord review",
            assignment_id=args.assignment_id,
            retry_command=(
                "research-cockpit coord review --print-schema --action assignment_result"
            ),
        )
    if args.action != "assignment_result":
        handle_role_cli_input_error(
            args,
            parser,
            ValueError("--action is only valid with --print-schema"),
            operation="coord review",
            assignment_id=args.assignment_id,
            retry_command=(
                f"research-cockpit coord review --print-schema --action {args.action}"
            ),
        )

    try:
        if not args.file.is_file():
            raise FileNotFoundError(args.file)
        raw_plan = load_yaml(args.file)
        payload = apply_coord_review(
            args.root,
            plan=raw_plan,
            producer_assignment_id=args.assignment_id,
        )
    except AssignmentLeaseError as exc:
        handle_work_error(args, exc)
        raise SystemExit(1) from None
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        handle_role_cli_input_error(
            args,
            parser,
            exc,
            operation="coord review",
            assignment_id=args.assignment_id,
            operation_id=raw_plan.get("operation_id"),
            retry_command=(
                "research-cockpit coord review --print-schema --action assignment_result"
            ),
        )
    emit_work_result(args, payload)


if __name__ == "__main__":
    main()
