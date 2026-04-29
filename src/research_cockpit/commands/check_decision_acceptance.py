from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.model import (
    ValidationError,
    build_decision_acceptance_checklist,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
)


def decision_acceptance_payload(root: Path, decision_id: str) -> dict:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
    return build_decision_acceptance_checklist(nodes, decision_id)


def _print_human(payload: dict) -> None:
    state = "READY" if payload.get("ready") else "NOT READY"
    print(f"{state}: {payload.get('decision_id')} - {payload.get('decision_title')}")
    for item in payload.get("checks", []):
        marker = {"pass": "OK", "fail": "FAIL", "warning": "WARN"}.get(item.get("state"), str(item.get("state")))
        print(f"- [{marker}] {item.get('label')}: {item.get('reason')}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="decision_id")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    try:
        payload = decision_acceptance_payload(args.root, args.decision_id)
    except (ValidationError, ValueError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_human(payload)
    raise SystemExit(0 if payload.get("ready") else 1)


if __name__ == "__main__":
    main()
