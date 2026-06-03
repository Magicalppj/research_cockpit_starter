from __future__ import annotations

import argparse
import copy
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.assignment_scope import AssignmentScopeError, ensure_assignment_scope
from research_cockpit.commands._evidence import append_unique, validate_artifact_ids
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
from research_cockpit.commands.record_finding import find_node_file
from research_cockpit.model import (
    ResearchNode,
    VALID_FINDING_CONFIDENCES,
    VALID_FINDING_OUTCOMES,
    ValidationError,
    load_yaml,
    script_command,
    validate_cockpit,
)


def _find_finding(findings: list[Any], finding_id: str) -> tuple[int, dict[str, Any]]:
    for index, finding in enumerate(findings):
        if isinstance(finding, dict) and str(finding.get("id")) == finding_id:
            return index, finding
    raise ValueError(f"Finding id does not exist: {finding_id}")


def update_finding(
    root: Path,
    *,
    experiment_id: str,
    finding_id: str,
    statement: str | None = None,
    confidence: str | None = None,
    outcome: str | None = None,
    metrics: list[str] | None = None,
    artifact_ids: list[str] | None = None,
    replace_metrics: bool = False,
    replace_artifacts: bool = False,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    assignment_id: str | None = None,
    coordinator: bool = False,
) -> dict[str, Any]:
    if (
        statement is None
        and confidence is None
        and outcome is None
        and not metrics
        and not artifact_ids
        and not replace_metrics
        and not replace_artifacts
    ):
        raise ValueError("At least one finding update is required")
    if confidence is not None and confidence not in VALID_FINDING_CONFIDENCES:
        allowed = ", ".join(sorted(VALID_FINDING_CONFIDENCES))
        raise ValueError(f"Invalid confidence {confidence!r}; allowed: {allowed}")
    if outcome is not None and outcome not in VALID_FINDING_OUTCOMES:
        allowed = ", ".join(sorted(VALID_FINDING_OUTCOMES))
        raise ValueError(f"Invalid outcome {outcome!r}; allowed: {allowed}")

    state = load_validated_state(root)
    nodes = state.nodes
    if experiment_id not in nodes:
        raise ValueError(f"Experiment node does not exist: {experiment_id}")
    if nodes[experiment_id].type != "experiment":
        raise ValueError(f"Node {experiment_id} must be experiment, got {nodes[experiment_id].type}")

    artifact_ids = artifact_ids or []
    validate_artifact_ids(nodes, artifact_ids)
    ensure_assignment_scope(
        root,
        nodes,
        assignment_id=assignment_id,
        coordinator=coordinator,
        target_node_ids=[experiment_id, *artifact_ids],
    )

    path = find_node_file(root, experiment_id)
    data = load_yaml(path)
    before_data = copy.deepcopy(data)
    findings = data.get("findings", []) or []
    if not isinstance(findings, list):
        raise ValueError(f"{experiment_id}: findings must be a list")
    finding_index, finding = _find_finding(findings, finding_id)
    before_finding = copy.deepcopy(finding)

    if statement is not None:
        finding["statement"] = statement
    if confidence is not None:
        finding["confidence"] = confidence
    if outcome is not None:
        finding["outcome"] = outcome
    if replace_metrics:
        finding["metrics"] = [str(item) for item in metrics or [] if str(item).strip()]
    elif metrics:
        finding["metrics"], _ = append_unique(finding.get("metrics"), metrics, "metrics")
    if replace_artifacts:
        finding["linked_artifacts"] = [str(item) for item in artifact_ids if str(item).strip()]
    elif artifact_ids:
        finding["linked_artifacts"], _ = append_unique(
            finding.get("linked_artifacts"),
            artifact_ids,
            "linked_artifacts",
        )
    added_experiment_artifacts: list[str] = []
    if artifact_ids:
        data["linked_artifacts"], added_experiment_artifacts = append_unique(
            data.get("linked_artifacts"),
            artifact_ids,
            "linked_artifacts",
        )
    finding["updated_at"] = str(date.today())
    findings[finding_index] = finding
    data["findings"] = findings
    data["updated_at"] = str(date.today())

    candidate = dict(nodes)
    candidate[experiment_id] = ResearchNode.from_dict(data)
    ensure_assignment_scope(
        root,
        candidate,
        assignment_id=assignment_id,
        coordinator=coordinator,
        target_node_ids=[experiment_id],
    )
    validate_cockpit(root, candidate, state.current, state.explicit_edges, raise_on_error=True)

    changed = before_data != data
    result: dict[str, Any] = {
        "experiment_id": experiment_id,
        "finding_id": finding_id,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "path": str(path),
        "before": before_finding,
        "after": finding,
        "added_experiment_artifacts": added_experiment_artifacts,
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(path, before_data, data)]) if changed else ""
    if dry_run:
        return dry_run_preflight_result(root, result)
    if not changed:
        return result

    finish_mutation(
        root,
        [(path, before_data, data)],
        interaction={
            "kind": "update_finding",
            "actor": "researcher",
            "node_id": experiment_id,
            "command": (
                f"{script_command('update_finding.py')} --experiment {experiment_id} "
                f"--finding-id {finding_id}"
            ),
            "before": before_finding,
            "after": finding,
            "extra": {
                "experiment_id": experiment_id,
                "finding_id": finding_id,
                "replace_metrics": replace_metrics,
                "replace_artifacts": replace_artifacts,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--experiment", required=True, dest="experiment_id")
    parser.add_argument("--finding-id", required=True)
    parser.add_argument("--statement")
    parser.add_argument("--confidence", choices=sorted(VALID_FINDING_CONFIDENCES))
    parser.add_argument("--outcome", choices=sorted(VALID_FINDING_OUTCOMES))
    parser.add_argument("--metric", action="append", dest="metrics")
    parser.add_argument("--artifact-id", action="append", dest="artifact_ids")
    parser.add_argument("--replace-metrics", action="store_true")
    parser.add_argument("--replace-artifacts", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    add_assignment_scope_args(parser)
    args = parser.parse_args()

    try:
        result = update_finding(
            args.root,
            experiment_id=args.experiment_id,
            finding_id=args.finding_id,
            statement=args.statement,
            confidence=args.confidence,
            outcome=args.outcome,
            metrics=args.metrics,
            artifact_ids=args.artifact_ids,
            replace_metrics=args.replace_metrics,
            replace_artifacts=args.replace_artifacts,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
            assignment_id=args.assignment,
            coordinator=args.coordinator,
        )
    except AssignmentScopeError as exc:
        emit_assignment_scope_error(args, exc)
        raise SystemExit(1) from exc
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(
            compact_mutation_result(
                result,
                command="update-finding",
                target={"experiment": result["experiment_id"], "finding": result["finding_id"]},
                root=args.root,
                updated=[result["experiment_id"]],
            ) if args.compact else result
        )
        return
    verb = "Would update" if args.dry_run else "Updated"
    safe_print(f"{verb} finding {args.finding_id} on {args.experiment_id}")
    if args.show_diff and result.get("diff"):
        safe_print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        safe_print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
