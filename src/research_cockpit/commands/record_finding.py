from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from research_cockpit.paths import default_data_root
from typing import Any

ROOT = default_data_root()

from research_cockpit.model import (
    ResearchNode,
    VALID_FINDING_CONFIDENCES,
    VALID_FINDING_OUTCOMES,
    load_yaml,
    script_command,
    validate_cockpit,
)
from research_cockpit.commands._runtime import finish_mutation, load_validated_state


def find_node_file(root: Path, node_id: str) -> Path:
    for path in sorted((root / "graph" / "nodes").glob("*.yaml")):
        data = load_yaml(path)
        if str(data.get("id")) == node_id:
            return path
    raise FileNotFoundError(f"Node does not exist: {node_id}")


def _next_finding_id(experiment_id: str, findings: list[dict[str, Any]]) -> str:
    used = {str(finding.get("id")) for finding in findings if isinstance(finding, dict)}
    index = len(used) + 1
    while True:
        candidate = f"{experiment_id}_finding_{index:03d}"
        if candidate not in used:
            return candidate
        index += 1


def record_finding(
    root: Path,
    *,
    experiment_id: str,
    statement: str,
    confidence: str,
    outcome: str | None = None,
    metrics: list[str] | None = None,
    artifacts: list[str] | None = None,
    summary: str | None = None,
    rebuild_dashboard: bool = True,
) -> Path:
    state = load_validated_state(root)
    nodes = state.nodes
    if experiment_id not in nodes:
        raise FileNotFoundError(f"Experiment node does not exist: {experiment_id}")
    experiment = nodes[experiment_id]
    if experiment.type != "experiment":
        raise ValueError(f"Node {experiment_id} must be experiment, got {experiment.type}")
    if confidence not in VALID_FINDING_CONFIDENCES:
        allowed = ", ".join(sorted(VALID_FINDING_CONFIDENCES))
        raise ValueError(f"Invalid confidence {confidence!r}; allowed: {allowed}")
    if outcome is not None and outcome not in VALID_FINDING_OUTCOMES:
        allowed = ", ".join(sorted(VALID_FINDING_OUTCOMES))
        raise ValueError(f"Invalid outcome {outcome!r}; allowed: {allowed}")

    artifacts = artifacts or []
    for artifact_id in artifacts:
        if artifact_id not in nodes:
            raise ValueError(f"Artifact node id does not exist: {artifact_id}")
        if nodes[artifact_id].type != "artifact":
            raise ValueError(f"Artifact node id {artifact_id} must be artifact, got {nodes[artifact_id].type}")

    path = find_node_file(root, experiment_id)
    data = load_yaml(path)
    findings = data.get("findings", []) or []
    if not isinstance(findings, list):
        raise ValueError(f"{experiment_id}: findings must be a list")
    before_summary = {
        "finding_count": len(findings),
        "result_summary": data.get("result_summary"),
    }

    finding = {
        "id": _next_finding_id(experiment_id, findings),
        "statement": statement,
        "confidence": confidence,
        "evidence": [experiment_id],
        "outcome": outcome,
        "metrics": metrics or [],
        "linked_artifacts": artifacts,
        "created_at": str(date.today()),
    }
    findings.append(finding)
    data["findings"] = findings
    if summary is not None:
        data["result_summary"] = summary
    data["updated_at"] = str(date.today())

    candidate = dict(nodes)
    candidate[experiment_id] = ResearchNode.from_dict(data)
    validate_cockpit(root, candidate, state.current, state.explicit_edges, raise_on_error=True)

    finish_mutation(
        root,
        [(path, data)],
        interaction={
            "kind": "record_finding",
            "actor": "researcher",
            "node_id": experiment_id,
            "command": f"{script_command('record_finding.py')} --experiment {experiment_id} --confidence {confidence}",
            "before": before_summary,
            "after": {
                "finding_count": len(findings),
                "result_summary": data.get("result_summary"),
                "latest_finding_id": finding["id"],
            },
            "extra": {
                "experiment_id": experiment_id,
                "finding_id": finding["id"],
                "confidence": confidence,
                "outcome": outcome,
                "metric_count": len(metrics or []),
                "linked_artifacts": artifacts,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--statement", required=True)
    parser.add_argument("--confidence", required=True, choices=sorted(VALID_FINDING_CONFIDENCES))
    parser.add_argument("--outcome", choices=sorted(VALID_FINDING_OUTCOMES))
    parser.add_argument("--metric", action="append", dest="metrics")
    parser.add_argument("--artifact-id", action="append", dest="artifacts", help="Artifact node id; repeat for multiple artifacts.")
    parser.add_argument("--artifact", action="append", dest="artifacts", help="Deprecated alias for --artifact-id.")
    parser.add_argument("--summary")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    out = record_finding(
        args.root,
        experiment_id=args.experiment,
        statement=args.statement,
        confidence=args.confidence,
        outcome=args.outcome,
        metrics=args.metrics,
        artifacts=args.artifacts,
        summary=args.summary,
        rebuild_dashboard=not args.no_build,
    )
    print(f"Updated {out}")
    if not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
