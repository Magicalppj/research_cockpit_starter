from __future__ import annotations

import argparse
import copy
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import (
    MutationError,
    compact_mutation_result,
    dry_run_preflight_result,
    emit_json,
    finish_mutation,
    load_validated_state,
    yaml_change_diff,
)
from research_cockpit.commands._assignment_scope_cli import add_assignment_scope_args, emit_assignment_scope_error
from research_cockpit.commands._evidence import (
    append_unique,
    evidence_warnings,
    inline_evidence_artifact,
    parse_link_values,
    validate_artifact_ids,
)
from research_cockpit.assignment_scope import AssignmentScopeError, ensure_assignment_scope
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


def _focus_completion_guidance(state_current: dict[str, Any], experiment: ResearchNode, root: Path) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    commands: list[str] = []
    next_focus = experiment.parent or state_current.get("current_option") or state_current.get("current_problem")
    if state_current.get("current_focus_node") == experiment.id:
        warnings.append("current_focus_node_is_terminal")
        if next_focus:
            commands.append(f"research-cockpit set-focus --root {root} --focus-node {next_focus}")
    agent_focuses = state_current.get("agent_focuses")
    if isinstance(agent_focuses, dict):
        for agent_id, focus in agent_focuses.items():
            if not isinstance(focus, dict):
                continue
            if focus.get("current_focus_node") == experiment.id:
                warnings.append(f"agent_focus_is_terminal:{agent_id}")
                if next_focus:
                    commands.append(
                        f"research-cockpit set-agent-focus --root {root} --agent {agent_id} --node {next_focus}"
                    )
    return warnings, commands


def _assignment_cursor_completion_guidance(
    root: Path,
    *,
    assignment_id: str | None,
    experiment: ResearchNode,
) -> tuple[list[str], list[str]]:
    if not assignment_id:
        return [], []
    from research_cockpit.model import load_assignments

    assignment = load_assignments(root).get(assignment_id)
    if assignment is None or assignment.current_node != experiment.id:
        return [], []
    next_focus = experiment.parent
    commands = []
    if next_focus:
        commands.append(
            f"research-cockpit set-cursor --root {root} --assignment {assignment_id} --node {next_focus}"
        )
    return [f"assignment_cursor_is_terminal:{assignment_id}"], commands


def complete_experiment(
    root: Path,
    *,
    experiment_id: str,
    finding: str,
    confidence: str,
    outcome: str | None = None,
    metrics: list[str] | None = None,
    artifact_ids: list[str] | None = None,
    evidence_path: str | None = None,
    evidence_links: dict[str, str] | None = None,
    result_summary: str | None = None,
    next_actions: list[str] | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    assignment_id: str | None = None,
    coordinator: bool = False,
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
    evidence_links = evidence_links or {}
    next_actions = next_actions or []
    if next_actions:
        raise ValueError(
            "complete-experiment no longer writes next_actions to done experiments; "
            "use create-followup-experiment for follow-up work"
        )
    validate_artifact_ids(nodes, artifact_ids)
    resolved_assignment_id = ensure_assignment_scope(
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

    before = {
        "status": data.get("status"),
        "finding_count": len(findings),
        "result_summary": data.get("result_summary"),
        "next_actions": list(data.get("next_actions", []) or []),
    }
    today = str(date.today())
    finding_id = _next_finding_id(experiment_id, findings)
    created_artifact_id, evidence_artifact = inline_evidence_artifact(
        existing_node_ids=set(nodes),
        finding_id=finding_id,
        path=evidence_path,
        links=evidence_links,
        today=today,
    )
    all_artifact_ids = [*artifact_ids, *([created_artifact_id] if created_artifact_id else [])]
    finding_record = {
        "id": finding_id,
        "statement": finding,
        "confidence": confidence,
        "evidence": [experiment_id, *all_artifact_ids],
        "outcome": outcome,
        "metrics": metrics,
        "linked_artifacts": all_artifact_ids,
        "created_at": today,
    }
    findings.append(finding_record)
    data["findings"] = findings
    data["status"] = "done"
    if result_summary is not None:
        data["result_summary"] = result_summary
    existing_next_actions = data.get("next_actions", []) or []
    if not isinstance(existing_next_actions, list):
        raise ValueError(f"{experiment_id}: next_actions must be a list")
    removed_next_actions = list(existing_next_actions)
    data.pop("next_actions", None)
    data["linked_artifacts"], added_artifacts = append_unique(data.get("linked_artifacts"), all_artifact_ids, "linked_artifacts")
    if not data["linked_artifacts"]:
        data.pop("linked_artifacts", None)
    data["updated_at"] = today

    candidate = dict(nodes)
    candidate[experiment_id] = ResearchNode.from_dict(data)
    changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]] = [(path, before_data, data)]
    if evidence_artifact:
        artifact_path = root / "graph" / "nodes" / f"{evidence_artifact['id']}.yaml"
        candidate[str(evidence_artifact["id"])] = ResearchNode.from_dict(evidence_artifact)
        changes.append((artifact_path, None, evidence_artifact))
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
        "removed_next_actions": removed_next_actions,
        "created_artifacts": [created_artifact_id] if created_artifact_id else [],
        "linked_artifacts": all_artifact_ids,
        "added_experiment_artifacts": added_artifacts,
        "warnings": evidence_warnings(all_artifact_ids),
        "recommended_commands": [],
    }
    focus_warnings, focus_commands = _focus_completion_guidance(state.current, experiment, root)
    result["warnings"].extend(focus_warnings)
    result["recommended_commands"].extend(focus_commands)
    assignment_warnings, assignment_commands = _assignment_cursor_completion_guidance(
        root,
        assignment_id=resolved_assignment_id,
        experiment=experiment,
    )
    result["warnings"].extend(assignment_warnings)
    result["recommended_commands"].extend(assignment_commands)
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        result["changed"] = False
        return dry_run_preflight_result(root, result)

    finish_mutation(
        root,
        changes,
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
                "linked_artifacts": all_artifact_ids,
                "created_artifacts": result["created_artifacts"],
                "removed_next_actions": removed_next_actions,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="experiment_id")
    parser.add_argument("--finding", required=True)
    parser.add_argument("--confidence", required=True, choices=sorted(VALID_FINDING_CONFIDENCES))
    parser.add_argument("--outcome", choices=sorted(VALID_FINDING_OUTCOMES))
    parser.add_argument("--metric", action="append", dest="metrics")
    parser.add_argument("--artifact-id", action="append", dest="artifact_ids", help="Artifact node id; repeat for multiple artifacts.")
    parser.add_argument("--evidence-path", help="Result directory or primary evidence path; creates and links an artifact.")
    parser.add_argument("--evidence-link", action="append", dest="evidence_links", help="Evidence link in key=value form; repeat for metrics, plots, reports.")
    parser.add_argument("--result-summary")
    parser.add_argument("--next-action", action="append", dest="next_actions")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    add_assignment_scope_args(parser)
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
            evidence_path=args.evidence_path,
            evidence_links=parse_link_values(args.evidence_links, flag_name="--evidence-link"),
            result_summary=args.result_summary,
            next_actions=args.next_actions,
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
        if args.json:
            emit_json(exc.payload)
        else:
            print(str(exc))
        raise SystemExit(1) from exc
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(
            compact_mutation_result(
                result,
                command="complete-experiment",
                target=result["experiment_id"],
                root=args.root,
                updated=[result["experiment_id"]],
            ) if args.compact else result
        )
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
