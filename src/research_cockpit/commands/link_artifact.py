from __future__ import annotations

import argparse
import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._evidence import (
    append_unique,
    linked_resource_rows,
    parse_link_values,
    validate_artifact_ids,
    validate_node_refs,
)
from research_cockpit.commands._runtime import finish_mutation, load_validated_state, yaml_change_diff
from research_cockpit.commands.record_finding import find_node_file
from research_cockpit.model import ResearchNode, ValidationError, load_yaml, script_command, validate_cockpit


def link_artifact(
    root: Path,
    *,
    artifact_id: str,
    to_nodes: list[str] | None = None,
    path: str | None = None,
    links: dict[str, str] | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    state = load_validated_state(root)
    nodes = state.nodes
    to_nodes = to_nodes or []
    links = links or {}
    validate_artifact_ids(nodes, [artifact_id])
    validate_node_refs(nodes, to_nodes, "--to")
    if not to_nodes and path is None and not links:
        raise ValueError("At least one of --to, --path, or --link is required")

    candidate = dict(nodes)
    today = str(date.today())
    changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]] = []
    linked_to: list[str] = []

    artifact_path = find_node_file(root, artifact_id)
    artifact_before = load_yaml(artifact_path)
    artifact_data = copy.deepcopy(artifact_before)
    if path is not None:
        artifact_data["path"] = path
    if links:
        existing_links = artifact_data.get("links") or {}
        if not isinstance(existing_links, dict):
            raise ValueError(f"{artifact_id}: links must be a mapping")
        artifact_data["links"] = {**existing_links, **links}
    if artifact_data != artifact_before:
        artifact_data["updated_at"] = today
        candidate[artifact_id] = ResearchNode.from_dict(artifact_data)
        changes.append((artifact_path, artifact_before, artifact_data))

    for node_id in to_nodes:
        node_path = find_node_file(root, node_id)
        before = load_yaml(node_path)
        data = copy.deepcopy(before)
        linked_artifacts, added = append_unique(data.get("linked_artifacts"), [artifact_id], "linked_artifacts")
        data["linked_artifacts"] = linked_artifacts
        if added:
            linked_to.append(node_id)
            data["updated_at"] = today
        candidate[node_id] = ResearchNode.from_dict(data)
        if before != data:
            changes.append((node_path, before, data))

    validate_cockpit(root, candidate, state.current, state.explicit_edges, raise_on_error=True)
    changed = bool(changes)
    result: dict[str, Any] = {
        "artifact_id": artifact_id,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "path": str(artifact_path),
        "linked_to": linked_to,
        "changed_files": [str(item[0]) for item in changes],
        "before": {"artifact": artifact_before},
        "after": {"artifact": artifact_data},
        "resource_rows": linked_resource_rows(root, candidate, [artifact_id, *to_nodes]),
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes) if changed else ""
    if dry_run or not changed:
        return result

    finish_mutation(
        root,
        [(change_path, after) for change_path, _, after in changes],
        interaction={
            "kind": "link_artifact",
            "actor": "researcher",
            "node_id": artifact_id,
            "command": f"{script_command('link_artifact.py')} --artifact {artifact_id}",
            "after": {
                "artifact_id": artifact_id,
                "path_updated": path is not None,
                "links": sorted(links),
                "linked_to": linked_to,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--artifact", required=True, dest="artifact_id")
    parser.add_argument("--to", action="append", dest="to_nodes", help="Node id to link to this artifact; repeatable.")
    parser.add_argument("--path")
    parser.add_argument("--link", action="append", dest="links", help="Artifact resource link in key=value form; repeatable.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = link_artifact(
            args.root,
            artifact_id=args.artifact_id,
            to_nodes=args.to_nodes,
            path=args.path,
            links=parse_link_values(args.links),
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    verb = "Would link" if args.dry_run else "Linked"
    print(f"{verb} artifact {args.artifact_id}")
    if args.show_diff and result.get("diff"):
        print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
