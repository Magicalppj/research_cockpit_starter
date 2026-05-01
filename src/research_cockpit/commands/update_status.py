from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.model import (
    ResearchNode,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    script_command,
    validate_cockpit,
    validate_status,
)
from research_cockpit.commands._runtime import finish_mutation


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
    rebuild_dashboard: bool = True,
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
    before = {
        "status": data.get("status"),
        "summary": data.get("summary"),
        "result_summary": data.get("result_summary"),
    }
    data["status"] = status
    if summary is not None:
        data["summary"] = summary
    if result_summary is not None:
        data["result_summary"] = result_summary
    data["updated_at"] = str(date.today())

    candidate = dict(nodes)
    candidate[node_id] = ResearchNode.from_dict(data)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, candidate, current, explicit_edges, raise_on_error=True)

    after = {
        "status": data.get("status"),
        "summary": data.get("summary"),
        "result_summary": data.get("result_summary"),
    }
    finish_mutation(
        root,
        [(path, data)],
        interaction={
            "kind": "update_status",
            "actor": "researcher",
            "node_id": node_id,
            "command": f"{script_command('update_status.py')} --id {node_id} --status {status}",
            "before": before,
            "after": after,
            "extra": {
                "node_id": node_id,
                "status": status,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--summary")
    parser.add_argument("--result-summary", help="Experiment nodes only; rejected for other node types.")
    parser.add_argument("--no-build", action="store_true", help="Only update YAML; do not rebuild dashboards")
    args = parser.parse_args()

    path = update_status(
        args.root,
        node_id=args.id,
        status=args.status,
        summary=args.summary,
        result_summary=args.result_summary,
        rebuild_dashboard=not args.no_build,
    )
    print(f"Updated {path}")
    if not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
