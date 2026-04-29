from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.model import (
    ResearchNode,
    load_nodes,
    load_yaml,
    save_yaml,
    validate_cockpit,
    validate_status,
)


def find_node_file(root: Path, node_id: str) -> Path:
    for path in sorted((root / "graph" / "nodes").glob("*.yaml")):
        data = load_yaml(path)
        if str(data.get("id")) == node_id:
            return path
    raise FileNotFoundError(f"Node does not exist: {node_id}")


def update_status(
    root: Path,
    *,
    node_id: str,
    status: str,
    summary: str | None = None,
    result_summary: str | None = None,
) -> Path:
    nodes = load_nodes(root)
    if node_id not in nodes:
        raise FileNotFoundError(f"Node does not exist: {node_id}")

    node = nodes[node_id]
    validate_status(node.type, status)
    if node.type == "decision" and status == "accepted":
        raise ValueError("Use `research-cockpit accept-decision` to accept a decision so option/problem state stays synchronized.")
    if result_summary is not None and node.type != "experiment":
        raise ValueError("--result-summary can only be used with experiment nodes")

    path = find_node_file(root, node_id)
    data = load_yaml(path)
    data["status"] = status
    if summary is not None:
        data["summary"] = summary
    if result_summary is not None:
        data["result_summary"] = result_summary
    data["updated_at"] = str(date.today())

    candidate = dict(nodes)
    candidate[node_id] = ResearchNode.from_dict(data)
    validate_cockpit(root, candidate, load_yaml(root / "current_state.yaml"), raise_on_error=True)

    save_yaml(path, data)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--summary")
    parser.add_argument("--result-summary", help="Experiment nodes only; rejected for other node types.")
    args = parser.parse_args()

    path = update_status(
        args.root,
        node_id=args.id,
        status=args.status,
        summary=args.summary,
        result_summary=args.result_summary,
    )
    print(f"Updated {path}")


if __name__ == "__main__":
    main()
