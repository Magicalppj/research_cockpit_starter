from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "research_cockpit"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cockpit.model import (
    ResearchNode,
    ValidationError,
    build_decision_acceptance_checklist,
    decision_acceptance_failure_message,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    save_yaml,
    validate_cockpit,
)
from scripts.build_dashboard import build_dashboard
from scripts.record_finding import find_node_file


def accept_decision(
    root: Path,
    *,
    decision_id: str,
    force_accept: bool = False,
    rebuild_dashboard: bool = True,
) -> dict[str, Any]:
    nodes = load_nodes(root)
    if decision_id not in nodes:
        raise ValueError(f"Decision node does not exist: {decision_id}")
    decision = nodes[decision_id]
    if decision.type != "decision":
        raise ValueError(f"Node {decision_id} must be decision, got {decision.type}")

    checklist = build_decision_acceptance_checklist(nodes, decision_id)
    if not force_accept and not checklist["ready"]:
        raise ValueError(decision_acceptance_failure_message(checklist))

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
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, candidate, current, explicit_edges, raise_on_error=True)

    save_yaml(decision_path, decision_data)
    save_yaml(option_path, option_data)
    save_yaml(problem_path, problem_data)
    if rebuild_dashboard:
        build_dashboard(root)
    return {
        "decision_id": decision_id,
        "option_id": str(option_id),
        "problem_id": str(problem_id),
        "forced": force_accept,
        "checklist": checklist,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="decision_id")
    parser.add_argument("--force-accept", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = accept_decision(
            args.root,
            decision_id=args.decision_id,
            force_accept=args.force_accept,
            rebuild_dashboard=not args.no_build,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    forced = " with force-accept" if result["forced"] else ""
    print(f"Accepted {result['decision_id']}{forced}")
    if not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
