from __future__ import annotations

import argparse
import copy
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.artifact_records import promoted_artifact_record_update
from research_cockpit.assignment_scope import AssignmentScopeError, ensure_assignment_scope
from research_cockpit.commands._assignment_scope_cli import add_assignment_scope_args, emit_assignment_scope_error
from research_cockpit.commands._evidence import append_unique, validate_node_refs
from research_cockpit.commands._runtime import (
    compact_mutation_result,
    dry_run_preflight_result,
    emit_json,
    finish_mutation,
    load_validated_state,
    safe_print,
    yaml_change_diff,
)
from research_cockpit.commands.record_finding import find_node_file
from research_cockpit.model import ResearchNode, ValidationError, load_yaml, script_command, validate_cockpit
from research_cockpit.retention import validate_retention


def _promoted_artifact_data(
    record: dict[str, Any],
    *,
    artifact_id: str,
    today: str,
    promotion_reason: str,
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "id": artifact_id,
        "type": "artifact",
        "title": record.get("title") or artifact_id,
        "status": "done",
        "summary": record.get("summary") or "",
        "path": record.get("stable_path"),
        "links": dict(record.get("links") or {}),
        "artifact_kind": record.get("artifact_kind") or "run_output",
        "retention": validate_retention(record.get("retention") or {"class": "reproducible_output"}, "retention"),
        "created_at": today,
        "updated_at": today,
        "source_artifact_record": record.get("record_id"),
        "promotion": {
            "reason": promotion_reason,
            "source": "promote_artifact_record",
        },
    }
    if record.get("agent"):
        artifact["agent"] = record.get("agent")
    return {key: value for key, value in artifact.items() if value not in (None, {}, [])}


def promote_artifact_record(
    root: Path,
    *,
    record_id: str,
    artifact_id: str | None = None,
    link_to: list[str] | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    assignment_id: str | None = None,
    coordinator: bool = False,
    promotion_reason: str | None = None,
    operation_request: dict[str, Any] | None = None,
    interaction_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reason = str(promotion_reason or "").strip()
    if not reason:
        raise ValueError("Artifact record promotion requires a non-empty promotion reason")
    state = load_validated_state(root)
    nodes = state.nodes
    link_to = link_to or []
    artifact_id = artifact_id or record_id
    if artifact_id in nodes:
        raise FileExistsError(root / "graph" / "nodes" / f"{artifact_id}.yaml")
    validate_node_refs(nodes, link_to, "--link-to")

    today = str(date.today())
    record_path, record_before, record_after, promoted_record = promoted_artifact_record_update(
        root,
        record_id=record_id,
        artifact_id=artifact_id,
        updated_at=today,
        promotion_reason=reason,
    )
    artifact_data = _promoted_artifact_data(
        promoted_record,
        artifact_id=artifact_id,
        today=today,
        promotion_reason=reason,
    )
    artifact_path = root / "graph" / "nodes" / f"{artifact_id}.yaml"

    candidate = dict(nodes)
    candidate[artifact_id] = ResearchNode.from_dict(artifact_data)
    changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]] = [
        (artifact_path, None, artifact_data),
        (record_path, record_before, record_after),
    ]
    linked_to: list[str] = []
    for node_id in link_to:
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

    ensure_assignment_scope(
        root,
        candidate,
        assignment_id=assignment_id,
        coordinator=coordinator,
        target_node_ids=[artifact_id, *link_to],
    )
    validate_cockpit(root, candidate, state.current, state.explicit_edges, raise_on_error=True)
    changed = bool(changes)
    result: dict[str, Any] = {
        "ok": True,
        "record_id": record_id,
        "artifact_id": artifact_id,
        "promotion_reason": reason,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "path": str(artifact_path),
        "record_path": str(record_path),
        "linked_to": linked_to,
        "changed_files": [str(path) for path, _, _ in changes],
        "changed_records": [f"artifact:{record_id}"],
        "before": {"artifact_record": record_before},
        "after": {"artifact": artifact_data, "artifact_record": promoted_record},
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        return dry_run_preflight_result(root, result)

    transaction = finish_mutation(
        root,
        changes,
        interaction=interaction_override or {
            "kind": "promote_artifact_record",
            "actor": "researcher",
            "node_id": artifact_id,
            "command": script_command("promote_artifact_record.py", "--id", record_id),
            "after": {
                "record_id": record_id,
                "artifact_id": artifact_id,
                "promotion_reason": reason,
                "linked_to": linked_to,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
        operation_request=operation_request,
    )

    if operation_request is not None:
        result["_operation_transaction"] = transaction
    return result

def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit promote-artifact-record")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="record_id")
    parser.add_argument("--artifact-id")
    parser.add_argument("--promotion-reason", required=True, help="Why this record requires durable graph navigation.")
    parser.add_argument("--link-to", action="append", dest="link_to")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    add_assignment_scope_args(parser)
    args = parser.parse_args()

    try:
        result = promote_artifact_record(
            args.root,
            record_id=args.record_id,
            artifact_id=args.artifact_id,
            link_to=args.link_to,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
            assignment_id=args.assignment,
            coordinator=args.coordinator,
            promotion_reason=args.promotion_reason,
        )
    except AssignmentScopeError as exc:
        emit_assignment_scope_error(args, exc)
        raise SystemExit(1) from exc
    except (ValidationError, ValueError, FileExistsError, FileNotFoundError) as exc:
        if args.json:
            emit_json({"ok": False, "error": str(exc)})
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(
            compact_mutation_result(
                result,
                command="promote-artifact-record",
                target={"record_id": result["record_id"], "artifact_id": result["artifact_id"]},
                root=args.root,
                created=[result["artifact_id"]],
                updated=result.get("linked_to", []),
                records=result.get("changed_records", []),
            ) if args.compact else result
        )
        return
    verb = "Would promote" if args.dry_run else "Promoted"
    safe_print(f"{verb} artifact record {result['record_id']} to {result['artifact_id']}")
    if args.show_diff and result.get("diff"):
        safe_print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        safe_print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()