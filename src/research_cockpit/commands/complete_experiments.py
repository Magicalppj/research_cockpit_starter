from __future__ import annotations

import argparse
import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._evidence import append_unique, validate_artifact_ids
from research_cockpit.commands._runtime import finish_mutation, load_validated_state, yaml_change_diff
from research_cockpit.commands.complete_experiment import TERMINAL_NON_COMPLETABLE_STATUSES
from research_cockpit.commands.file_schemas import COMPLETE_EXPERIMENTS_EXAMPLE
from research_cockpit.commands.record_finding import _next_finding_id, find_node_file
from research_cockpit.model import (
    ResearchNode,
    VALID_FINDING_CONFIDENCES,
    VALID_FINDING_OUTCOMES,
    ValidationError,
    load_yaml,
    script_command,
    validate_cockpit,
)


def _as_list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise ValueError(f"{field_name} must be a list")


def _as_str_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    raise ValueError(f"{field_name} must be a string or list")


def _text(value: Any, field_name: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    text = str(value).strip()
    if required and not text:
        raise ValueError(f"{field_name} is required")
    return text


def load_completion_plan(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Completion plan file does not exist: {path}")
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError("Completion plan file must contain a mapping")
    return data


def complete_experiments(
    root: Path,
    *,
    plan: dict[str, Any],
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("Completion plan must be a mapping")
    defaults = plan.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("defaults must be a mapping")
    entries = _as_list(plan.get("experiments"), "experiments")
    if not entries:
        raise ValueError("experiments must include at least one item")

    state = load_validated_state(root)
    nodes = state.nodes
    candidate = dict(nodes)
    today = str(date.today())
    seen_experiments: set[str] = set()
    data_by_id: dict[str, dict[str, Any]] = {}
    before_by_id: dict[str, dict[str, Any]] = {}
    path_by_id: dict[str, Path] = {}
    completed: list[dict[str, Any]] = []
    experiment_order: list[str] = []

    default_artifact_ids = _as_str_list(defaults.get("artifact_ids", defaults.get("artifacts")), "defaults.artifact_ids")
    default_metrics = _as_str_list(defaults.get("metrics"), "defaults.metrics")
    default_next_actions = _as_str_list(defaults.get("next_actions"), "defaults.next_actions")

    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"experiments[{index}] must be a mapping")
        owner = f"experiments[{index}]"
        experiment_id = _text(entry.get("id"), f"{owner}.id", required=True)
        assert experiment_id is not None
        if experiment_id in seen_experiments:
            raise ValueError(f"Duplicate experiment id in completion plan: {experiment_id}")
        seen_experiments.add(experiment_id)
        experiment_order.append(experiment_id)
        if experiment_id not in nodes:
            raise ValueError(f"Experiment node does not exist: {experiment_id}")
        experiment = nodes[experiment_id]
        if experiment.type != "experiment":
            raise ValueError(f"Node {experiment_id} must be experiment, got {experiment.type}")
        if experiment.status in TERMINAL_NON_COMPLETABLE_STATUSES:
            raise ValueError(f"Cannot complete experiment {experiment_id} from status {experiment.status!r}")

        confidence = _text(entry.get("confidence", defaults.get("confidence")), f"{owner}.confidence", required=True)
        assert confidence is not None
        if confidence not in VALID_FINDING_CONFIDENCES:
            allowed = ", ".join(sorted(VALID_FINDING_CONFIDENCES))
            raise ValueError(f"Invalid confidence {confidence!r}; allowed: {allowed}")
        outcome = _text(entry.get("outcome", defaults.get("outcome")), f"{owner}.outcome")
        if outcome is not None and outcome not in VALID_FINDING_OUTCOMES:
            allowed = ", ".join(sorted(VALID_FINDING_OUTCOMES))
            raise ValueError(f"Invalid outcome {outcome!r}; allowed: {allowed}")
        statement = _text(entry.get("finding"), f"{owner}.finding", required=True)
        assert statement is not None

        artifact_ids = [
            *default_artifact_ids,
            *_as_str_list(entry.get("artifact_ids", entry.get("artifacts")), f"{owner}.artifact_ids"),
        ]
        metrics = [*default_metrics, *_as_str_list(entry.get("metrics"), f"{owner}.metrics")]
        next_actions = [
            *default_next_actions,
            *_as_str_list(entry.get("next_actions"), f"{owner}.next_actions"),
        ]
        validate_artifact_ids(nodes, artifact_ids)

        path = find_node_file(root, experiment_id)
        data = load_yaml(path)
        before = copy.deepcopy(data)
        findings = data.get("findings", []) or []
        if not isinstance(findings, list):
            raise ValueError(f"{experiment_id}: findings must be a list")
        finding_record = {
            "id": _next_finding_id(experiment_id, findings),
            "statement": statement,
            "confidence": confidence,
            "evidence": [experiment_id],
            "outcome": outcome,
            "metrics": metrics,
            "linked_artifacts": artifact_ids,
            "created_at": today,
        }
        findings.append(finding_record)
        data["findings"] = findings
        data["status"] = "done"
        if "result_summary" in entry:
            data["result_summary"] = "" if entry.get("result_summary") is None else str(entry.get("result_summary"))
        data["next_actions"], added_actions = append_unique(data.get("next_actions"), next_actions, "next_actions")
        if not data["next_actions"]:
            data.pop("next_actions", None)
        data["updated_at"] = today
        candidate[experiment_id] = ResearchNode.from_dict(data)
        before_by_id[experiment_id] = before
        data_by_id[experiment_id] = data
        path_by_id[experiment_id] = path
        completed.append({
            "experiment_id": experiment_id,
            "finding_id": finding_record["id"],
            "path": str(path),
            "before": {
                "status": before.get("status"),
                "finding_count": len(before.get("findings", []) or []),
                "result_summary": before.get("result_summary"),
            },
            "after": {
                "status": data.get("status"),
                "finding_count": len(findings),
                "result_summary": data.get("result_summary"),
            },
            "finding": finding_record,
            "added_next_actions": added_actions,
        })

    validate_cockpit(root, candidate, state.current, state.explicit_edges, raise_on_error=True)
    changes = [
        (path_by_id[experiment_id], before_by_id[experiment_id], data_by_id[experiment_id])
        for experiment_id in experiment_order
        if before_by_id[experiment_id] != data_by_id[experiment_id]
    ]
    changed = bool(changes)
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "completed_experiments": completed,
        "experiment_ids": [item["experiment_id"] for item in completed],
        "changed_files": [str(path) for path, _, _ in changes],
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes) if changed else ""
    if dry_run or not changed:
        return result

    finish_mutation(
        root,
        [(path, after) for path, _, after in changes],
        interaction={
            "kind": "complete_experiments",
            "actor": "researcher",
            "command": script_command("complete_experiments.py"),
            "after": {
                "experiment_ids": result["experiment_ids"],
                "experiment_count": len(completed),
                "finding_ids": [item["finding_id"] for item in completed],
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=COMPLETE_EXPERIMENTS_EXAMPLE,
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--file", type=Path, dest="plan_file")
    parser.add_argument("--print-schema", action="store_true", help="Print the completion YAML schema example and exit.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    if args.print_schema:
        print(COMPLETE_EXPERIMENTS_EXAMPLE)
        return
    if args.plan_file is None:
        parser.error("--file is required unless --print-schema is used")

    try:
        result = complete_experiments(
            args.root,
            plan=load_completion_plan(args.plan_file),
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    verb = "Would complete" if args.dry_run else "Completed"
    print(f"{verb} {len(result['completed_experiments'])} experiment(s)")
    if args.show_diff and result.get("diff"):
        print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
