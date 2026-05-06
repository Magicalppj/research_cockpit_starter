from __future__ import annotations

import argparse
import copy
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._evidence import append_unique, linked_resource_rows, parse_link_values, validate_node_refs
from research_cockpit.commands._runtime import (
    compact_mutation_result,
    dry_run_preflight_result,
    emit_json,
    finish_mutation,
    load_validated_state,
    safe_print,
    yaml_change_diff,
)
from research_cockpit.commands.file_schemas import CREATE_ARTIFACT_EXAMPLE
from research_cockpit.commands.record_finding import find_node_file
from research_cockpit.model import (
    ResearchNode,
    ValidationError,
    default_status_for_type,
    load_yaml,
    script_command,
    validate_cockpit,
    validate_status,
)


def _optional_text(data: dict[str, Any], key: str) -> str | None:
    if key not in data or data.get(key) is None:
        return None
    text = str(data.get(key)).strip()
    return text or None


def _required_text(data: dict[str, Any], key: str) -> str:
    text = _optional_text(data, key)
    if not text:
        raise ValueError(f"Artifact file field {key!r} is required")
    return text


def _string_mapping(value: Any, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return {str(key): str(item) for key, item in value.items()}


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a string or list")
    return [str(item) for item in value if str(item).strip()]


def load_artifact_spec(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Artifact file does not exist: {path}")
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError("Artifact file must contain a mapping")
    return data


def artifact_spec_from_mapping(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": _required_text(data, "id"),
        "title": _required_text(data, "title"),
        "status": _optional_text(data, "status"),
        "summary": str(data.get("summary") or ""),
        "path": _optional_text(data, "path"),
        "links": _string_mapping(data.get("links"), "links"),
        "link_to": _string_list(data.get("link_to", data.get("link-to")), "link_to"),
    }


def _merge_cli_over_file(
    file_spec: dict[str, Any] | None,
    *,
    artifact_id: str | None,
    title: str | None,
    status: str | None,
    summary: str | None,
    path: str | None,
    links: dict[str, str],
    link_to: list[str] | None,
) -> dict[str, Any]:
    data = artifact_spec_from_mapping(file_spec or {}) if file_spec is not None else {}
    if artifact_id is not None:
        data["artifact_id"] = artifact_id
    if title is not None:
        data["title"] = title
    if status is not None:
        data["status"] = status
    if summary is not None:
        data["summary"] = summary
    if path is not None:
        data["path"] = path
    data["links"] = {**data.get("links", {}), **links}
    data["link_to"] = [*data.get("link_to", []), *(link_to or [])]
    if not data.get("artifact_id"):
        raise ValueError("--id is required unless provided by --file")
    if not data.get("title"):
        raise ValueError("--title is required unless provided by --file")
    return data


def create_artifact(
    root: Path,
    *,
    artifact_id: str,
    title: str,
    status: str | None = None,
    summary: str = "",
    path: str | None = None,
    links: dict[str, str] | None = None,
    link_to: list[str] | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    state = load_validated_state(root)
    nodes = state.nodes
    if artifact_id in nodes:
        raise FileExistsError(root / "graph" / "nodes" / f"{artifact_id}.yaml")
    link_to = link_to or []
    links = links or {}
    validate_node_refs(nodes, link_to, "--link-to")

    node_status = status or default_status_for_type("artifact")
    validate_status("artifact", node_status)
    today = str(date.today())
    artifact_data: dict[str, Any] = {
        "id": artifact_id,
        "type": "artifact",
        "title": title,
        "status": node_status,
        "summary": summary,
        "created_at": today,
        "updated_at": today,
    }
    if path:
        artifact_data["path"] = path
    if links:
        artifact_data["links"] = links

    candidate = dict(nodes)
    candidate[artifact_id] = ResearchNode.from_dict(artifact_data)
    artifact_path = root / "graph" / "nodes" / f"{artifact_id}.yaml"
    changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]] = [(artifact_path, None, artifact_data)]
    linked_to: list[str] = []

    for node_id in link_to:
        node_path = find_node_file(root, node_id)
        before = load_yaml(node_path)
        data = copy.deepcopy(before)
        linked_artifacts, added = append_unique(data.get("linked_artifacts"), [artifact_id], "linked_artifacts")
        data["linked_artifacts"] = linked_artifacts
        data["updated_at"] = today
        if added:
            linked_to.append(node_id)
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
        "before": None,
        "after": artifact_data,
        "resource_rows": linked_resource_rows(root, candidate, [artifact_id, *link_to]),
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        return dry_run_preflight_result(root, result)

    finish_mutation(
        root,
        changes,
        interaction={
            "kind": "create_artifact",
            "actor": "researcher",
            "node_id": artifact_id,
            "command": f"{script_command('create_artifact.py')} --id {artifact_id}",
            "after": {
                "artifact_id": artifact_id,
                "status": node_status,
                "path": path,
                "links": sorted(links),
                "linked_to": linked_to,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CREATE_ARTIFACT_EXAMPLE,
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--file", type=Path, dest="artifact_file")
    parser.add_argument("--print-schema", action="store_true", help="Print the artifact YAML schema example and exit.")
    parser.add_argument("--id", dest="artifact_id")
    parser.add_argument("--title")
    parser.add_argument("--status")
    parser.add_argument("--summary")
    parser.add_argument("--path")
    parser.add_argument("--link", action="append", dest="links", help="Artifact resource link in key=value form; repeatable.")
    parser.add_argument("--link-to", action="append", dest="link_to", help="Node id that should link this artifact.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    if args.print_schema:
        safe_print(CREATE_ARTIFACT_EXAMPLE)
        return

    try:
        spec = _merge_cli_over_file(
            load_artifact_spec(args.artifact_file) if args.artifact_file else None,
            artifact_id=args.artifact_id,
            title=args.title,
            status=args.status,
            summary=args.summary,
            path=args.path,
            links=parse_link_values(args.links),
            link_to=args.link_to,
        )
        result = create_artifact(
            args.root,
            artifact_id=spec["artifact_id"],
            title=spec["title"],
            status=spec.get("status"),
            summary=spec.get("summary", ""),
            path=spec.get("path"),
            links=spec.get("links", {}),
            link_to=spec.get("link_to", []),
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValidationError, ValueError, FileExistsError, FileNotFoundError) as exc:
        safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(
            compact_mutation_result(
                result,
                command="create-artifact",
                target=result["artifact_id"],
                root=args.root,
                created=[result["artifact_id"]],
                updated=result.get("linked_to", []),
            ) if args.compact else result
        )
        return
    verb = "Would create" if args.dry_run else "Created"
    safe_print(f"{verb} artifact {result['artifact_id']}: {result['path']}")
    if args.show_diff and result.get("diff"):
        safe_print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        safe_print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
