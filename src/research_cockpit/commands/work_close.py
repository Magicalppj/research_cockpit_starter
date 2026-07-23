from __future__ import annotations

import argparse
from pathlib import Path

from research_cockpit.assignment_leases import AssignmentLeaseError
from research_cockpit.assignment_results import close_assignment_work
from research_cockpit.commands._runtime import emit_json
from research_cockpit.commands._work_lease_cli import (
    emit_work_result,
    handle_role_cli_input_error,
    handle_work_error,
)
from research_cockpit.paths import default_data_root
from research_cockpit.storage import load_yaml
from research_cockpit.types import ValidationError


WORK_CLOSE_SCHEMA = {
    "schema_version": "work_close_v1",
    "agent_id": "agent_x",
    "lease_id": "lease_x",
    "lease_epoch": 1,
    "operation_id": "op_close_x",
    "input_revision": "input-v1:<from-work-packet>",
    "run": {"id": "run_x", "status": "completed"},
    "experiment": {
        "status": "done",
        "result_summary": "The bounded run completed.",
    },
    "finding": {
        "statement": "The run produced reviewable evidence.",
        "confidence": "medium",
        "outcome": "positive",
    },
    "assignment_result": {
        "outcome": "positive",
        "summary": "The assigned experiment completed.",
        "delivery": {
            "git_commit": None,
            "changed_files": [],
            "tests": {"status": "passed", "summary": "Targeted checks passed."},
        },
        "attempts": [
            {
                "attempt_id": "seed_17",
                "status": "completed",
                "outcome": "positive",
                "summary": "Selected final seed met the acceptance criteria.",
                "evidence_refs": ["s3://research-output/attempts/seed_17/metrics.json"],
            }
        ],
        "proposals": [],
    },
    "review_required": False,
}


def _resolve_evidence_source(plan: dict, input_path: Path) -> None:
    evidence_inputs = plan.get("evidence_inputs")
    if not isinstance(evidence_inputs, dict) or "source" not in evidence_inputs:
        return
    source = Path(str(evidence_inputs["source"])).expanduser()
    if not source.is_absolute():
        source = input_path.resolve().parent / source
    evidence_inputs["source"] = str(source.absolute())


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit work close", allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--assignment", dest="assignment_id")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--print-schema", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    if args.print_schema:
        emit_json(WORK_CLOSE_SCHEMA, compact=args.compact)
        return
    if args.assignment_id is None or args.file is None:
        handle_role_cli_input_error(
            args,
            parser,
            ValueError("--assignment and --file are required unless --print-schema is used"),
            operation="work close",
            assignment_id=args.assignment_id,
            retry_command="research-cockpit work close --print-schema --json --compact",
        )

    plan = {}

    try:
        if not args.file.is_file():
            raise FileNotFoundError(args.file)
        plan = load_yaml(args.file)
        if not isinstance(plan, dict):
            raise ValueError("work close input must be a mapping")
        _resolve_evidence_source(plan, args.file)
        payload = close_assignment_work(
            args.root,
            assignment_id=args.assignment_id,
            plan=plan,
        )
    except AssignmentLeaseError as exc:
        handle_work_error(args, exc)
        raise SystemExit(1) from None
    except (ValidationError, ValueError, FileNotFoundError, FileExistsError) as exc:
        handle_role_cli_input_error(
            args,
            parser,
            exc,
            operation="work close",
            assignment_id=args.assignment_id,
            operation_id=plan.get("operation_id"),
            input_revision=plan.get("input_revision"),
            retry_command="research-cockpit work close --print-schema --json --compact",
        )
    emit_work_result(args, payload)


if __name__ == "__main__":
    main()
