from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.model import (
    ValidationError,
    build_node_onboarding_context,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
)


def node_context_payload(root: Path, *, node_id: str) -> dict:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
    return build_node_onboarding_context(root, nodes, current, node_id)


def _print_human(payload: dict) -> None:
    node = payload["node"]
    print(f"Node: {node['id']} - {node['title']} ({node['type']}/{node['status']})")
    parent_chain = " -> ".join(item["id"] for item in payload.get("parent_chain", []))
    if parent_chain:
        print(f"Parent chain: {parent_chain}")
    for item in payload.get("recommended_next_steps", []):
        print(f"Next: {item.get('action')}")
        if item.get("command"):
            print(f"Command: {item['command']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="node_id")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    try:
        payload = node_context_payload(args.root, node_id=args.node_id)
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    _print_human(payload)


if __name__ == "__main__":
    main()
