from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.assignment_scope import AssignmentScopeError, ensure_assignment_scope
from research_cockpit.commands._assignment_scope_cli import add_assignment_scope_args, emit_assignment_scope_error
from research_cockpit.commands._runtime import (
    compact_mutation_result,
    dry_run_preflight_result,
    emit_json,
    finish_mutation,
    load_validated_state,
    safe_print,
    yaml_change_diff,
)
from research_cockpit.gate_result_records import (
    build_gate_record_data,
    gate_record_path,
    validate_attached_gate_artifact,
    validate_gate_result_relative_path,
)
from research_cockpit.gate_results import load_gate_result
from research_cockpit.model import (
    ValidationError,
    load_runs,
    script_command,
)
from research_cockpit.mutation_lock import MutationError


def _resolve_attachment(
    root: Path,
    *,
    gate_result_file: str,
    experiment_id: str | None,
    run_id: str | None,
) -> tuple[str, str | None, dict[str, Any]]:
    state = load_validated_state(root)
    runs = load_runs(root)
    gate_path = validate_gate_result_relative_path(root, gate_result_file)
    if not gate_path.exists():
        raise FileNotFoundError(gate_path)
    probe = load_gate_result(root, gate_result_file)
    if probe is None:
        raise ValueError("gate_result_file is required")

    linked_run_id = str(run_id or probe.get("run_id") or "").strip() or None
    if linked_run_id:
        if linked_run_id not in runs:
            raise ValueError(f"Run does not exist: {linked_run_id}")
        run_experiment_id = runs[linked_run_id].experiment_id
        if experiment_id and experiment_id != run_experiment_id:
            raise ValueError(f"Run {linked_run_id} belongs to experiment {run_experiment_id}, not {experiment_id}")
        experiment_id = run_experiment_id

    linked_experiment_id = str(experiment_id or probe.get("experiment_id") or "").strip()
    if not linked_experiment_id:
        raise ValueError("--experiment or --run is required when the gate file does not declare an experiment_id")
    if linked_experiment_id not in state.nodes:
        raise ValueError(f"Experiment does not exist: {linked_experiment_id}")
    if state.nodes[linked_experiment_id].type != "experiment":
        raise ValueError(f"Gate result must attach to an experiment node, got {state.nodes[linked_experiment_id].type}")

    gate = load_gate_result(
        root,
        gate_result_file,
        experiment_id=linked_experiment_id,
        run_id=linked_run_id,
    )
    assert gate is not None
    return linked_experiment_id, linked_run_id, gate


def ingest_gate_result(
    root: Path,
    *,
    gate_id: str,
    gate_result_file: str,
    experiment_id: str | None = None,
    run_id: str | None = None,
    artifact_id: str | None = None,
    recorded_at: str | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    assignment_id: str | None = None,
    coordinator: bool = False,
) -> dict[str, Any]:
    state = load_validated_state(root)
    linked_experiment_id, linked_run_id, gate = _resolve_attachment(
        root,
        gate_result_file=gate_result_file,
        experiment_id=experiment_id,
        run_id=run_id,
    )
    if artifact_id:
        validate_attached_gate_artifact(
            state.nodes,
            experiment_id=linked_experiment_id,
            artifact_id=artifact_id,
            gate_result_file=gate_result_file,
        )
    ensure_assignment_scope(
        root,
        state.nodes,
        assignment_id=assignment_id,
        coordinator=coordinator,
        target_node_ids=[linked_experiment_id, artifact_id],
    )

    path = gate_record_path(root, gate_id)
    normalized_id = path.stem
    if path.exists():
        raise FileExistsError(path)
    data = build_gate_record_data(
        gate_id=normalized_id,
        experiment_id=linked_experiment_id,
        run_id=linked_run_id,
        artifact_id=artifact_id,
        gate_result_file=gate_result_file,
        recorded_at=recorded_at,
    )
    changes = [(path, None, data)]
    result: dict[str, Any] = {
        "gate_id": normalized_id,
        "experiment_id": linked_experiment_id,
        "run_id": linked_run_id,
        "artifact_id": artifact_id,
        "gate_result_file": gate_result_file,
        "gate_result": gate,
        "dry_run": dry_run,
        "changed": False if dry_run else True,
        "would_change": True,
        "path": str(path),
        "changed_files": [str(path)],
        "before": None,
        "after": data,
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        return dry_run_preflight_result(root, result)

    finish_mutation(
        root,
        changes,
        interaction={
            "kind": "ingest_gate_result",
            "actor": "researcher",
            "node_id": linked_experiment_id,
            "command": script_command(
                "ingest_gate_result.py",
                "--id",
                normalized_id,
                "--gate-result-file",
                gate_result_file,
            ),
            "after": {
                "gate_id": normalized_id,
                "experiment_id": linked_experiment_id,
                "run_id": linked_run_id,
                "artifact_id": artifact_id,
                "blocks_next_action": gate.get("blocks_next_action"),
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit ingest-gate-result")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="gate_id")
    parser.add_argument("--gate-result-file", "--file", required=True, dest="gate_result_file")
    parser.add_argument("--experiment", "--experiment-id", dest="experiment_id")
    parser.add_argument("--run", "--run-id", dest="run_id")
    parser.add_argument("--artifact", "--artifact-id", dest="artifact_id")
    parser.add_argument("--recorded-at")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    add_assignment_scope_args(parser)
    args = parser.parse_args()

    try:
        result = ingest_gate_result(
            args.root,
            gate_id=args.gate_id,
            gate_result_file=args.gate_result_file,
            experiment_id=args.experiment_id,
            run_id=args.run_id,
            artifact_id=args.artifact_id,
            recorded_at=args.recorded_at,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
            assignment_id=args.assignment,
            coordinator=args.coordinator,
        )
    except MutationError as exc:
        if args.json and exc.payload:
            emit_json(exc.payload)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc
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
                command="ingest-gate-result",
                target=result["gate_id"],
                root=args.root,
                created=[result["gate_id"]],
                updated=[result["experiment_id"]],
            )
            if args.compact
            else result
        )
        return
    verb = "Would ingest" if args.dry_run else "Ingested"
    safe_print(f"{verb} gate result {result['gate_id']}: {result['gate_result_file']}")
    if args.show_diff and result.get("diff"):
        safe_print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        safe_print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
