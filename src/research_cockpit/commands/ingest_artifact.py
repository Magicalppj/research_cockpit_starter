from __future__ import annotations

import argparse
import copy
import errno
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any
import uuid

from research_cockpit.commands._evidence import append_unique, linked_resource_rows, parse_link_values
from research_cockpit.commands._runtime import (
    compact_mutation_result,
    dry_run_preflight_result,
    emit_json,
    finish_mutation,
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
from research_cockpit.model import (
    ResearchNode,
    ValidationError,
    load_yaml,
    script_command,
    validate_cockpit,
)
from research_cockpit.mutation_lock import MutationError
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
MANIFEST_NAME = "_research_cockpit_ingest.json"


def _stable_path(*parts: str) -> str:
    return "/".join(str(part).strip("/\\") for part in parts if str(part).strip("/\\"))


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


def _validate_no_symlinks(source_dir: Path) -> None:
    symlinks = [path for path in source_dir.rglob("*") if path.is_symlink()]
    if symlinks:
        preview = ", ".join(path.relative_to(source_dir).as_posix() for path in symlinks[:3])
        extra = "" if len(symlinks) <= 3 else f", ... ({len(symlinks)} total)"
        raise ValueError(f"Artifact source must not contain symlinks: {preview}{extra}")


def _relative_link_path(source_dir: Path, value: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("Artifact link path must be non-empty")
    raw_path = Path(text)
    if raw_path.is_absolute():
        raise ValueError(f"Artifact link path must be relative to --from: {value}")
    candidate = (source_dir / raw_path).resolve()
    try:
        relative = candidate.relative_to(source_dir)
    except ValueError as exc:
        raise ValueError(f"Artifact link path escapes --from: {value}") from exc
    if not candidate.exists():
        raise FileNotFoundError(f"Artifact link path does not exist under --from: {value}")
    return relative.as_posix()


def _stable_links(source_dir: Path, stable_base: str, links: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in links.items():
        relative = _relative_link_path(source_dir, value)
        out[str(key)] = _stable_path(stable_base, relative)
    return out


def _target_artifact_dir(root: Path, node_id: str, run_id: str, source_dir: Path) -> Path:
    _validate_path_segment("node_id", node_id)
    artifact_root = (root / "artifacts").resolve()
    target_dir = artifact_root / node_id / run_id
    target_resolved = target_dir.resolve(strict=False)
    try:
        target_resolved.relative_to(artifact_root)
    except ValueError as exc:
        raise ValueError("Artifact target directory escapes the canonical artifact store") from exc
    try:
        target_resolved.relative_to(source_dir)
    except ValueError:
        return target_dir
    raise ValueError("--from must not contain the target artifact directory")


def _source_file_count(source_dir: Path) -> int:
    return sum(1 for path in source_dir.rglob("*") if path.is_file())


def _git_output(source_dir: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source_dir), *args],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    text = completed.stdout.strip()
    return text or None


def _source_git(source_dir: Path) -> dict[str, str | None]:
    return {
        "branch": _git_output(source_dir, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit": _git_output(source_dir, "rev-parse", "HEAD"),
    }


def _portable_source_path(root: Path, source_dir: Path) -> tuple[str, str]:
    base = root.resolve().parent
    try:
        return source_dir.relative_to(base).as_posix(), "canonical_root_parent"
    except ValueError:
        return source_dir.name, "external_hint"


def _manifest(
    *,
    root: Path,
    node_id: str,
    run_id: str,
    artifact_id: str,
    agent_id: str | None,
    source_dir: Path,
    stable_path: str,
    file_count: int,
    links: dict[str, str],
    mode: str,
    promotion_reason: str | None,
) -> dict[str, Any]:
    source_path, source_path_base = _portable_source_path(root, source_dir)
    manifest: dict[str, Any] = {
        "schema_version": "research_cockpit_ingest_v1",
        "node_id": node_id,
        "run_id": run_id,
        "artifact_id": artifact_id,
        "agent_id": agent_id,
        "source_path": source_path,
        "source_path_base": source_path_base,
        "stable_path": stable_path,
        "ingest_mode": mode,
        "ingested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_file_count": file_count,
        "links": links,
        "source_git": _source_git(source_dir),
    }
    if promotion_reason:
        manifest["promotion_reason"] = promotion_reason
    return manifest

def _rename_directory_with_retry(
    source: Path,
    target: Path,
    *,
    attempts: int = 6,
    initial_delay_seconds: float = 0.05,
) -> None:
    retryable_errnos = {errno.EACCES, errno.EBUSY, errno.EPERM}
    delay = initial_delay_seconds
    for attempt in range(attempts):
        try:
            source.rename(target)
            return
        except OSError as exc:
            if exc.errno not in retryable_errnos or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2

def _copy_to_stable_store(source_dir: Path, target_dir: Path, manifest: dict[str, Any]) -> None:
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = target_dir.parents[1] / f".tmp-{uuid.uuid4().hex[:12]}"
    try:
        shutil.copytree(source_dir, staging_dir, symlinks=True)
        _validate_no_symlinks(staging_dir)
        (staging_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _rename_directory_with_retry(staging_dir, target_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


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
    _validate_no_symlinks(source_resolved)
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

    stable_path = _stable_path("artifacts", node_id, run_id)
    target_dir = _target_artifact_dir(root, node_id, run_id, source_resolved)
    if target_dir.exists():
        raise FileExistsError(target_dir)
    stable_links = _stable_links(source_resolved, stable_path, links)
    file_count = _source_file_count(source_resolved)
    manifest = _manifest(
        root=root,
        node_id=node_id,
        run_id=run_id,
        artifact_id=artifact_id,
        agent_id=agent_id,
        source_dir=source_resolved,
        stable_path=stable_path,
        file_count=file_count,
        links=stable_links,
        mode=mode,
        promotion_reason=reason,
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
            manifest_path=_stable_path(stable_path, MANIFEST_NAME),
            source_file_count=file_count,
            links=stable_links,
            agent_id=agent_id,
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
        "changed": not dry_run,
        "would_change": True,
        "verified": not dry_run,
        "additional_verification_required": dry_run,
        "path": str(artifact_path),
        "changed_files": [str(path) for path, _, _ in changes],
        "linked_to": [node_id],
        "stable_path": stable_path,
        "target_dir": str(target_dir),
        "manifest_path": str(target_dir / MANIFEST_NAME),
        "source_path_resolved": str(source_resolved),
        "source_file_count": file_count,
        "links": stable_links,
        "before": None,
        "after": result_after,
        "resolved_inputs": {
            "source_path_resolved": str(source_resolved),
            "manifest_source_path": manifest["source_path"],
            "manifest_source_path_base": manifest["source_path_base"],
            "target_dir": str(target_dir),
            "stable_path": stable_path,
            "manifest_path": str(target_dir / MANIFEST_NAME),
            "source_git": manifest["source_git"],
        },
        "resource_rows": linked_resource_rows(root, candidate, [artifact_id, node_id]) if promote else [],
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        result["changed"] = False
        return dry_run_preflight_result(root, result)

    preflight_mutation(root)
    copied = False
    _copy_to_stable_store(source_resolved, target_dir, manifest)
    copied = True
    interaction_args = ["--node", node_id, "--run-id", run_id]
    if promote:
        interaction_args.append("--promote")
    try:
        finish_mutation(
            root,
            changes,
            interaction={
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
            },
            rebuild_dashboard=rebuild_dashboard,
        )
    except MutationError as exc:
        if copied and not exc.payload.get("partial_success"):
            shutil.rmtree(target_dir, ignore_errors=True)
        raise
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
