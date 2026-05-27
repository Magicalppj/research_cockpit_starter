from __future__ import annotations

from pathlib import Path
from typing import Any

from research_cockpit.model import ResearchNode, RunRecord


RUN_OPTIONAL_FIELDS = (
    "started_at",
    "finished_at",
    "launcher",
    "command",
    "tmux_session",
    "pid",
    "log_root",
    "output_root",
    "monitor_command",
    "stop_command",
    "progress_file",
    "config_file",
)

RUN_FIELDS = ("run_id", "status", "experiment_id", *RUN_OPTIONAL_FIELDS)


def normalize_run_id(run_id: str) -> str:
    text = str(run_id or "").strip()
    if not text:
        raise ValueError("run_id is required")
    if "/" in text or "\\" in text or text in {".", ".."}:
        raise ValueError(f"run_id must be a file-safe id, got {run_id!r}")
    return text


def run_path(root: Path, run_id: str) -> Path:
    return root / "runs" / f"{normalize_run_id(run_id)}.yaml"


def build_run_data(
    *,
    run_id: str,
    status: str,
    experiment_id: str,
    **fields: Any,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "run_id": normalize_run_id(run_id),
        "status": status,
        "experiment_id": str(experiment_id or "").strip(),
    }
    for field in RUN_OPTIONAL_FIELDS:
        value = fields.get(field)
        if value is not None:
            data[field] = value
    return data


def update_fields(**fields: Any) -> dict[str, Any]:
    return {field: value for field, value in fields.items() if value is not None}


def experiment_summary(node: ResearchNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "title": node.title,
        "type": node.type,
        "status": node.status,
        "parent": node.parent,
    }


def run_payload(run: RunRecord, nodes: dict[str, ResearchNode] | None = None) -> dict[str, Any]:
    data = {field: getattr(run, field) for field in RUN_FIELDS if getattr(run, field) is not None}
    if nodes and run.experiment_id in nodes:
        data["experiment"] = experiment_summary(nodes[run.experiment_id])
    return data


def compact_run_payload(run: RunRecord, nodes: dict[str, ResearchNode] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run.run_id,
        "status": run.status,
        "experiment_id": run.experiment_id,
        "monitor_command": run.monitor_command,
        "stop_command": run.stop_command,
        "progress_file": run.progress_file,
        "log_root": run.log_root,
        "output_root": run.output_root,
        "tmux_session": run.tmux_session,
        "pid": run.pid,
    }
    if nodes and run.experiment_id in nodes:
        payload["experiment_title"] = nodes[run.experiment_id].title
    return payload
