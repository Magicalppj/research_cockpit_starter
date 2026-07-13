from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import yaml

from research_cockpit.progress import load_progress_heartbeat, progress_heartbeat_signature
from research_cockpit.storage import load_yaml


RUN_STALE_AFTER_HOURS = 24
ACTIVE_RUN_STATUSES = {"queued", "running"}
COMPACT_PROGRESS_FIELDS = (
    "percent_complete",
    "completed_steps",
    "total_steps",
    "last_update",
    "current_stage",
    "latest_artifact",
    "warnings",
    "schema_warnings",
    "possibly_stale",
)


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


def _run_sort_key(summary: dict[str, Any]) -> tuple[datetime, str]:
    timestamp = _parse_time(summary.get("finished_at")) or _parse_time(summary.get("started_at"))
    if timestamp is None:
        timestamp = datetime.min.replace(tzinfo=timezone.utc)
    return (timestamp, str(summary.get("run_id") or ""))


def _load_run_records(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    run_dir = root / "runs"
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not run_dir.exists():
        return records, warnings
    for path in sorted(run_dir.glob("*.yaml")):
        rel_path = f"runs/{path.name}"
        try:
            data = load_yaml(path)
        except (OSError, yaml.YAMLError) as exc:
            warnings.append(f"{rel_path}: YAML parse error: {exc}")
            continue
        if not data:
            continue
        if not isinstance(data, dict):
            warnings.append(f"{rel_path}: run record must be a mapping")
            continue
        if data.get("run_id") in (None, ""):
            warnings.append(f"{rel_path}: missing required field 'run_id'")
            continue
        records.append(dict(data))
    return records, warnings


def _compact_progress(progress: dict[str, Any]) -> dict[str, Any]:
    return {
        field: progress[field]
        for field in COMPACT_PROGRESS_FIELDS
        if progress.get(field) not in (None, "", [])
    }


def _record_summary(
    root: Path,
    record: dict[str, Any],
    nodes: dict[str, Any],
    *,
    now: datetime,
    stale_after_hours: int,
) -> tuple[dict[str, Any] | None, str | None]:
    run_id = str(record.get("run_id") or "")
    experiment_id = str(record.get("experiment_id") or "")
    experiment = nodes.get(experiment_id)
    if not experiment or getattr(experiment, "type", None) != "experiment":
        return None, f"{run_id}: experiment_id references missing or non-experiment node {experiment_id!r}"

    started_at = record.get("started_at")
    status = str(record.get("status") or "")
    started = _parse_time(started_at)
    active_run = status in ACTIVE_RUN_STATUSES and not record.get("finished_at")
    stale_reasons: list[str] = []
    run_started_stale = (
        active_run
        and started is not None
        and now - started > timedelta(hours=stale_after_hours)
    )
    if run_started_stale:
        stale_reasons.append("run_started_at")
    progress = load_progress_heartbeat(root, record.get("progress_file"), now=now)
    if progress and not active_run and progress.get("possibly_stale"):
        progress = dict(progress)
        progress["possibly_stale"] = False
    if active_run and progress and progress.get("possibly_stale"):
        stale_reasons.append("progress_heartbeat")
    summary: dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "experiment_id": experiment_id,
        "experiment_title": getattr(experiment, "title", None),
        "started_at": started_at,
        "finished_at": record.get("finished_at"),
        "launcher": record.get("launcher"),
        "tmux_session": record.get("tmux_session"),
        "pid": record.get("pid"),
        "progress_file": record.get("progress_file"),
        "log_root": record.get("log_root"),
        "output_root": record.get("output_root"),
        "progress": _compact_progress(progress) if isinstance(progress, dict) else progress,
        "possibly_stale": bool(stale_reasons),
        "stale_reasons": stale_reasons,
    }
    return {key: value for key, value in summary.items() if value not in (None, "", [])}, None


def build_run_summaries(
    root: Path,
    nodes: dict[str, Any],
    *,
    now: datetime | None = None,
    stale_after_hours: int = RUN_STALE_AFTER_HOURS,
    records: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if records is None:
        records, warnings = _load_run_records(root)
    else:
        records = [dict(record) for record in records]
        warnings = []
    summaries: list[dict[str, Any]] = []
    for record in records:
        summary, warning = _record_summary(root, record, nodes, now=now, stale_after_hours=stale_after_hours)
        if warning:
            warnings.append(warning)
        if summary:
            summaries.append(summary)
    return summaries, warnings


def run_staleness_signature(
    root: Path,
    *,
    now: datetime | None = None,
    stale_after_hours: int = RUN_STALE_AFTER_HOURS,
) -> tuple[tuple[str, str, bool], ...]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    records, _warnings = _load_run_records(root)
    items: list[tuple[str, str, bool]] = []
    threshold = timedelta(hours=stale_after_hours)
    for record in records:
        run_id = str(record.get("run_id") or "")
        status = str(record.get("status") or "")
        started = _parse_time(record.get("started_at"))
        if not run_id or status not in ACTIVE_RUN_STATUSES or started is None or record.get("finished_at"):
            continue
        items.append((run_id, status, now - started > threshold))
    return tuple(sorted(items))


def run_progress_signature(
    root: Path,
    *,
    now: datetime | None = None,
) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    records, _warnings = _load_run_records(root)
    items: list[tuple[str, tuple[Any, ...]]] = []
    for record in records:
        run_id = str(record.get("run_id") or "")
        status = str(record.get("status") or "")
        active_run = status in ACTIVE_RUN_STATUSES and not record.get("finished_at")
        signature = progress_heartbeat_signature(root, record.get("progress_file"), now=now)
        if run_id and signature:
            if len(signature) == 4 and isinstance(signature[-1], bool):
                signature = (*signature[:-1], bool(active_run and signature[-1]))
            items.append((run_id, signature))
    return tuple(sorted(items))


def compact_run_summary(summaries: list[dict[str, Any]], *, limit: int = 5) -> dict[str, Any]:
    sorted_runs = sorted(summaries, key=_run_sort_key, reverse=True)
    active = [item for item in sorted_runs if item.get("status") in ACTIVE_RUN_STATUSES]
    failed = [item for item in sorted_runs if item.get("status") == "failed"]
    completed = [item for item in sorted_runs if item.get("status") == "completed"]
    cancelled = [item for item in sorted_runs if item.get("status") == "cancelled"]
    stale = [item for item in active if item.get("possibly_stale")]
    recent = sorted_runs[:limit]
    active_progress = [
        {"run_id": str(item["run_id"]), **_compact_progress(item["progress"])}
        for item in active[:limit]
        if isinstance(item.get("progress"), dict)
    ]
    summary = {
        "total_count": len(summaries),
        "active_count": len(active),
        "running_count": len([item for item in active if item.get("status") == "running"]),
        "queued_count": len([item for item in active if item.get("status") == "queued"]),
        "completed_count": len(completed),
        "failed_count": len(failed),
        "cancelled_count": len(cancelled),
        "possibly_stale_count": len(stale),
        "active_run_ids": [str(item["run_id"]) for item in active[:limit]],
        "recent_run_ids": [str(item["run_id"]) for item in recent],
    }
    if active_progress:
        summary["active_progress"] = active_progress
    return summary


def build_experiment_run_context(
    root: Path,
    nodes: dict[str, Any],
    experiment_id: str,
    *,
    limit: int = 5,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    summaries, warnings = build_run_summaries(root, nodes, records=records)
    experiment_runs = [item for item in summaries if item.get("experiment_id") == experiment_id]
    sorted_runs = sorted(experiment_runs, key=_run_sort_key, reverse=True)
    current = [item for item in sorted_runs if item.get("status") in ACTIVE_RUN_STATUSES]
    return {
        "summary": compact_run_summary(experiment_runs, limit=limit),
        "current": current[:limit],
        "recent": sorted_runs[:limit],
        "warnings": warnings,
    }


def build_run_summaries_by_experiment(
    root: Path,
    nodes: dict[str, Any],
    experiment_ids: list[str],
    *,
    limit: int = 5,
) -> dict[str, dict[str, Any]]:
    summaries, _warnings = build_run_summaries(root, nodes)
    by_experiment: dict[str, list[dict[str, Any]]] = {experiment_id: [] for experiment_id in experiment_ids}
    for summary in summaries:
        experiment_id = str(summary.get("experiment_id") or "")
        if experiment_id in by_experiment:
            by_experiment[experiment_id].append(summary)
    return {
        experiment_id: compact_run_summary(items, limit=limit)
        for experiment_id, items in by_experiment.items()
    }


def build_run_overview(root: Path, nodes: dict[str, Any], *, limit: int = 5) -> dict[str, Any]:
    summaries, warnings = build_run_summaries(root, nodes)
    sorted_runs = sorted(summaries, key=_run_sort_key, reverse=True)
    queued = [item for item in sorted_runs if item.get("status") == "queued"]
    running = [item for item in sorted_runs if item.get("status") == "running"]
    failed = [item for item in sorted_runs if item.get("status") == "failed"]
    completed = [item for item in sorted_runs if item.get("status") == "completed"]
    cancelled = [item for item in sorted_runs if item.get("status") == "cancelled"]
    stale = [item for item in sorted_runs if item.get("status") in ACTIVE_RUN_STATUSES and item.get("possibly_stale")]
    return {
        **compact_run_summary(summaries, limit=limit),
        "stale_after_hours": RUN_STALE_AFTER_HOURS,
        "queued": queued[:limit],
        "running": running[:limit],
        "failed": failed[:limit],
        "recently_completed": completed[:limit],
        "cancelled": cancelled[:limit],
        "possibly_stale": stale[:limit],
        "warnings": warnings,
    }
