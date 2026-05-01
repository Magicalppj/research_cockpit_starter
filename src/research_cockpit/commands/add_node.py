from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_cockpit.paths import default_data_root
from datetime import date
from typing import Any

ROOT = default_data_root()

from research_cockpit.commands._runtime import finish_mutation, yaml_change_diff
from research_cockpit.model import (
    VALID_NODE_TYPES,
    ResearchNode,
    ValidationError,
    default_status_for_type,
    load_nodes,
    load_yaml,
    script_command,
    validate_cockpit,
    validate_status,
)


def add_node(
    root: Path,
    *,
    node_id: str,
    node_type: str,
    title: str,
    parent: str | None = None,
    status: str | None = None,
    summary: str = "",
    rebuild_dashboard: bool = False,
) -> Path:
    result = add_node_result(
        root,
        node_id=node_id,
        node_type=node_type,
        title=title,
        parent=parent,
        status=status,
        summary=summary,
        rebuild_dashboard=rebuild_dashboard,
        dry_run=False,
        show_diff=False,
    )
    return Path(str(result["path"]))


def add_node_result(
    root: Path,
    *,
    node_id: str,
    node_type: str,
    title: str,
    parent: str | None = None,
    status: str | None = None,
    summary: str = "",
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    nodes = load_nodes(root)
    if node_id in nodes:
        raise FileExistsError(root / "graph" / "nodes" / f"{node_id}.yaml")
    if parent and parent not in nodes:
        raise ValueError(f"Parent node does not exist: {parent}")

    node_status = status or default_status_for_type(node_type)
    validate_status(node_type, node_status)

    today = str(date.today())
    data = {
        "id": node_id,
        "type": node_type,
        "title": title,
        "status": node_status,
        "summary": summary,
        "created_at": today,
        "updated_at": today,
    }
    if parent:
        data["parent"] = parent

    candidate = dict(nodes)
    candidate[node_id] = ResearchNode.from_dict(data)
    validate_cockpit(root, candidate, load_yaml(root / "current_state.yaml"), raise_on_error=True)

    out = root / "graph" / "nodes" / f"{node_id}.yaml"
    result: dict[str, Any] = {
        "node_id": node_id,
        "dry_run": dry_run,
        "changed": False if dry_run else True,
        "would_change": True,
        "path": str(out),
        "before": None,
        "after": data,
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(out, None, data)])
    if dry_run:
        return result

    finish_mutation(
        root,
        [(out, data)],
        interaction={
            "kind": "add_node",
            "actor": "researcher",
            "node_id": node_id,
            "command": f"{script_command('add_node.py')} --id {node_id} --type {node_type}",
            "after": {
                "id": node_id,
                "type": node_type,
                "status": node_status,
                "parent": parent,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True)
    parser.add_argument("--type", required=True, choices=sorted(VALID_NODE_TYPES))
    parser.add_argument("--title", required=True)
    parser.add_argument("--parent", default=None)
    parser.add_argument("--status", default=None)
    parser.add_argument("--summary", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = add_node_result(
            args.root,
            node_id=args.id,
            node_type=args.type,
            title=args.title,
            parent=args.parent,
            status=args.status,
            summary=args.summary,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValidationError, ValueError, FileExistsError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.dry_run:
        print(f"Would create {result['path']}")
        if args.show_diff and result.get("diff"):
            print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
        return
    print(f"Created {result['path']}")
    if not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
