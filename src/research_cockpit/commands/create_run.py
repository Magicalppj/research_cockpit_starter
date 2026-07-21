from __future__ import annotations

import argparse
import copy
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import (
    compact_mutation_result,
    dry_run_preflight_result,
    emit_json,
    load_targeted_state,
    safe_print,
    validate_mutation_candidate,
    yaml_change_diff,
)
from research_cockpit.commands._assignment_scope_cli import add_assignment_scope_args, emit_assignment_scope_error
from research_cockpit.commands._runs import RUN_OPTIONAL_FIELDS, build_run_data, run_path
from research_cockpit.commands.record_finding import find_node_file
from research_cockpit.assignment_scope import AssignmentScopeError, ensure_assignment_scope
from research_cockpit.retention import load_mapping_argument
from research_cockpit.mutation_runtime import execute_mutation_transaction
from research_cockpit.model import (
    ResearchNode,
    RunRecord,
    VALID_RUN_STATUSES,
    ValidationError,
    load_runs,
    load_yaml,
    script_command,
)


def create_run(
    root: Path,
    *,
    run_id: str,
    experiment_id: str,
    status: str = "queued",
    start_experiment: bool = False,
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
    resources: dict[str, Any] | None = None,
    output_retention: dict[str, Any] | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    assignment_id: str | None = None,
    coordinator: bool = False,
    additional_yaml_changes: list[tuple] | None = None,
    interaction_override: dict[str, Any] | None = None,
    operation_request: dict[str, Any] | None = None,
    run_extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = load_targeted_state(root, node_ids=[experiment_id])
    ensure_assignment_scope(
        root,
        state.nodes,
        assignment_id=assignment_id,
        coordinator=coordinator,
        target_node_ids=[experiment_id],
    )
    path = run_path(root, run_id)
    normalized_id = path.stem
    runs = {} if state.targeted else load_runs(root)
    indexed_runs = (
        (state.validation_index.get("runs", {}) or {})
        if state.targeted and isinstance(state.validation_index, dict)
        else {}
    )
    if path.exists() or normalized_id in runs or normalized_id in indexed_runs:
        raise FileExistsError(path)

    data = build_run_data(
        run_id=normalized_id,
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
        resources=resources,
        output_retention=output_retention,
    )
    if run_extra_fields:
        protected = {"run_id", "status", "experiment_id", *RUN_OPTIONAL_FIELDS}
        collisions = sorted(set(run_extra_fields) & protected)
        if collisions:
            raise ValueError(
                "run_extra_fields cannot override run contract fields: "
                + ", ".join(collisions)
            )
        data.update(copy.deepcopy(run_extra_fields))
    candidate_runs = dict(runs)
    candidate_runs[normalized_id] = RunRecord.from_dict(data)
    candidate_nodes = dict(state.nodes)
    changes = [(path, None, data)]
    experiment_status_changed = False
    if start_experiment:
        if status != "running":
            raise ValueError("--start-experiment requires --status running")
        experiment = state.nodes.get(experiment_id)
        if experiment is None or experiment.type != "experiment":
            raise ValueError(f"Node {experiment_id!r} must be an experiment")
        if experiment.status not in {"planned", "queued", "running"}:
            raise ValueError(
                f"Cannot start experiment {experiment_id} from status {experiment.status!r}"
            )
        if experiment.status != "running":
            experiment_path = find_node_file(root, experiment_id)
            experiment_before = load_yaml(experiment_path)
            experiment_after = copy.deepcopy(experiment_before)
            experiment_after["status"] = "running"
            experiment_after["updated_at"] = str(date.today())
            candidate_nodes[experiment_id] = ResearchNode.from_dict(experiment_after)
            changes.append((experiment_path, experiment_before, experiment_after))
            experiment_status_changed = True
    changes.extend(additional_yaml_changes or [])

    validate_mutation_candidate(
        root,
        state,
        nodes=candidate_nodes,
        runs=candidate_runs,
    )
    result: dict[str, Any] = {
        "run_id": normalized_id,
        "experiment_id": data["experiment_id"],
        "status": data["status"],
        "dry_run": dry_run,
        "changed": False if dry_run else True,
        "would_change": True,
        "path": str(path),
        "changed_files": [str(change_path) for change_path, _, _ in changes],
        "before": None,
        "after": data,
        "started_experiment": start_experiment,
        "experiment_status_changed": experiment_status_changed,
        "verified": not dry_run,
        "additional_verification_required": dry_run,
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        return dry_run_preflight_result(root, result)

    interaction = interaction_override or {
        "kind": "create_run",
        "actor": "researcher",
        "node_id": data["experiment_id"],
        "command": f"{script_command('create_run.py')} --id {normalized_id} --experiment {data['experiment_id']}",
        "after": {
            key: data.get(key)
            for key in ("run_id", "status", "experiment_id", *RUN_OPTIONAL_FIELDS)
        },
        "extra": {
            "started_experiment": start_experiment,
            "experiment_status_changed": experiment_status_changed,
        },
    }
    transaction = execute_mutation_transaction(
        root,
        changes,
        interactions=[interaction],
        rebuild_dashboard=rebuild_dashboard,
        operation_request=operation_request,
    )
    if operation_request is not None:
        result["_operation_transaction"] = transaction
    return result


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="run_id")
    parser.add_argument("--experiment", "--experiment-id", required=True, dest="experiment_id")
    parser.add_argument("--status", choices=sorted(VALID_RUN_STATUSES), default="queued")
    parser.add_argument(
        "--start-experiment",
        action="store_true",
        help="Atomically set a planned or queued experiment to running with this run.",
    )
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
    args = parser.parse_args()

    try:
        result = create_run(
            args.root,
            run_id=args.run_id,
            experiment_id=args.experiment_id,
            status=args.status,
            start_experiment=args.start_experiment,
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
    except AssignmentScopeError as exc:
        emit_assignment_scope_error(args, exc)
        raise SystemExit(1) from exc
    except (ValidationError, ValueError, FileExistsError, FileNotFoundError) as exc:
        safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(
            compact_mutation_result(
                result,
                command="create-run",
                target=result["run_id"],
                root=args.root,
                created=[result["run_id"]],
                updated=[result["experiment_id"]] if result.get("experiment_status_changed") else [],
            )
            if args.compact
            else result
        )
        return
    verb = "Would create" if args.dry_run else "Created"
    safe_print(f"{verb} run {result['run_id']}: {result['path']}")
    if args.show_diff and result.get("diff"):
        safe_print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        safe_print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
