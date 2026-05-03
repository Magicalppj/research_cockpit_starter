from __future__ import annotations

import argparse
import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import finish_mutation, load_validated_state, yaml_change_diff
from research_cockpit.commands.record_finding import _next_finding_id, find_node_file
from research_cockpit.model import (
    ResearchNode,
    VALID_FINDING_CONFIDENCES,
    VALID_FINDING_OUTCOMES,
    ValidationError,
    load_yaml,
    script_command,
    validate_cockpit,
)


TERMINAL_NON_COMPLETABLE_STATUSES = {"failed", "cancelled"}


def _append_unique_actions(existing: Any, additions: list[str]) -> tuple[list[str], list[str]]:
    if existing is None:
        existing = []
    if not isinstance(existing, list):
        raise ValueError("next_actions must be a list")
    actions = list(existing)
    seen = {str(item) for item in actions}
    added: list[str] = []
    for item in additions:
        action = str(item).strip()
        if not action or action in seen:
            continue
        actions.append(action)
        added.append(action)
        seen.add(action)
    return actions, added


def _validate_artifact_ids(nodes: dict[str, ResearchNode], artifact_ids: list[str]) -> None:
    for artifact_id in artifact_ids:
        if artifact_id not in nodes:
            raise ValueError(f"Artifact node id does not exist: {artifact_id}")
        if nodes[artifact_id].type != "artifact":
            raise ValueError(f"Artifact node id {artifact_id} must be artifact, got {nodes[artifact_id].type}")


def complete_experiment(
    root: Path,
    *,
    experiment_id: str,
    finding: str,
    confidence: str,
    outcome: str | None = None,
    metrics: list[str] | None = None,
    artifact_ids: list[str] | None = None,
    result_summary: str | None = None,
    next_actions: list[str] | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    state = load_validated_state(root)
    nodes = state.nodes
    if experiment_id not in nodes:
        raise ValueError(f"Experiment node does not exist: {experiment_id}")
    experiment = nodes[experiment_id]
    if experiment.type != "experiment":
        raise ValueError(f"Node {experiment_id} must be experiment, got {experiment.type}")
    if experiment.status in TERMINAL_NON_COMPLETABLE_STATUSES:
        raise ValueError(f"Cannot complete experiment {experiment_id} from status {experiment.status!r}")
    if confidence not in VALID_FINDING_CONFIDENCES:
        allowed = ", ".join(sorted(VALID_FINDING_CONFIDENCES))
        raise ValueError(f"Invalid confidence {confidence!r}; allowed: {allowed}")
    if outcome is not None and outcome not in VALID_FINDING_OUTCOMES:
        allowed = ", ".join(sorted(VALID_FINDING_OUTCOMES))
        raise ValueError(f"Invalid outcome {outcome!r}; allowed: {allowed}")

    metrics = metrics or []
    artifact_ids = artifact_ids or []
    next_actions = next_actions or []
    _validate_artifact_ids(nodes, artifact_ids)

    path = find_node_file(root, experiment_id)
    data = load_yaml(path)
    before_data = copy.deepcopy(data)
    findings = data.get("findings", []) or []
    if not isinstance(findings, list):
        raise ValueError(f"{experiment_id}: findings must be a list")

    before = {
        "status": data.get("status"),
        "finding_count": len(findings),
        "result_summary": data.get("result_summary"),
        "next_actions": list(data.get("next_actions", []) or []),
    }
    today = str(date.today())
    finding_record = {
        "id": _next_finding_id(experiment_id, findings),
        "statement": finding,
        "confidence": confidence,
        "evidence": [experiment_id],
        "outcome": outcome,
        "metrics": metrics,
        "linked_artifacts": artifact_ids,
        "created_at": today,
    }
    findings.append(finding_record)
    data["findings"] = findings
    data["status"] = "done"
    if result_summary is not None:
        data["result_summary"] = result_summary
    data["next_actions"], added_actions = _append_unique_actions(data.get("next_actions"), next_actions)
    if not data["next_actions"]:
        data.pop("next_actions", None)
    data["updated_at"] = today

    candidate = dict(nodes)
    candidate[experiment_id] = ResearchNode.from_dict(data)
    validate_cockpit(root, candidate, state.current, state.explicit_edges, raise_on_error=True)

    after = {
        "status": data.get("status"),
        "finding_count": len(findings),
        "result_summary": data.get("result_summary"),
        "next_actions": list(data.get("next_actions", []) or []),
    }
    result = {
        "experiment_id": experiment_id,
        "finding_id": finding_record["id"],
        "dry_run": dry_run,
        "changed": not dry_run,
        "would_change": True,
        "path": str(path),
        "before": before,
        "after": after,
        "finding": finding_record,
        "added_next_actions": added_actions,
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(path, before_data, data)])
    if dry_run:
        result["changed"] = False
        return result

    finish_mutation(
        root,
        [(path, data)],
        interaction={
            "kind": "complete_experiment",
            "actor": "researcher",
            "node_id": experiment_id,
            "command": f"{script_command('complete_experiment.py')} --id {experiment_id} --confidence {confidence}",
            "before": before,
            "after": after,
            "extra": {
                "experiment_id": experiment_id,
                "finding_id": finding_record["id"],
                "confidence": confidence,
                "outcome": outcome,
                "metric_count": len(metrics),
                "linked_artifacts": artifact_ids,
                "added_next_actions": added_actions,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="experiment_id")
    parser.add_argument("--finding", required=True)
    parser.add_argument("--confidence", required=True, choices=sorted(VALID_FINDING_CONFIDENCES))
    parser.add_argument("--outcome", choices=sorted(VALID_FINDING_OUTCOMES))
    parser.add_argument("--metric", action="append", dest="metrics")
    parser.add_argument("--artifact-id", action="append", dest="artifact_ids", help="Artifact node id; repeat for multiple artifacts.")
    parser.add_argument("--result-summary")
    parser.add_argument("--next-action", action="append", dest="next_actions")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = complete_experiment(
            args.root,
            experiment_id=args.experiment_id,
            finding=args.finding,
            confidence=args.confidence,
            outcome=args.outcome,
            metrics=args.metrics,
            artifact_ids=args.artifact_ids,
            result_summary=args.result_summary,
            next_actions=args.next_actions,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.dry_run:
        print(f"Would complete experiment {args.experiment_id}")
        if args.show_diff and result.get("diff"):
            print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
        return
    print(f"Completed experiment {args.experiment_id}: {result['path']}")
    if not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
