from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from research_cockpit.artifact_records import upsert_artifact_record
from research_cockpit.interaction_log import _append_interaction_log_unlocked
from research_cockpit.maintenance import _artifact_reference_index, _retention_class
from research_cockpit.model import ResearchNode, load_explicit_edges, load_nodes, load_yaml, script_command, validate_cockpit
from research_cockpit.mutation_lock import MutationError, mutation_lock
from research_cockpit.mutation_runtime import ensure_interaction_log_valid
from research_cockpit.retention import validate_retention
from research_cockpit.storage import save_yaml
from research_cockpit.commands._runtime import yaml_change_diff

KEEP_RETENTION_CLASSES = {"evidence_critical", "portable_review_bundle", "final_checkpoint"}
DEMOTABLE_RETENTION_CLASSES = {"reproducible_output", "deprecated_payload", "disposable_cache"}


def _unique_strings(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError:
        return path.as_posix()


def _reference_reasons(node: ResearchNode, references: list[dict[str, str]]) -> tuple[list[str], list[str], list[str]]:
    keep: list[str] = []
    review: list[str] = []
    rewritable: list[str] = []
    for ref in references:
        source = str(ref.get("source") or "")
        ref_node_id = str(ref.get("node_id") or "")
        ref_node_type = str(ref.get("node_type") or "")
        if source == "baseline.artifacts":
            keep.append(f"baseline:{ref_node_id}")
        elif source.startswith("findings["):
            keep.append(f"finding:{ref_node_id}")
        elif ref_node_type == "decision" and ref_node_id:
            keep.append(f"decision:{ref_node_id}")
        elif source == "linked_artifacts" and ref_node_type == "experiment" and ref_node_id:
            rewritable.append(ref_node_id)
        elif source == "linked_artifacts":
            review.append(f"linked:{ref_node_id}")
        else:
            review.append(f"{source}:{ref_node_id}" if ref_node_id else source)
    rewritable = sorted(set(rewritable))
    if len(rewritable) > 1:
        review.append("multiple_linked_experiments:" + ",".join(rewritable))
        rewritable = []
    if node.raw.get("source_artifact_record"):
        keep.append("already_promoted_from_record")
    return sorted(set(keep)), sorted(set(review)), rewritable


def classify_artifact_node(node: ResearchNode, references: list[dict[str, str]]) -> dict[str, Any]:
    retention_class, retention_warnings = _retention_class(node)
    artifact_kind = str(node.raw.get("artifact_kind") or "").strip()
    path = str(node.raw.get("path") or "").strip()
    keep_reasons, review_reasons, rewritable_refs = _reference_reasons(node, references)
    warnings = list(retention_warnings)
    reasons: list[str] = []

    if retention_class in KEEP_RETENTION_CLASSES:
        reasons.append(f"retention:{retention_class}")
    reasons.extend(keep_reasons)
    if reasons:
        classification = "must_keep_node"
    elif retention_warnings:
        classification = "cannot_demote"
        reasons.extend(retention_warnings)
    elif not path:
        classification = "cannot_demote"
        reasons.append("missing_path")
    elif retention_class is None:
        classification = "needs_review"
        reasons.append("missing_retention")
    elif review_reasons:
        classification = "needs_review"
        reasons.extend(review_reasons)
    elif retention_class in DEMOTABLE_RETENTION_CLASSES and artifact_kind in {"", "run_output"}:
        classification = "can_demote"
        reasons.append(f"retention:{retention_class}")
        reasons.extend(f"rewritable_link:{node_id}" for node_id in rewritable_refs)
    else:
        classification = "needs_review"
        reasons.append(f"retention:{retention_class}" if retention_class else "unknown_retention")
    return {
        "artifact_id": node.id,
        "title": node.title,
        "classification": classification,
        "reasons": sorted(set(reasons)),
        "retention_class": retention_class,
        "artifact_kind": artifact_kind or None,
        "path": path or None,
        "linked_references": references,
        "rewritable_references": rewritable_refs,
        "warnings": sorted(set(warnings)),
        "recommended_command": _recommended_command(node.id, classification),
    }


def _recommended_command(artifact_id: str, classification: str) -> str | None:
    if classification != "can_demote":
        return None
    return f"research-cockpit compact-artifacts --root <root> --id {artifact_id} --execute --json --show-diff"


def artifact_compaction_plan(root: Path, *, artifact_id: str | None = None) -> dict[str, Any]:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
    references = _artifact_reference_index(nodes)
    artifact_nodes = [node for node in sorted(nodes.values(), key=lambda item: item.id) if node.type == "artifact"]
    if artifact_id:
        artifact_nodes = [node for node in artifact_nodes if node.id == artifact_id]
        if not artifact_nodes:
            raise ValueError(f"Artifact node does not exist: {artifact_id}")
    rows = [classify_artifact_node(node, references.get(node.id, [])) for node in artifact_nodes]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    return {
        "ok": True,
        "schema_version": "artifact_compaction_plan_v1",
        "root": str(root),
        "dry_run": True,
        "artifact_count": len(rows),
        "counts": counts,
        "artifacts": rows,
        "warnings": sorted({warning for row in rows for warning in row.get("warnings", [])}),
        "notes": ["No payload files are deleted by artifact compaction."],
    }


def _infer_experiment_id(node: ResearchNode, row: dict[str, Any], nodes: dict[str, ResearchNode]) -> str | None:
    rewritable = row.get("rewritable_references") or []
    if len(rewritable) == 1 and str(rewritable[0]) in nodes:
        return str(rewritable[0])
    for field_name in ("experiment_id", "node_id", "source_experiment"):
        value = node.raw.get(field_name)
        if value not in (None, "") and str(value) in nodes and nodes[str(value)].type == "experiment":
            return str(value)
    path = str(node.raw.get("path") or "").replace("\\", "/").strip("/")
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "artifacts" and parts[1] in nodes and nodes[parts[1]].type == "experiment":
        return parts[1]
    return None


def _infer_run_id(node: ResearchNode, experiment_id: str) -> str:
    value = node.raw.get("run_id")
    if value not in (None, ""):
        return str(value)
    path = str(node.raw.get("path") or "").replace("\\", "/").strip("/")
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] == "artifacts" and parts[1] == experiment_id:
        return parts[2]
    return node.id


def _artifact_record_from_node(
    node: ResearchNode,
    *,
    experiment_id: str,
    record_id: str,
    run_id: str,
    today: str,
) -> dict[str, Any]:
    links = node.raw.get("links") if isinstance(node.raw.get("links"), dict) else {}
    retention = node.raw.get("retention") or {"class": "reproducible_output"}
    record: dict[str, Any] = {
        "record_id": record_id,
        "experiment_id": experiment_id,
        "run_id": run_id,
        "artifact_id": node.id,
        "demoted_from_artifact_id": node.id,
        "title": node.title,
        "summary": node.summary,
        "artifact_kind": node.raw.get("artifact_kind") or "run_output",
        "status": "recorded",
        "stable_path": node.raw.get("path"),
        "path": node.raw.get("path"),
        "manifest_path": node.raw.get("manifest_path") or "",
        "links": dict(links),
        "retention": validate_retention(retention, "retention"),
        "promoted_artifact_id": None,
        "created_at": str(node.raw.get("created_at") or today),
        "updated_at": today,
    }
    if node.raw.get("agent"):
        record["agent"] = node.raw.get("agent")
    if node.raw.get("source_file_count") not in (None, ""):
        record["source_file_count"] = node.raw.get("source_file_count")
    return {key: value for key, value in record.items() if value not in ({}, [])}


def _migrate_linked_artifact_node(before: dict[str, Any], *, artifact_id: str, record_id: str, today: str) -> dict[str, Any]:
    after = copy.deepcopy(before)
    linked_artifacts = [str(item) for item in after.get("linked_artifacts", []) or []]
    linked_artifacts = [item for item in linked_artifacts if item != artifact_id]
    if linked_artifacts:
        after["linked_artifacts"] = linked_artifacts
    else:
        after.pop("linked_artifacts", None)
    linked_records = _unique_strings([*(after.get("linked_artifact_records", []) or []), record_id])
    after["linked_artifact_records"] = linked_records
    after["updated_at"] = today
    return after


def _bytes_or_none(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _restore_files(backups: dict[Path, bytes | None]) -> list[str]:
    errors: list[str] = []
    for path, content in reversed(list(backups.items())):
        try:
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return errors


def _apply_demotion(
    root: Path,
    *,
    yaml_writes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]],
    delete_paths: list[Path],
    interaction: dict[str, Any],
    rebuild_dashboard: bool,
) -> None:
    paths = _unique_strings([str(path) for path, _, _ in yaml_writes] + [str(path) for path in delete_paths])
    path_objects = [Path(path) for path in paths]
    backups = {path: _bytes_or_none(path) for path in path_objects}
    with mutation_lock(root):
        ensure_interaction_log_valid(root)
        conflicts = [str(path) for path, before in backups.items() if _bytes_or_none(path) != before]
        if conflicts:
            error = "Mutation conflict: truth-source file changed after command planning"
            raise MutationError(error, {"ok": False, "partial_success": False, "written_files": [], "error": error, "conflict_files": conflicts})
        written_files: list[str] = []
        try:
            for path, _, after in yaml_writes:
                save_yaml(path, after)
                written_files.append(str(path))
            for path in delete_paths:
                path.unlink()
                written_files.append(str(path))
            validate_cockpit(root, raise_on_error=True)
            _append_interaction_log_unlocked(root, prevalidated=True, **interaction)
        except Exception as exc:
            rollback_errors = _restore_files(backups)
            payload = {
                "ok": False,
                "partial_success": bool(written_files),
                "rolled_back": not rollback_errors,
                "written_files": written_files,
                "error": str(exc),
                "recovery_commands": [f"research-cockpit validate --root {root} --json"],
            }
            if rollback_errors:
                payload["rollback_errors"] = rollback_errors
            raise MutationError(f"Artifact demotion failed; rolled_back={not rollback_errors}: {exc}", payload) from exc
    if rebuild_dashboard:
        try:
            from research_cockpit.commands.build_dashboard import build_dashboard

            build_dashboard(root)
        except Exception as exc:
            raise MutationError(
                f"Artifact demotion succeeded but dashboard build failed: {exc}",
                {
                    "ok": False,
                    "partial_success": True,
                    "rolled_back": False,
                    "written_files": [str(path) for path in path_objects],
                    "error": str(exc),
                    "recovery_commands": [f"research-cockpit build --root {root}"],
                },
            ) from exc


def demote_artifact_node(
    root: Path,
    *,
    artifact_id: str,
    dry_run: bool = False,
    show_diff: bool = False,
    rebuild_dashboard: bool = True,
) -> dict[str, Any]:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
    if artifact_id not in nodes or nodes[artifact_id].type != "artifact":
        raise ValueError(f"Artifact node does not exist: {artifact_id}")
    node = nodes[artifact_id]
    references = _artifact_reference_index(nodes)
    row = classify_artifact_node(node, references.get(artifact_id, []))
    if row["classification"] != "can_demote":
        raise ValueError(f"Artifact {artifact_id!r} is {row['classification']}; only can_demote artifacts can be executed")
    experiment_id = _infer_experiment_id(node, row, nodes)
    if not experiment_id:
        raise ValueError(f"Cannot infer experiment_id for demoting artifact {artifact_id!r}")

    today = str(date.today())
    record_id = artifact_id
    run_id = _infer_run_id(node, experiment_id)
    record = _artifact_record_from_node(node, experiment_id=experiment_id, record_id=record_id, run_id=run_id, today=today)
    record_path, record_before, record_after = upsert_artifact_record(root, experiment_id, record)
    node_path = root / "graph" / "nodes" / f"{artifact_id}.yaml"
    node_before = load_yaml(node_path)

    candidate = dict(nodes)
    updated_nodes: list[str] = []
    yaml_writes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]] = [(record_path, record_before, record_after)]
    for ref_node_id in row.get("rewritable_references", []) or []:
        ref_path = root / "graph" / "nodes" / f"{ref_node_id}.yaml"
        ref_before = load_yaml(ref_path)
        ref_after = _migrate_linked_artifact_node(ref_before, artifact_id=artifact_id, record_id=record_id, today=today)
        if ref_before != ref_after:
            yaml_writes.append((ref_path, ref_before, ref_after))
            candidate[str(ref_node_id)] = ResearchNode.from_dict(ref_after)
            updated_nodes.append(str(ref_node_id))
    del candidate[artifact_id]

    report_path = root / "artifact_migrations" / f"{artifact_id}.yaml"
    report = {
        "schema_version": "artifact_demotion_report_v1",
        "artifact_id": artifact_id,
        "record_id": record_id,
        "experiment_id": experiment_id,
        "run_id": run_id,
        "demoted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "deleted_node_file": _relative_to_root(root, node_path),
        "artifact_record_file": _relative_to_root(root, record_path),
        "updated_nodes": updated_nodes,
        "payload_path": node.raw.get("path"),
        "payload_files_deleted": False,
        "classification": row,
    }
    yaml_writes.append((report_path, load_yaml(report_path) if report_path.exists() else None, report))
    diff_changes = [*yaml_writes, (node_path, node_before, None)]

    result: dict[str, Any] = {
        "ok": True,
        "schema_version": "artifact_compaction_result_v1",
        "root": str(root),
        "dry_run": dry_run,
        "changed": False if dry_run else True,
        "would_change": True,
        "artifact_id": artifact_id,
        "record_id": record_id,
        "experiment_id": experiment_id,
        "run_id": run_id,
        "artifact_record_path": str(record_path),
        "deleted_node_path": str(node_path),
        "migration_report_path": str(report_path),
        "updated_nodes": updated_nodes,
        "changed_files": [str(path) for path, _, _ in yaml_writes] + [str(node_path)],
        "changed_records": [f"artifact:{record_id}"],
        "payload_files_deleted": False,
        "notes": ["No payload files were deleted by artifact compaction."],
        "verify_commands": [f"research-cockpit validate --root {root} --json"],
        "final_handoff_commands": [
            f"research-cockpit validate --root {root} --json",
            f"research-cockpit build --root {root}",
            f"research-cockpit smoke --root {root} --json --progress",
        ],
    }
    if show_diff:
        result["diff"] = yaml_change_diff(diff_changes)
    if dry_run:
        return result

    _apply_demotion(
        root,
        yaml_writes=yaml_writes,
        delete_paths=[node_path],
        interaction={
            "kind": "compact_artifact_demotion",
            "actor": "researcher",
            "node_id": artifact_id,
            "command": script_command("compact_artifacts.py", "--id", artifact_id, "--execute"),
            "after": {
                "artifact_id": artifact_id,
                "record_id": record_id,
                "experiment_id": experiment_id,
                "updated_nodes": updated_nodes,
                "payload_files_deleted": False,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result