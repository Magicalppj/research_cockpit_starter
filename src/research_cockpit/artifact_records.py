from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.storage import load_yaml

SCHEMA_VERSION = "artifact_records_v1"


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
    data["records"] = records
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
    return path, before, after, promoted_record


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
        "retention": {"class": "reproducible_output"},
        "promoted_artifact_id": None,
        "created_at": today,
        "updated_at": today,
    }
    if agent_id:
        record["agent"] = agent_id
    return record


def upsert_artifact_record(root: Path, experiment_id: str, record: dict[str, Any]) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    path = record_file_path(root, experiment_id)
    before = load_artifact_record_file(root, experiment_id)
    after = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "records": dict(before.get("records", {})),
    }
    record_id = str(record.get("record_id") or "").strip()
    if not record_id:
        raise ValueError("artifact record_id is required")
    if record_id in after["records"]:
        raise FileExistsError(path)
    after["records"][record_id] = record
    return path, before, after
