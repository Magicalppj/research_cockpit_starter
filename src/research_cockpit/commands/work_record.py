from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.assignment_leases import DEFAULT_LEASE_SECONDS, AssignmentLeaseError
from research_cockpit.assignment_records import record_assignment_evidence
from research_cockpit.commands._runtime import emit_json
from research_cockpit.commands._work_lease_cli import (
    emit_work_result,
    handle_role_cli_input_error,
    handle_work_error,
)
from research_cockpit.paths import default_data_root
from research_cockpit.storage import load_yaml
from research_cockpit.types import ValidationError


WORK_RECORD_SCHEMA = {
    "schema_version": "work_record_v1",
    "agent_id": "agent_x",
    "lease_id": "lease_x",
    "lease_epoch": 1,
    "operation_id": "op_record_x",
    "run_id": "run_x",
    "source_dir": "relative/or/absolute/output-directory",
    "node_id": "experiment_x",
    "record_id": None,
    "title": "Incremental evidence",
    "summary": "Optional bounded summary.",
    "links": {"metrics": "metrics.json"},
    "lease_seconds": DEFAULT_LEASE_SECONDS,
}

_INPUT_FIELDS = set(WORK_RECORD_SCHEMA)


def parse_record_input(payload: dict[str, Any], *, input_path: Path | None = None) -> dict[str, Any]:
    if payload.get("schema_version") != "work_record_v1":
        raise ValueError("work record input requires schema_version: work_record_v1")
    unknown = sorted(set(payload) - _INPUT_FIELDS)
    if unknown:
        raise ValueError("work record input contains unknown fields: " + ", ".join(unknown))
    required = (
        "agent_id",
        "lease_id",
        "lease_epoch",
        "operation_id",
        "run_id",
        "source_dir",
    )
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValueError("work record input is missing fields: " + ", ".join(missing))
    for field in ("agent_id", "lease_id", "operation_id", "run_id", "source_dir"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ValueError(f"work record input {field} must be a non-empty string")
    lease_epoch = payload["lease_epoch"]
    if isinstance(lease_epoch, bool) or not isinstance(lease_epoch, int) or lease_epoch < 1:
        raise ValueError("work record input lease_epoch must be an integer >= 1")
    lease_seconds = payload.get("lease_seconds", DEFAULT_LEASE_SECONDS)
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds < 1:
        raise ValueError("work record input lease_seconds must be an integer >= 1")
    links = payload.get("links", {})
    if not isinstance(links, dict) or not all(
        isinstance(key, str) and key and isinstance(value, str) and value
        for key, value in links.items()
    ):
        raise ValueError("work record input links must be a string-to-string mapping")
    source = Path(payload["source_dir"])
    if not source.is_absolute() and input_path is not None:
        source = input_path.parent / source
    parsed = {
        "agent_id": payload["agent_id"].strip(),
        "lease_id": payload["lease_id"].strip(),
        "lease_epoch": lease_epoch,
        "operation_id": payload["operation_id"].strip(),
        "run_id": payload["run_id"].strip(),
        "source_dir": source,
        "links": dict(links),
        "lease_seconds": lease_seconds,
    }
    for source_name, target_name in (
        ("node_id", "node_id"),
        ("record_id", "record_id"),
        ("title", "title"),
        ("summary", "summary"),
    ):
        value = payload.get(source_name)
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(f"work record input {source_name} must be a string or null")
            parsed[target_name] = value.strip() if source_name != "summary" else value
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit work record", allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--assignment", dest="assignment_id")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--print-schema", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    if args.print_schema:
        emit_json(WORK_RECORD_SCHEMA, compact=args.compact)
        return
    if not args.assignment_id or args.file is None:
        handle_role_cli_input_error(
            args,
            parser,
            ValueError("--assignment and --file are required unless --print-schema is used"),
            operation="work record",
            assignment_id=args.assignment_id,
            retry_command="research-cockpit work record --print-schema --json --compact",
        )
    raw_input = {}
    try:
        if not args.file.is_file():
            raise FileNotFoundError(args.file)
        raw_input = load_yaml(args.file)
        payload = record_assignment_evidence(
            args.root,
            assignment_id=args.assignment_id,
            **parse_record_input(raw_input, input_path=args.file.resolve()),
        )
    except AssignmentLeaseError as exc:
        handle_work_error(args, exc)
        raise SystemExit(1) from None
    except (ValidationError, ValueError, FileNotFoundError, OSError) as exc:
        handle_role_cli_input_error(
            args,
            parser,
            exc,
            operation="work record",
            assignment_id=args.assignment_id,
            operation_id=raw_input.get("operation_id"),
            retry_command="research-cockpit work record --print-schema --json --compact",
        )
    emit_work_result(args, payload)


if __name__ == "__main__":
    main()
