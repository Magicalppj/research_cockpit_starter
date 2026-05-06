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
    load_explicit_edges,
    load_nodes,
    load_yaml,
    script_command,
    validate_cockpit,
)
from research_cockpit.decisions import build_decision_evidence_bundle, normalize_locale
from research_cockpit.commands._runtime import finish_mutation, yaml_change_diff
from research_cockpit.commands.record_finding import find_node_file


def update_decision_evidence(
    root: Path,
    *,
    decision_id: str,
    locale: str | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
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
    before_data = copy.deepcopy(decision_data)
    current = load_yaml(root / "current_state.yaml")
    bundle = build_decision_evidence_bundle(
        nodes,
        str(option_id),
        decision_data.get("supporting_experiments", []) or [],
        locale=normalize_locale(locale, current),
    )
    decision_data["supporting_experiments"] = bundle["supporting_experiments"]
    decision_data["evidence_strength"] = bundle["evidence_strength"]
    decision_data["evidence_summary"] = bundle["evidence_summary"]
    decision_data["updated_at"] = str(date.today())

    candidate = dict(nodes)
    candidate[decision_id] = ResearchNode.from_dict(decision_data)
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, candidate, current, explicit_edges, raise_on_error=True)
    changed = before_data != decision_data
    if changed and not dry_run:
        finish_mutation(
            root,
            [(decision_path, decision_data)],
            interaction={
                "kind": "update_decision_evidence",
                "actor": "researcher",
                "node_id": decision_id,
                "command": f"{script_command('update_decision_evidence.py')} --id {decision_id}",
                "before": {
                    "decision_id": decision_id,
                    "evidence_strength": before_data.get("evidence_strength"),
                    "supporting_experiments": before_data.get("supporting_experiments"),
                },
                "after": {
                    "decision_id": decision_id,
                    "evidence_strength": bundle["evidence_strength"],
                    "supporting_experiments": bundle["supporting_experiments"],
                },
            },
            rebuild_dashboard=rebuild_dashboard,
        )
    result: dict[str, Any] = {
        "decision_id": decision_id,
        "path": str(decision_path),
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "before": {
            "supporting_experiments": before_data.get("supporting_experiments"),
            "evidence_strength": before_data.get("evidence_strength"),
            "evidence_summary": before_data.get("evidence_summary"),
        },
        "after": {
            "supporting_experiments": decision_data.get("supporting_experiments"),
            "evidence_strength": decision_data.get("evidence_strength"),
            "evidence_summary": decision_data.get("evidence_summary"),
        },
        "bundle": bundle,
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(decision_path, before_data, decision_data)])
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="decision_id")
    parser.add_argument("--locale", choices=["en", "zh"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = update_decision_evidence(
            args.root,
            decision_id=args.decision_id,
            locale=args.locale,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValidationError, ValueError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    verb = "Would update" if args.dry_run else "Updated"
    print(
        f"{verb} evidence for {result['decision_id']}: "
        f"{result['bundle']['evidence_strength']}"
    )
    if args.show_diff and result.get("diff"):
        print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build and result["changed"]:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
