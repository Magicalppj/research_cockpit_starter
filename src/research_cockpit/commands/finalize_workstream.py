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
from research_cockpit.commands.record_finding import find_node_file
from research_cockpit.graph_core import derive_focus_path, node_id_by_type_in_path
from research_cockpit.model import (
    ResearchNode,
    VALID_WORKSTREAM_RECOMMENDATIONS,
    ValidationError,
    load_yaml,
    script_command,
    validate_cockpit,
    validate_status,
)
from research_cockpit.option_workstreams import build_option_workstream_context, upstream_problem_id
from research_cockpit.decisions import normalize_locale


SUMMARY_TARGETS = {"report", "option", "problem", "all"}


def _recommendation_for_status(status: str) -> str:
    if status == "accepted":
        return "accept"
    if status == "rejected":
        return "reject"
    return "continue"


def _read_summary(path: Path | None) -> str | None:
    if path is None:
        return None
    if not path.exists():
        raise FileNotFoundError(f"Summary file does not exist: {path}")
    return path.read_text(encoding="utf-8").strip()


def finalize_workstream(
    root: Path,
    *,
    option_id: str,
    status: str,
    problem_status: str | None = None,
    stage_status: str | None = None,
    summary_file: Path | None = None,
    summary_target: str = "report",
    artifact_ids: list[str] | None = None,
    sync_focus: bool = False,
    report: bool = False,
    agent_id: str = "researcher",
    locale: str | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    if summary_target not in SUMMARY_TARGETS:
        allowed = ", ".join(sorted(SUMMARY_TARGETS))
        raise ValueError(f"Invalid summary target {summary_target!r}; allowed: {allowed}")
    state = load_validated_state(root)
    nodes = state.nodes
    if option_id not in nodes:
        raise ValueError(f"Option node does not exist: {option_id}")
    option = nodes[option_id]
    if option.type != "option":
        raise ValueError(f"Node {option_id} must be option, got {option.type}")
    validate_status("option", status)
    problem_id = upstream_problem_id(nodes, option_id)
    if problem_status and not problem_id:
        raise ValueError(f"Option {option_id} has no upstream problem")
    if problem_status:
        validate_status("problem", problem_status)
    path = derive_focus_path(nodes, option_id)
    stage_id = node_id_by_type_in_path(nodes, path, "stage")
    if stage_status and not stage_id:
        raise ValueError(f"Option {option_id} has no upstream stage")
    if stage_status:
        validate_status("stage", stage_status)

    artifact_ids = artifact_ids or []
    validate_artifact_ids(nodes, artifact_ids)
    summary_text = _read_summary(summary_file)
    today = str(date.today())
    candidate = dict(nodes)
    changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]] = []

    option_path = find_node_file(root, option_id)
    option_before = load_yaml(option_path)
    option_data = copy.deepcopy(option_before)
    option_data["status"] = status
    if summary_text is not None and summary_target in {"option", "all"}:
        option_data["summary"] = summary_text
    if artifact_ids:
        option_data["linked_artifacts"], _ = append_unique(
            option_data.get("linked_artifacts"),
            artifact_ids,
            "linked_artifacts",
        )
    if report:
        context = build_option_workstream_context(
            root,
            candidate,
            state.current,
            option_id,
            locale=normalize_locale(locale, state.current),
        )
        evidence = context["evidence_summary"]
        recommendation = _recommendation_for_status(status)
        if recommendation not in VALID_WORKSTREAM_RECOMMENDATIONS:
            recommendation = "continue"
        workstream = option_data.get("agent_workstream") if isinstance(option_data.get("agent_workstream"), dict) else {}
        option_data["workstream_report"] = {
            "reporting_agent": agent_id,
            "recommendation": recommendation,
            "summary": summary_text or option_data.get("summary") or "",
            "evidence_summary": evidence.get("evidence_summary"),
            "experiment_count": evidence.get("experiment_count", 0),
            "finding_count": evidence.get("findings_count", 0),
            "linked_artifacts": artifact_ids,
            "reported_at": today,
        }
        option_data["agent_workstream"] = {
            **workstream,
            "owner": workstream.get("owner") or agent_id,
            "status": "reported",
            "report_to_problem": problem_id,
            "started_at": workstream.get("started_at") or today,
            "updated_at": today,
        }
    option_data["updated_at"] = today
    candidate[option_id] = ResearchNode.from_dict(option_data)
    if option_before != option_data:
        changes.append((option_path, option_before, option_data))

    problem_before = None
    problem_data = None
    if problem_id:
        problem_path = find_node_file(root, problem_id)
        problem_before = load_yaml(problem_path)
        problem_data = copy.deepcopy(problem_before)
        if problem_status:
            problem_data["status"] = problem_status
        if status == "accepted":
            problem_data["current_best_option"] = option_id
        if summary_text is not None and summary_target in {"problem", "all"}:
            problem_data["summary"] = summary_text
        if artifact_ids:
            problem_data["linked_artifacts"], _ = append_unique(
                problem_data.get("linked_artifacts"),
                artifact_ids,
                "linked_artifacts",
            )
        if problem_data != problem_before:
            problem_data["updated_at"] = today
            candidate[problem_id] = ResearchNode.from_dict(problem_data)
            changes.append((problem_path, problem_before, problem_data))

    if stage_status and stage_id:
        stage_path = find_node_file(root, stage_id)
        stage_before = load_yaml(stage_path)
        stage_data = copy.deepcopy(stage_before)
        stage_data["status"] = stage_status
        stage_data["updated_at"] = today
        candidate[stage_id] = ResearchNode.from_dict(stage_data)
        if stage_before != stage_data:
            changes.append((stage_path, stage_before, stage_data))

    current_path = root / "current_state.yaml"
    current_before = load_yaml(current_path)
    current_data = copy.deepcopy(current_before)
    if sync_focus:
        source_actions = []
        if problem_data is not None:
            source_actions = problem_data.get("next_actions", []) or []
        else:
            source_actions = option_data.get("next_actions", []) or []
        if not isinstance(source_actions, list):
            raise ValueError("focus sync source next_actions must be a list")
        current_data["next_actions"] = [str(item) for item in source_actions if str(item).strip()]
        current_data["updated_at"] = today
        if current_data != current_before:
            changes.append((current_path, current_before, current_data))

    validate_cockpit(root, candidate, current_data, state.explicit_edges, raise_on_error=True)
    changed = bool(changes)
    result: dict[str, Any] = {
        "option_id": option_id,
        "problem_id": problem_id,
        "stage_id": stage_id,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "changed_files": [str(item[0]) for item in changes],
        "before": {
            "option": {
                "status": option_before.get("status"),
                "summary": option_before.get("summary"),
                "linked_artifacts": option_before.get("linked_artifacts", []) or [],
            },
            "problem": {
                "status": problem_before.get("status"),
                "summary": problem_before.get("summary"),
                "current_best_option": problem_before.get("current_best_option"),
            } if problem_before else None,
            "focus_next_actions": current_before.get("next_actions", []) or [],
        },
        "after": {
            "option": {
                "status": option_data.get("status"),
                "summary": option_data.get("summary"),
                "linked_artifacts": option_data.get("linked_artifacts", []) or [],
                "workstream_report": option_data.get("workstream_report"),
            },
            "problem": {
                "status": problem_data.get("status"),
                "summary": problem_data.get("summary"),
                "current_best_option": problem_data.get("current_best_option"),
            } if problem_data else None,
            "focus_next_actions": current_data.get("next_actions", []) or [],
        },
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes) if changed else ""
    if dry_run or not changed:
        return result

    finish_mutation(
        root,
        [(change_path, after) for change_path, _, after in changes],
        interaction={
            "kind": "finalize_workstream",
            "actor": agent_id,
            "node_id": option_id,
            "command": f"{script_command('finalize_workstream.py')} --option {option_id} --status {status}",
            "before": result["before"],
            "after": result["after"],
            "extra": {
                "option_id": option_id,
                "problem_id": problem_id,
                "stage_id": stage_id,
                "summary_target": summary_target,
                "artifact_ids": artifact_ids,
                "sync_focus": sync_focus,
                "report": report,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--option", required=True, dest="option_id")
    parser.add_argument("--status", required=True)
    parser.add_argument("--problem-status")
    parser.add_argument("--stage-status")
    parser.add_argument("--summary-file", type=Path)
    parser.add_argument("--summary-target", choices=sorted(SUMMARY_TARGETS), default="report")
    parser.add_argument("--artifact", action="append", dest="artifact_ids")
    parser.add_argument("--sync-focus", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--agent", default="researcher", dest="agent_id")
    parser.add_argument("--locale", choices=["en", "zh"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--build", action="store_true", help="Accepted for readability; build is the default.")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = finalize_workstream(
            args.root,
            option_id=args.option_id,
            status=args.status,
            problem_status=args.problem_status,
            stage_status=args.stage_status,
            summary_file=args.summary_file,
            summary_target=args.summary_target,
            artifact_ids=args.artifact_ids,
            sync_focus=args.sync_focus,
            report=args.report,
            agent_id=args.agent_id,
            locale=args.locale,
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
    verb = "Would finalize" if args.dry_run else "Finalized"
    print(f"{verb} workstream {args.option_id}")
    if args.show_diff and result.get("diff"):
        print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
