from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.artifact_compaction import artifact_compaction_plan, demote_artifact_node
from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.model import ValidationError
from research_cockpit.mutation_lock import MutationError


def compact_artifacts_payload(
    root: Path,
    *,
    artifact_id: str | None = None,
    dry_run: bool = True,
    execute: bool = False,
    show_diff: bool = False,
    rebuild_dashboard: bool = True,
) -> dict[str, Any]:
    if dry_run and execute:
        raise ValueError("--dry-run cannot be combined with --execute")
    if execute:
        if not artifact_id:
            raise ValueError("--execute requires --id <artifact_id>")
        return demote_artifact_node(
            root,
            artifact_id=artifact_id,
            dry_run=False,
            show_diff=show_diff,
            rebuild_dashboard=rebuild_dashboard,
        )
    if not dry_run:
        raise ValueError("compact-artifacts requires --dry-run or --execute")
    return artifact_compaction_plan(root, artifact_id=artifact_id)


def _print_human(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") == "artifact_compaction_result_v1":
        verb = "Would demote" if payload.get("dry_run") else "Demoted"
        safe_print(f"{verb} {payload['artifact_id']} to artifact record {payload['record_id']}.")
        safe_print("No payload files were deleted.")
        return
    for row in payload.get("artifacts", []):
        safe_print(f"{row['artifact_id']} [{row['classification']}] {', '.join(row.get('reasons', []))}")
    if not payload.get("artifacts"):
        safe_print("No artifact nodes found.")
    safe_print("Dry-run only; no payload files were deleted.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit compact-artifacts")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", dest="artifact_id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Apply a safe demotion for one can_demote artifact id.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        payload = compact_artifacts_payload(
            args.root,
            artifact_id=args.artifact_id,
            dry_run=args.dry_run,
            execute=args.execute,
            show_diff=args.show_diff,
            rebuild_dashboard=not args.no_build,
        )
    except (ValidationError, MutationError, ValueError, FileNotFoundError) as exc:
        if args.json:
            detail = exc.payload if isinstance(exc, MutationError) and exc.payload else {"ok": False, "error": str(exc)}
            emit_json(detail)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(payload)
        return
    _print_human(payload)
    if args.show_diff and payload.get("diff"):
        safe_print(payload["diff"], end="" if str(payload["diff"]).endswith("\n") else "\n")


if __name__ == "__main__":
    main()