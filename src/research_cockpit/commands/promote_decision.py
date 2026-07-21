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
    ValidationError,
    load_nodes,
    load_yaml,
    script_command,
    validate_cockpit,
)
from research_cockpit.decisions import (
    build_decision_acceptance_checklist,
    build_decision_evidence_bundle,
    decision_acceptance_failure_message,
    normalize_locale,
)
from research_cockpit.commands._runtime import dry_run_preflight_result, finish_mutation
from research_cockpit.commands.record_finding import find_node_file
from research_cockpit.lifecycle_guards import LifecycleGuardError, raise_for_terminal_parent_transitions


VALID_DECISION_STATUSES = {"proposed", "accepted"}
VALID_EVIDENCE_STRENGTHS = {"none", "weak", "medium", "strong"}


def _validate_refs(
    nodes: dict[str, ResearchNode],
    refs: list[str],
    expected_type: str,
    field_name: str,
) -> None:
    for ref_id in refs:
        if ref_id not in nodes:
            raise ValueError(f"{field_name} references missing node {ref_id}")
        if nodes[ref_id].type != expected_type:
            raise ValueError(f"{field_name} reference {ref_id} must be {expected_type}, got {nodes[ref_id].type}")


def promote_decision(
    root: Path,
    *,
    decision_id: str,
    option_id: str,
    title: str,
    summary: str,
    status: str = "proposed",
    supporting_experiments: list[str] | None = None,
    alternatives: list[str] | None = None,
    consequences: list[str] | None = None,
    next_required_actions: list[str] | None = None,
    evidence_strength: str = "none",
    auto_evidence: bool = False,
    locale: str | None = None,
    force_accept: bool = False,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    operation_request: dict[str, Any] | None = None,
) -> Path | dict[str, Any]:
    nodes = load_nodes(root)
    if decision_id in nodes:
        raise FileExistsError(root / "graph" / "nodes" / f"{decision_id}.yaml")
    if option_id not in nodes:
        raise ValueError(f"Option node does not exist: {option_id}")
    option = nodes[option_id]
    if option.type != "option":
        raise ValueError(f"Node {option_id} must be option, got {option.type}")
    if status not in VALID_DECISION_STATUSES:
        allowed = ", ".join(sorted(VALID_DECISION_STATUSES))
        raise ValueError(f"Invalid decision status {status!r}; allowed: {allowed}")
    if evidence_strength not in VALID_EVIDENCE_STRENGTHS:
        allowed = ", ".join(sorted(VALID_EVIDENCE_STRENGTHS))
        raise ValueError(f"Invalid evidence strength {evidence_strength!r}; allowed: {allowed}")

    supporting_experiments = supporting_experiments or []
    alternatives = alternatives or []
    consequences = consequences or []
    next_required_actions = next_required_actions or []
    _validate_refs(nodes, supporting_experiments, "experiment", "supporting_experiments")
    _validate_refs(nodes, alternatives, "option", "alternatives")
    evidence_summary = None
    if auto_evidence:
        current = load_yaml(root / "current_state.yaml")
        evidence_bundle = build_decision_evidence_bundle(
            nodes,
            option_id,
            supporting_experiments,
            locale=normalize_locale(locale, current),
        )
        supporting_experiments = evidence_bundle["supporting_experiments"]
        if evidence_strength == "none":
            evidence_strength = evidence_bundle["evidence_strength"]
        evidence_summary = evidence_bundle["evidence_summary"]

    today = str(date.today())
    decision_data = {
        "id": decision_id,
        "type": "decision",
        "title": title,
        "status": status,
        "priority": option.priority,
        "parent": option_id,
        "summary": summary,
        "decision_status": status,
        "derived_from": [option_id],
        "supporting_experiments": supporting_experiments,
        "alternatives_considered": alternatives,
        "consequences": consequences,
        "next_required_actions": next_required_actions,
        "evidence_strength": evidence_strength,
        "created_at": today,
        "updated_at": today,
    }
    if evidence_summary:
        decision_data["evidence_summary"] = evidence_summary

    candidate = dict(nodes)
    candidate[decision_id] = ResearchNode.from_dict(decision_data)
    option_data = None
    problem_data = None
    option_before = None
    problem_before = None
    option_before_data = None
    problem_before_data = None
    problem_id = option.parent
    if status == "accepted":
        checklist = build_decision_acceptance_checklist(candidate, decision_id)
        if not force_accept and not checklist["ready"]:
            raise ValueError(decision_acceptance_failure_message(checklist))

        option_path = find_node_file(root, option_id)
        option_data = load_yaml(option_path)
        option_before_data = copy.deepcopy(option_data)
        option_before = {
            "status": option_data.get("status"),
            "decision_state": option_data.get("decision_state"),
        }
        option_data["status"] = "accepted"
        option_data["decision_state"] = "accepted"
        option_data["updated_at"] = today
        candidate[option_id] = ResearchNode.from_dict(option_data)

        if problem_id and problem_id in nodes and nodes[problem_id].type == "problem":
            problem_path = find_node_file(root, str(problem_id))
            problem_data = load_yaml(problem_path)
            problem_before_data = copy.deepcopy(problem_data)
            problem_before = {
                "status": problem_data.get("status"),
                "resolved_by": problem_data.get("resolved_by"),
                "current_best_option": problem_data.get("current_best_option"),
            }
            problem_data["status"] = "resolved"
            problem_data["resolved_by"] = decision_id
            problem_data["current_best_option"] = option_id
            problem_data["updated_at"] = today
            candidate[str(problem_id)] = ResearchNode.from_dict(problem_data)

    guard_node_ids = [option_id]
    if problem_data is not None and problem_id:
        guard_node_ids.append(str(problem_id))
    raise_for_terminal_parent_transitions(root, nodes, candidate, guard_node_ids)
    validate_cockpit(root, candidate, load_yaml(root / "current_state.yaml"), raise_on_error=True)

    out = root / "graph" / "nodes" / f"{decision_id}.yaml"
    preview = {
        "dry_run": dry_run,
        "changed": not dry_run,
        "decision_id": decision_id,
        "option_id": option_id,
        "status": status,
        "path": str(out),
        "decision": decision_data,
        "option": {
            "before": option_before,
            "after": {
                "status": option_data.get("status"),
                "decision_state": option_data.get("decision_state"),
            } if option_data is not None else None,
        },
        "problem": {
            "before": problem_before,
            "after": {
                "status": problem_data.get("status"),
                "resolved_by": problem_data.get("resolved_by"),
                "current_best_option": problem_data.get("current_best_option"),
            } if problem_data is not None else None,
        },
    }
    if dry_run:
        preview["changed"] = False
        return dry_run_preflight_result(root, preview)

    changes = [(out, None, decision_data)]
    if option_data is not None:
        changes.append((find_node_file(root, option_id), option_before_data, option_data))
    if problem_data is not None and problem_id:
        changes.append((find_node_file(root, str(problem_id)), problem_before_data, problem_data))
    finish_mutation(
        root,
        changes,
        interaction={
            "kind": "promote_decision",
            "actor": "researcher",
            "node_id": decision_id,
            "command": f"{script_command('promote_decision.py')} --id {decision_id} --option {option_id}",
            "after": {
                "decision_id": decision_id,
                "option_id": option_id,
                "status": status,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
        operation_request=operation_request,
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="decision_id")
    parser.add_argument("--option", required=True, dest="option_id")
    parser.add_argument("--title", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--status", default="proposed", choices=sorted(VALID_DECISION_STATUSES))
    parser.add_argument("--supporting-experiment", action="append", dest="supporting_experiments")
    parser.add_argument("--alternative", action="append", dest="alternatives")
    parser.add_argument("--consequence", action="append", dest="consequences")
    parser.add_argument("--next-required-action", action="append", dest="next_required_actions")
    parser.add_argument("--evidence-strength", default="none", choices=sorted(VALID_EVIDENCE_STRENGTHS))
    parser.add_argument("--auto-evidence", action="store_true")
    parser.add_argument("--locale", choices=["en", "zh"])
    parser.add_argument("--force-accept", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        out = promote_decision(
            args.root,
            decision_id=args.decision_id,
            option_id=args.option_id,
            title=args.title,
            summary=args.summary,
            status=args.status,
            supporting_experiments=args.supporting_experiments,
            alternatives=args.alternatives,
            consequences=args.consequences,
            next_required_actions=args.next_required_actions,
            evidence_strength=args.evidence_strength,
            auto_evidence=args.auto_evidence,
            locale=args.locale,
            force_accept=args.force_accept,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
        )
    except LifecycleGuardError as exc:
        if args.json:
            print(json.dumps(exc.payload, ensure_ascii=False, indent=2))
        else:
            print(str(exc))
        raise SystemExit(1) from exc
    except (ValidationError, ValueError, FileExistsError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        if isinstance(out, dict):
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({
                "dry_run": False,
                "changed": True,
                "decision_id": args.decision_id,
                "option_id": args.option_id,
                "status": args.status,
                "path": str(out),
            }, ensure_ascii=False, indent=2))
        return

    if args.dry_run:
        print(f"Would create {out['path'] if isinstance(out, dict) else out}")
        return

    print(f"Created {out}")
    if not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
