from __future__ import annotations

import argparse
from pathlib import Path

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.commands._runs import run_payload
from research_cockpit.model import (
    VALID_RUN_STATUSES,
    ValidationError,
    load_explicit_edges,
    load_nodes,
    load_runs,
    load_yaml,
    validate_cockpit,
)


def list_runs_payload(
    root: Path,
    *,
    experiment_id: str | None = None,
    status: str | None = None,
    compact: bool = False,
) -> dict:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    runs = load_runs(root)
    validate_cockpit(root, nodes, current, explicit_edges, runs=runs, raise_on_error=True)

    selected = [
        run
        for run in sorted(runs.values(), key=lambda item: item.run_id)
        if (experiment_id is None or run.experiment_id == experiment_id) and (status is None or run.status == status)
    ]
    filters = {"experiment_id": experiment_id, "status": status}
    if compact:
        return {
            "root": str(root),
            "count": len(selected),
            "runs": [run.run_id for run in selected],
            "filters": filters,
        }
    return {
        "root": str(root),
        "count": len(selected),
        "runs": [run_payload(run, nodes) for run in selected],
        "filters": filters,
    }


def _print_human(payload: dict) -> None:
    for run in payload.get("runs", []):
        experiment = run.get("experiment", {})
        safe_print(f"{run['run_id']} [{run['status']}] experiment={run['experiment_id']} {experiment.get('title', '')}")
    if not payload.get("runs"):
        safe_print("No runs found.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--experiment", "--experiment-id", dest="experiment_id")
    parser.add_argument("--status", choices=sorted(VALID_RUN_STATUSES))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    try:
        payload = list_runs_payload(
            args.root,
            experiment_id=args.experiment_id,
            status=args.status,
            compact=args.compact and args.json,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(payload)
        return
    _print_human(payload)


if __name__ == "__main__":
    main()
