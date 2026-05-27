from __future__ import annotations

import argparse
from pathlib import Path

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.commands._runs import compact_run_payload, experiment_summary, run_path, run_payload
from research_cockpit.model import (
    ValidationError,
    load_explicit_edges,
    load_nodes,
    load_runs,
    load_yaml,
    validate_cockpit,
)
from research_cockpit.gate_result_records import build_run_gate_context
from research_cockpit.progress import load_progress_heartbeat


def run_context_payload(root: Path, *, run_id: str, compact: bool = False) -> dict:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    runs = load_runs(root)
    validate_cockpit(root, nodes, current, explicit_edges, runs=runs, raise_on_error=True)

    normalized_id = run_path(root, run_id).stem
    if normalized_id not in runs:
        raise FileNotFoundError(run_path(root, normalized_id))
    run = runs[normalized_id]
    gate_results = build_run_gate_context(root, normalized_id)
    if compact:
        payload = compact_run_payload(run, nodes)
        progress = load_progress_heartbeat(root, run.progress_file)
        if progress:
            payload["progress"] = progress
        if gate_results["summary"]["total_count"]:
            payload["gate_results"] = gate_results
        return payload

    experiment = nodes[run.experiment_id]
    progress = load_progress_heartbeat(root, run.progress_file)
    monitor = {
        "monitor_command": run.monitor_command,
        "progress_file": run.progress_file,
        "log_root": run.log_root,
        "output_root": run.output_root,
    }
    if progress:
        monitor["progress"] = progress
    return {
        "root": str(root),
        "run": run_payload(run, nodes),
        "experiment": experiment_summary(experiment),
        "monitor": monitor,
        "control": {
            "stop_command": run.stop_command,
            "tmux_session": run.tmux_session,
            "pid": run.pid,
        },
        "gate_results": gate_results,
    }


def _print_human(payload: dict) -> None:
    run = payload["run"]
    safe_print(f"Run: {run['run_id']} [{run['status']}] experiment={run['experiment_id']}")
    monitor = payload.get("monitor", {})
    control = payload.get("control", {})
    if monitor.get("monitor_command"):
        safe_print(f"Monitor: {monitor['monitor_command']}")
    progress = monitor.get("progress") or payload.get("progress")
    if progress:
        schema_warnings = progress.get("schema_warnings") or []
        if schema_warnings:
            detail = f"unavailable ({schema_warnings[0]})"
        else:
            detail = progress.get("current_stage") or progress.get("status") or "progress"
        if "percent_complete" in progress:
            detail = f"{detail} ({progress['percent_complete']}%)"
        safe_print(f"Progress: {detail}")
    if control.get("stop_command"):
        safe_print(f"Stop: {control['stop_command']}")
    gate_results = payload.get("gate_results") or {}
    gate_summary = gate_results.get("summary") or {}
    if gate_summary.get("total_count"):
        latest = gate_results.get("latest") or {}
        state = "blocked" if latest.get("blocks_next_action") else "passed"
        safe_print(f"Gate: {latest.get('gate_type') or latest.get('gate_id')} ({state})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="run_id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    try:
        payload = run_context_payload(args.root, run_id=args.run_id, compact=args.compact and args.json)
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(payload)
        return
    _print_human(payload)


if __name__ == "__main__":
    main()
