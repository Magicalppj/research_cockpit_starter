from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.assignment_leases import (
    DEFAULT_LEASE_SECONDS,
    AssignmentLeaseError,
)
from research_cockpit.assignment_runs import start_assignment_run
from research_cockpit.commands._runtime import emit_json
from research_cockpit.commands._work_lease_cli import (
    emit_work_result,
    handle_role_cli_input_error,
    handle_work_error,
)
from research_cockpit.paths import default_data_root
from research_cockpit.storage import load_yaml
from research_cockpit.types import ValidationError


_START_INPUT_FIELDS = {
    "schema_version",
    "agent_id",
    "lease_id",
    "lease_epoch",
    "operation_id",
    "input_revision",
    "experiment_id",
    "slug",
    "lease_seconds",
    "run",
}

WORK_START_SCHEMA = {
    "schema_version": "work_start_v1",
    "agent_id": "agent_x",
    "lease_id": "lease_x",
    "lease_epoch": 1,
    "operation_id": "op_start_x",
    "input_revision": "input-v1:<from-work-packet>",
    "experiment_id": "experiment_x",
    "slug": "trial",
    "run": {"launcher": "shell", "command": "python train.py"},
}


def parse_start_input(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != "work_start_v1":
        raise ValueError("work start input requires schema_version: work_start_v1")
    unknown = sorted(set(payload) - _START_INPUT_FIELDS)
    if unknown:
        raise ValueError("work start input contains unknown fields: " + ", ".join(unknown))
    required = ("agent_id", "lease_id", "lease_epoch", "operation_id")
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValueError("work start input is missing fields: " + ", ".join(missing))
    for field in ("agent_id", "lease_id", "operation_id"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ValueError(f"work start input {field} must be a non-empty string")
    lease_epoch = payload["lease_epoch"]
    if isinstance(lease_epoch, bool) or not isinstance(lease_epoch, int) or lease_epoch < 1:
        raise ValueError("work start input lease_epoch must be an integer >= 1")
    lease_seconds = payload.get("lease_seconds", DEFAULT_LEASE_SECONDS)
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int):
        raise ValueError("work start input lease_seconds must be an integer")
    input_revision = payload.get("input_revision")
    if input_revision is not None and (
        not isinstance(input_revision, str) or not input_revision.strip()
    ):
        raise ValueError("work start input input_revision must be a non-empty string or null")
    run = payload.get("run", {})
    if not isinstance(run, dict):
        raise ValueError("work start input run must be a mapping")
    for field in ("experiment_id", "slug"):
        value = payload.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"work start input {field} must be a non-empty string or null")
    return {
        "agent_id": payload["agent_id"].strip(),
        "lease_id": payload["lease_id"].strip(),
        "lease_epoch": lease_epoch,
        "operation_id": payload["operation_id"].strip(),
        "input_revision": input_revision.strip() if isinstance(input_revision, str) else None,
        "experiment_id": payload.get("experiment_id"),
        "slug_hint": payload.get("slug"),
        "lease_seconds": lease_seconds,
        "run_fields": run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit work start", allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--assignment", dest="assignment_id")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--print-schema", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    if args.print_schema:
        emit_json(WORK_START_SCHEMA, compact=args.compact)
        return
    if args.assignment_id is None or args.file is None:
        handle_role_cli_input_error(
            args,
            parser,
            ValueError("--assignment and --file are required unless --print-schema is used"),
            operation="work start",
            assignment_id=args.assignment_id,
            retry_command="research-cockpit work start --print-schema --json --compact",
        )

    raw_input: dict[str, Any] = {}
    try:
        if not args.file.is_file():
            raise FileNotFoundError(args.file)
        raw_input = load_yaml(args.file)
        start_input = parse_start_input(raw_input)
        payload = start_assignment_run(
            args.root,
            assignment_id=args.assignment_id,
            **start_input,
        )
    except AssignmentLeaseError as exc:
        handle_work_error(args, exc)
        raise SystemExit(1) from None
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        handle_role_cli_input_error(
            args,
            parser,
            exc,
            operation="work start",
            assignment_id=args.assignment_id,
            operation_id=raw_input.get("operation_id"),
            input_revision=raw_input.get("input_revision"),
            retry_command="research-cockpit work start --print-schema --json --compact",
        )
    emit_work_result(args, payload)


if __name__ == "__main__":
    main()
