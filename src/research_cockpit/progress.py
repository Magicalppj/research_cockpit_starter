from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any


PROGRESS_SCHEMA_VERSION = "progress_heartbeat_v1"
PROGRESS_STALE_AFTER_MINUTES = 60
PROGRESS_STATUSES = {"queued", "running", "completed", "failed", "cancelled"}


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


def _optional_int(data: dict[str, Any], field: str, warnings: list[str]) -> int | None:
    value = data.get(field)
    if value in (None, ""):
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        warnings.append(f"{field} must be an integer")
        return None
    if number < 0:
        warnings.append(f"{field} must be non-negative")
        return None
    return number


def _optional_text(data: dict[str, Any], field: str, warnings: list[str]) -> str | None:
    value = data.get(field)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        warnings.append(f"{field} must be a string")
        return None
    text = value.strip()
    return text or None


def _resolve_progress_path(root: Path, progress_file: str) -> tuple[Path | None, list[str]]:
    raw = str(progress_file or "").strip()
    if not raw:
        return None, ["progress_file is empty"]
    normalized = raw.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or path.drive or ".." in path.parts:
        return None, [f"progress_file must be a relative path inside the data root: {progress_file}"]
    candidate = root / path
    try:
        root_resolved = root.resolve()
        candidate_resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        return None, [f"progress_file could not be resolved inside the data root: {progress_file}: {exc}"]
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError:
        return None, [f"progress_file must resolve inside the data root: {progress_file}"]
    return candidate, []


def normalize_progress_heartbeat(
    data: Any,
    *,
    path: str = "",
    now: datetime | None = None,
    stale_after_minutes: int = PROGRESS_STALE_AFTER_MINUTES,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    schema_warnings: list[str] = []
    if not isinstance(data, dict):
        return {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "path": path,
            "exists": True,
            "schema_warnings": ["progress heartbeat must be a JSON object"],
        }

    status = str(data.get("status") or "").strip()
    if status and status not in PROGRESS_STATUSES:
        schema_warnings.append(f"status must be one of {', '.join(sorted(PROGRESS_STATUSES))}")

    completed_steps = _optional_int(data, "completed_steps", schema_warnings)
    total_steps = _optional_int(data, "total_steps", schema_warnings)
    if total_steps == 0:
        schema_warnings.append("total_steps must be positive when provided")
        total_steps = None
    if completed_steps is not None and total_steps is not None and completed_steps > total_steps:
        schema_warnings.append("completed_steps must not exceed total_steps")

    last_update = str(data.get("last_update") or "").strip()
    parsed_update = _parse_time(last_update)
    if last_update and parsed_update is None:
        schema_warnings.append("last_update must be an ISO-8601 timestamp")
    if status in {"queued", "running"} and not last_update:
        schema_warnings.append("last_update is required for active progress heartbeats")

    warnings_value = data.get("warnings", [])
    if warnings_value in (None, ""):
        heartbeat_warnings: list[str] = []
    elif isinstance(warnings_value, list):
        heartbeat_warnings = [str(item) for item in warnings_value if str(item).strip()]
    else:
        heartbeat_warnings = []
        schema_warnings.append("warnings must be a list")

    heartbeat: dict[str, Any] = {
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "path": path,
        "exists": True,
        "status": status or None,
        "completed_steps": completed_steps,
        "total_steps": total_steps,
        "last_update": last_update or None,
        "current_stage": _optional_text(data, "current_stage", schema_warnings),
        "latest_artifact": _optional_text(data, "latest_artifact", schema_warnings),
        "warnings": heartbeat_warnings,
        "possibly_stale": (
            parsed_update is not None
            and now - parsed_update > timedelta(minutes=stale_after_minutes)
            and status in {"queued", "running"}
        ),
        "stale_after_minutes": stale_after_minutes,
        "schema_warnings": schema_warnings,
    }
    if (
        completed_steps is not None
        and total_steps is not None
        and completed_steps <= total_steps
    ):
        heartbeat["percent_complete"] = round((completed_steps / total_steps) * 100, 2)
    return {key: value for key, value in heartbeat.items() if value not in (None, "", [])}


def load_progress_heartbeat(
    root: Path,
    progress_file: str | None,
    *,
    now: datetime | None = None,
    stale_after_minutes: int = PROGRESS_STALE_AFTER_MINUTES,
) -> dict[str, Any] | None:
    if not progress_file:
        return None
    path, path_warnings = _resolve_progress_path(root, progress_file)
    if path_warnings:
        return {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "path": progress_file,
            "exists": False,
            "schema_warnings": path_warnings,
        }
    assert path is not None
    if not path.exists():
        return {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "path": progress_file,
            "exists": False,
            "schema_warnings": [f"progress file does not exist: {progress_file}"],
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "path": progress_file,
            "exists": False,
            "schema_warnings": [f"progress file read error: {exc}"],
        }
    except json.JSONDecodeError as exc:
        return {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "path": progress_file,
            "exists": True,
            "schema_warnings": [f"progress file JSON parse error: {exc}"],
        }
    return normalize_progress_heartbeat(
        data,
        path=progress_file,
        now=now,
        stale_after_minutes=stale_after_minutes,
    )
