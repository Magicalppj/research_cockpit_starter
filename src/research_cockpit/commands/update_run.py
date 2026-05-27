from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import (
    compact_mutation_result,
    dry_run_preflight_result,
    emit_json,
    finish_mutation,
    load_validated_state,
    safe_print,
    yaml_change_diff,
)
from research_cockpit.commands._runs import update_fields, run_path
from research_cockpit.model import (
    RunRecord,
    VALID_RUN_STATUSES,
    ValidationError,
    load_runs,
    load_yaml,
    script_command,
    validate_cockpit,
)


def update_run(
    root: Path,
    *,
    run_id: str,
    status: str | None = None,
    experiment_id: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    launcher: str | None = None,
    command: str | None = None,
    tmux_session: str | None = None,
    pid: int | str | None = None,
    log_root: str | None = None,
    output_root: str | None = None,
    monitor_command: str | None = None,
    stop_command: str | None = None,
    progress_file: str | None = None,
    config_file: str | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    interaction_kind: str = "update_run",
    interaction_command: str | None = None,
) -> dict[str, Any]:
    state = load_validated_state(root)
    runs = load_runs(root)
    path = run_path(root, run_id)
    normalized_id = path.stem
    if normalized_id not in runs:
        raise FileNotFoundError(path)

    updates = update_fields(
        status=status,
        experiment_id=experiment_id,
        started_at=started_at,
        finished_at=finished_at,
        launcher=launcher,
        command=command,
        tmux_session=tmux_session,
        pid=pid,
        log_root=log_root,
        output_root=output_root,
        monitor_command=monitor_command,
        stop_command=stop_command,
        progress_file=progress_file,
        config_file=config_file,
    )
    if not updates:
        raise ValueError("At least one run field update is required")

    before_data = load_yaml(path)
    after_data = copy.deepcopy(before_data)
    after_data.update(updates)
    after_data["run_id"] = normalized_id

    candidate = dict(runs)
    candidate[normalized_id] = RunRecord.from_dict(after_data)
    validate_cockpit(root, state.nodes, state.current, state.explicit_edges, runs=candidate, raise_on_error=True)

    changed = before_data != after_data
    changes = [(path, before_data, after_data)] if changed else []
    result: dict[str, Any] = {
        "run_id": normalized_id,
        "experiment_id": after_data.get("experiment_id"),
        "status": after_data.get("status"),
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "path": str(path),
        "changed_files": [str(path)] if changed else [],
        "before": before_data,
        "after": after_data,
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        return dry_run_preflight_result(root, result)
    if not changed:
        return result

    finish_mutation(
        root,
        changes,
        interaction={
            "kind": interaction_kind,
            "actor": "researcher",
            "node_id": str(after_data.get("experiment_id") or ""),
            "command": interaction_command or f"{script_command('update_run.py')} --id {normalized_id}",
            "before": before_data,
            "after": after_data,
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="run_id")
    parser.add_argument("--status", choices=sorted(VALID_RUN_STATUSES))
    parser.add_argument("--experiment", "--experiment-id", dest="experiment_id")
    parser.add_argument("--started-at")
    parser.add_argument("--finished-at")
    parser.add_argument("--launcher")
    parser.add_argument("--command")
    parser.add_argument("--tmux-session")
    parser.add_argument("--pid")
    parser.add_argument("--log-root")
    parser.add_argument("--output-root")
    parser.add_argument("--monitor-command")
    parser.add_argument("--stop-command")
    parser.add_argument("--progress-file")
    parser.add_argument("--config-file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = update_run(
            args.root,
            run_id=args.run_id,
            status=args.status,
            experiment_id=args.experiment_id,
            started_at=args.started_at,
            finished_at=args.finished_at,
            launcher=args.launcher,
            command=args.command,
            tmux_session=args.tmux_session,
            pid=args.pid,
            log_root=args.log_root,
            output_root=args.output_root,
            monitor_command=args.monitor_command,
            stop_command=args.stop_command,
            progress_file=args.progress_file,
            config_file=args.config_file,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(
            compact_mutation_result(
                result,
                command="update-run",
                target=result["run_id"],
                root=args.root,
                created=[],
                updated=[result["run_id"]] if result["would_change"] else [],
            )
            if args.compact
            else result
        )
        return
    verb = "Would update" if args.dry_run else "Updated"
    safe_print(f"{verb} run {result['run_id']}: {result['path']}")
    if args.show_diff and result.get("diff"):
        safe_print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build and result["changed"]:
        safe_print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
