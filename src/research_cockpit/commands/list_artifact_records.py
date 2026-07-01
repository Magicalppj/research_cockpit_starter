from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.artifact_records import list_artifact_records
from research_cockpit.commands._runtime import emit_json, safe_print


COMPACT_FIELDS = (
    "record_id",
    "experiment_id",
    "run_id",
    "status",
    "title",
    "stable_path",
    "promoted_artifact_id",
)


def _compact_record(record: dict[str, Any]) -> dict[str, Any]:
    return {field: record.get(field) for field in COMPACT_FIELDS if field in record}


def artifact_records_payload(
    root: Path,
    *,
    experiment_id: str | None = None,
    record_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    compact: bool = False,
) -> dict[str, Any]:
    records = list_artifact_records(
        root,
        experiment_id=experiment_id,
        record_id=record_id,
        run_id=run_id,
        status=status,
    )
    return {
        "ok": True,
        "schema_version": "artifact_records_query_v1",
        "root": str(root),
        "count": len(records),
        "filters": {
            "experiment_id": experiment_id,
            "record_id": record_id,
            "run_id": run_id,
            "status": status,
        },
        "records": [_compact_record(record) for record in records] if compact else records,
    }


def _print_human(payload: dict[str, Any]) -> None:
    for record in payload.get("records", []):
        bits = [
            str(record.get("record_id")),
            f"experiment={record.get('experiment_id')}",
            f"run={record.get('run_id')}",
            f"status={record.get('status')}",
        ]
        if record.get("promoted_artifact_id"):
            bits.append(f"promoted={record.get('promoted_artifact_id')}")
        if record.get("stable_path"):
            bits.append(str(record.get("stable_path")))
        safe_print(" ".join(bits))
    if not payload.get("records"):
        safe_print("No artifact records found.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit artifact-records")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--experiment", "--experiment-id", dest="experiment_id")
    parser.add_argument("--id", dest="record_id")
    parser.add_argument("--run-id")
    parser.add_argument("--status")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    try:
        payload = artifact_records_payload(
            args.root,
            experiment_id=args.experiment_id,
            record_id=args.record_id,
            run_id=args.run_id,
            status=args.status,
            compact=args.compact and args.json,
        )
    except (ValueError, FileNotFoundError) as exc:
        safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(payload)
        return
    _print_human(payload)


if __name__ == "__main__":
    main()