from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.commands._runs import experiment_summary
from research_cockpit.model import (
    ValidationError,
    load_explicit_edges,
    load_nodes,
    load_runs,
    load_yaml,
    validate_cockpit,
)
from research_cockpit.run_summaries import ACTIVE_RUN_STATUSES


RESOURCE_RUN_FIELDS = (
    "run_id",
    "status",
    "experiment_id",
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


def _resource_run_payload(run: Any, nodes: dict[str, Any]) -> dict[str, Any]:
    payload = {field: getattr(run, field) for field in RESOURCE_RUN_FIELDS if getattr(run, field) is not None}
    resources = run.raw.get("resources")
    if resources is not None:
        payload["resources"] = resources
    if run.experiment_id in nodes:
        payload["experiment"] = experiment_summary(nodes[run.experiment_id])
    return payload


def active_resources_payload(root: Path, *, include_terminal: bool = False) -> dict[str, Any]:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    runs = load_runs(root)
    validate_cockpit(root, nodes, current, explicit_edges, runs=runs, raise_on_error=True)

    sorted_runs = sorted(runs.values(), key=lambda item: item.run_id)
    active_runs = [run for run in sorted_runs if run.status in ACTIVE_RUN_STATUSES]
    selected_runs = sorted_runs if include_terminal else active_runs
    return {
        "ok": True,
        "schema_version": "active_resources_v1",
        "root": str(root),
        "filters": {
            "include_terminal": include_terminal,
            "active_statuses": sorted(ACTIVE_RUN_STATUSES),
        },
        "active_count": len(active_runs),
        "selected_count": len(selected_runs),
        "runs": [_resource_run_payload(run, nodes) for run in selected_runs],
        "warnings": [],
    }


def _resources_label(resources: Any) -> str:
    if isinstance(resources, Mapping):
        keys = ",".join(sorted(str(key) for key in resources.keys()))
        return keys or "empty"
    return type(resources).__name__


def _print_human(payload: dict[str, Any]) -> None:
    for run in payload.get("runs", []):
        bits = [run["run_id"], f"[{run['status']}]", f"experiment={run['experiment_id']}"]
        if run.get("tmux_session"):
            bits.append(f"tmux={run['tmux_session']}")
        if run.get("pid"):
            bits.append(f"pid={run['pid']}")
        if "resources" in run:
            bits.append(f"resources={_resources_label(run['resources'])}")
        safe_print(" ".join(bits))
    if not payload.get("runs"):
        safe_print("No active resources found.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--include-terminal", action="store_true", help="Include completed, failed, or cancelled runs.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        payload = active_resources_payload(args.root, include_terminal=args.include_terminal)
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(payload)
        return
    _print_human(payload)


if __name__ == "__main__":
    main()
