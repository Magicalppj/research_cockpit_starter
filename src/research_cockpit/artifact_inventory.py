from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
from typing import Any

from research_cockpit.artifact_records import (
    iter_artifact_record_files,
    load_artifact_record_file,
)
from research_cockpit.evidence_staging import MANIFEST_NAME
from research_cockpit.mutation_lock import mutation_lock
from research_cockpit.storage import load_yaml, save_text
from research_cockpit.storage_layout import StorageLayout, resolve_storage_layout


SCHEMA_VERSION = "artifact_inventory_v1"
DEFAULT_MAX_MANAGED_ENTRIES = 10_000
MAX_MANIFEST_BYTES = 1024 * 1024
_RESERVED_MANAGED_DIRECTORIES = {".staging", ".quarantine"}


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def artifact_inventory_path(root: Path) -> Path:
    return root / "dashboards" / "artifact_inventory.json"


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError:
        return path.as_posix()


def _metadata_signature(path: Path) -> dict[str, int]:
    stat_result = path.stat()
    return {"size": int(stat_result.st_size), "mtime_ns": int(stat_result.st_mtime_ns)}


def _source_signatures(root: Path) -> dict[str, dict[str, int]]:
    paths: list[Path] = []
    storage_profile = root / "storage.yaml"
    if storage_profile.is_file():
        paths.append(storage_profile)
    paths.extend(iter_artifact_record_files(root))
    graph_nodes = root / "graph" / "nodes"
    if graph_nodes.exists():
        paths.extend(sorted(path for path in graph_nodes.glob("*.yaml") if path.is_file()))
    return {
        _relative_path(root, path): _metadata_signature(path)
        for path in paths
    }


def _is_metadata_source(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    suffix = Path(rel_path).suffix.lower()
    return (
        rel_path == "storage.yaml"
        or (
            len(parts) >= 2
            and parts[-2] == "artifact_records"
            and suffix in {".yaml", ".yml"}
        )
        or (
            len(parts) >= 3
            and parts[-3:-1] == ("graph", "nodes")
            and suffix in {".yaml", ".yml"}
        )
    )


def _refresh_source_signature(index: dict[str, Any], root: Path, rel_path: str) -> None:
    if not _is_metadata_source(rel_path):
        return
    signatures = index.setdefault("source_signatures", {})
    if not isinstance(signatures, dict):
        signatures = {}
        index["source_signatures"] = signatures
    path = root / rel_path
    if path.is_file():
        signatures[rel_path] = _metadata_signature(path)
    else:
        signatures.pop(rel_path, None)


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _inventory_metadata(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "size_bytes": _non_negative_int(raw.get("size_bytes")),
        "file_count": _non_negative_int(raw.get("file_count")),
        "complete": raw.get("complete") is True,
    }


def _record_row(record: dict[str, Any]) -> dict[str, Any]:
    storage = record.get("storage") if isinstance(record.get("storage"), dict) else {}
    retention = record.get("retention") if isinstance(record.get("retention"), dict) else {}
    integrity = record.get("integrity") if isinstance(record.get("integrity"), dict) else {}
    availability = record.get("availability") if isinstance(record.get("availability"), dict) else {}
    return {
        "record_id": str(record.get("record_id") or ""),
        "experiment_id": str(record.get("experiment_id") or ""),
        "run_id": str(record.get("run_id") or ""),
        "source_file": str(record.get("source_file") or ""),
        "storage": {
            "mode": str(storage.get("mode") or ""),
            "ownership": str(storage.get("ownership") or ""),
            "managed_key": (
                str(storage.get("managed_key") or "") or None
            ),
        },
        "inventory": _inventory_metadata(record.get("inventory")),
        "retention_class": str(retention.get("class") or "") or None,
        "integrity_level": str(integrity.get("level") or "") or None,
        "availability_status": str(availability.get("status") or "") or None,
    }


def _record_rows(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    rows: dict[str, dict[str, Any]] = {}
    files: dict[str, list[str]] = {}
    for path in iter_artifact_record_files(root):
        rel_path = _relative_path(root, path)
        data = load_artifact_record_file(root, path.stem)
        record_ids: list[str] = []
        for record_id, record in sorted((data.get("records") or {}).items()):
            if not isinstance(record, dict):
                continue
            row = _record_row({**record, "source_file": rel_path})
            normalized_id = str(record_id)
            row["record_id"] = normalized_id
            rows[normalized_id] = row
            record_ids.append(normalized_id)
        files[rel_path] = sorted(record_ids)
    return rows, files


def _graph_artifact_row(data: dict[str, Any], *, source_file: str) -> dict[str, Any] | None:
    if str(data.get("type") or "") != "artifact":
        return None
    node_id = str(data.get("id") or "").strip()
    if not node_id:
        return None
    retention = data.get("retention") if isinstance(data.get("retention"), dict) else {}
    return {
        "artifact_id": node_id,
        "title": str(data.get("title") or node_id),
        "status": str(data.get("status") or ""),
        "source_file": source_file,
        "path": data.get("path"),
        "stable_path": data.get("stable_path"),
        "retention_class": str(retention.get("class") or "") or None,
    }


def _graph_artifact_rows(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    rows: dict[str, dict[str, Any]] = {}
    files: dict[str, str] = {}
    directory = root / "graph" / "nodes"
    if not directory.exists():
        return rows, files
    for path in sorted(directory.glob("*.yaml")):
        data = load_yaml(path)
        if not isinstance(data, dict):
            continue
        rel_path = _relative_path(root, path)
        row = _graph_artifact_row(data, source_file=rel_path)
        if row is None:
            continue
        artifact_id = str(row["artifact_id"])
        rows[artifact_id] = row
        files[rel_path] = artifact_id
    return rows, files


def _is_link_like(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_symlink()
    except OSError:
        return True


def _read_manifest(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        metadata = path.lstat()
    except OSError:
        return None, "missing"
    if stat.S_ISLNK(metadata.st_mode):
        return None, "unsafe_link"
    if not stat.S_ISREG(metadata.st_mode):
        return None, "invalid_type"
    if metadata.st_size > MAX_MANIFEST_BYTES:
        return None, "too_large"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "unreadable"
    if not isinstance(data, dict):
        return None, "invalid"
    return data, "valid"


def _managed_payload_row(target: Path, managed_key: str) -> dict[str, Any]:
    manifest_path = target / MANIFEST_NAME
    manifest, manifest_status = _read_manifest(manifest_path)
    row: dict[str, Any] = {
        "managed_key": managed_key,
        "record_id": None,
        "manifest_status": manifest_status,
        "inventory": {
            "size_bytes": None,
            "file_count": None,
            "complete": False,
        },
        "integrity_level": None,
    }
    if manifest is None:
        return row
    storage = manifest.get("storage") if isinstance(manifest.get("storage"), dict) else {}
    inventory = _inventory_metadata(manifest.get("inventory"))
    integrity = manifest.get("integrity") if isinstance(manifest.get("integrity"), dict) else {}
    record_id = str(manifest.get("record_id") or "").strip()
    if (
        storage.get("mode") != "managed"
        or storage.get("ownership") != "cockpit_managed"
        or storage.get("managed_key") != managed_key
    ):
        manifest_status = "mismatch"
    row.update(
        {
            "record_id": record_id or None,
            "manifest_status": manifest_status,
            "inventory": inventory,
            "integrity_level": str(integrity.get("level") or "") or None,
        }
    )
    return row


def _empty_managed_scan(*, configured: bool, exists: bool) -> dict[str, Any]:
    return {
        "configured": configured,
        "exists": exists,
        "complete": True,
        "truncated": False,
        "entries_scanned": 0,
        "unsafe_entry_count": 0,
        "reserved_directory_count": 0,
    }


def _scan_managed_store(
    managed_root: Path | None,
    *,
    max_entries: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if managed_root is None:
        return {}, _empty_managed_scan(configured=False, exists=False)
    if managed_root.is_symlink() or not managed_root.exists() or not managed_root.is_dir():
        scan = _empty_managed_scan(configured=True, exists=managed_root.exists())
        if managed_root.exists():
            scan["unsafe_entry_count"] = 1
        return {}, scan

    payloads: dict[str, dict[str, Any]] = {}
    scan = _empty_managed_scan(configured=True, exists=True)
    queue: deque[tuple[Path, tuple[str, ...]]] = deque([(managed_root, ())])
    while queue and not scan["truncated"]:
        directory, parts = queue.popleft()
        try:
            with os.scandir(directory) as scanned:
                entries = sorted(scanned, key=lambda entry: entry.name)
        except OSError:
            scan["unsafe_entry_count"] += 1
            continue
        for entry in entries:
            if entry.name in _RESERVED_MANAGED_DIRECTORIES and not parts:
                scan["reserved_directory_count"] += 1
                continue
            if scan["entries_scanned"] >= max_entries:
                scan["complete"] = False
                scan["truncated"] = True
                break
            scan["entries_scanned"] += 1
            if _is_link_like(entry):
                scan["unsafe_entry_count"] += 1
                continue
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError:
                scan["unsafe_entry_count"] += 1
                continue
            if not is_directory:
                continue
            child_parts = (*parts, entry.name)
            child = Path(entry.path)
            if len(child_parts) == 3:
                managed_key = "/".join(child_parts)
                payloads[managed_key] = _managed_payload_row(child, managed_key)
            elif len(child_parts) < 3:
                queue.append((child, child_parts))
    return payloads, scan


def _counts(rows: list[dict[str, Any]], field_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value: Any = row
        for segment in field_name.split("."):
            value = value.get(segment) if isinstance(value, dict) else None
        value = str(value or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _inventory_statistics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    size_bytes = 0
    file_count = 0
    lower_bound = False
    unknown_count = 0
    for row in rows:
        inventory = row.get("inventory") if isinstance(row.get("inventory"), dict) else {}
        size = _non_negative_int(inventory.get("size_bytes"))
        files = _non_negative_int(inventory.get("file_count"))
        complete = inventory.get("complete") is True
        if size is not None:
            size_bytes += size
        if files is not None:
            file_count += files
        if size is None or files is None or not complete:
            lower_bound = True
            unknown_count += 1
    return {
        "size_bytes": size_bytes,
        "file_count": file_count,
        "exact": not lower_bound,
        "lower_bound": lower_bound,
        "unknown_or_incomplete_count": unknown_count,
    }


def _managed_orphans(
    records: dict[str, dict[str, Any]],
    payloads: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    known_keys = {
        str(row.get("storage", {}).get("managed_key") or "")
        for row in records.values()
        if isinstance(row.get("storage"), dict)
        and row["storage"].get("mode") == "managed"
    }
    out: dict[str, dict[str, Any]] = {}
    for managed_key, row in payloads.items():
        if managed_key not in known_keys or row.get("manifest_status") != "valid":
            out[managed_key] = dict(row)
    return out


def _aggregates(
    records: dict[str, dict[str, Any]],
    graph_artifacts: dict[str, dict[str, Any]],
    managed_payloads: dict[str, dict[str, Any]],
    managed_orphans: dict[str, dict[str, Any]],
    scan: dict[str, Any],
) -> dict[str, Any]:
    record_rows = list(records.values())
    graph_rows = list(graph_artifacts.values())
    payload_rows = list(managed_payloads.values())
    orphan_rows = list(managed_orphans.values())
    payload_statistics = _inventory_statistics(payload_rows)
    if scan.get("truncated"):
        payload_statistics["exact"] = False
        payload_statistics["lower_bound"] = True
    return {
        "records": {
            "count": len(record_rows),
            "by_storage_mode": _counts(record_rows, "storage.mode"),
            "by_ownership": _counts(record_rows, "storage.ownership"),
            "by_retention_class": _counts(record_rows, "retention_class"),
            "by_integrity_level": _counts(record_rows, "integrity_level"),
            "by_availability_status": _counts(record_rows, "availability_status"),
            "statistics": _inventory_statistics(record_rows),
        },
        "graph_artifacts": {
            "count": len(graph_rows),
            "by_retention_class": _counts(graph_rows, "retention_class"),
        },
        "managed_payloads": {
            "count": len(payload_rows),
            "count_exact": not bool(scan.get("truncated")),
            "count_lower_bound": bool(scan.get("truncated")),
            "statistics": payload_statistics,
        },
        "managed_orphans": {
            "count": len(orphan_rows),
            "count_exact": not bool(scan.get("truncated")),
            "count_lower_bound": bool(scan.get("truncated")),
            "statistics": _inventory_statistics(orphan_rows),
        },
    }


def _refresh_derived(index: dict[str, Any]) -> None:
    records = index.get("records") if isinstance(index.get("records"), dict) else {}
    graph_artifacts = (
        index.get("graph_artifacts")
        if isinstance(index.get("graph_artifacts"), dict)
        else {}
    )
    managed_payloads = (
        index.get("managed_payloads")
        if isinstance(index.get("managed_payloads"), dict)
        else {}
    )
    scan = index.get("scan") if isinstance(index.get("scan"), dict) else {}
    managed_scan = (
        scan.get("managed_store") if isinstance(scan.get("managed_store"), dict) else {}
    )
    index["record_files"] = {
        rel_path: sorted(
            record_id
            for record_id, row in records.items()
            if isinstance(row, dict) and row.get("source_file") == rel_path
        )
        for rel_path in sorted(
            {
                str(row.get("source_file") or "")
                for row in records.values()
                if isinstance(row, dict) and row.get("source_file")
            }
        )
    }
    index["graph_artifact_files"] = {
        str(row["source_file"]): str(artifact_id)
        for artifact_id, row in graph_artifacts.items()
        if isinstance(row, dict) and row.get("source_file")
    }
    orphans = _managed_orphans(records, managed_payloads)
    index["managed_orphans"] = orphans
    index["aggregates"] = _aggregates(
        records,
        graph_artifacts,
        managed_payloads,
        orphans,
        managed_scan,
    )


def build_artifact_inventory(
    root: Path,
    *,
    max_managed_entries: int = DEFAULT_MAX_MANAGED_ENTRIES,
) -> dict[str, Any]:
    if max_managed_entries <= 0:
        raise ValueError("max_managed_entries must be positive")
    root = root.resolve()
    layout = resolve_storage_layout(root)
    records, record_files = _record_rows(root)
    graph_artifacts, graph_artifact_files = _graph_artifact_rows(root)
    managed_payloads, managed_scan = _scan_managed_store(
        layout.managed_artifact_root,
        max_entries=max_managed_entries,
    )
    index: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_timestamp(),
        "root": str(root),
        "source_signatures": _source_signatures(root),
        "managed_artifact_root": (
            str(layout.managed_artifact_root)
            if layout.managed_artifact_root is not None
            else None
        ),
        "records": records,
        "record_files": record_files,
        "graph_artifacts": graph_artifacts,
        "graph_artifact_files": graph_artifact_files,
        "managed_payloads": managed_payloads,
        "scan": {"managed_store": managed_scan},
    }
    _refresh_derived(index)
    return index


def load_artifact_inventory(root: Path) -> dict[str, Any] | None:
    path = artifact_inventory_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _is_inventory_structure_compatible(
    root: Path,
    inventory: dict[str, Any] | None,
) -> bool:
    if not isinstance(inventory, dict) or inventory.get("schema_version") != SCHEMA_VERSION:
        return False
    if inventory.get("stale"):
        return False
    if inventory.get("root") != str(root.resolve()):
        return False
    layout = resolve_storage_layout(root)
    expected_managed_root = (
        str(layout.managed_artifact_root)
        if layout.managed_artifact_root is not None
        else None
    )
    return (
        inventory.get("managed_artifact_root") == expected_managed_root
        and isinstance(inventory.get("records"), dict)
        and isinstance(inventory.get("graph_artifacts"), dict)
        and isinstance(inventory.get("managed_payloads"), dict)
        and isinstance(inventory.get("aggregates"), dict)
    )


def is_inventory_compatible(root: Path, inventory: dict[str, Any] | None) -> bool:
    return bool(
        _is_inventory_structure_compatible(root, inventory)
        and inventory is not None
        and inventory.get("source_signatures") == _source_signatures(root)
    )


def _save_inventory(root: Path, inventory: dict[str, Any]) -> None:
    save_text(
        artifact_inventory_path(root),
        json.dumps(inventory, indent=2, ensure_ascii=False),
    )


def ensure_artifact_inventory(
    root: Path,
    *,
    max_managed_entries: int = DEFAULT_MAX_MANAGED_ENTRIES,
) -> dict[str, Any]:
    with mutation_lock(root, lock_name=".artifact-inventory.lock"):
        current = load_artifact_inventory(root)
        if is_inventory_compatible(root, current):
            return {"status": "current", "updated": False, "inventory": current}
        rebuilt = build_artifact_inventory(root, max_managed_entries=max_managed_entries)
        _save_inventory(root, rebuilt)
        return {"status": "rebuilt", "updated": True, "inventory": rebuilt}


def _refresh_record_file(index: dict[str, Any], root: Path, rel_path: str) -> None:
    records = index.setdefault("records", {})
    for record_id, row in list(records.items()):
        if isinstance(row, dict) and row.get("source_file") == rel_path:
            records.pop(record_id, None)
    path = root / rel_path
    if not path.exists():
        return
    data = load_artifact_record_file(root, path.stem)
    for record_id, record in sorted((data.get("records") or {}).items()):
        if not isinstance(record, dict):
            continue
        row = _record_row({**record, "source_file": rel_path})
        row["record_id"] = str(record_id)
        records[str(record_id)] = row


def _refresh_graph_artifact_file(index: dict[str, Any], root: Path, rel_path: str) -> None:
    rows = index.setdefault("graph_artifacts", {})
    invalidated_ids: set[str] = set()
    for artifact_id, row in list(rows.items()):
        if isinstance(row, dict) and row.get("source_file") == rel_path:
            rows.pop(artifact_id, None)
            invalidated_ids.add(str(artifact_id))
    path = root / rel_path
    if not path.exists():
        _invalidate_retention_target_scans(index, invalidated_ids)
        return
    data = load_yaml(path)
    if not isinstance(data, dict):
        _invalidate_retention_target_scans(index, invalidated_ids)
        return
    row = _graph_artifact_row(data, source_file=rel_path)
    if row is not None:
        artifact_id = str(row["artifact_id"])
        rows[artifact_id] = row
        invalidated_ids.add(artifact_id)
    _invalidate_retention_target_scans(index, invalidated_ids)


def _managed_key_for_path(layout: StorageLayout, path: Path) -> str | None:
    managed_root = layout.managed_artifact_root
    if managed_root is None:
        return None
    try:
        relative = path.resolve(strict=False).relative_to(managed_root.resolve(strict=False))
    except ValueError:
        return None
    if len(relative.parts) < 3:
        return None
    return "/".join(relative.parts[:3])


def _refresh_managed_target(index: dict[str, Any], layout: StorageLayout, path: Path) -> bool:
    managed_key = _managed_key_for_path(layout, path)
    if managed_key is None or layout.managed_artifact_root is None:
        return False
    target = layout.managed_artifact_root.joinpath(*managed_key.split("/"))
    payloads = index.setdefault("managed_payloads", {})
    if not target.exists():
        payloads.pop(managed_key, None)
    else:
        payloads[managed_key] = _managed_payload_row(target, managed_key)
    return True


def _changed_relative_path(root: Path, path: Path) -> str:
    return _relative_path(root, path)


def _patch_artifact_inventory_unlocked(root: Path, changed_paths: list[Path]) -> dict[str, Any]:
    inventory = load_artifact_inventory(root)
    if inventory is None:
        return {"status": "missing", "updated": False}
    if not _is_inventory_structure_compatible(root, inventory):
        return {"status": "unavailable", "updated": False}

    layout = resolve_storage_layout(root)
    changed_files = sorted({_changed_relative_path(root, Path(path)) for path in changed_paths})
    managed_changed = False
    for changed_path, rel_path in zip(changed_paths, [_changed_relative_path(root, Path(path)) for path in changed_paths]):
        if rel_path == "storage.yaml":
            inventory["stale"] = {
                "reason": "storage_layout_changed",
                "detail": "storage.yaml changed",
                "marked_at": _utc_timestamp(),
            }
            _save_inventory(root, inventory)
            return {"status": "stale", "updated": False, "changed_files": changed_files}
        _refresh_source_signature(inventory, root, rel_path)
        parts = Path(rel_path).parts
        suffix = Path(rel_path).suffix.lower()
        if len(parts) >= 2 and parts[-2] == "artifact_records" and suffix in {".yaml", ".yml"}:
            _refresh_record_file(inventory, root, rel_path)
        elif len(parts) >= 3 and parts[-3:-1] == ("graph", "nodes") and suffix in {".yaml", ".yml"}:
            _refresh_graph_artifact_file(inventory, root, rel_path)
        managed_changed = _refresh_managed_target(inventory, layout, Path(changed_path)) or managed_changed

    if managed_changed:
        scan = inventory.setdefault("scan", {}).setdefault("managed_store", {})
        scan["incremental_updates"] = int(scan.get("incremental_updates") or 0) + 1
    _refresh_derived(inventory)
    inventory["generated_at"] = _utc_timestamp()
    inventory.pop("stale", None)
    _save_inventory(root, inventory)
    return {"status": "updated", "updated": True, "changed_files": changed_files}


def patch_artifact_inventory(root: Path, changed_paths: list[Path]) -> dict[str, Any]:
    with mutation_lock(root, lock_name=".artifact-inventory.lock"):
        return _patch_artifact_inventory_unlocked(root, changed_paths)


def mark_artifact_inventory_stale(root: Path, *, reason: str, detail: str = "") -> None:
    with mutation_lock(root, lock_name=".artifact-inventory.lock"):
        inventory = load_artifact_inventory(root)
        if inventory is None:
            return
        inventory["stale"] = {
            "reason": reason,
            "detail": detail,
            "marked_at": _utc_timestamp(),
        }
        _save_inventory(root, inventory)


def _retention_target_key(artifact_id: str, label: str, target: str) -> str:
    return json.dumps([artifact_id, label, target], ensure_ascii=False, separators=(",", ":"))


def _repo_key(repo: Path) -> str:
    return str(repo.resolve())


def _local_path_signature(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"exists": None}
    try:
        stat = path.lstat()
    except OSError:
        return {"exists": False}
    if path.is_symlink():
        kind = "symlink"
    elif path.is_file():
        kind = "file"
    elif path.is_dir():
        kind = "directory"
    else:
        kind = "other"
    return {
        "exists": True,
        "kind": kind,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _retention_target_context(
    inventory: dict[str, Any],
    *,
    repo: Path,
) -> dict[str, Any] | None:
    contexts = inventory.get("retention_target_scans")
    if not isinstance(contexts, dict):
        return None
    context = contexts.get(_repo_key(repo))
    return context if isinstance(context, dict) else None


def cached_retention_target_scan(
    inventory: dict[str, Any],
    *,
    repo: Path,
    max_files: int,
    artifact_id: str,
    label: str,
    target: str,
    resolved_path: Path,
) -> dict[str, Any] | None:
    context = _retention_target_context(inventory, repo=repo)
    if context is None or context.get("max_files") != max_files:
        return None
    targets = context.get("targets")
    if not isinstance(targets, dict):
        return None
    row = targets.get(_retention_target_key(artifact_id, label, target))
    if not isinstance(row, dict):
        return None
    if row.get("resolved_path") != str(resolved_path):
        return None
    if row.get("path_signature") != _local_path_signature(resolved_path):
        return None
    scan = row.get("scan")
    return dict(scan) if isinstance(scan, dict) else None


def _invalidate_retention_target_scans(
    inventory: dict[str, Any],
    artifact_ids: set[str],
) -> None:
    if not artifact_ids:
        return
    contexts = inventory.get("retention_target_scans")
    if not isinstance(contexts, dict):
        return
    for context in contexts.values():
        if not isinstance(context, dict):
            continue
        targets = context.get("targets")
        if not isinstance(targets, dict):
            continue
        for key, row in list(targets.items()):
            if isinstance(row, dict) and str(row.get("artifact_id") or "") in artifact_ids:
                targets.pop(key, None)


def patch_retention_target_scans(
    root: Path,
    *,
    repo: Path,
    max_files: int,
    scans: list[dict[str, Any]],
) -> dict[str, Any]:
    with mutation_lock(root, lock_name=".artifact-inventory.lock"):
        inventory = load_artifact_inventory(root)
        if not is_inventory_compatible(root, inventory):
            return {"status": "unavailable", "updated": False}
        assert inventory is not None
        contexts = inventory.setdefault("retention_target_scans", {})
        key = _repo_key(repo)
        context = contexts.get(key)
        if not isinstance(context, dict) or context.get("max_files") != max_files:
            context = {"max_files": max_files, "targets": {}}
            contexts[key] = context
        targets = context.setdefault("targets", {})
        if not isinstance(targets, dict):
            targets = {}
            context["targets"] = targets
        for item in scans:
            artifact_id = str(item.get("artifact_id") or "")
            label = str(item.get("label") or "")
            target = str(item.get("target") or "")
            resolved_path = item.get("resolved_path")
            scan = item.get("scan")
            if not artifact_id or not label or not target or not isinstance(scan, dict):
                continue
            resolved = Path(str(resolved_path)) if resolved_path else None
            targets[_retention_target_key(artifact_id, label, target)] = {
                "artifact_id": artifact_id,
                "label": label,
                "target": target,
                "resolved_path": str(resolved) if resolved is not None else None,
                "path_signature": _local_path_signature(resolved),
                "scan": dict(scan),
            }
        inventory["generated_at"] = _utc_timestamp()
        _save_inventory(root, inventory)
        return {"status": "updated", "updated": True, "scan_count": len(scans)}
