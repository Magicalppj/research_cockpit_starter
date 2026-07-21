from __future__ import annotations

import argparse
from pathlib import Path

from research_cockpit.assignment_leases import AssignmentLeaseError
from research_cockpit.commands._runtime import emit_json
from research_cockpit.commands._work_lease_cli import (
    emit_work_result,
    handle_role_cli_input_error,
    handle_work_error,
)
from research_cockpit.coordinator_decisions import apply_coord_decision
from research_cockpit.paths import default_data_root
from research_cockpit.storage import load_yaml
from research_cockpit.types import ValidationError


COORD_DECIDE_SCHEMAS = {
    "promote": {
        "schema_version": "coord_decide_v1",
        "operation_id": "op_decide_promote_x",
        "action": "promote",
        "parameters": {
            "decision_id": "decision_x",
            "option_id": "option_x",
            "title": "Adopt option X",
            "summary": "Reviewed evidence supports option X.",
        },
    },
    "refresh_evidence": {
        "schema_version": "coord_decide_v1",
        "operation_id": "op_decide_refresh_x",
        "action": "refresh_evidence",
        "parameters": {"decision_id": "decision_x"},
    },
    "update_checklist": {
        "schema_version": "coord_decide_v1",
        "operation_id": "op_decide_checklist_x",
        "action": "update_checklist",
        "parameters": {
            "decision_id": "decision_x",
            "alternatives": ["option_y"],
            "consequences": ["Update the active baseline."],
            "next_required_actions": ["Run the downstream verification."],
        },
    },
    "accept": {
        "schema_version": "coord_decide_v1",
        "operation_id": "op_decide_accept_x",
        "action": "accept",
        "parameters": {"decision_id": "decision_x", "force_accept": False},
    },
    "set_baseline": {
        "schema_version": "coord_decide_v1",
        "operation_id": "op_decide_baseline_x",
        "action": "set_baseline",
        "parameters": {
            "node_id": "problem_x",
            "option_id": "option_x",
            "decision_id": "decision_x",
            "artifacts": ["record_x"],
            "reason": "Reviewed evidence supports option X.",
            "clear": False,
        },
    },
}
COORD_DECIDE_SCHEMA = COORD_DECIDE_SCHEMAS["set_baseline"]


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit coord decide", allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--file", type=Path)
    parser.add_argument("--print-schema", action="store_true")
    parser.add_argument(
        "--action",
        choices=tuple(COORD_DECIDE_SCHEMAS),
        default="set_baseline",
        help="Schema action to print with --print-schema.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    if args.print_schema:
        emit_json(COORD_DECIDE_SCHEMAS[args.action], compact=args.compact)
        return
    raw_plan = {}
    if args.file is None:
        handle_role_cli_input_error(
            args,
            parser,
            ValueError("--file is required unless --print-schema is used"),
            operation="coord decide",
            retry_command="research-cockpit coord decide --print-schema --action set_baseline",
        )
    if args.action != "set_baseline":
        handle_role_cli_input_error(
            args,
            parser,
            ValueError("--action is only valid with --print-schema"),
            operation="coord decide",
            retry_command=(
                f"research-cockpit coord decide --print-schema --action {args.action}"
            ),
        )
    try:
        if not args.file.is_file():
            raise FileNotFoundError(args.file)
        raw_plan = load_yaml(args.file)
        payload = apply_coord_decision(args.root, raw_plan)
    except AssignmentLeaseError as exc:
        handle_work_error(args, exc)
        raise SystemExit(1) from None
    except (ValidationError, ValueError, FileNotFoundError, OSError) as exc:
        action = raw_plan.get("action")
        handle_role_cli_input_error(
            args,
            parser,
            exc,
            operation="coord decide",
            operation_id=raw_plan.get("operation_id"),
            code="decision_not_ready" if action == "accept" else "invalid_request",
            retry_command=(
                f"research-cockpit coord decide --print-schema --action {action or 'set_baseline'}"
            ),
        )
    emit_work_result(args, payload)


if __name__ == "__main__":
    main()
