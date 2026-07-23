from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any
from urllib.parse import unquote, urlparse

from research_cockpit.artifact_records import (
    find_artifact_record,
    list_artifact_records,
    normalize_artifact_record,
)
from research_cockpit.evidence_staging import (
    MANIFEST_NAME,
    _content_digest,
    _copy_source_tree_hashed,
    _inside_git_worktree,
    _is_link_like,
)
from research_cockpit.interaction_log import (
    _append_interaction_log_unlocked,
    interaction_append_checkpoint,
    restore_interaction_append_checkpoint,
)
from research_cockpit.model import (
    ACTIVE_ASSIGNMENT_STATUSES,
    load_assignments,
    load_nodes,
    load_runs,
    validate_artifact_records,
)
from research_cockpit.mutation_lock import mutation_lock
from research_cockpit.mutation_runtime import ensure_interaction_log_valid
from research_cockpit.run_summaries import ACTIVE_RUN_STATUSES
from research_cockpit.storage import load_yaml, save_yaml
from research_cockpit.storage_layout import StorageLayout, resolve_storage_layout


MIGRATION_SCHEMA_VERSION = "artifact_storage_migration_v1"
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class _MigrationContext:
    root: Path
    layout: StorageLayout
    record_id: str
    operation_id: str
    migration_id: str
    record_path: Path
    record_before: dict[str, Any]
    record_before_bytes: bytes
    raw_record: dict[str, Any]
    record: dict[str, Any]
    experiment_id: str
    run_id: str
    source: Path
    source_relative_path: str
    managed_key: str
    staging_dir: Path
    target_dir: Path
    journal_path: Path
    transfer_method: str


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _safe_segment(label: str, value: Any) -> str:
    text = str(value or "").strip()
    if not _SAFE_SEGMENT.fullmatch(text):
        raise ValueError(
            f"{label} must be a path-safe identifier using letters, digits, dot, dash, or underscore"
        )
    return text


def _migration_id(record_id: str, operation_id: str) -> str:
    digest = hashlib.sha256(f"{record_id}\0{operation_id}".encode("utf-8")).hexdigest()
    return f"migration-{digest[:20]}"


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""


def _restore_bytes(path: Path, content: bytes) -> None:
    if not content:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.is_relative_to(parent)
    except ValueError:
        return False
    return True


def _assert_no_link_components(root: Path, path: Path) -> None:
    root = root.resolve(strict=False)
    candidate = Path(os.path.abspath(path))
    if not _is_relative_to(candidate, root):
        raise ValueError(f"artifact path escapes approved root: {candidate}")
    current = root
    for part in candidate.relative_to(root).parts:
        current /= part
        if os.path.lexists(current) and _is_link_like(current):
            raise ValueError(f"artifact path contains a symlink or junction: {current}")


def _legacy_source(root: Path, record: dict[str, Any]) -> tuple[Path, str]:
    storage = record.get("storage") if isinstance(record.get("storage"), dict) else {}
    locator = str(storage.get("uri") or record.get("stable_path") or "").strip()
    portable = PurePosixPath(locator.replace("\\", "/"))
    if (
        not locator
        or portable.is_absolute()
        or ".." in portable.parts
        or not portable.parts
        or portable.parts[0] != "artifacts"
    ):
        raise ValueError("legacy artifact record must point to a safe artifacts/<...> path")
    source = root.joinpath(*portable.parts)
    legacy_root = (root / "artifacts").resolve(strict=False)
    _assert_no_link_components(root, source)
    if not _is_relative_to(source.resolve(strict=False), legacy_root):
        raise ValueError("legacy artifact source must remain under artifacts/")
    return source, portable.as_posix()


def _existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _same_filesystem(source: Path, artifact_root: Path) -> bool:
    return os.stat(source).st_dev == os.stat(_existing_parent(artifact_root)).st_dev


def _subtree_ids(nodes: dict[str, Any], root_id: str) -> set[str]:
    children: dict[str, list[str]] = {}
    for node in nodes.values():
        parent = getattr(node, "parent", None)
        if parent:
            children.setdefault(str(parent), []).append(str(node.id))
    selected: set[str] = set()
    pending = [root_id]
    while pending:
        node_id = pending.pop()
        if node_id in selected or node_id not in nodes:
            continue
        selected.add(node_id)
        pending.extend(children.get(node_id, []))
    return selected


def active_record_blockers(root: Path, *, experiment_id: str, run_id: str) -> list[str]:
    nodes = load_nodes(root)
    blockers: list[str] = []
    for assignment in load_assignments(root).values():
        if assignment.status not in ACTIVE_ASSIGNMENT_STATUSES:
            continue
        scope = _subtree_ids(nodes, assignment.root_node)
        if experiment_id in scope or assignment.current_node == experiment_id:
            blockers.append(f"active_assignment:{assignment.assignment_id}")
    for run in load_runs(root).values():
        if (
            run.experiment_id == experiment_id
            and run.status in ACTIVE_RUN_STATUSES
            and not run.finished_at
        ):
            blockers.append(f"active_run:{run.run_id}")
    if run_id:
        for run in load_runs(root).values():
            if run.run_id == run_id and run.status in ACTIVE_RUN_STATUSES:
                marker = f"active_run:{run.run_id}"
                if marker not in blockers:
                    blockers.append(marker)
    return sorted(set(blockers))


def _journal_path(root: Path, migration_id: str) -> Path:
    return root / "artifact_migrations" / f"{migration_id}.yaml"


def _load_journal(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    journal = load_yaml(path)
    if not isinstance(journal, dict) or journal.get("schema_version") != MIGRATION_SCHEMA_VERSION:
        raise ValueError(f"{path}: unsupported artifact migration journal")
    return journal


def _managed_paths(
    layout: StorageLayout,
    *,
    experiment_id: str,
    run_id: str,
    record_id: str,
    migration_id: str,
) -> tuple[str, Path, Path]:
    artifact_root = layout.require_managed_artifact_root()
    parts = [
        _safe_segment("experiment_id", experiment_id),
        _safe_segment("run_id", run_id),
        _safe_segment("record_id", record_id),
    ]
    managed_key = PurePosixPath(*parts).as_posix()
    target = artifact_root.joinpath(*parts)
    staging = artifact_root / ".staging" / migration_id
    _assert_no_link_components(artifact_root, target)
    _assert_no_link_components(artifact_root, staging)
    return managed_key, staging, target


def _raw_record(root: Path, record_id: str) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    path, _normalized_file, normalized = find_artifact_record(root, record_id)
    raw_file = load_yaml(path)
    raw_records = raw_file.get("records") if isinstance(raw_file, dict) else None
    raw_record = raw_records.get(record_id) if isinstance(raw_records, dict) else None
    if not isinstance(raw_record, dict):
        raise ValueError(f"{path}: artifact record {record_id!r} is missing")
    return path, raw_file, deepcopy(raw_record), normalized


def _build_context(root: Path, *, record_id: str, operation_id: str) -> _MigrationContext:
    record_id = _safe_segment("record_id", record_id)
    operation_id = _safe_segment("operation_id", operation_id)
    root = root.resolve()
    layout = resolve_storage_layout(root)
    artifact_root = layout.require_managed_artifact_root()
    if _inside_git_worktree(artifact_root):
        raise ValueError(
            "managed artifact root must be outside a Git worktree; configure external storage"
        )
    record_path, record_before, raw_record, record = _raw_record(root, record_id)
    storage = record.get("storage") if isinstance(record.get("storage"), dict) else {}
    if storage.get("mode") != "legacy":
        raise ValueError(f"Artifact record {record_id!r} is not a legacy artifact")
    experiment_id = _safe_segment("experiment_id", record.get("experiment_id"))
    run_id = _safe_segment("run_id", record.get("run_id"))
    source, source_relative_path = _legacy_source(root, record)
    migration_id = _migration_id(record_id, operation_id)
    managed_key, staging_dir, target_dir = _managed_paths(
        layout,
        experiment_id=experiment_id,
        run_id=run_id,
        record_id=record_id,
        migration_id=migration_id,
    )
    transfer_method = (
        "same_filesystem_rename"
        if source.exists() and _same_filesystem(source, artifact_root)
        else "copy_and_verify"
    )
    return _MigrationContext(
        root=root,
        layout=layout,
        record_id=record_id,
        operation_id=operation_id,
        migration_id=migration_id,
        record_path=record_path,
        record_before=record_before,
        record_before_bytes=_read_bytes(record_path),
        raw_record=raw_record,
        record=record,
        experiment_id=experiment_id,
        run_id=run_id,
        source=source,
        source_relative_path=source_relative_path,
        managed_key=managed_key,
        staging_dir=staging_dir,
        target_dir=target_dir,
        journal_path=_journal_path(root, migration_id),
        transfer_method=transfer_method,
    )


def _migration_journal(context: _MigrationContext) -> dict[str, Any]:
    return {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_id": context.migration_id,
        "operation_id": context.operation_id,
        "record_id": context.record_id,
        "experiment_id": context.experiment_id,
        "run_id": context.run_id,
        "phase": "planned",
        "source_relative_path": context.source_relative_path,
        "managed_key": context.managed_key,
        "transfer_method": context.transfer_method,
        "created_at": _utc_timestamp(),
        "updated_at": _utc_timestamp(),
    }


def _check_journal(context: _MigrationContext, journal: dict[str, Any]) -> None:
    expected = {
        "migration_id": context.migration_id,
        "operation_id": context.operation_id,
        "record_id": context.record_id,
        "experiment_id": context.experiment_id,
        "run_id": context.run_id,
        "source_relative_path": context.source_relative_path,
        "managed_key": context.managed_key,
    }
    mismatched = [
        key for key, value in expected.items() if journal.get(key) != value
    ]
    if mismatched:
        raise ValueError(
            "artifact migration journal does not match this request: "
            + ", ".join(mismatched)
        )


def _write_initial_journal(context: _MigrationContext) -> dict[str, Any]:
    with mutation_lock(context.root):
        ensure_interaction_log_valid(context.root)
        existing = _load_journal(context.journal_path)
        if existing is not None:
            _check_journal(context, existing)
            return existing
        journal = _migration_journal(context)
        save_yaml(context.journal_path, journal)
        return journal


def _update_journal(context: _MigrationContext, journal: dict[str, Any], **updates: Any) -> dict[str, Any]:
    updated = deepcopy(journal)
    updated.update(deepcopy(updates))
    updated["updated_at"] = _utc_timestamp()
    with mutation_lock(context.root):
        current = _load_journal(context.journal_path)
        if current is None:
            raise ValueError("artifact migration journal disappeared during migration")
        _check_journal(context, current)
        save_yaml(context.journal_path, updated)
    return updated


def _map_link(
    value: Any,
    *,
    root: Path,
    source: Path,
    target: Path,
) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    candidate: Path | None = None
    parsed = urlparse(text)
    if parsed.scheme == "file":
        candidate = Path(unquote(parsed.path))
    elif not parsed.scheme:
        raw = Path(text)
        if raw.is_absolute():
            candidate = raw
        else:
            portable = PurePosixPath(text.replace("\\", "/"))
            if not portable.is_absolute() and ".." not in portable.parts:
                candidate = root.joinpath(*portable.parts)
    if candidate is None:
        return text
    try:
        relative = candidate.resolve(strict=False).relative_to(source.resolve(strict=False))
    except ValueError:
        return text
    return target.joinpath(*relative.parts).as_uri()


def _migrated_links(context: _MigrationContext) -> dict[str, Any]:
    raw_links = context.raw_record.get("links")
    if not isinstance(raw_links, dict):
        return {}
    return {
        str(key): _map_link(
            value,
            root=context.root,
            source=context.source,
            target=context.target_dir,
        )
        for key, value in raw_links.items()
    }


def _manifest_for(
    context: _MigrationContext,
    *,
    digest: str,
    file_count: int,
    size_bytes: int,
) -> dict[str, Any]:
    integrity = {
        "level": "content",
        "algorithm": "sha256",
        "digest": f"sha256:{digest}",
    }
    inventory = {
        "size_bytes": size_bytes,
        "file_count": file_count,
        "complete": True,
        "entries_scanned": file_count,
    }
    storage = {
        "mode": "managed",
        "ownership": "cockpit_managed",
        "uri": context.target_dir.as_uri(),
        "managed_key": context.managed_key,
    }
    return {
        "schema_version": "evidence_ingest_v1",
        "assignment_id": f"maintenance-{context.migration_id}",
        "experiment_id": context.experiment_id,
        "run_id": context.run_id,
        "record_id": context.record_id,
        "storage": storage,
        "links": _migrated_links(context),
        "inventory": inventory,
        "integrity": integrity,
        "migration": {
            "schema_version": MIGRATION_SCHEMA_VERSION,
            "migration_id": context.migration_id,
            "source_relative_path": context.source_relative_path,
        },
    }


def _read_json_manifest(path: Path) -> dict[str, Any]:
    manifest_path = path / MANIFEST_NAME
    if not manifest_path.is_file() or _is_link_like(manifest_path):
        raise ValueError(f"managed artifact has no trusted manifest: {path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"managed artifact manifest is unreadable: {path}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"managed artifact manifest is invalid: {path}")
    return manifest


def verify_managed_payload(
    context: _MigrationContext,
    payload: Path,
) -> dict[str, Any]:
    _assert_no_link_components(
        context.layout.require_managed_artifact_root(), payload
    )
    if not payload.is_dir() or _is_link_like(payload):
        raise ValueError(f"managed artifact payload is not a directory: {payload}")
    manifest = _read_json_manifest(payload)
    storage = manifest.get("storage") if isinstance(manifest.get("storage"), dict) else {}
    integrity = manifest.get("integrity") if isinstance(manifest.get("integrity"), dict) else {}
    inventory = manifest.get("inventory") if isinstance(manifest.get("inventory"), dict) else {}
    expected = {
        "schema_version": "evidence_ingest_v1",
        "experiment_id": context.experiment_id,
        "run_id": context.run_id,
        "record_id": context.record_id,
    }
    mismatched = [
        field_name
        for field_name, value in expected.items()
        if manifest.get(field_name) != value
    ]
    if (
        storage.get("mode") != "managed"
        or storage.get("ownership") != "cockpit_managed"
        or storage.get("managed_key") != context.managed_key
        or storage.get("uri") != context.target_dir.as_uri()
    ):
        mismatched.append("storage")
    if integrity.get("level") != "content" or integrity.get("algorithm") != "sha256":
        mismatched.append("integrity")
    if inventory.get("complete") is not True:
        mismatched.append("inventory")
    if mismatched:
        raise ValueError(
            "managed artifact manifest does not match migration: "
            + ", ".join(sorted(set(mismatched)))
        )
    digest, file_count, size_bytes = _content_digest(
        payload,
        excluded_paths={MANIFEST_NAME},
    )
    if integrity.get("digest") != f"sha256:{digest}":
        raise ValueError("managed artifact content digest does not match manifest")
    if (
        inventory.get("file_count") != file_count
        or inventory.get("size_bytes") != size_bytes
    ):
        raise ValueError("managed artifact inventory does not match manifest")
    return {
        "manifest": manifest,
        "integrity": deepcopy(integrity),
        "inventory": deepcopy(inventory),
        "digest": digest,
        "file_count": file_count,
        "size_bytes": size_bytes,
    }


def _write_manifest(
    context: _MigrationContext,
    staging: Path,
    *,
    digest: str,
    file_count: int,
    size_bytes: int,
) -> dict[str, Any]:
    manifest_path = staging / MANIFEST_NAME
    if manifest_path.exists() or os.path.lexists(manifest_path):
        raise FileExistsError(f"legacy artifact contains reserved manifest name: {manifest_path}")
    manifest = _manifest_for(
        context,
        digest=digest,
        file_count=file_count,
        size_bytes=size_bytes,
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _remove_tree(path: Path) -> None:
    if not path.exists() and not os.path.lexists(path):
        return
    if _is_link_like(path):
        raise ValueError(f"refusing to remove symlink or junction: {path}")
    shutil.rmtree(path)


def _restore_renamed_source(context: _MigrationContext) -> None:
    if context.source.exists() or not context.staging_dir.exists():
        return
    context.source.parent.mkdir(parents=True, exist_ok=True)
    context.staging_dir.replace(context.source)


def _stage_payload(context: _MigrationContext) -> tuple[Path, dict[str, Any]]:
    artifact_root = context.layout.require_managed_artifact_root()
    artifact_root.mkdir(parents=True, exist_ok=True)
    _assert_no_link_components(artifact_root, context.staging_dir)
    _assert_no_link_components(artifact_root, context.target_dir)
    if context.target_dir.exists() or os.path.lexists(context.target_dir):
        verification = verify_managed_payload(context, context.target_dir)
        return context.target_dir, verification

    if context.staging_dir.exists() or os.path.lexists(context.staging_dir):
        try:
            return context.staging_dir, verify_managed_payload(context, context.staging_dir)
        except ValueError:
            if context.transfer_method == "same_filesystem_rename" and not context.source.exists():
                manifest_path = context.staging_dir / MANIFEST_NAME
                if manifest_path.exists() or os.path.lexists(manifest_path):
                    raise
                digest, file_count, size_bytes = _content_digest(context.staging_dir)
                _write_manifest(
                    context,
                    context.staging_dir,
                    digest=digest,
                    file_count=file_count,
                    size_bytes=size_bytes,
                )
                return context.staging_dir, verify_managed_payload(
                    context, context.staging_dir
                )
            if context.source.exists():
                _remove_tree(context.staging_dir)
            else:
                raise ValueError(
                    "migration staging is incomplete and legacy source is unavailable"
                )

    if not context.source.exists():
        raise FileNotFoundError(
            f"legacy artifact source is unavailable: {context.source_relative_path}"
        )
    if _is_link_like(context.source) or not context.source.is_dir():
        raise ValueError("legacy artifact source must be a non-symlink directory")
    if (context.source / MANIFEST_NAME).exists() or os.path.lexists(
        context.source / MANIFEST_NAME
    ):
        raise ValueError("legacy artifact source contains reserved managed manifest name")
    context.staging_dir.parent.mkdir(parents=True, exist_ok=True)
    if context.transfer_method == "same_filesystem_rename":
        context.source.replace(context.staging_dir)
        try:
            digest, file_count, size_bytes = _content_digest(context.staging_dir)
            _write_manifest(
                context,
                context.staging_dir,
                digest=digest,
                file_count=file_count,
                size_bytes=size_bytes,
            )
            return context.staging_dir, verify_managed_payload(
                context, context.staging_dir
            )
        except BaseException:
            _restore_renamed_source(context)
            raise
    try:
        digest, file_count, size_bytes = _copy_source_tree_hashed(
            context.source,
            context.staging_dir,
            enforce_admission_limits=False,
        )
        _write_manifest(
            context,
            context.staging_dir,
            digest=digest,
            file_count=file_count,
            size_bytes=size_bytes,
        )
        return context.staging_dir, verify_managed_payload(context, context.staging_dir)
    except BaseException:
        _remove_tree(context.staging_dir)
        raise


def _updated_raw_record(
    context: _MigrationContext,
    verification: dict[str, Any],
) -> dict[str, Any]:
    updated = deepcopy(context.raw_record)
    storage = deepcopy(verification["manifest"]["storage"])
    updated["stable_path"] = context.target_dir.as_uri()
    if "path" in updated:
        updated["path"] = context.target_dir.as_uri()
    updated["manifest_path"] = (context.target_dir / MANIFEST_NAME).as_uri()
    updated["links"] = _migrated_links(context)
    updated["storage"] = storage
    updated["integrity"] = deepcopy(verification["integrity"])
    updated["inventory"] = deepcopy(verification["inventory"])
    updated["availability"] = {
        "status": "available",
        "last_verified_at": _utc_timestamp(),
    }
    updated.setdefault("lifecycle", {"supersedes": [], "superseded_by": None})
    updated["content_sha256"] = str(verification["digest"])
    updated["source_file_count"] = int(verification["file_count"])
    updated["updated_at"] = _utc_timestamp()
    updated["migration"] = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_id": context.migration_id,
        "source_relative_path": context.source_relative_path,
        "transfer_method": context.transfer_method,
    }
    return updated


def _validate_record_update(context: _MigrationContext, updated_record: dict[str, Any]) -> None:
    records = list_artifact_records(context.root)
    candidate_records = [
        normalize_artifact_record(
            updated_record,
            record_id=context.record_id,
            experiment_id=context.experiment_id,
        )
        if row.get("record_id") == context.record_id
        else row
        for row in records
    ]
    errors = validate_artifact_records(
        context.root,
        load_nodes(context.root),
        candidate_records,
    )
    if errors:
        raise ValueError("artifact migration record validation failed: " + "; ".join(errors))


def _publish_migration(
    context: _MigrationContext,
    journal: dict[str, Any],
    staging: Path,
    verification: dict[str, Any],
) -> dict[str, Any]:
    updated_record = _updated_raw_record(context, verification)
    _validate_record_update(context, updated_record)
    record_after = deepcopy(context.record_before)
    records = record_after.get("records")
    if not isinstance(records, dict):
        raise ValueError(f"{context.record_path}: records must be a mapping")
    records[context.record_id] = updated_record
    journal_after = deepcopy(journal)
    journal_after.update(
        {
            "phase": "published",
            "published_at": _utc_timestamp(),
            "source_disposition": (
                "moved"
                if context.transfer_method == "same_filesystem_rename"
                else "retained"
            ),
            "integrity": deepcopy(verification["integrity"]),
            "inventory": deepcopy(verification["inventory"]),
            "target_managed_key": context.managed_key,
            "updated_at": _utc_timestamp(),
        }
    )
    record_backup = _read_bytes(context.record_path)
    journal_backup = _read_bytes(context.journal_path)
    moved_target = False
    with mutation_lock(context.root):
        ensure_interaction_log_valid(context.root)
        if _read_bytes(context.record_path) != context.record_before_bytes:
            raise ValueError("artifact record changed after migration planning; rerun the migration plan")
        current_journal = _load_journal(context.journal_path)
        if current_journal is None:
            raise ValueError("artifact migration journal disappeared before publish")
        _check_journal(context, current_journal)
        if context.target_dir.exists() or os.path.lexists(context.target_dir):
            verification = verify_managed_payload(context, context.target_dir)
            record_after["records"][context.record_id] = _updated_raw_record(
                context, verification
            )
            journal_after["integrity"] = deepcopy(verification["integrity"])
            journal_after["inventory"] = deepcopy(verification["inventory"])
        else:
            verify_managed_payload(context, staging)
            context.target_dir.parent.mkdir(parents=True, exist_ok=True)
            _assert_no_link_components(
                context.layout.require_managed_artifact_root(), context.target_dir
            )
            staging.replace(context.target_dir)
            moved_target = True
        checkpoint = interaction_append_checkpoint(context.root)
        try:
            save_yaml(context.record_path, record_after)
            save_yaml(context.journal_path, journal_after)
            _append_interaction_log_unlocked(
                context.root,
                prevalidated=True,
                kind="migrate_legacy_artifact",
                actor="maintainer",
                node_id=context.experiment_id,
                command=(
                    "research-cockpit maintenance migrate --file "
                    "<artifact-storage-migration.yaml> --json --compact"
                ),
                after={
                    "record_id": context.record_id,
                    "managed_key": context.managed_key,
                    "transfer_method": context.transfer_method,
                },
            )
        except BaseException:
            restore_interaction_append_checkpoint(context.root, checkpoint)
            _restore_bytes(context.record_path, record_backup)
            _restore_bytes(context.journal_path, journal_backup)
            if moved_target and context.target_dir.exists() and not staging.exists():
                context.target_dir.replace(staging)
            raise
    return {
        "record": record_after["records"][context.record_id],
        "journal": journal_after,
    }


def _replay_result(
    *,
    root: Path,
    record_id: str,
    operation_id: str,
    journal: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": "artifact_storage_migration_result_v1",
        "root": str(root),
        "record_id": record_id,
        "operation_id": operation_id,
        "migration_id": journal.get("migration_id"),
        "migration_report_path": str(
            _journal_path(root, str(journal.get("migration_id") or ""))
        ),
        "managed_key": journal.get("managed_key"),
        "transfer_method": journal.get("transfer_method"),
        "status": "replayed",
        "replayed": True,
        "changed": False,
        "source_disposition": journal.get("source_disposition"),
    }


def _recover_published_record(
    root: Path,
    *,
    record_id: str,
    operation_id: str,
) -> dict[str, Any] | None:
    normalized_record_id = _safe_segment("record_id", record_id)
    normalized_operation_id = _safe_segment("operation_id", operation_id)
    migration_id = _migration_id(normalized_record_id, normalized_operation_id)
    journal_path = _journal_path(root, migration_id)
    journal = _load_journal(journal_path)
    if journal is None:
        return None
    expected = {
        "migration_id": migration_id,
        "record_id": normalized_record_id,
        "operation_id": normalized_operation_id,
    }
    if any(journal.get(key) != value for key, value in expected.items()):
        raise ValueError("artifact migration journal identity mismatch")
    _path, _raw_file, raw_record_value, record = _raw_record(root, normalized_record_id)
    del raw_record_value
    storage = record.get("storage") if isinstance(record.get("storage"), dict) else {}
    if (
        storage.get("mode") != "managed"
        or storage.get("managed_key") != journal.get("managed_key")
    ):
        return None
    if journal.get("phase") == "published":
        return journal
    with mutation_lock(root):
        current = _load_journal(journal_path)
        if current is None or any(
            current.get(key) != value for key, value in expected.items()
        ):
            raise ValueError("artifact migration journal changed during recovery")
        _path, _raw_file, raw_record_value, latest_record = _raw_record(
            root, normalized_record_id
        )
        del raw_record_value
        latest_storage = (
            latest_record.get("storage")
            if isinstance(latest_record.get("storage"), dict)
            else {}
        )
        if (
            latest_storage.get("mode") != "managed"
            or latest_storage.get("managed_key") != current.get("managed_key")
        ):
            return None
        current.update(
            {
                "phase": "published",
                "published_at": current.get("published_at") or _utc_timestamp(),
                "source_disposition": current.get("source_disposition")
                or (
                    "moved"
                    if current.get("transfer_method") == "same_filesystem_rename"
                    else "retained"
                ),
                "updated_at": _utc_timestamp(),
            }
        )
        save_yaml(journal_path, current)
        return current


def plan_legacy_artifact_migration(
    root: Path,
    *,
    record_id: str,
    operation_id: str,
) -> dict[str, Any]:
    context = _build_context(root, record_id=record_id, operation_id=operation_id)
    blockers = active_record_blockers(
        context.root,
        experiment_id=context.experiment_id,
        run_id=context.run_id,
    )
    if not context.source.exists():
        blockers.append("legacy_source_missing")
    elif _is_link_like(context.source) or not context.source.is_dir():
        blockers.append("legacy_source_not_directory")
    target_state = "absent"
    try:
        if context.target_dir.exists() or os.path.lexists(context.target_dir):
            verify_managed_payload(context, context.target_dir)
            target_state = "verified_target"
        elif context.staging_dir.exists() or os.path.lexists(context.staging_dir):
            target_state = "staged_or_incomplete"
    except ValueError:
        blockers.append("managed_target_conflict")
        target_state = "conflict"
    journal = _load_journal(context.journal_path)
    if journal is not None:
        _check_journal(context, journal)
    return {
        "ok": True,
        "schema_version": "artifact_storage_migration_plan_v1",
        "root": str(context.root),
        "dry_run": True,
        "eligible": not blockers,
        "record_id": context.record_id,
        "operation_id": context.operation_id,
        "migration_id": context.migration_id,
        "experiment_id": context.experiment_id,
        "run_id": context.run_id,
        "source_relative_path": context.source_relative_path,
        "managed_key": context.managed_key,
        "transfer_method": context.transfer_method,
        "target_state": target_state,
        "migration_report_path": str(context.journal_path),
        "blockers": sorted(set(blockers)),
        "notes": [
            "Dry-run does not allocate managed storage or modify legacy evidence.",
            "Cross-filesystem migration preserves the legacy source for later explicit cleanup.",
        ],
    }


def migrate_legacy_artifact(
    root: Path,
    *,
    record_id: str,
    operation_id: str,
    execute: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    if not execute:
        return plan_legacy_artifact_migration(
            root,
            record_id=record_id,
            operation_id=operation_id,
        )
    recovered = _recover_published_record(
        root,
        record_id=record_id,
        operation_id=operation_id,
    )
    if recovered is not None:
        return _replay_result(
            root=root,
            record_id=record_id,
            operation_id=operation_id,
            journal=recovered,
        )
    context = _build_context(root, record_id=record_id, operation_id=operation_id)
    plan = plan_legacy_artifact_migration(
        context.root,
        record_id=context.record_id,
        operation_id=context.operation_id,
    )
    blockers = list(plan["blockers"])
    if blockers:
        raise ValueError("artifact storage migration is blocked: " + ", ".join(blockers))
    journal = _write_initial_journal(context)
    staging, verification = _stage_payload(context)
    if staging == context.staging_dir:
        journal = _update_journal(
            context,
            journal,
            phase="staged",
            integrity=deepcopy(verification["integrity"]),
            inventory=deepcopy(verification["inventory"]),
        )
    try:
        published = _publish_migration(context, journal, staging, verification)
    except BaseException:
        if context.transfer_method == "same_filesystem_rename":
            _restore_renamed_source(context)
        raise
    try:
        from research_cockpit.artifact_inventory import patch_artifact_inventory

        inventory_update = patch_artifact_inventory(
            context.root,
            [context.record_path, context.target_dir],
        )
    except Exception as exc:
        inventory_update = {"status": "stale", "updated": False, "error": str(exc)}
    return {
        "ok": True,
        "schema_version": "artifact_storage_migration_result_v1",
        "root": str(context.root),
        "record_id": context.record_id,
        "operation_id": context.operation_id,
        "migration_id": context.migration_id,
        "migration_report_path": str(context.journal_path),
        "experiment_id": context.experiment_id,
        "run_id": context.run_id,
        "managed_key": context.managed_key,
        "transfer_method": context.transfer_method,
        "status": "published",
        "replayed": False,
        "changed": True,
        "source_disposition": published["journal"]["source_disposition"],
        "inventory_update": inventory_update,
    }
