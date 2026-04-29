from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from research_cockpit.paths import default_data_root
from typing import Any

ROOT = default_data_root()

from research_cockpit.model import (
    ResearchNode,
    ValidationError,
    build_decision_evidence_bundle,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    save_yaml,
    validate_cockpit,
)
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.commands.record_finding import find_node_file


def update_decision_evidence(
    root: Path,
    *,
    decision_id: str,
    rebuild_dashboard: bool = True,
) -> dict[str, Any]:
    nodes = load_nodes(root)
    if decision_id not in nodes:
        raise ValueError(f"Decision node does not exist: {decision_id}")
    decision = nodes[decision_id]
    if decision.type != "decision":
        raise ValueError(f"Node {decision_id} must be decision, got {decision.type}")
    option_id = decision.parent
    if not option_id or option_id not in nodes or nodes[str(option_id)].type != "option":
        raise ValueError(f"{decision_id}: decision parent must be an option node")

    decision_path = find_node_file(root, decision_id)
    decision_data = load_yaml(decision_path)
    bundle = build_decision_evidence_bundle(
        nodes,
        str(option_id),
        decision_data.get("supporting_experiments", []) or [],
    )
    decision_data["supporting_experiments"] = bundle["supporting_experiments"]
    decision_data["evidence_strength"] = bundle["evidence_strength"]
    decision_data["evidence_summary"] = bundle["evidence_summary"]
    decision_data["updated_at"] = str(date.today())

    candidate = dict(nodes)
    candidate[decision_id] = ResearchNode.from_dict(decision_data)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, candidate, current, explicit_edges, raise_on_error=True)
    save_yaml(decision_path, decision_data)
    if rebuild_dashboard:
        build_dashboard(root)
    return {
        "decision_id": decision_id,
        "bundle": bundle,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="decision_id")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = update_decision_evidence(
            args.root,
            decision_id=args.decision_id,
            rebuild_dashboard=not args.no_build,
        )
    except (ValidationError, ValueError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    print(
        f"Updated evidence for {result['decision_id']}: "
        f"{result['bundle']['evidence_strength']}"
    )
    if not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
