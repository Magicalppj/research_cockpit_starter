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
from research_cockpit.coordinator_operations import apply_coord_assignment, parse_coord_assign_input
from research_cockpit.paths import default_data_root
from research_cockpit.storage import load_yaml
from research_cockpit.types import ValidationError


COORD_ASSIGN_SCHEMAS = {
    "graph_plan": {
        "schema_version": "coord_assign_v1",
        "operation_id": "op_assign_graph_x",
        "action": "graph_plan",
        "graph_plan": {
            "nodes": [
                {
                    "id": "stage_x",
                    "type": "stage",
                    "title": "Research stage X",
                    "status": "active",
                },
                {
                    "id": "problem_x",
                    "type": "problem",
                    "title": "Research problem X",
                    "parent": "stage_x",
                    "status": "active",
                },
                {
                    "id": "option_x",
                    "type": "option",
                    "title": "Candidate option X",
                    "parent": "problem_x",
                    "status": "active",
                },
                {
                    "id": "experiment_x",
                    "type": "experiment",
                    "title": "Experiment X",
                    "parent": "option_x",
                    "status": "queued",
                },
                {
                    "id": "decision_x",
                    "type": "decision",
                    "title": "Decision for option X",
                    "parent": "option_x",
                    "status": "proposed",
                },
            ],
            "updates": [],
        },
    },
    "session": {
        "schema_version": "coord_assign_v1",
        "operation_id": "op_assign_session_x",
        "action": "session",
        "session": {
            "kind": "experiment",
            "option_id": "option_x",
            "experiment_id": "experiment_x",
            "objective": "Evaluate option X in an isolated worktree.",
            "branch": "codex/option-x",
            "worktree": "../worktrees/option-x",
            "agent_id": "agent_x",
            "assignment_id": "assignment_x",
            "create_worktree": True,
            "force": False,
            "tracking_reason": "stage_deliverable",
        },
    },
    "review_session": {
        "schema_version": "coord_assign_v1",
        "operation_id": "op_assign_review_x",
        "action": "session",
        "session": {
            "kind": "review",
            "option_id": "option_x",
            "producer_assignment_id": "assignment_producer_x",
            "objective": "Review producer evidence independently.",
            "branch": "codex/review-option-x",
            "worktree": "../worktrees/review-option-x",
            "agent_id": "reviewer_x",
            "assignment_id": "assignment_review_x",
            "create_worktree": True,
            "force": False,
            "tracking_reason": "independent_review",
        },
    },
}
COORD_ASSIGN_SCHEMA = COORD_ASSIGN_SCHEMAS["graph_plan"]


def _resolve_session_worktree(plan: dict, input_path: Path) -> dict:
    parsed = parse_coord_assign_input(plan)
    if parsed["action"] != "session":
        return parsed
    worktree = Path(parsed["session"]["worktree"])
    if not worktree.is_absolute():
        worktree = input_path.parent / worktree
    parsed["session"]["worktree"] = str(worktree.resolve(strict=False))
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit coord assign", allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--file", type=Path)
    parser.add_argument("--print-schema", action="store_true")
    parser.add_argument(
        "--action",
        choices=tuple(COORD_ASSIGN_SCHEMAS),
        default="graph_plan",
        help="Schema action to print with --print-schema.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    if args.print_schema:
        emit_json(COORD_ASSIGN_SCHEMAS[args.action], compact=args.compact)
        return
    raw_plan = {}
    if args.file is None:
        handle_role_cli_input_error(
            args,
            parser,
            ValueError("--file is required unless --print-schema is used"),
            operation="coord assign",
            retry_command="research-cockpit coord assign --print-schema --action graph_plan",
        )
    if args.action != "graph_plan":
        handle_role_cli_input_error(
            args,
            parser,
            ValueError("--action is only valid with --print-schema"),
            operation="coord assign",
            retry_command=(
                f"research-cockpit coord assign --print-schema --action {args.action}"
            ),
        )
    try:
        if not args.file.is_file():
            raise FileNotFoundError(args.file)
        raw_plan = load_yaml(args.file)
        payload = apply_coord_assignment(
            args.root, _resolve_session_worktree(raw_plan, args.file.resolve())
        )
    except AssignmentLeaseError as exc:
        handle_work_error(args, exc)
        raise SystemExit(1) from None
    except (ValidationError, ValueError, FileNotFoundError, OSError) as exc:
        handle_role_cli_input_error(
            args,
            parser,
            exc,
            operation="coord assign",
            operation_id=raw_plan.get("operation_id"),
            retry_command=(
                "research-cockpit coord assign --print-schema --action graph_plan"
            ),
        )
    emit_work_result(args, payload)


if __name__ == "__main__":
    main()
