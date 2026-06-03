from __future__ import annotations

import argparse
import json
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
    text_change_diff,
    yaml_change_diff,
)
from research_cockpit.commands._assignment_scope_cli import add_assignment_scope_args, emit_assignment_scope_error
from research_cockpit.assignment_scope import AssignmentScopeError, ensure_assignment_scope
from research_cockpit.gate_result_records import (
    build_gate_record_data,
    default_gate_result_file,
    gate_record_path,
    validate_attached_gate_artifact,
    validate_gate_result_relative_path,
)
from research_cockpit.gate_results import normalize_gate_result
from research_cockpit.model import (
    ValidationError,
    load_runs,
    script_command,
)
from research_cockpit.mutation_lock import MutationError


def _parse_json_object(value: str | None, field: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} must be a JSON object: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{field} must be a JSON object")
    return data


def _parse_bool(value: str) -> bool:
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "passed"}:
        return True
    if text in {"false", "0", "no", "failed"}:
        return False
    raise argparse.ArgumentTypeError("--passed must be true or false")


def _resolve_attachment(root: Path, *, experiment_id: str, run_id: str | None) -> tuple[str, str | None]:
    state = load_validated_state(root)
    if experiment_id not in state.nodes:
        raise ValueError(f"Experiment does not exist: {experiment_id}")
    if state.nodes[experiment_id].type != "experiment":
        raise ValueError(f"Gate result must attach to an experiment node, got {state.nodes[experiment_id].type}")
    if run_id:
        runs = load_runs(root)
        if run_id not in runs:
            raise ValueError(f"Run does not exist: {run_id}")
        if runs[run_id].experiment_id != experiment_id:
            raise ValueError(f"Run {run_id} belongs to experiment {runs[run_id].experiment_id}, not {experiment_id}")
    return experiment_id, run_id


def record_gate_result(
    root: Path,
    *,
    gate_id: str,
    experiment_id: str,
    gate_type: str,
    passed: bool,
    run_id: str | None = None,
    expected: dict[str, Any] | None = None,
    observed: dict[str, Any] | None = None,
    fatal_failures: dict[str, Any] | None = None,
    preflight: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    next_allowed_action: str | None = None,
    artifact_id: str | None = None,
    gate_result_file: str | None = None,
    recorded_at: str | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    assignment_id: str | None = None,
    coordinator: bool = False,
) -> dict[str, Any]:
    state = load_validated_state(root)
    linked_experiment_id, linked_run_id = _resolve_attachment(root, experiment_id=experiment_id, run_id=run_id)
    record_path = gate_record_path(root, gate_id)
    normalized_id = record_path.stem
    if record_path.exists():
        raise FileExistsError(record_path)
    gate_result_file = gate_result_file or default_gate_result_file(normalized_id)
    gate_path = validate_gate_result_relative_path(root, gate_result_file)
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
    if gate_path.exists():
        raise FileExistsError(gate_path)

    gate_payload: dict[str, Any] = {
        "gate_type": gate_type,
        "passed": passed,
        "expected": expected or {},
        "observed": observed or {},
        "fatal_failures": fatal_failures or {},
        "warnings": warnings or [],
        "experiment_id": linked_experiment_id,
    }
    if preflight:
        gate_payload["preflight"] = preflight
    if linked_run_id:
        gate_payload["run_id"] = linked_run_id
    if next_allowed_action:
        gate_payload["next_allowed_action"] = next_allowed_action

    gate = normalize_gate_result(
        gate_payload,
        path=gate_result_file,
        experiment_id=linked_experiment_id,
        run_id=linked_run_id,
    )
    if not gate.get("valid"):
        raise ValueError("; ".join(gate.get("schema_warnings", [])) or "gate result is invalid")

    record_data = build_gate_record_data(
        gate_id=normalized_id,
        experiment_id=linked_experiment_id,
        run_id=linked_run_id,
        artifact_id=artifact_id,
        gate_result_file=gate_result_file,
        recorded_at=recorded_at,
    )
    gate_text = json.dumps(gate_payload, indent=2, ensure_ascii=False) + "\n"
    yaml_changes = [(record_path, None, record_data)]
    text_changes = [(gate_path, None, gate_text)]
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
        "path": str(record_path),
        "gate_result_path": str(gate_path),
        "changed_files": [str(record_path), str(gate_path)],
        "before": None,
        "after": record_data,
    }
    if show_diff:
        result["diff"] = yaml_change_diff(yaml_changes) + text_change_diff(text_changes)
    if dry_run:
        return dry_run_preflight_result(root, result)

    finish_mutation(
        root,
        yaml_changes,
        text_changes=text_changes,
        interaction={
            "kind": "record_gate_result",
            "actor": "researcher",
            "node_id": linked_experiment_id,
            "command": script_command(
                "record_gate_result.py",
                "--id",
                normalized_id,
                "--experiment",
                linked_experiment_id,
            ),
            "after": {
                "gate_id": normalized_id,
                "experiment_id": linked_experiment_id,
                "run_id": linked_run_id,
                "artifact_id": artifact_id,
                "gate_type": gate_type,
                "passed": passed,
                "blocks_next_action": gate.get("blocks_next_action"),
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit record-gate-result")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="gate_id")
    parser.add_argument("--experiment", "--experiment-id", required=True, dest="experiment_id")
    parser.add_argument("--run", "--run-id", dest="run_id")
    parser.add_argument("--type", "--gate-type", required=True, dest="gate_type")
    parser.add_argument("--passed", required=True, type=_parse_bool)
    parser.add_argument("--expected-json")
    parser.add_argument("--observed-json")
    parser.add_argument("--fatal-json", dest="fatal_failures_json")
    parser.add_argument("--preflight-json")
    parser.add_argument("--warning", action="append", dest="warnings")
    parser.add_argument("--next-allowed-action")
    parser.add_argument("--artifact", "--artifact-id", dest="artifact_id")
    parser.add_argument("--gate-result-file")
    parser.add_argument("--recorded-at")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    add_assignment_scope_args(parser)
    args = parser.parse_args()

    try:
        result = record_gate_result(
            args.root,
            gate_id=args.gate_id,
            experiment_id=args.experiment_id,
            run_id=args.run_id,
            gate_type=args.gate_type,
            passed=args.passed,
            expected=_parse_json_object(args.expected_json, "--expected-json"),
            observed=_parse_json_object(args.observed_json, "--observed-json"),
            fatal_failures=_parse_json_object(args.fatal_failures_json, "--fatal-json"),
            preflight=_parse_json_object(args.preflight_json, "--preflight-json"),
            warnings=args.warnings,
            next_allowed_action=args.next_allowed_action,
            artifact_id=args.artifact_id,
            gate_result_file=args.gate_result_file,
            recorded_at=args.recorded_at,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
            assignment_id=args.assignment,
            coordinator=args.coordinator,
        )
    except AssignmentScopeError as exc:
        emit_assignment_scope_error(args, exc)
        raise SystemExit(1) from exc
    except MutationError as exc:
        if args.json and exc.payload:
            emit_json(exc.payload)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc
    except (ValidationError, ValueError, FileExistsError, FileNotFoundError) as exc:
        safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(
            compact_mutation_result(
                result,
                command="record-gate-result",
                target=result["gate_id"],
                root=args.root,
                created=[result["gate_id"]],
                updated=[result["experiment_id"]],
            )
            if args.compact
            else result
        )
        return
    verb = "Would record" if args.dry_run else "Recorded"
    safe_print(f"{verb} gate result {result['gate_id']}: {result['gate_result_file']}")
    if args.show_diff and result.get("diff"):
        safe_print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        safe_print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
