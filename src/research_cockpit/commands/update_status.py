from __future__ import annotations

import argparse
import copy
import json
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
from research_cockpit.commands._runtime import finish_mutation, yaml_change_diff


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
    result = update_status_result(
        root,
        node_id=node_id,
        status=status,
        summary=summary,
        result_summary=result_summary,
        rebuild_dashboard=rebuild_dashboard,
        dry_run=False,
        show_diff=False,
    )
    return Path(str(result["path"]))


def update_status_result(
    root: Path,
    *,
    node_id: str,
    status: str,
    summary: str | None = None,
    result_summary: str | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, object]:
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
    before_data = copy.deepcopy(data)
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
    changed = before != after
    result: dict[str, object] = {
        "node_id": node_id,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "path": str(path),
        "before": before,
        "after": after,
        "summary_replaced": summary is not None and before.get("summary") not in (None, "", summary),
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(path, before_data, data)]) if changed else ""
    if dry_run or not changed:
        return result

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
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--summary")
    parser.add_argument("--result-summary", help="Experiment nodes only; rejected for other node types.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true", help="Only update YAML; do not rebuild dashboards")
    args = parser.parse_args()

    try:
        result = update_status_result(
            args.root,
            node_id=args.id,
            status=args.status,
            summary=args.summary,
            result_summary=args.result_summary,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    verb = "Would update" if args.dry_run else "Updated"
    print(f"{verb} {result['path']}")
    if result.get("summary_replaced") and not args.dry_run:
        print("Warning: --summary replaced an existing summary; use --dry-run --show-diff to preview.")
    if args.show_diff and result.get("diff"):
        print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build and result["changed"]:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
