from __future__ import annotations

import argparse
import copy
from datetime import date
from pathlib import Path
import re
from typing import Any

from research_cockpit.commands._evidence import append_unique, linked_resource_rows, parse_link_values
from research_cockpit.commands._runtime import (
    compact_mutation_result,
    dry_run_preflight_result,
    emit_json,
    indexed_artifact_record_stubs,
    load_targeted_state,
    load_validated_state,
    preflight_mutation,
    safe_print,
    validate_mutation_candidate,
    yaml_change_diff,
)
from research_cockpit.commands._assignment_scope_cli import add_assignment_scope_args, emit_assignment_scope_error
from research_cockpit.commands.record_finding import find_node_file
from research_cockpit.artifact_records import build_artifact_record, list_artifact_records, upsert_artifact_record
from research_cockpit.assignment_scope import AssignmentScopeError, ensure_assignment_scope
from research_cockpit.evidence_staging import StagedEvidence, prepare_final_evidence
from research_cockpit.model import (
    ResearchNode,
    ValidationError,
    load_yaml,
    script_command,
)
from research_cockpit.mutation_lock import MutationError
from research_cockpit.mutation_runtime import execute_mutation_transaction
from research_cockpit.paths import default_data_root
from research_cockpit.runtime_ids import generate_runtime_id

ROOT = default_data_root()
PATH_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
WINDOWS_RESERVED_SEGMENTS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _validate_run_id(run_id: str) -> None:
    _validate_path_segment("run_id", run_id)


def _validate_path_segment(label: str, value: str) -> None:
    reserved_name = value.split(".", 1)[0].upper()
    if (
        not PATH_SEGMENT_PATTERN.fullmatch(value)
        or value.endswith(".")
        or reserved_name in WINDOWS_RESERVED_SEGMENTS
    ):
        raise ValueError(
            f"{label} must be a single path-safe segment using letters, digits, dot, dash, or underscore; "
            "it must not be a Windows reserved name or end with dot"
        )


def _validate_source_directory(source_dir: Path) -> Path:
    if not source_dir.exists():
        raise FileNotFoundError(f"Artifact source directory does not exist: {source_dir}")
    if source_dir.is_symlink():
        raise ValueError(f"Artifact source must not be a symlink: {source_dir}")
    if not source_dir.is_dir():
        raise ValueError(f"Artifact source must be a directory: {source_dir}")
    return source_dir.resolve()


def _operation_identity(
    operation_request: dict[str, Any] | None,
) -> dict[str, str] | None:
    if not isinstance(operation_request, dict):
        return None
    identity = {
        key: operation_request.get(key)
        for key in ("scope", "operation_id", "request_hash", "operation")
    }
    if not all(isinstance(value, str) and value for value in identity.values()):
        return None
    return identity


def ingest_artifact(
    root: Path,
    *,
    node_id: str,
    source_dir: Path,
    run_id: str,
    artifact_id: str | None = None,
    title: str | None = None,
    summary: str = "",
    agent_id: str | None = None,
    links: dict[str, str] | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    assignment_id: str | None = None,
    coordinator: bool = False,
    record_only: bool | None = None,
    promote: bool = False,
    promotion_reason: str | None = None,
    additional_yaml_changes: list[tuple] | None = None,
    interaction_override: dict[str, Any] | None = None,
    operation_request: dict[str, Any] | None = None,
    prepared_evidence: StagedEvidence | None = None,
) -> dict[str, Any]:
    reason = str(promotion_reason or "").strip() or None
    if promote and record_only is True:
        raise ValueError("Artifact ingest modes --record-only and --promote are mutually exclusive")
    if promote and not reason:
        raise ValueError("Explicit promotion requires a non-empty promotion reason")
    if reason and not promote:
        raise ValueError("A promotion reason is valid only with explicit promotion")
    if record_only is False and not promote:
        raise ValueError("Graph artifact ingest now requires explicit promotion")

    _validate_run_id(run_id)
    source_resolved = _validate_source_directory(source_dir)
    links = links or {}
    state = (
        load_validated_state(root)
        if promote
        else load_targeted_state(
            root,
            node_ids=[node_id],
            include_artifact_records=True,
        )
    )
    nodes = state.nodes
    if node_id not in nodes:
        raise ValueError(f"Target node does not exist: {node_id}")
    if nodes[node_id].type == "artifact":
        raise ValueError("--node must be a non-artifact research node")

    is_record = not promote
    if is_record and nodes[node_id].type != "experiment":
        if record_only is True:
            raise ValueError("--record-only requires --node to target an experiment")
        raise ValueError(
            "Non-experiment artifact ingest requires an explicit --promote mode with --promotion-reason; "
            "use create-artifact when no run output directory must be copied"
        )
    mode = "record" if is_record else "promote"
    ensure_assignment_scope(
        root,
        nodes,
        assignment_id=assignment_id,
        coordinator=coordinator,
        target_node_ids=[node_id],
    )

    artifact_id = artifact_id or generate_runtime_id(
        "record" if is_record else "artifact",
        scope_hint=assignment_id or node_id,
        slug_hint=run_id,
    )
    _validate_path_segment("artifact_id", artifact_id)
    if artifact_id in nodes:
        raise FileExistsError(root / "graph" / "nodes" / f"{artifact_id}.yaml")

    operation_identity = _operation_identity(operation_request)
    operation_bound_record = bool(
        is_record
        and operation_identity
        and operation_identity.get("operation") == "work record"
    )
    if not operation_bound_record and source_resolved.is_relative_to(root.resolve()):
        raise ValueError("direct ingest source must be outside the Cockpit state root")
    if prepared_evidence is None and operation_bound_record:
        raise ValueError("work record ingest requires prepared evidence")
    if prepared_evidence is None:
        prepared_evidence = prepare_final_evidence(
            root,
            assignment_id=assignment_id or f"direct-ingest-{node_id}",
            experiment_id=node_id,
            run_id=run_id,
            agent_id=agent_id or "",
            spec={
                "source": str(source_resolved),
                "title": title or f"Artifact record for {node_id} {run_id}",
                "summary": summary,
                "links": links,
                "mode": "reference",
            },
            record_id=artifact_id,
        )
    if prepared_evidence.record_spec.get("record_id") != artifact_id:
        raise ValueError("prepared evidence record_id does not match artifact_id")
    if not operation_bound_record and prepared_evidence.mode != "reference":
        raise ValueError(
            "direct ingest only supports reference evidence; use work record or "
            "work close for managed evidence"
        )

    prepared_spec = prepared_evidence.record_spec
    stable_path = str(prepared_spec["stable_path"])
    target_dir = prepared_evidence.target_dir or prepared_evidence.source
    stable_links = dict(prepared_spec.get("links", {}))
    file_count = int(prepared_spec.get("source_file_count") or 0)
    manifest = prepared_evidence.manifest
    reused_payload = (
        prepared_evidence.mode == "managed"
        and prepared_evidence.staged_move is None
    )

    today = str(date.today())
    artifact_data: dict[str, Any] = {
        "id": artifact_id,
        "type": "artifact",
        "title": title or f"Artifact for {node_id} {run_id}",
        "status": "done",
        "summary": summary,
        "path": stable_path,
        "links": stable_links,
        "created_at": today,
        "updated_at": today,
    }
    if agent_id:
        artifact_data["agent"] = agent_id
    if promote:
        artifact_data["promotion"] = {
            "reason": reason,
            "source": "ingest_artifact",
        }

    node_path = find_node_file(root, node_id)
    node_before = load_yaml(node_path)
    node_after = copy.deepcopy(node_before)
    candidate = dict(nodes)

    if is_record:
        record_id = artifact_id
        record = build_artifact_record(
            record_id=record_id,
            experiment_id=node_id,
            run_id=run_id,
            artifact_id=artifact_id,
            title=title or f"Artifact record for {node_id} {run_id}",
            summary=summary,
            stable_path=stable_path,
            manifest_path=str(prepared_spec.get("manifest_path") or ""),
            source_file_count=file_count,
            links=stable_links,
            agent_id=agent_id,
            storage=prepared_spec.get("storage"),
            integrity=prepared_spec.get("integrity"),
            inventory=prepared_spec.get("inventory"),
            retention=prepared_spec.get("retention"),
            availability=prepared_spec.get("availability"),
            lifecycle=prepared_spec.get("lifecycle"),
        )
        record_path, record_before, record_after = upsert_artifact_record(root, node_id, record)
        linked_records, _ = append_unique(
            node_after.get("linked_artifact_records"),
            [record_id],
            "linked_artifact_records",
        )
        node_after["linked_artifact_records"] = linked_records
        node_after["updated_at"] = today
        candidate[node_id] = ResearchNode.from_dict(node_after)
        known_records = (
            indexed_artifact_record_stubs(state)
            if state.targeted
            else list_artifact_records(root)
        )
        validate_mutation_candidate(
            root,
            state,
            nodes=candidate,
            artifact_records=[*known_records, record],
        )
        artifact_path = record_path
        result_after = record
        changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]] = [
            (record_path, record_before if record_path.exists() else None, record_after),
            (node_path, node_before, node_after),
        ]
    else:
        linked_artifacts, _ = append_unique(node_after.get("linked_artifacts"), [artifact_id], "linked_artifacts")
        node_after["linked_artifacts"] = linked_artifacts
        node_after["updated_at"] = today
        candidate[artifact_id] = ResearchNode.from_dict(artifact_data)
        candidate[node_id] = ResearchNode.from_dict(node_after)
        validate_mutation_candidate(root, state, nodes=candidate)
        artifact_path = root / "graph" / "nodes" / f"{artifact_id}.yaml"
        result_after = artifact_data
        changes = [
            (artifact_path, None, artifact_data),
            (node_path, node_before, node_after),
        ]
    changes = [*list(additional_yaml_changes or []), *changes]
    result: dict[str, Any] = {
        "ok": True,
        "artifact_id": artifact_id,
        "record_id": artifact_id if is_record else None,
        "record_only": is_record,
        "mode": mode,
        "promoted": promote,
        "promotion_reason": reason,
        "node_id": node_id,
        "run_id": run_id,
        "dry_run": dry_run,
        "reused_payload": reused_payload,
        "changed": not dry_run,
        "would_change": True,
        "verified": not dry_run,
        "additional_verification_required": dry_run,
        "path": str(artifact_path),
        "changed_files": [str(path) for path, _, _ in changes],
        "linked_to": [node_id],
        "stable_path": stable_path,
        "target_dir": str(target_dir),
        "manifest_path": str(prepared_spec.get("manifest_path") or ""),
        "source_path_resolved": str(source_resolved),
        "source_file_count": file_count,
        "links": stable_links,
        "before": None,
        "after": result_after,
        "resolved_inputs": {
            "source_path_resolved": str(source_resolved),
            "manifest_source_path": manifest.get(
                "source_path",
                stable_path if prepared_evidence.mode == "reference" else str(source_resolved),
            ),
            "manifest_source_path_base": manifest.get(
                "source_path_base",
                "uri" if prepared_evidence.mode == "reference" else "absolute",
            ),
            "target_dir": str(target_dir),
            "stable_path": stable_path,
            "manifest_path": str(prepared_spec.get("manifest_path") or ""),
            "source_git": manifest.get("source_git"),
        },
        "resource_rows": linked_resource_rows(root, candidate, [artifact_id, node_id]) if promote else [],
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        result["changed"] = False
        return dry_run_preflight_result(root, result)

    preflight_mutation(root)
    result["reused_payload"] = reused_payload
    interaction_args = ["--node", node_id, "--run-id", run_id]
    if promote:
        interaction_args.append("--promote")
    transaction = execute_mutation_transaction(
        root,
        changes,
        interactions=[interaction_override or {
            "kind": "ingest_artifact",
            "actor": agent_id or "researcher",
            "node_id": node_id,
            "command": script_command("ingest_artifact.py", *interaction_args),
            "after": {
                "artifact_id": artifact_id,
                "record_id": artifact_id if is_record else None,
                "record_only": is_record,
                "mode": mode,
                "promotion_reason": reason,
                "node_id": node_id,
                "run_id": run_id,
                "stable_path": stable_path,
                "links": sorted(stable_links),
                "agent_id": agent_id,
            },
        }],
        rebuild_dashboard=rebuild_dashboard,
        operation_request=operation_request,
        staged_moves=[prepared_evidence.staged_move] if prepared_evidence.staged_move else None,
        staged_move_roots=[prepared_evidence.move_root] if prepared_evidence.move_root else None,
    )
    if operation_request is not None:
        result["_operation_transaction"] = transaction
    result["resource_rows"] = linked_resource_rows(root, candidate, [artifact_id, node_id]) if promote else []
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit ingest-artifact")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--node", required=True, dest="node_id")
    parser.add_argument("--from", required=True, type=Path, dest="source_dir")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--id", dest="artifact_id")
    parser.add_argument("--title")
    parser.add_argument("--summary", default="")
    parser.add_argument("--agent", dest="agent_id")
    parser.add_argument("--link", action="append", dest="links", help="Artifact resource link in key=relative/path form; repeatable.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--record-only",
        action="store_const",
        const="record",
        dest="ingest_mode",
        help="Explicitly retain record mode; experiment targets already use this mode by default.",
    )
    mode_group.add_argument(
        "--promote",
        action="store_const",
        const="promote",
        dest="ingest_mode",
        help="Create a graph artifact node immediately; requires --promotion-reason.",
    )
    parser.add_argument("--promotion-reason", help="Why this output requires durable graph navigation.")
    add_assignment_scope_args(parser)
    parser.add_argument("--progress", action="store_true", help="Print phase progress to stderr.")
    args = parser.parse_args()

    try:
        result = ingest_artifact(
            args.root,
            node_id=args.node_id,
            source_dir=args.source_dir,
            run_id=args.run_id,
            artifact_id=args.artifact_id,
            title=args.title,
            summary=args.summary,
            agent_id=args.agent_id,
            links=parse_link_values(args.links),
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
            assignment_id=args.assignment,
            coordinator=args.coordinator,
            record_only=True if args.ingest_mode == "record" else None,
            promote=args.ingest_mode == "promote",
            promotion_reason=args.promotion_reason,
        )
    except AssignmentScopeError as exc:
        emit_assignment_scope_error(args, exc)
        raise SystemExit(1) from exc
    except MutationError as exc:
        if args.json and exc.payload:
            emit_json(exc.payload)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc
    except (ValidationError, ValueError, FileExistsError, FileNotFoundError, OSError) as exc:
        if args.json:
            emit_json({
                "ok": False,
                "partial_success": False,
                "rolled_back": False,
                "written_files": [],
                "error": str(exc),
            })
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(
            compact_mutation_result(
                result,
                command="ingest-artifact",
                target={"node_id": result["node_id"], "artifact_id": result["artifact_id"], "mode": result.get("mode")},
                root=args.root,
                created=[] if result.get("record_only") else [result["artifact_id"]],
                updated=result.get("linked_to", []),
                records=[f"artifact:{result['record_id']}"] if result.get("record_only") else [],
            ) if args.compact else result
        )
        return
    verb = "Would ingest" if args.dry_run else "Ingested"
    noun = "artifact record" if result.get("record_only") else "promoted artifact"
    safe_print(f"{verb} {noun} {result['artifact_id']}: {result['stable_path']}")
    if args.show_diff and result.get("diff"):
        safe_print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        safe_print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
