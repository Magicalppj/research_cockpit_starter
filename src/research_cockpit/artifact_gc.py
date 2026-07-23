from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any

from research_cockpit.artifact_migration import active_record_blockers
from research_cockpit.artifact_records import (
    find_artifact_record,
    list_artifact_records,
    normalize_artifact_record,
)
from research_cockpit.evidence_staging import MANIFEST_NAME, _content_digest, _is_link_like
from research_cockpit.interaction_log import (
    _append_interaction_log_unlocked,
    interaction_append_checkpoint,
    restore_interaction_append_checkpoint,
)
from research_cockpit.milestone_handoffs import root_truth_revision
from research_cockpit.model import load_nodes, validate_artifact_records
from research_cockpit.mutation_lock import mutation_lock
from research_cockpit.mutation_runtime import ensure_interaction_log_valid
from research_cockpit.storage import load_yaml, save_yaml
from research_cockpit.storage_layout import StorageLayout, resolve_storage_layout


GC_SCHEMA_VERSION = "artifact_gc_transition_v1"
DEFAULT_PURGE_DELAY_SECONDS = 7 * 24 * 60 * 60
MIN_PURGE_DELAY_SECONDS = 60
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_GC_RETENTION_CLASSES = {
    "disposable_cache",
    "reproducible_output",
    "deprecated_payload",
}
_MUST_KEEP_RETENTION_CLASSES = {
    "evidence_critical",
    "portable_review_bundle",
    "final_checkpoint",
    "resume_state",
}


@dataclass(frozen=True)
class _GcContext:
    root: Path
    layout: StorageLayout
    record_id: str
    operation_id: str
    phase: str
    gc_id: str
    record_path: Path
    record_before: dict[str, Any]
    record_before_bytes: bytes
    raw_record: dict[str, Any]
    record: dict[str, Any]
    experiment_id: str
    run_id: str
    managed_key: str
    target_dir: Path
    quarantine_key: str
    quarantine_dir: Path
    manifest_dir: Path
    prepared_path: Path
    final_path: Path


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_timestamp(value: datetime | None = None) -> str:
    current = value or _utc_now()
    return current.isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_segment(label: str, value: Any) -> str:
    text = str(value or "").strip()
    if not _SAFE_SEGMENT.fullmatch(text):
        raise ValueError(
            f"{label} must be a path-safe identifier using letters, digits, dot, dash, or underscore"
        )
    return text


def _safe_relative_key(label: str, value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts or any(
        part in {"", "."} for part in path.parts
    ):
        raise ValueError(f"{label} must be a safe relative managed path")
    return path.as_posix()


def _gc_id(record_id: str, operation_id: str, phase: str) -> str:
    digest = hashlib.sha256(
        f"{record_id}\0{operation_id}\0{phase}".encode("utf-8")
    ).hexdigest()
    return f"gc-{digest[:20]}"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.is_relative_to(parent)
    except ValueError:
        return False
    return True


def _assert_safe_path(root: Path, path: Path) -> None:
    root = root.resolve(strict=False)
    candidate = Path(os.path.abspath(path))
    if not _is_relative_to(candidate, root):
        raise ValueError(f"artifact path escapes approved root: {candidate}")
    current = root
    for part in candidate.relative_to(root).parts:
        current /= part
        if os.path.lexists(current) and _is_link_like(current):
            raise ValueError(f"artifact path contains a symlink or junction: {current}")


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


def _raw_record(
    root: Path, record_id: str
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    path, _normalized_file, normalized = find_artifact_record(root, record_id)
    raw_file = load_yaml(path)
    raw_records = raw_file.get("records") if isinstance(raw_file, dict) else None
    raw_record = raw_records.get(record_id) if isinstance(raw_records, dict) else None
    if not isinstance(raw_record, dict):
        raise ValueError(f"{path}: artifact record {record_id!r} is missing")
    return path, raw_file, deepcopy(raw_record), normalized


def _transition_paths(root: Path, gc_id: str, phase: str) -> tuple[Path, Path, Path]:
    directory = root / "artifact_gc_manifests"
    final_name = "quarantined" if phase == "quarantine" else "purged"
    return (
        directory,
        directory / f"{gc_id}-{phase}-prepared.yaml",
        directory / f"{gc_id}-{final_name}.yaml",
    )


def _build_context(
    root: Path,
    *,
    record_id: str,
    operation_id: str,
    phase: str,
) -> _GcContext:
    if phase not in {"quarantine", "purge"}:
        raise ValueError("artifact GC phase must be quarantine or purge")
    root = root.resolve()
    record_id = _safe_segment("record_id", record_id)
    operation_id = _safe_segment("operation_id", operation_id)
    layout = resolve_storage_layout(root)
    artifact_root = layout.require_managed_artifact_root()
    record_path, record_before, raw_record, record = _raw_record(root, record_id)
    experiment_id = _safe_segment("experiment_id", record.get("experiment_id"))
    run_id = _safe_segment("run_id", record.get("run_id"))
    storage = record.get("storage") if isinstance(record.get("storage"), dict) else {}
    try:
        managed_key = _safe_relative_key(
            "storage.managed_key", storage.get("managed_key")
        )
        target = artifact_root.joinpath(*PurePosixPath(managed_key).parts)
    except ValueError:
        managed_key = ""
        target = artifact_root / ".invalid" / _gc_id(record_id, operation_id, phase)
    _assert_safe_path(artifact_root, target)
    current_gc = record.get("gc") if isinstance(record.get("gc"), dict) else {}
    current_gc_id = _gc_id(record_id, operation_id, phase)
    if phase == "quarantine":
        quarantine_key = PurePosixPath(record_id, current_gc_id).as_posix()
    else:
        try:
            quarantine_key = _safe_relative_key(
                "gc.quarantine_key", current_gc.get("quarantine_key")
            )
        except ValueError:
            quarantine_key = ""
    quarantine = artifact_root / ".quarantine" / (
        PurePosixPath(quarantine_key)
        if quarantine_key
        else PurePosixPath(".invalid", current_gc_id)
    )
    _assert_safe_path(artifact_root, quarantine)
    manifest_dir, prepared_path, final_path = _transition_paths(
        root, current_gc_id, phase
    )
    return _GcContext(
        root=root,
        layout=layout,
        record_id=record_id,
        operation_id=operation_id,
        phase=phase,
        gc_id=current_gc_id,
        record_path=record_path,
        record_before=record_before,
        record_before_bytes=_read_bytes(record_path),
        raw_record=raw_record,
        record=record,
        experiment_id=experiment_id,
        run_id=run_id,
        managed_key=managed_key,
        target_dir=target,
        quarantine_key=quarantine_key,
        quarantine_dir=quarantine,
        manifest_dir=manifest_dir,
        prepared_path=prepared_path,
        final_path=final_path,
    )


def _parse_delay(value: Any) -> int:
    if value is None:
        return DEFAULT_PURGE_DELAY_SECONDS
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("purge_after_seconds must be an integer")
    if value < MIN_PURGE_DELAY_SECONDS:
        raise ValueError(
            f"purge_after_seconds must be at least {MIN_PURGE_DELAY_SECONDS}"
        )
    return value


def _parse_timestamp(value: Any, *, field_name: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _read_manifest(payload: Path) -> dict[str, Any]:
    manifest_path = payload / MANIFEST_NAME
    if not manifest_path.is_file() or _is_link_like(manifest_path):
        raise ValueError(f"managed payload has no trusted manifest: {payload}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"managed payload manifest is unreadable: {payload}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"managed payload manifest is invalid: {payload}")
    return manifest


def _verify_managed_payload(context: _GcContext, payload: Path) -> dict[str, Any]:
    artifact_root = context.layout.require_managed_artifact_root()
    _assert_safe_path(artifact_root, payload)
    if not payload.is_dir() or _is_link_like(payload):
        raise ValueError(f"managed payload is not a directory: {payload}")
    manifest = _read_manifest(payload)
    storage = manifest.get("storage") if isinstance(manifest.get("storage"), dict) else {}
    integrity = manifest.get("integrity") if isinstance(manifest.get("integrity"), dict) else {}
    inventory = manifest.get("inventory") if isinstance(manifest.get("inventory"), dict) else {}
    record_storage = (
        context.record.get("storage")
        if isinstance(context.record.get("storage"), dict)
        else {}
    )
    record_integrity = (
        context.record.get("integrity")
        if isinstance(context.record.get("integrity"), dict)
        else {}
    )
    record_inventory = (
        context.record.get("inventory")
        if isinstance(context.record.get("inventory"), dict)
        else {}
    )
    expected = {
        "schema_version": "evidence_ingest_v1",
        "record_id": context.record_id,
        "experiment_id": context.experiment_id,
        "run_id": context.run_id,
    }
    mismatched = [
        key for key, value in expected.items() if manifest.get(key) != value
    ]
    if (
        record_storage.get("mode") != "managed"
        or record_storage.get("ownership") != "cockpit_managed"
        or record_storage.get("managed_key") != context.managed_key
        or record_storage.get("uri") != context.target_dir.as_uri()
    ):
        mismatched.append("record_storage")
    if (
        storage.get("mode") != "managed"
        or storage.get("ownership") != "cockpit_managed"
        or storage.get("managed_key") != context.managed_key
        or storage.get("uri") != context.target_dir.as_uri()
    ):
        mismatched.append("manifest_storage")
    if record_inventory.get("complete") is not True or inventory.get("complete") is not True:
        mismatched.append("incomplete_inventory")
    for field_name in ("file_count", "size_bytes"):
        if record_inventory.get(field_name) != inventory.get(field_name):
            mismatched.append(f"inventory.{field_name}")
    level = str(record_integrity.get("level") or "")
    digest = str(record_integrity.get("digest") or "")
    if level not in {"content", "manifest"}:
        mismatched.append("weak_integrity")
    if not mismatched and level == "content":
        digest_value, file_count, size_bytes = _content_digest(
            payload,
            excluded_paths={MANIFEST_NAME},
        )
        expected_digest = f"sha256:{digest_value}"
        if (
            digest != expected_digest
            or integrity.get("digest") != expected_digest
            or integrity.get("level") != "content"
        ):
            mismatched.append("content_digest")
        if (
            inventory.get("file_count") != file_count
            or inventory.get("size_bytes") != size_bytes
        ):
            mismatched.append("content_inventory")
    elif not mismatched and level == "manifest":
        manifest_path = payload / MANIFEST_NAME
        manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        allowed = {f"sha256:{manifest_digest}", f"manifest-sha256:{manifest_digest}"}
        if digest not in allowed:
            mismatched.append("manifest_digest")
    if mismatched:
        raise ValueError(
            "managed payload verification failed: " + ", ".join(sorted(set(mismatched)))
        )
    return {
        "integrity": deepcopy(record_integrity),
        "inventory": deepcopy(record_inventory),
        "manifest": manifest,
    }


def _retention_class(record: dict[str, Any]) -> str:
    retention = record.get("retention") if isinstance(record.get("retention"), dict) else {}
    return str(retention.get("class") or "")


def _availability(record: dict[str, Any]) -> str:
    availability = (
        record.get("availability") if isinstance(record.get("availability"), dict) else {}
    )
    return str(availability.get("status") or "")


def _plan_blockers(context: _GcContext, *, purge_after_seconds: int) -> tuple[list[str], dict[str, Any] | None]:
    storage = context.record.get("storage") if isinstance(context.record.get("storage"), dict) else {}
    integrity = context.record.get("integrity") if isinstance(context.record.get("integrity"), dict) else {}
    inventory = context.record.get("inventory") if isinstance(context.record.get("inventory"), dict) else {}
    blockers: list[str] = []
    if storage.get("mode") != "managed" or storage.get("ownership") != "cockpit_managed":
        blockers.append("not_cockpit_managed")
    if str(integrity.get("level") or "") not in {"content", "manifest"}:
        blockers.append("weak_integrity")
    if inventory.get("complete") is not True:
        blockers.append("incomplete_inventory")
    retention = _retention_class(context.record)
    if retention in _MUST_KEEP_RETENTION_CLASSES:
        blockers.append("must_keep_retention")
    elif retention not in _GC_RETENTION_CLASSES:
        blockers.append("retention_not_gc_eligible")
    blockers.extend(
        active_record_blockers(
            context.root,
            experiment_id=context.experiment_id,
            run_id=context.run_id,
        )
    )
    availability = _availability(context.record)
    payload = context.target_dir if context.phase == "quarantine" else context.quarantine_dir
    if context.phase == "quarantine":
        if availability != "available":
            blockers.append(f"availability_{availability or 'unknown'}")
    else:
        if availability != "quarantined":
            blockers.append(f"availability_{availability or 'unknown'}")
        current_gc = context.record.get("gc") if isinstance(context.record.get("gc"), dict) else {}
        try:
            purge_not_before = _parse_timestamp(
                current_gc.get("purge_not_before"), field_name="gc.purge_not_before"
            )
            if _utc_now() < purge_not_before:
                blockers.append("purge_delay_not_elapsed")
        except ValueError:
            blockers.append("missing_quarantine_delay")
    if not blockers:
        try:
            verification = _verify_managed_payload(context, payload)
        except (OSError, ValueError) as exc:
            blockers.append("payload_verification_failed")
            verification = {"error": str(exc)}
        return sorted(set(blockers)), verification
    return sorted(set(blockers)), None


def _transition_manifest(
    context: _GcContext,
    *,
    transition: str,
    expected_revision: str,
    verification: dict[str, Any],
    purge_after_seconds: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": GC_SCHEMA_VERSION,
        "gc_id": context.gc_id,
        "operation_id": context.operation_id,
        "phase": context.phase,
        "transition": transition,
        "record_id": context.record_id,
        "experiment_id": context.experiment_id,
        "run_id": context.run_id,
        "managed_key": context.managed_key,
        "quarantine_key": context.quarantine_key,
        "expected_revision": expected_revision,
        "integrity": deepcopy(verification.get("integrity") or {}),
        "inventory": deepcopy(verification.get("inventory") or {}),
        "created_at": _utc_timestamp(),
    }
    if context.phase == "quarantine":
        payload["purge_after_seconds"] = purge_after_seconds
        payload["purge_not_before"] = _utc_timestamp(
            _utc_now() + timedelta(seconds=purge_after_seconds)
        )
    return payload


def _load_transition(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = load_yaml(path)
    if not isinstance(data, dict) or data.get("schema_version") != GC_SCHEMA_VERSION:
        raise ValueError(f"{path}: unsupported artifact GC manifest")
    return data


def _check_transition(context: _GcContext, manifest: dict[str, Any], *, transition: str) -> None:
    expected = {
        "gc_id": context.gc_id,
        "operation_id": context.operation_id,
        "phase": context.phase,
        "transition": transition,
        "record_id": context.record_id,
        "experiment_id": context.experiment_id,
        "run_id": context.run_id,
        "managed_key": context.managed_key,
        "quarantine_key": context.quarantine_key,
    }
    mismatched = [key for key, value in expected.items() if manifest.get(key) != value]
    if mismatched:
        raise ValueError(
            "artifact GC manifest does not match this request: " + ", ".join(mismatched)
        )


def _prepare_transition(
    context: _GcContext,
    *,
    expected_revision: str,
    verification: dict[str, Any],
    purge_after_seconds: int,
) -> dict[str, Any]:
    existing = _load_transition(context.prepared_path)
    if existing is not None:
        _check_transition(context, existing, transition="prepared")
        return existing
    if not isinstance(expected_revision, str) or not expected_revision.strip():
        raise ValueError("expected_revision is required for an artifact GC execution")
    with mutation_lock(context.root):
        ensure_interaction_log_valid(context.root)
        existing = _load_transition(context.prepared_path)
        if existing is not None:
            _check_transition(context, existing, transition="prepared")
            return existing
        current_revision = root_truth_revision(context.root)
        if current_revision != expected_revision:
            raise ValueError(
                "artifact GC plan is stale; rerun maintenance audit/plan before execution"
            )
        prepared = _transition_manifest(
            context,
            transition="prepared",
            expected_revision=expected_revision,
            verification=verification,
            purge_after_seconds=purge_after_seconds,
        )
        save_yaml(context.prepared_path, prepared)
        return prepared


def _record_after_quarantine(
    context: _GcContext, prepared: dict[str, Any]
) -> dict[str, Any]:
    updated = deepcopy(context.raw_record)
    updated["availability"] = {
        "status": "quarantined",
        "last_verified_at": _utc_timestamp(),
    }
    updated["gc"] = {
        "schema_version": GC_SCHEMA_VERSION,
        "operation_id": context.operation_id,
        "gc_id": context.gc_id,
        "quarantine_key": context.quarantine_key,
        "quarantined_at": _utc_timestamp(),
        "purge_not_before": prepared["purge_not_before"],
        "expected_revision": prepared["expected_revision"],
    }
    updated["updated_at"] = _utc_timestamp()
    return updated


def _record_after_purge(context: _GcContext) -> dict[str, Any]:
    updated = deepcopy(context.raw_record)
    current_gc = updated.get("gc") if isinstance(updated.get("gc"), dict) else {}
    current_gc = deepcopy(current_gc)
    current_gc.update(
        {
            "purge_operation_id": context.operation_id,
            "purge_gc_id": context.gc_id,
            "purged_at": _utc_timestamp(),
        }
    )
    updated["gc"] = current_gc
    updated["availability"] = {
        "status": "deleted",
        "last_verified_at": _utc_timestamp(),
    }
    updated["updated_at"] = _utc_timestamp()
    return updated


def _validate_record_update(context: _GcContext, updated_record: dict[str, Any]) -> None:
    candidate_records = [
        normalize_artifact_record(
            updated_record,
            record_id=context.record_id,
            experiment_id=context.experiment_id,
        )
        if row.get("record_id") == context.record_id
        else row
        for row in list_artifact_records(context.root)
    ]
    errors = validate_artifact_records(
        context.root,
        load_nodes(context.root),
        candidate_records,
    )
    if errors:
        raise ValueError("artifact GC record validation failed: " + "; ".join(errors))


def _publish_record_transition(
    context: _GcContext,
    *,
    updated_record: dict[str, Any],
    final_manifest: dict[str, Any],
    event_kind: str,
) -> None:
    _validate_record_update(context, updated_record)
    record_after = deepcopy(context.record_before)
    records = record_after.get("records")
    if not isinstance(records, dict):
        raise ValueError(f"{context.record_path}: records must be a mapping")
    records[context.record_id] = updated_record
    record_backup = _read_bytes(context.record_path)
    final_backup = _read_bytes(context.final_path)
    with mutation_lock(context.root):
        ensure_interaction_log_valid(context.root)
        if _read_bytes(context.record_path) != context.record_before_bytes:
            raise ValueError("artifact record changed after GC planning; rerun the plan")
        existing_final = _load_transition(context.final_path)
        if existing_final is not None:
            _check_transition(
                context,
                existing_final,
                transition="quarantined" if context.phase == "quarantine" else "purged",
            )
            return
        checkpoint = interaction_append_checkpoint(context.root)
        try:
            save_yaml(context.record_path, record_after)
            save_yaml(context.final_path, final_manifest)
            _append_interaction_log_unlocked(
                context.root,
                prevalidated=True,
                kind=event_kind,
                actor="maintainer",
                node_id=context.experiment_id,
                command=(
                    "research-cockpit maintenance compact --file "
                    "<artifact-gc.yaml> --json --compact"
                ),
                after={
                    "record_id": context.record_id,
                    "managed_key": context.managed_key,
                    "quarantine_key": context.quarantine_key,
                },
            )
        except BaseException:
            restore_interaction_append_checkpoint(context.root, checkpoint)
            _restore_bytes(context.record_path, record_backup)
            _restore_bytes(context.final_path, final_backup)
            raise


def _safe_rename(source: Path, target: Path, *, artifact_root: Path) -> None:
    _assert_safe_path(artifact_root, source)
    _assert_safe_path(artifact_root, target)
    if not source.is_dir() or _is_link_like(source):
        raise ValueError(f"managed payload is not a safe directory: {source}")
    if target.exists() or os.path.lexists(target):
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_path(artifact_root, target)
    source.replace(target)


def _assert_safe_tree(path: Path) -> None:
    if _is_link_like(path) or not path.is_dir():
        raise ValueError(f"refusing to purge unsafe payload: {path}")
    pending = [path]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                child = Path(entry.path)
                if entry.is_symlink() or _is_link_like(child):
                    raise ValueError(f"refusing to purge symlink or junction: {child}")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(child)


def _purge_tree(path: Path) -> None:
    _assert_safe_tree(path)
    shutil.rmtree(path)


def _patch_inventory(context: _GcContext, changed_paths: list[Path]) -> dict[str, Any]:
    try:
        from research_cockpit.artifact_inventory import patch_artifact_inventory

        return patch_artifact_inventory(context.root, changed_paths)
    except Exception as exc:
        return {"status": "stale", "updated": False, "error": str(exc)}


def _replay_result(context: _GcContext, final_manifest: dict[str, Any]) -> dict[str, Any]:
    status = "quarantined" if context.phase == "quarantine" else "purged"
    return {
        "ok": True,
        "schema_version": "artifact_gc_result_v1",
        "root": str(context.root),
        "record_id": context.record_id,
        "operation_id": context.operation_id,
        "phase": context.phase,
        "gc_id": context.gc_id,
        "status": "replayed",
        "replayed": True,
        "changed": False,
        "transition": status,
        "manifest_path": str(context.final_path),
        "quarantine_path": str(context.quarantine_dir),
        "managed_key": context.managed_key,
        "expected_revision": final_manifest.get("expected_revision"),
    }


def plan_managed_artifact_gc(
    root: Path,
    *,
    record_id: str,
    operation_id: str,
    phase: str,
    purge_after_seconds: int | None = None,
) -> dict[str, Any]:
    delay = _parse_delay(purge_after_seconds)
    context = _build_context(
        root,
        record_id=record_id,
        operation_id=operation_id,
        phase=phase,
    )
    blockers, verification = _plan_blockers(context, purge_after_seconds=delay)
    final = _load_transition(context.final_path)
    if final is not None:
        _check_transition(
            context,
            final,
            transition="quarantined" if phase == "quarantine" else "purged",
        )
    return {
        "ok": True,
        "schema_version": "artifact_gc_plan_v1",
        "root": str(context.root),
        "dry_run": True,
        "eligible": not blockers,
        "state_revision": root_truth_revision(context.root),
        "record_id": context.record_id,
        "operation_id": context.operation_id,
        "phase": context.phase,
        "gc_id": context.gc_id,
        "managed_key": context.managed_key,
        "quarantine_key": context.quarantine_key,
        "quarantine_path": str(context.quarantine_dir),
        "purge_after_seconds": delay,
        "prepared_manifest_path": str(context.prepared_path),
        "final_manifest_path": str(context.final_path),
        "already_completed": final is not None,
        "blockers": blockers,
        "verification": verification,
        "notes": [
            "Dry-run does not move or delete managed payload bytes.",
            "Purge occurs only after a successful quarantine and the recorded delay.",
        ],
    }


def _execute_quarantine(
    context: _GcContext,
    *,
    expected_revision: str,
    delay: int,
) -> dict[str, Any]:
    final = _load_transition(context.final_path)
    if final is not None and _availability(context.record) == "quarantined":
        _check_transition(context, final, transition="quarantined")
        return _replay_result(context, final)
    prepared = _load_transition(context.prepared_path)
    if prepared is None:
        plan = plan_managed_artifact_gc(
            context.root,
            record_id=context.record_id,
            operation_id=context.operation_id,
            phase="quarantine",
            purge_after_seconds=delay,
        )
        if not plan["eligible"]:
            raise ValueError("artifact GC quarantine is blocked: " + ", ".join(plan["blockers"]))
        prepared = _prepare_transition(
            context,
            expected_revision=expected_revision,
            verification=plan["verification"] or {},
            purge_after_seconds=delay,
        )
    else:
        _check_transition(context, prepared, transition="prepared")
    artifact_root = context.layout.require_managed_artifact_root()
    moved = False
    if context.quarantine_dir.exists() or os.path.lexists(context.quarantine_dir):
        verification = _verify_managed_payload(context, context.quarantine_dir)
    else:
        verification = _verify_managed_payload(context, context.target_dir)
        _safe_rename(context.target_dir, context.quarantine_dir, artifact_root=artifact_root)
        moved = True
    final_manifest = _transition_manifest(
        context,
        transition="quarantined",
        expected_revision=str(prepared["expected_revision"]),
        verification=verification,
        purge_after_seconds=int(prepared["purge_after_seconds"]),
    )
    try:
        _publish_record_transition(
            context,
            updated_record=_record_after_quarantine(context, prepared),
            final_manifest=final_manifest,
            event_kind="artifact_gc_quarantined",
        )
    except BaseException:
        if moved and context.quarantine_dir.exists() and not context.target_dir.exists():
            context.quarantine_dir.replace(context.target_dir)
        raise
    inventory = _patch_inventory(
        context,
        [context.record_path, context.target_dir, context.quarantine_dir],
    )
    return {
        "ok": True,
        "schema_version": "artifact_gc_result_v1",
        "root": str(context.root),
        "record_id": context.record_id,
        "operation_id": context.operation_id,
        "phase": "quarantine",
        "gc_id": context.gc_id,
        "status": "quarantined",
        "replayed": False,
        "changed": True,
        "managed_key": context.managed_key,
        "quarantine_key": context.quarantine_key,
        "quarantine_path": str(context.quarantine_dir),
        "manifest_path": str(context.final_path),
        "inventory_update": inventory,
    }


def _execute_purge(
    context: _GcContext,
    *,
    expected_revision: str,
    delay: int,
) -> dict[str, Any]:
    final = _load_transition(context.final_path)
    if final is not None and _availability(context.record) == "deleted":
        _check_transition(context, final, transition="purged")
        return _replay_result(context, final)
    prepared = _load_transition(context.prepared_path)
    if prepared is None:
        plan = plan_managed_artifact_gc(
            context.root,
            record_id=context.record_id,
            operation_id=context.operation_id,
            phase="purge",
            purge_after_seconds=delay,
        )
        if not plan["eligible"]:
            raise ValueError("artifact GC purge is blocked: " + ", ".join(plan["blockers"]))
        prepared = _prepare_transition(
            context,
            expected_revision=expected_revision,
            verification=plan["verification"] or {},
            purge_after_seconds=delay,
        )
    else:
        _check_transition(context, prepared, transition="prepared")

    if context.quarantine_dir.exists() or os.path.lexists(context.quarantine_dir):
        verification = _verify_managed_payload(context, context.quarantine_dir)
        if _read_bytes(context.record_path) != context.record_before_bytes:
            raise ValueError("artifact record changed after purge preparation; rerun the plan")
        _purge_tree(context.quarantine_dir)
    else:
        verification = {
            "integrity": deepcopy(prepared.get("integrity") or {}),
            "inventory": deepcopy(prepared.get("inventory") or {}),
        }
    final_manifest = _transition_manifest(
        context,
        transition="purged",
        expected_revision=str(prepared["expected_revision"]),
        verification=verification,
        purge_after_seconds=delay,
    )
    _publish_record_transition(
        context,
        updated_record=_record_after_purge(context),
        final_manifest=final_manifest,
        event_kind="artifact_gc_purged",
    )
    inventory = _patch_inventory(
        context,
        [context.record_path, context.quarantine_dir],
    )
    return {
        "ok": True,
        "schema_version": "artifact_gc_result_v1",
        "root": str(context.root),
        "record_id": context.record_id,
        "operation_id": context.operation_id,
        "phase": "purge",
        "gc_id": context.gc_id,
        "status": "purged",
        "replayed": False,
        "changed": True,
        "managed_key": context.managed_key,
        "quarantine_key": context.quarantine_key,
        "quarantine_path": str(context.quarantine_dir),
        "manifest_path": str(context.final_path),
        "inventory_update": inventory,
    }


def execute_managed_artifact_gc(
    root: Path,
    *,
    record_id: str,
    operation_id: str,
    phase: str,
    expected_revision: str | None = None,
    purge_after_seconds: int | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    if not execute:
        return plan_managed_artifact_gc(
            root,
            record_id=record_id,
            operation_id=operation_id,
            phase=phase,
            purge_after_seconds=purge_after_seconds,
        )
    delay = _parse_delay(purge_after_seconds)
    context = _build_context(
        root,
        record_id=record_id,
        operation_id=operation_id,
        phase=phase,
    )
    if phase == "quarantine":
        return _execute_quarantine(
            context,
            expected_revision=str(expected_revision or ""),
            delay=delay,
        )
    return _execute_purge(
        context,
        expected_revision=str(expected_revision or ""),
        delay=delay,
    )
