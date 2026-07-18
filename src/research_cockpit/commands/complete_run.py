from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import compact_mutation_result, emit_json, safe_print
from research_cockpit.commands._assignment_scope_cli import add_assignment_scope_args, emit_assignment_scope_error
from research_cockpit.commands.file_schemas import RUN_CLOSEOUT_EXAMPLE
from research_cockpit.commands.update_run import update_run
from research_cockpit.assignment_scope import AssignmentScopeError
from research_cockpit.model import ValidationError, script_command
from research_cockpit.mutation_lock import MutationError
from research_cockpit.run_closeout import complete_run_closeout, load_run_closeout
from research_cockpit.retention import load_mapping_argument


TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled")


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if "--json" in sys.argv[1:]:
            emit_json(
                {"ok": False, "error": message},
                compact="--compact" in sys.argv[1:],
            )
            raise SystemExit(2)
        super().error(message)


def complete_run(
    root: Path,
    *,
    run_id: str,
    status: str = "completed",
    finished_at: str | None = None,
    progress_file: str | None = None,
    log_root: str | None = None,
    output_root: str | None = None,
    monitor_command: str | None = None,
    stop_command: str | None = None,
    resources: dict[str, Any] | None = None,
    output_retention: dict[str, Any] | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    assignment_id: str | None = None,
    coordinator: bool = False,
) -> dict[str, Any]:
    if status not in TERMINAL_RUN_STATUSES:
        allowed = ", ".join(TERMINAL_RUN_STATUSES)
        raise ValueError(f"Invalid terminal run status {status!r}; allowed: {allowed}")
    return update_run(
        root,
        run_id=run_id,
        status=status,
        finished_at=finished_at,
        progress_file=progress_file,
        log_root=log_root,
        output_root=output_root,
        monitor_command=monitor_command,
        stop_command=stop_command,
        resources=resources,
        output_retention=output_retention,
        rebuild_dashboard=rebuild_dashboard,
        dry_run=dry_run,
        show_diff=show_diff,
        interaction_kind="complete_run",
        interaction_command=f"{script_command('complete_run.py')} --id {run_id} --status {status}",
        assignment_id=assignment_id,
        coordinator=coordinator,
    )


def main() -> None:
    parser = _JsonArgumentParser(
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=RUN_CLOSEOUT_EXAMPLE,
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", dest="run_id")
    parser.add_argument("--file", type=Path, dest="closeout_file")
    parser.add_argument("--print-schema", action="store_true")
    parser.add_argument("--status", choices=TERMINAL_RUN_STATUSES, default="completed")
    parser.add_argument("--finished-at")
    parser.add_argument("--progress-file")
    parser.add_argument("--log-root")
    parser.add_argument("--output-root")
    parser.add_argument("--monitor-command")
    parser.add_argument("--stop-command")
    parser.add_argument("--resources-json")
    parser.add_argument("--resources-file", type=Path)
    parser.add_argument("--output-retention-json")
    parser.add_argument("--output-retention-file", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    add_assignment_scope_args(parser)
    parser.add_argument("--progress", action="store_true", help="Print phase progress to stderr.")
    args = parser.parse_args()

    if args.print_schema:
        safe_print(RUN_CLOSEOUT_EXAMPLE)
        return
    if args.closeout_file and args.run_id:
        message = "--id cannot be used together with --file"
        if args.json:
            emit_json({"ok": False, "error": message}, compact=args.compact)
            raise SystemExit(2)
        parser.error(message)
    if not args.closeout_file and not args.run_id:
        message = "--id is required unless --file or --print-schema is used"
        if args.json:
            emit_json({"ok": False, "error": message}, compact=args.compact)
            raise SystemExit(2)
        parser.error(message)

    try:
        if args.closeout_file:
            result = complete_run_closeout(
                args.root,
                plan=load_run_closeout(args.closeout_file),
                rebuild_dashboard=not args.no_build,
                dry_run=args.dry_run,
                show_diff=args.show_diff,
                assignment_id=args.assignment,
                coordinator=args.coordinator,
            )
        else:
            result = complete_run(
                args.root,
                run_id=args.run_id,
                status=args.status,
                finished_at=args.finished_at,
                progress_file=args.progress_file,
                log_root=args.log_root,
                output_root=args.output_root,
                monitor_command=args.monitor_command,
                stop_command=args.stop_command,
                resources=load_mapping_argument(
                    json_text=args.resources_json,
                    file_path=args.resources_file,
                    field_name="resources",
                ),
                output_retention=load_mapping_argument(
                    json_text=args.output_retention_json,
                    file_path=args.output_retention_file,
                    field_name="output_retention",
                    validate_retention_class=True,
                ),
                rebuild_dashboard=not args.no_build,
                dry_run=args.dry_run,
                show_diff=args.show_diff,
                assignment_id=args.assignment,
                coordinator=args.coordinator,
            )
    except MutationError as exc:
        if args.json and exc.payload:
            emit_json(exc.payload, compact=args.compact)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc
    except AssignmentScopeError as exc:
        emit_assignment_scope_error(args, exc)
        raise SystemExit(1) from exc
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        if args.json:
            emit_json({"ok": False, "error": str(exc)}, compact=args.compact)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(
            compact_mutation_result(
                result,
                command="complete-run",
                target=result["run_id"],
                root=args.root,
                created=[*result.get("gate_ids", []), *([result["next_experiment_id"]] if result.get("next_experiment_id") else [])],
                updated=[result["run_id"], *([result["experiment_id"]] if result.get("experiment_id") else [])],
                records=[f"artifact:{result['record_id']}"] if result.get("record_id") else [],
            )
            if args.compact
            else result,
            compact=args.compact,
        )
        return
    verb = "Would complete" if args.dry_run else "Completed"
    safe_print(f"{verb} run {result['run_id']}: {result['status']}")
    if args.show_diff and result.get("diff"):
        safe_print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build and result["changed"]:
        safe_print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
