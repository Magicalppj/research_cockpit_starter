from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from research_cockpit.gate_results import load_gate_result
from research_cockpit.storage import load_yaml


GATE_RESULT_RECORD_SCHEMA_VERSION = "gate_result_record_v1"
COMPACT_GATE_FIELDS = (
    "gate_id",
    "gate_type",
    "passed",
    "blocks_next_action",
    "next_allowed_action",
    "fatal_failures",
    "warnings",
    "schema_warnings",
    "experiment_id",
    "run_id",
    "artifact_id",
    "gate_result_file",
    "recorded_at",
)


def normalize_gate_id(gate_id: str) -> str:
    text = str(gate_id or "").strip()
    if not text:
        raise ValueError("gate_id is required")
    if "/" in text or "\\" in text or text in {".", ".."}:
        raise ValueError(f"gate_id must be a file-safe id, got {gate_id!r}")
    return text


def gate_record_path(root: Path, gate_id: str) -> Path:
    return root / "gate_results" / f"{normalize_gate_id(gate_id)}.yaml"


def default_gate_result_file(gate_id: str) -> str:
    return f"gate_results/{normalize_gate_id(gate_id)}.json"


def validate_gate_result_relative_path(root: Path, gate_result_file: str) -> Path:
    raw = str(gate_result_file or "").strip()
    if not raw:
        raise ValueError("gate_result_file is required")
    normalized = raw.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or path.drive or ".." in path.parts:
        raise ValueError(f"gate_result_file must be a relative path inside the data root: {gate_result_file}")
    if path.suffix.lower() != ".json":
        raise ValueError("gate_result_file must use a .json suffix")
    parts = path.parts
    if not parts or parts[0] not in {"gate_results", "artifacts"}:
        raise ValueError("gate_result_file must live under gate_results/ or artifacts/")
    candidate = root / path
    try:
        root_resolved = root.resolve()
        candidate_resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"gate_result_file could not be resolved inside the data root: {gate_result_file}: {exc}") from exc
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"gate_result_file must resolve inside the data root: {gate_result_file}") from exc
    return candidate


def validate_attached_gate_artifact(
    nodes: dict[str, Any],
    *,
    experiment_id: str,
    artifact_id: str,
    gate_result_file: str,
) -> None:
    artifact = nodes.get(artifact_id)
    if artifact is None:
        raise ValueError(f"Artifact does not exist: {artifact_id}")
    if getattr(artifact, "type", None) != "artifact":
        raise ValueError(f"--artifact must reference an artifact node, got {getattr(artifact, 'type', None)}")
    experiment = nodes.get(experiment_id)
    linked = []
    if experiment is not None:
        linked = [str(item) for item in (getattr(experiment, "raw", {}) or {}).get("linked_artifacts", []) or []]
    if artifact_id not in linked:
        raise ValueError(f"Artifact {artifact_id} is not linked to experiment {experiment_id}")

    normalized_gate = str(gate_result_file).replace("\\", "/").strip("/")
    raw = getattr(artifact, "raw", {}) or {}
    links = raw.get("links") if isinstance(raw.get("links"), dict) else {}
    if str(links.get("gate_result") or "").replace("\\", "/").strip("/") == normalized_gate:
        return
    artifact_path = str(raw.get("path") or "").replace("\\", "/").strip("/")
    if artifact_path and (
        normalized_gate == artifact_path
        or normalized_gate.startswith(f"{artifact_path}/")
    ):
        return
    raise ValueError(f"Artifact {artifact_id} does not reference gate result file {gate_result_file}")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_gate_record_data(
    *,
    gate_id: str,
    experiment_id: str,
    gate_result_file: str,
    run_id: str | None = None,
    artifact_id: str | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": GATE_RESULT_RECORD_SCHEMA_VERSION,
        "gate_id": normalize_gate_id(gate_id),
        "experiment_id": str(experiment_id or "").strip(),
        "gate_result_file": str(gate_result_file or "").strip(),
        "recorded_at": recorded_at or utc_timestamp(),
    }
    if not data["experiment_id"]:
        raise ValueError("experiment_id is required")
    if not data["gate_result_file"]:
        raise ValueError("gate_result_file is required")
    if run_id:
        data["run_id"] = str(run_id).strip()
    if artifact_id:
        data["artifact_id"] = str(artifact_id).strip()
    return data


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_gate_records(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    record_dir = root / "gate_results"
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not record_dir.exists():
        return records, warnings
    for path in sorted(record_dir.glob("*.yaml")):
        rel_path = f"gate_results/{path.name}"
        try:
            data = load_yaml(path)
        except (OSError, yaml.YAMLError) as exc:
            warnings.append(f"{rel_path}: YAML parse error: {exc}")
            continue
        if not data:
            continue
        if not isinstance(data, dict):
            warnings.append(f"{rel_path}: gate result record must be a mapping")
            continue
        gate_id = str(data.get("gate_id") or path.stem).strip()
        if not gate_id:
            warnings.append(f"{rel_path}: missing required field 'gate_id'")
            continue
        record = dict(data)
        record["gate_id"] = gate_id
        records.append(record)
    return records, warnings


def _gate_sort_key(summary: dict[str, Any]) -> tuple[datetime, str]:
    timestamp = _parse_time(summary.get("recorded_at"))
    if timestamp is None:
        timestamp = datetime.min.replace(tzinfo=timezone.utc)
    return (timestamp, str(summary.get("gate_id") or ""))


def _compact_gate(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        field: summary[field]
        for field in COMPACT_GATE_FIELDS
        if summary.get(field) not in (None, "", [], {})
    }


def _invalid_gate_summary(record: dict[str, Any], gate_file: str, warning: str) -> dict[str, Any]:
    return _compact_gate(
        {
            "gate_id": record["gate_id"],
            "experiment_id": record.get("experiment_id"),
            "run_id": record.get("run_id"),
            "artifact_id": record.get("artifact_id"),
            "gate_result_file": gate_file,
            "recorded_at": record.get("recorded_at"),
            "blocks_next_action": True,
            "schema_warnings": [warning],
        }
    )


def build_gate_summaries(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records, warnings = _load_gate_records(root)
    summaries: list[dict[str, Any]] = []
    for record in records:
        gate_file = str(record.get("gate_result_file") or "").strip()
        if not gate_file:
            warning = "missing gate_result_file"
            warnings.append(f"{record['gate_id']}: {warning}")
            summaries.append(_invalid_gate_summary(record, gate_file, warning))
            continue
        try:
            validate_gate_result_relative_path(root, gate_file)
        except ValueError as exc:
            warning = f"{record['gate_id']}: invalid gate_result_file: {exc}"
            warnings.append(warning)
            summaries.append(_invalid_gate_summary(record, gate_file, str(exc)))
            continue
        gate = load_gate_result(
            root,
            gate_file,
            experiment_id=record.get("experiment_id"),
            run_id=record.get("run_id"),
        )
        if gate is None:
            warnings.append(f"{record['gate_id']}: missing gate result payload")
            continue
        summary = {
            **gate,
            "gate_id": record["gate_id"],
            "experiment_id": record.get("experiment_id") or gate.get("experiment_id"),
            "run_id": record.get("run_id") or gate.get("run_id"),
            "artifact_id": record.get("artifact_id"),
            "gate_result_file": gate_file,
            "recorded_at": record.get("recorded_at"),
        }
        summaries.append(_compact_gate(summary))
    return summaries, warnings


def compact_gate_summary(summaries: list[dict[str, Any]], *, limit: int = 5) -> dict[str, Any]:
    sorted_gates = sorted(summaries, key=_gate_sort_key, reverse=True)
    blocking = [item for item in sorted_gates if item.get("blocks_next_action")]
    failed = [item for item in sorted_gates if item.get("passed") is False]
    warning_items = [
        item
        for item in sorted_gates
        if item.get("warnings") or item.get("schema_warnings")
    ]
    summary: dict[str, Any] = {
        "total_count": len(summaries),
        "blocking_count": len(blocking),
        "failed_count": len(failed),
        "warning_count": len(warning_items),
        "recent_gate_ids": [str(item["gate_id"]) for item in sorted_gates[:limit]],
        "blocking_gate_ids": [str(item["gate_id"]) for item in blocking[:limit]],
    }
    if sorted_gates:
        latest = sorted_gates[0]
        summary.update({
            "latest_gate_id": latest.get("gate_id"),
            "latest_gate_type": latest.get("gate_type"),
            "latest_passed": latest.get("passed"),
            "latest_blocks_next_action": latest.get("blocks_next_action"),
            "latest_next_allowed_action": latest.get("next_allowed_action"),
        })
    return summary


def build_gate_context(
    root: Path,
    *,
    experiment_id: str | None = None,
    run_id: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    summaries, warnings = build_gate_summaries(root)
    filtered = summaries
    if experiment_id:
        filtered = [item for item in filtered if item.get("experiment_id") == experiment_id]
    if run_id:
        filtered = [item for item in filtered if item.get("run_id") == run_id]
    sorted_gates = sorted(filtered, key=_gate_sort_key, reverse=True)
    scoped_gate_ids = {str(item.get("gate_id")) for item in filtered if item.get("gate_id")}
    scoped_warnings = warnings
    if experiment_id or run_id:
        scoped_warnings = [
            warning
            for warning in warnings
            if any(str(warning).startswith(f"{gate_id}:") for gate_id in scoped_gate_ids)
        ]
    return {
        "summary": compact_gate_summary(filtered, limit=limit),
        "latest": sorted_gates[0] if sorted_gates else None,
        "blocking": [item for item in sorted_gates if item.get("blocks_next_action")][:limit],
        "recent": sorted_gates[:limit],
        "warnings": scoped_warnings,
    }


def build_experiment_gate_context(root: Path, experiment_id: str, *, limit: int = 5) -> dict[str, Any]:
    return build_gate_context(root, experiment_id=experiment_id, limit=limit)


def build_run_gate_context(root: Path, run_id: str, *, limit: int = 5) -> dict[str, Any]:
    return build_gate_context(root, run_id=run_id, limit=limit)


def build_gate_overview(root: Path, *, limit: int = 5) -> dict[str, Any]:
    return build_gate_context(root, limit=limit)["summary"]


def build_gate_summaries_by_experiment(
    root: Path,
    experiment_ids: list[str],
    *,
    limit: int = 5,
) -> dict[str, dict[str, Any]]:
    summaries, _warnings = build_gate_summaries(root)
    by_experiment: dict[str, list[dict[str, Any]]] = {experiment_id: [] for experiment_id in experiment_ids}
    for summary in summaries:
        experiment_id = str(summary.get("experiment_id") or "")
        if experiment_id in by_experiment:
            by_experiment[experiment_id].append(summary)
    return {
        experiment_id: compact_gate_summary(items, limit=limit)
        for experiment_id, items in by_experiment.items()
    }


def gate_result_signature(root: Path) -> tuple[tuple[str, str, int | str, int], ...]:
    records, _warnings = _load_gate_records(root)
    items: list[tuple[str, str, int | str, int]] = []
    for record in records:
        gate_id = str(record.get("gate_id") or "")
        gate_file = str(record.get("gate_result_file") or "")
        if not gate_id or not gate_file:
            continue
        try:
            candidate = validate_gate_result_relative_path(root, gate_file)
        except ValueError:
            items.append((gate_id, gate_file, "invalid", 0))
            continue
        if not candidate.exists():
            items.append((gate_id, gate_file, "missing", 0))
            continue
        stat = candidate.stat()
        items.append((gate_id, gate_file, stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(items))
