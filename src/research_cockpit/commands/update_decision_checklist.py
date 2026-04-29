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
    load_explicit_edges,
    load_nodes,
    load_yaml,
    save_yaml,
    validate_cockpit,
)
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.commands.record_finding import find_node_file


def _append_unique(field_name: str, existing: Any, additions: list[str]) -> tuple[list[str], list[str]]:
    if existing is None:
        existing = []
    if not isinstance(existing, list):
        raise ValueError(f"{field_name} must be a list")
    values = [str(item) for item in existing if str(item).strip()]
    added: list[str] = []
    seen = set(values)
    for item in additions:
        value = str(item).strip()
        if not value or value in seen:
            continue
        values.append(value)
        added.append(value)
        seen.add(value)
    return values, added


def update_decision_checklist(
    root: Path,
    *,
    decision_id: str,
    alternatives: list[str] | None = None,
    consequences: list[str] | None = None,
    next_required_actions: list[str] | None = None,
    evidence_summary: str | None = None,
    rebuild_dashboard: bool = True,
) -> dict[str, Any]:
    nodes = load_nodes(root)
    if decision_id not in nodes:
        raise ValueError(f"Decision node does not exist: {decision_id}")
    decision = nodes[decision_id]
    if decision.type != "decision":
        raise ValueError(f"Node {decision_id} must be decision, got {decision.type}")

    alternatives = alternatives or []
    for option_id in alternatives:
        if option_id not in nodes:
            raise ValueError(f"Alternative option does not exist: {option_id}")
        if nodes[option_id].type != "option":
            raise ValueError(f"Alternative {option_id} must be option, got {nodes[option_id].type}")

    decision_path = find_node_file(root, decision_id)
    decision_data = load_yaml(decision_path)
    added: dict[str, list[str]] = {}

    decision_data["alternatives_considered"], added["alternatives_considered"] = _append_unique(
        "alternatives_considered",
        decision_data.get("alternatives_considered", []),
        alternatives,
    )
    decision_data["consequences"], added["consequences"] = _append_unique(
        "consequences",
        decision_data.get("consequences", []),
        consequences or [],
    )
    decision_data["next_required_actions"], added["next_required_actions"] = _append_unique(
        "next_required_actions",
        decision_data.get("next_required_actions", []),
        next_required_actions or [],
    )
    if evidence_summary is not None:
        decision_data["evidence_summary"] = evidence_summary
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
        "path": str(decision_path),
        "added": added,
        "evidence_summary_updated": evidence_summary is not None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="decision_id")
    parser.add_argument("--alternative", action="append", dest="alternatives", help="Existing option node id; repeat for multiple alternatives.")
    parser.add_argument("--consequence", action="append", dest="consequences")
    parser.add_argument("--next-required-action", action="append", dest="next_required_actions")
    parser.add_argument("--evidence-summary")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = update_decision_checklist(
            args.root,
            decision_id=args.decision_id,
            alternatives=args.alternatives,
            consequences=args.consequences,
            next_required_actions=args.next_required_actions,
            evidence_summary=args.evidence_summary,
            rebuild_dashboard=not args.no_build,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    print(f"Updated decision checklist for {result['decision_id']}: {result['path']}")
    if not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
