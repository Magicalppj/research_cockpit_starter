from __future__ import annotations

import json
from pathlib import Path
from typing import Any


GATE_RESULT_SCHEMA_VERSION = "gate_result_v1"
PREFLIGHT_NUMERIC_FIELDS = {
    "disk_available_gb",
    "estimated_required_gb",
    "cache_available_gb",
    "estimated_cache_required_gb",
}
PREFLIGHT_INTEGER_FIELDS = {"port"}
PREFLIGHT_BOOLEAN_FIELDS = {"port_available", "cache_dir_exists", "cache_writable"}
PREFLIGHT_TEXT_FIELDS = {"cache_dir", "port_host"}
PREFLIGHT_LIST_FIELDS = {"gpu_ids", "conflicting_processes"}
PREFLIGHT_FIELDS = (
    PREFLIGHT_NUMERIC_FIELDS
    | PREFLIGHT_INTEGER_FIELDS
    | PREFLIGHT_BOOLEAN_FIELDS
    | PREFLIGHT_TEXT_FIELDS
    | PREFLIGHT_LIST_FIELDS
)


def _optional_text(data: dict[str, Any], field: str, warnings: list[str]) -> str | None:
    value = data.get(field)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        warnings.append(f"{field} must be a string")
        return None
    text = value.strip()
    return text or None


def _optional_mapping(data: dict[str, Any], field: str, warnings: list[str]) -> dict[str, Any]:
    if field not in data or data.get(field) is None:
        return {}
    value = data.get(field)
    if not isinstance(value, dict):
        warnings.append(f"{field} must be a JSON object")
        return {}
    return dict(value)


def _optional_warnings(data: dict[str, Any], warnings: list[str]) -> list[str]:
    if "warnings" not in data or data.get("warnings") is None:
        return []
    value = data.get("warnings")
    if not isinstance(value, list):
        warnings.append("warnings must be a list")
        return []
    return [str(item) for item in value if str(item).strip()]


def _optional_preflight(data: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    nested = data.get("preflight")
    if nested not in (None, ""):
        if isinstance(nested, dict):
            raw.update(nested)
        else:
            warnings.append("preflight must be a JSON object")
    for field in PREFLIGHT_FIELDS:
        if field in data:
            raw[field] = data[field]
    if not raw:
        return {}

    normalized: dict[str, Any] = {}
    for field, value in raw.items():
        if field in PREFLIGHT_NUMERIC_FIELDS:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                warnings.append(f"{field} must be a number")
            else:
                normalized[field] = value
        elif field in PREFLIGHT_INTEGER_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int):
                warnings.append(f"{field} must be an integer")
            else:
                normalized[field] = value
        elif field in PREFLIGHT_BOOLEAN_FIELDS:
            if not isinstance(value, bool):
                warnings.append(f"{field} must be a boolean")
            else:
                normalized[field] = value
        elif field in PREFLIGHT_TEXT_FIELDS:
            if not isinstance(value, str):
                warnings.append(f"{field} must be a string")
            elif value.strip():
                normalized[field] = value.strip()
        elif field in PREFLIGHT_LIST_FIELDS:
            if not isinstance(value, list):
                warnings.append(f"{field} must be a list")
            else:
                normalized[field] = value
        else:
            normalized[field] = value
    return normalized


def _resolve_gate_result_path(root: Path, gate_result_file: str) -> tuple[Path | None, list[str]]:
    raw = str(gate_result_file or "").strip()
    if not raw:
        return None, ["gate_result_file is empty"]
    normalized = raw.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or path.drive or ".." in path.parts:
        return None, [f"gate_result_file must be a relative path inside the data root: {gate_result_file}"]
    candidate = root / path
    try:
        root_resolved = root.resolve()
        candidate_resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        return None, [f"gate_result_file could not be resolved inside the data root: {gate_result_file}: {exc}"]
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError:
        return None, [f"gate_result_file must resolve inside the data root: {gate_result_file}"]
    return candidate, []


def normalize_gate_result(
    data: Any,
    *,
    path: str = "",
    experiment_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    schema_warnings: list[str] = []
    if not isinstance(data, dict):
        return {
            "schema_version": GATE_RESULT_SCHEMA_VERSION,
            "path": path,
            "exists": True,
            "valid": False,
            "blocks_next_action": True,
            "schema_warnings": ["gate result must be a JSON object"],
        }

    warning_count = len(schema_warnings)
    gate_type = _optional_text(data, "gate_type", schema_warnings)
    if not gate_type and len(schema_warnings) == warning_count:
        schema_warnings.append("gate_type is required")

    passed_value = data.get("passed")
    passed: bool | None
    if isinstance(passed_value, bool):
        passed = passed_value
    else:
        passed = None
        schema_warnings.append("passed must be a boolean")

    expected = _optional_mapping(data, "expected", schema_warnings)
    observed = _optional_mapping(data, "observed", schema_warnings)
    fatal_failures = _optional_mapping(data, "fatal_failures", schema_warnings)
    gate_warnings = _optional_warnings(data, schema_warnings)
    next_allowed_action = _optional_text(data, "next_allowed_action", schema_warnings)
    preflight = _optional_preflight(data, schema_warnings)
    file_experiment_id = _optional_text(data, "experiment_id", schema_warnings)
    file_run_id = _optional_text(data, "run_id", schema_warnings)
    if experiment_id and file_experiment_id and experiment_id != file_experiment_id:
        schema_warnings.append("experiment_id does not match gate result file")
    if run_id and file_run_id and run_id != file_run_id:
        schema_warnings.append("run_id does not match gate result file")
    linked_experiment_id = experiment_id or file_experiment_id
    linked_run_id = run_id or file_run_id

    valid = not schema_warnings
    blocks_next_action = not valid or passed is not True or bool(fatal_failures)
    blocked_actions = ["full_run"] if gate_type == "preflight" and blocks_next_action else []
    payload: dict[str, Any] = {
        "schema_version": GATE_RESULT_SCHEMA_VERSION,
        "path": path,
        "exists": True,
        "valid": valid,
        "gate_type": gate_type,
        "passed": passed,
        "expected": expected,
        "observed": observed,
        "fatal_failures": fatal_failures,
        "warnings": gate_warnings,
        "next_allowed_action": next_allowed_action,
        "preflight": preflight,
        "experiment_id": linked_experiment_id,
        "run_id": linked_run_id,
        "blocks_next_action": blocks_next_action,
        "blocked_actions": blocked_actions,
        "schema_warnings": schema_warnings,
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def load_gate_result(
    root: Path,
    gate_result_file: str | None,
    *,
    experiment_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    if not gate_result_file:
        return None
    path, path_warnings = _resolve_gate_result_path(root, gate_result_file)
    if path_warnings:
        return {
            "schema_version": GATE_RESULT_SCHEMA_VERSION,
            "path": gate_result_file,
            "exists": False,
            "valid": False,
            "blocks_next_action": True,
            "schema_warnings": path_warnings,
        }
    assert path is not None
    if not path.exists():
        return {
            "schema_version": GATE_RESULT_SCHEMA_VERSION,
            "path": gate_result_file,
            "exists": False,
            "valid": False,
            "blocks_next_action": True,
            "schema_warnings": [f"gate result file does not exist: {gate_result_file}"],
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {
            "schema_version": GATE_RESULT_SCHEMA_VERSION,
            "path": gate_result_file,
            "exists": False,
            "valid": False,
            "blocks_next_action": True,
            "schema_warnings": [f"gate result file read error: {exc}"],
        }
    except json.JSONDecodeError as exc:
        return {
            "schema_version": GATE_RESULT_SCHEMA_VERSION,
            "path": gate_result_file,
            "exists": True,
            "valid": False,
            "blocks_next_action": True,
            "schema_warnings": [f"gate result file JSON parse error: {exc}"],
        }
    return normalize_gate_result(data, path=gate_result_file, experiment_id=experiment_id, run_id=run_id)
