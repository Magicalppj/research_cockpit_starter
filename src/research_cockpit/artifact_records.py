from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from research_cockpit.storage import load_yaml

SCHEMA_VERSION = "artifact_records_v1"
STORAGE_MODES = {"reference", "managed", "legacy"}
INTEGRITY_LEVELS = {"content", "manifest", "inventory", "unverified"}
AVAILABILITY_STATUSES = {
    "available",
    "missing",
    "unknown",
    "quarantined",
    "deleted",
}


def _is_legacy_stable_path(value: str) -> bool:
    text = value.strip().replace("\\", "/")
    if not text:
        return False
    portable = PurePosixPath(text)
    windows = PureWindowsPath(value)
    return bool(
        not portable.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and ".." not in portable.parts
        and portable.parts
        and portable.parts[0] == "artifacts"
    )

def normalize_artifact_record(
    record: dict[str, Any],
    *,
    record_id: str | None = None,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("artifact record must be a mapping")
    normalized = deepcopy(record)
    if record_id:
        normalized.setdefault("record_id", record_id)
    if experiment_id:
        normalized.setdefault("experiment_id", experiment_id)

    stable_path = str(normalized.get("stable_path") or "").strip()
    storage = normalized.get("storage")
    if storage is None:
        legacy = _is_legacy_stable_path(stable_path)
        storage = {
            "mode": "legacy" if legacy else "reference",
            "ownership": "cockpit_managed" if legacy else "external",
            "uri": stable_path or None,
            "managed_key": None,
        }
    elif not isinstance(storage, dict):
        raise ValueError("artifact record storage must be a mapping")
    else:
        storage = deepcopy(storage)
    mode = str(storage.get("mode") or "").strip()
    if mode not in STORAGE_MODES:
        raise ValueError(
            f"artifact record storage.mode must be one of {sorted(STORAGE_MODES)}"
        )
    storage.setdefault(
        "ownership",
        "cockpit_managed" if mode != "reference" else "external",
    )
    storage.setdefault("uri", stable_path or None)
    storage.setdefault("managed_key", None)
    normalized["storage"] = storage

    integrity = normalized.get("integrity")
    if integrity is None:
        raw_digest = str(normalized.get("content_sha256") or "").strip()
        integrity = {
            "level": "content" if raw_digest else "unverified",
            "algorithm": "sha256" if raw_digest else None,
            "digest": f"sha256:{raw_digest}" if raw_digest else None,
        }
    elif not isinstance(integrity, dict):
        raise ValueError("artifact record integrity must be a mapping")
    else:
        integrity = deepcopy(integrity)
    level = str(integrity.get("level") or "unverified")
    if level not in INTEGRITY_LEVELS:
        raise ValueError(
            f"artifact record integrity.level must be one of {sorted(INTEGRITY_LEVELS)}"
        )
    integrity.setdefault("algorithm", None)
    integrity.setdefault("digest", None)
    normalized["integrity"] = integrity

    inventory = normalized.get("inventory")
    if inventory is None:
        source_count = normalized.get("source_file_count")
        inventory = {
            "size_bytes": None,
            "file_count": int(source_count) if isinstance(source_count, int) else None,
            "complete": source_count is not None,
        }
    elif not isinstance(inventory, dict):
        raise ValueError("artifact record inventory must be a mapping")
    else:
        inventory = deepcopy(inventory)
    inventory.setdefault("size_bytes", None)
    inventory.setdefault("file_count", None)
    inventory.setdefault("complete", False)
    normalized["inventory"] = inventory

    normalized.setdefault("retention", {"class": "reproducible_output"})
    availability = normalized.get("availability")
    if availability is None:
        availability = {
            "status": (
                "unknown"
                if mode == "legacy" or not storage.get("uri")
                else "available"
            ),
            "last_verified_at": None,
        }
    elif not isinstance(availability, dict):
        raise ValueError("artifact record availability must be a mapping")
    else:
        availability = deepcopy(availability)
    status = str(availability.get("status") or "unknown")
    if status not in AVAILABILITY_STATUSES:
        raise ValueError(
            "artifact record availability.status must be one of "
            f"{sorted(AVAILABILITY_STATUSES)}"
        )
    availability.setdefault("last_verified_at", None)
    normalized["availability"] = availability

    lifecycle = normalized.get("lifecycle")
    if lifecycle is None:
        lifecycle = {"supersedes": [], "superseded_by": None}
    elif not isinstance(lifecycle, dict):
        raise ValueError("artifact record lifecycle must be a mapping")
    else:
        lifecycle = deepcopy(lifecycle)
    lifecycle.setdefault("supersedes", [])
    lifecycle.setdefault("superseded_by", None)
    normalized["lifecycle"] = lifecycle
    return normalized


def record_file_path(root: Path, experiment_id: str) -> Path:
    return root / "artifact_records" / f"{experiment_id}.yaml"


def load_artifact_record_file(root: Path, experiment_id: str) -> dict[str, Any]:
    path = record_file_path(root, experiment_id)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "experiment_id": experiment_id, "records": {}}
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: artifact record file must be a mapping")
    if data.get("schema_version") not in (None, SCHEMA_VERSION):
        raise ValueError(f"{path}: unsupported artifact record schema {data.get('schema_version')!r}")
    records = data.get("records", {})
    if not isinstance(records, dict):
        raise ValueError(f"{path}: records must be a mapping")
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("experiment_id", experiment_id)
    data["records"] = {
        str(current_id): normalize_artifact_record(
            record,
            record_id=str(current_id),
            experiment_id=str(data["experiment_id"]),
        )
        for current_id, record in records.items()
    }
    return data



def iter_artifact_record_files(root: Path, experiment_id: str | None = None) -> list[Path]:
    if experiment_id:
        path = record_file_path(root, experiment_id)
        return [path] if path.exists() else []
    directory = root / "artifact_records"
    if not directory.exists():
        return []
    return sorted(path for path in directory.glob("*.yaml") if path.is_file())


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError:
        return path.as_posix()


def list_artifact_records(
    root: Path,
    *,
    experiment_id: str | None = None,
    record_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in iter_artifact_record_files(root, experiment_id=experiment_id):
        file_experiment_id = path.stem
        data = load_artifact_record_file(root, file_experiment_id)
        data_experiment_id = str(data.get("experiment_id") or file_experiment_id)
        for current_record_id, record in sorted(data.get("records", {}).items()):
            if record_id and current_record_id != record_id:
                continue
            if run_id and record.get("run_id") != run_id:
                continue
            if status and record.get("status") != status:
                continue
            payload = dict(record)
            payload.setdefault("record_id", current_record_id)
            payload.setdefault("experiment_id", data_experiment_id)
            payload["source_file"] = _relative_to_root(root, path)
            records.append(payload)
    return records


def find_artifact_record(root: Path, record_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    matches: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    for path in iter_artifact_record_files(root):
        data = load_artifact_record_file(root, path.stem)
        record = data.get("records", {}).get(record_id)
        if isinstance(record, dict):
            matches.append((path, data, record))
    if not matches:
        raise FileNotFoundError(f"Artifact record does not exist: {record_id}")
    if len(matches) > 1:
        paths = ", ".join(str(path) for path, _, _ in matches)
        raise ValueError(f"Artifact record id {record_id!r} is ambiguous across files: {paths}")
    return matches[0]


def promoted_artifact_record_update(
    root: Path,
    *,
    record_id: str,
    artifact_id: str,
    updated_at: str,
    promotion_reason: str,
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    path, before, record = find_artifact_record(root, record_id)
    raw_before = load_yaml(path)
    if record.get("promoted_artifact_id"):
        raise ValueError(f"Artifact record {record_id!r} is already promoted to {record.get('promoted_artifact_id')!r}")
    after = {
        **before,
        "schema_version": SCHEMA_VERSION,
        "experiment_id": before.get("experiment_id") or record.get("experiment_id") or path.stem,
        "records": dict(before.get("records", {})),
    }
    promoted_record = dict(record)
    promoted_record["promoted_artifact_id"] = artifact_id
    promoted_record["promotion_reason"] = promotion_reason
    promoted_record["updated_at"] = updated_at
    after["records"][record_id] = promoted_record
    return path, raw_before, after, promoted_record


def build_artifact_record(
    *,
    record_id: str,
    experiment_id: str,
    run_id: str,
    artifact_id: str,
    title: str,
    summary: str,
    stable_path: str,
    manifest_path: str,
    source_file_count: int,
    links: dict[str, str],
    agent_id: str | None = None,
    storage: dict[str, Any] | None = None,
    integrity: dict[str, Any] | None = None,
    inventory: dict[str, Any] | None = None,
    retention: dict[str, Any] | None = None,
    availability: dict[str, Any] | None = None,
    lifecycle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    today = str(date.today())
    record: dict[str, Any] = {
        "record_id": record_id,
        "experiment_id": experiment_id,
        "run_id": run_id,
        "artifact_id": artifact_id,
        "title": title,
        "summary": summary,
        "artifact_kind": "run_output",
        "status": "recorded",
        "stable_path": stable_path,
        "manifest_path": manifest_path,
        "source_file_count": source_file_count,
        "links": dict(links),
        "retention": retention or {"class": "reproducible_output"},
        "promoted_artifact_id": None,
        "created_at": today,
        "updated_at": today,
    }
    if agent_id:
        record["agent"] = agent_id
    for field_name, value in (
        ("storage", storage),
        ("integrity", integrity),
        ("inventory", inventory),
        ("availability", availability),
        ("lifecycle", lifecycle),
    ):
        if value is not None:
            record[field_name] = deepcopy(value)
    return normalize_artifact_record(record)


def upsert_artifact_record(root: Path, experiment_id: str, record: dict[str, Any]) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    path = record_file_path(root, experiment_id)
    raw_before = load_yaml(path) if path.exists() else {}
    before = load_artifact_record_file(root, experiment_id)
    after = {
        **before,
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "records": dict(before.get("records", {})),
    }
    record_id = str(record.get("record_id") or "").strip()
    if not record_id:
        raise ValueError("artifact record_id is required")
    if record_id in after["records"]:
        raise FileExistsError(path)
    after["records"][record_id] = normalize_artifact_record(
        record,
        record_id=record_id,
        experiment_id=experiment_id,
    )
    return path, raw_before, after
