from __future__ import annotations

import argparse
import copy
import json
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
from research_cockpit.assignment_scope import AssignmentScopeError, ensure_assignment_scope
from research_cockpit.commands._assignment_scope_cli import add_assignment_scope_args, emit_assignment_scope_error
from research_cockpit.commands._runtime import (
    compact_mutation_result,
    dry_run_preflight_result,
    emit_json,
    finish_mutation,
    load_targeted_state,
    validate_mutation_candidate,
    yaml_change_diff,
)
from research_cockpit.commands._evidence import (
    append_unique,
    evidence_warnings,
    inline_evidence_artifact,
    parse_link_values,
    validate_artifact_ids,
)


def find_node_file(root: Path, node_id: str) -> Path:
    direct_path = root / "graph" / "nodes" / f"{node_id}.yaml"
    if direct_path.exists():
        data = load_yaml(direct_path)
        if str(data.get("id") or "") == node_id:
            return direct_path
    for path in sorted((root / "graph" / "nodes").glob("*.yaml")):
        if path == direct_path:
            continue
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
    evidence_path: str | None = None,
    evidence_links: dict[str, str] | None = None,
    rebuild_dashboard: bool = True,
    assignment_id: str | None = None,
    coordinator: bool = False,
) -> Path:
    result = record_finding_result(
        root,
        experiment_id=experiment_id,
        statement=statement,
        confidence=confidence,
        outcome=outcome,
        metrics=metrics,
        artifacts=artifacts,
        summary=summary,
        evidence_path=evidence_path,
        evidence_links=evidence_links,
        rebuild_dashboard=rebuild_dashboard,
        dry_run=False,
        show_diff=False,
        assignment_id=assignment_id,
        coordinator=coordinator,
    )
    return Path(str(result["path"]))


def record_finding_result(
    root: Path,
    *,
    experiment_id: str,
    statement: str,
    confidence: str,
    outcome: str | None = None,
    metrics: list[str] | None = None,
    artifacts: list[str] | None = None,
    summary: str | None = None,
    evidence_path: str | None = None,
    evidence_links: dict[str, str] | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    assignment_id: str | None = None,
    coordinator: bool = False,
) -> dict[str, Any]:
    artifacts = artifacts or []
    evidence_links = evidence_links or {}
    state = load_targeted_state(root, node_ids=[experiment_id, *artifacts])
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
    validate_artifact_ids(nodes, artifacts)
    ensure_assignment_scope(
        root,
        nodes,
        assignment_id=assignment_id,
        coordinator=coordinator,
        target_node_ids=[experiment_id, *artifacts],
    )

    path = find_node_file(root, experiment_id)
    data = load_yaml(path)
    before_data = copy.deepcopy(data)
    findings = data.get("findings", []) or []
    if not isinstance(findings, list):
        raise ValueError(f"{experiment_id}: findings must be a list")
    before_summary = {
        "finding_count": len(findings),
        "result_summary": data.get("result_summary"),
    }

    finding_id = _next_finding_id(experiment_id, findings)
    created_artifact_id, evidence_artifact = inline_evidence_artifact(
        existing_node_ids=set(nodes),
        finding_id=finding_id,
        path=evidence_path,
        links=evidence_links,
        today=str(date.today()),
    )
    all_artifacts = [*artifacts, *([created_artifact_id] if created_artifact_id else [])]
    finding = {
        "id": finding_id,
        "statement": statement,
        "confidence": confidence,
        "evidence": [experiment_id],
        "outcome": outcome,
        "metrics": metrics or [],
        "linked_artifacts": all_artifacts,
        "created_at": str(date.today()),
    }
    findings.append(finding)
    data["findings"] = findings
    data["linked_artifacts"], added_artifacts = append_unique(data.get("linked_artifacts"), all_artifacts, "linked_artifacts")
    if not data["linked_artifacts"]:
        data.pop("linked_artifacts", None)
    if summary is not None:
        data["result_summary"] = summary
    data["updated_at"] = str(date.today())

    candidate = dict(nodes)
    candidate[experiment_id] = ResearchNode.from_dict(data)
    changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]] = [(path, before_data, data)]
    if evidence_artifact:
        artifact_path = root / "graph" / "nodes" / f"{evidence_artifact['id']}.yaml"
        candidate[str(evidence_artifact["id"])] = ResearchNode.from_dict(evidence_artifact)
        changes.append((artifact_path, None, evidence_artifact))
    validate_mutation_candidate(root, state, nodes=candidate)

    after_summary = {
        "finding_count": len(findings),
        "result_summary": data.get("result_summary"),
        "latest_finding_id": finding["id"],
    }
    result: dict[str, Any] = {
        "experiment_id": experiment_id,
        "finding_id": finding["id"],
        "dry_run": dry_run,
        "changed": False if dry_run else True,
        "would_change": True,
        "path": str(path),
        "before": before_summary,
        "after": after_summary,
        "finding": finding,
        "created_artifacts": [created_artifact_id] if created_artifact_id else [],
        "linked_artifacts": all_artifacts,
        "added_experiment_artifacts": added_artifacts,
        "warnings": evidence_warnings(all_artifacts),
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        return dry_run_preflight_result(root, result)

    finish_mutation(
        root,
        changes,
        interaction={
            "kind": "record_finding",
            "actor": "researcher",
            "node_id": experiment_id,
            "command": f"{script_command('record_finding.py')} --experiment {experiment_id} --confidence {confidence}",
            "before": before_summary,
            "after": after_summary,
            "extra": {
                "experiment_id": experiment_id,
                "finding_id": finding["id"],
                "confidence": confidence,
                "outcome": outcome,
                "metric_count": len(metrics or []),
                "linked_artifacts": all_artifacts,
                "created_artifacts": result["created_artifacts"],
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--statement", required=True)
    parser.add_argument("--confidence", required=True, choices=sorted(VALID_FINDING_CONFIDENCES))
    parser.add_argument("--outcome", choices=sorted(VALID_FINDING_OUTCOMES))
    parser.add_argument("--metric", action="append", dest="metrics")
    parser.add_argument("--artifact-id", action="append", dest="artifacts", help="Artifact node id; repeat for multiple artifacts.")
    parser.add_argument("--evidence-path", help="Result directory or primary evidence path; creates and links an artifact.")
    parser.add_argument("--evidence-link", action="append", dest="evidence_links", help="Evidence link in key=value form; repeat for metrics, plots, reports.")
    parser.add_argument("--summary")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    add_assignment_scope_args(parser)
    parser.add_argument("--progress", action="store_true", help="Print phase progress to stderr.")
    args = parser.parse_args()

    try:
        result = record_finding_result(
            args.root,
            experiment_id=args.experiment,
            statement=args.statement,
            confidence=args.confidence,
            outcome=args.outcome,
            metrics=args.metrics,
            artifacts=args.artifacts,
            summary=args.summary,
            evidence_path=args.evidence_path,
            evidence_links=parse_link_values(args.evidence_links, flag_name="--evidence-link"),
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
            assignment_id=args.assignment,
            coordinator=args.coordinator,
        )
    except AssignmentScopeError as exc:
        emit_assignment_scope_error(args, exc)
        raise SystemExit(1) from exc
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        payload = (
            compact_mutation_result(
                result,
                command="record-finding",
                target={"experiment_id": result["experiment_id"], "finding_id": result["finding_id"]},
                root=args.root,
                created=result.get("created_artifacts", []),
                updated=[result["experiment_id"]],
            )
            if args.compact
            else result
        )
        emit_json(payload, compact=args.compact)
        return
    verb = "Would update" if args.dry_run else "Updated"
    print(f"{verb} {result['path']}")
    if args.show_diff and result.get("diff"):
        print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
