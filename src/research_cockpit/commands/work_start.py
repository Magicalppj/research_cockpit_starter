from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.assignment_leases import (
    DEFAULT_LEASE_SECONDS,
    AssignmentLeaseError,
)
from research_cockpit.assignment_runs import start_assignment_run
from research_cockpit.commands._work_lease_cli import emit_work_result, handle_work_error
from research_cockpit.paths import default_data_root
from research_cockpit.storage import load_yaml
from research_cockpit.types import ValidationError


_START_INPUT_FIELDS = {
    "schema_version",
    "agent_id",
    "lease_id",
    "lease_epoch",
    "operation_id",
    "experiment_id",
    "slug",
    "lease_seconds",
    "run",
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
        "experiment_id": payload.get("experiment_id"),
        "slug_hint": payload.get("slug"),
        "lease_seconds": lease_seconds,
        "run_fields": run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit work start", allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--assignment", required=True, dest="assignment_id")
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    try:
        if not args.file.is_file():
            raise FileNotFoundError(args.file)
        start_input = parse_start_input(load_yaml(args.file))
        payload = start_assignment_run(
            args.root,
            assignment_id=args.assignment_id,
            **start_input,
        )
    except AssignmentLeaseError as exc:
        handle_work_error(args, exc)
        raise SystemExit(1) from None
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    emit_work_result(args, payload)


if __name__ == "__main__":
    main()
