from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1] / "research_cockpit"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cockpit.model import (
    ValidationError,
    build_option_workstream_context,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
)


def option_workstream_context_payload(root: Path, *, option_id: str) -> dict:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
    return build_option_workstream_context(root, nodes, current, option_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--option", required=True, dest="option_id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        payload = option_workstream_context_payload(args.root, option_id=args.option_id)
    except (ValidationError, ValueError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    option = payload["option"]
    problem = payload.get("upstream_problem") or {}
    evidence = payload["evidence_summary"]
    print(f"Option: {option['id']} - {option['title']}")
    print(f"Upstream problem: {problem.get('id') or '(none)'}")
    print(f"Subtree nodes: {len(payload['subtree']['node_ids'])}")
    print(f"Experiments: {evidence['experiment_count']}; findings: {evidence['findings_count']}")
    if evidence.get("latest_finding"):
        print(f"Latest finding: {evidence['latest_finding']}")


if __name__ == "__main__":
    main()
