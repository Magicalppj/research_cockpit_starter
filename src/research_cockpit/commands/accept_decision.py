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
    load_yaml,
    script_command,
    validate_cockpit,
)
from research_cockpit.decisions import build_decision_acceptance_checklist, decision_acceptance_failure_message
from research_cockpit.commands._runtime import dry_run_preflight_result, finish_mutation, load_validated_state
from research_cockpit.commands.record_finding import find_node_file


class DecisionNotReadyError(ValueError):
    def __init__(self, message: str, checklist: dict[str, Any]) -> None:
        super().__init__(message)
        self.checklist = checklist


def accept_decision(
    root: Path,
    *,
    decision_id: str,
    force_accept: bool = False,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    state = load_validated_state(root)
    nodes = state.nodes
    if decision_id not in nodes:
        raise ValueError(f"Decision node does not exist: {decision_id}")
    decision = nodes[decision_id]
    if decision.type != "decision":
        raise ValueError(f"Node {decision_id} must be decision, got {decision.type}")

    checklist = build_decision_acceptance_checklist(nodes, decision_id)
    if not force_accept and not checklist["ready"]:
        raise DecisionNotReadyError(decision_acceptance_failure_message(checklist), checklist)

    option_id = decision.parent
    if not option_id or option_id not in nodes or nodes[str(option_id)].type != "option":
        raise ValueError(f"{decision_id}: decision parent must be an option node")
    option = nodes[str(option_id)]
    problem_id = option.parent
    if not problem_id or problem_id not in nodes or nodes[str(problem_id)].type != "problem":
        raise ValueError(f"{decision_id}: parent option must belong to a problem node")

    today = str(date.today())
    decision_path = find_node_file(root, decision_id)
    option_path = find_node_file(root, str(option_id))
    problem_path = find_node_file(root, str(problem_id))
    decision_data = load_yaml(decision_path)
    option_data = load_yaml(option_path)
    problem_data = load_yaml(problem_path)
    decision_before_data = copy.deepcopy(decision_data)
    option_before_data = copy.deepcopy(option_data)
    problem_before_data = copy.deepcopy(problem_data)
    before = {
        "decision_status": decision_data.get("status"),
        "option_status": option_data.get("status"),
        "option_decision_state": option_data.get("decision_state"),
        "problem_status": problem_data.get("status"),
        "problem_resolved_by": problem_data.get("resolved_by"),
        "problem_current_best_option": problem_data.get("current_best_option"),
    }

    decision_data["status"] = "accepted"
    decision_data["decision_status"] = "accepted"
    decision_data["updated_at"] = today
    option_data["status"] = "accepted"
    option_data["decision_state"] = "accepted"
    option_data["updated_at"] = today
    problem_data["status"] = "resolved"
    problem_data["resolved_by"] = decision_id
    problem_data["current_best_option"] = str(option_id)
    problem_data["updated_at"] = today

    candidate = dict(nodes)
    candidate[decision_id] = ResearchNode.from_dict(decision_data)
    candidate[str(option_id)] = ResearchNode.from_dict(option_data)
    candidate[str(problem_id)] = ResearchNode.from_dict(problem_data)
    validate_cockpit(root, candidate, state.current, state.explicit_edges, raise_on_error=True)

    after = {
        "decision_status": decision_data.get("status"),
        "option_status": option_data.get("status"),
        "option_decision_state": option_data.get("decision_state"),
        "problem_status": problem_data.get("status"),
        "problem_resolved_by": problem_data.get("resolved_by"),
        "problem_current_best_option": problem_data.get("current_best_option"),
    }
    result = {
        "decision_id": decision_id,
        "option_id": str(option_id),
        "problem_id": str(problem_id),
        "forced": force_accept,
        "dry_run": dry_run,
        "changed": not dry_run,
        "checklist": checklist,
        "before": before,
        "after": after,
    }
    if dry_run:
        result["changed"] = False
        return dry_run_preflight_result(root, result)

    command = f"{script_command('accept_decision.py')} --id {decision_id}"
    if force_accept:
        command += " --force-accept"
    finish_mutation(
        root,
        [
            (decision_path, decision_before_data, decision_data),
            (option_path, option_before_data, option_data),
            (problem_path, problem_before_data, problem_data),
        ],
        interaction={
            "kind": "accept_decision",
            "actor": "researcher",
            "node_id": decision_id,
            "command": command,
            "before": before,
            "after": after,
            "extra": {
                "decision_id": decision_id,
                "option_id": str(option_id),
                "problem_id": str(problem_id),
                "forced": force_accept,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="decision_id")
    parser.add_argument("--force-accept", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = accept_decision(
            args.root,
            decision_id=args.decision_id,
            force_accept=args.force_accept,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
        )
    except DecisionNotReadyError as exc:
        if args.json:
            payload = {
                "decision_id": args.decision_id,
                "dry_run": args.dry_run,
                "changed": False,
                "ready": False,
                "error": str(exc),
                "checklist": exc.checklist,
                "blocking_failures": exc.checklist.get("blocking_failures", []),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(str(exc))
        raise SystemExit(1) from exc
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    forced = " with force-accept" if result["forced"] else ""
    if args.dry_run:
        print(f"Would accept {result['decision_id']}{forced}")
        return

    print(f"Accepted {result['decision_id']}{forced}")
    if not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
