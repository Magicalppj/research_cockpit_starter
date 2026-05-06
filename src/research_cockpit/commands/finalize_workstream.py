from __future__ import annotations

import argparse
import copy
from datetime import date
from pathlib import Path
from typing import Any
import yaml

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._evidence import append_unique, validate_artifact_ids
from research_cockpit.commands._runtime import (
    compact_mutation_result,
    dry_run_preflight_result,
    emit_json,
    finish_mutation,
    load_validated_state,
    safe_print,
    yaml_change_diff,
)
from research_cockpit.commands.file_schemas import FINALIZE_WORKSTREAM_EXAMPLE
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
    return path.read_text(encoding="utf-8-sig").strip()


def _optional_text(data: dict[str, Any], key: str) -> str | None:
    if key not in data or data.get(key) is None:
        return None
    text = str(data.get(key)).strip()
    return text or None


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a string or list")
    return [str(item) for item in value if str(item).strip()]


def load_finalize_spec(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Finalize file does not exist: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(data, dict):
        raise ValueError("Finalize file must contain a mapping")
    return data


def _unique_resolved_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = path.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        out.append(resolved)
    return out


def _resolve_file_summary_path(root: Path, finalize_file: Path, raw_value: str) -> Path:
    raw_path = Path(raw_value)
    if raw_path.is_absolute():
        attempts = [raw_path.resolve()]
    else:
        attempts = _unique_resolved_paths(
            [
                finalize_file.resolve().parent / raw_path,
                root / raw_path,
                Path.cwd() / raw_path,
            ]
        )
    for attempt in attempts:
        if attempt.exists():
            return attempt
    tried = "; ".join(str(path) for path in attempts)
    raise FileNotFoundError(f"Summary file does not exist: {raw_value}. Tried: {tried}")


def _merge_finalize_inputs(
    file_spec: dict[str, Any] | None,
    *,
    root: Path,
    finalize_file: Path | None,
    option_id: str | None,
    status: str | None,
    problem_status: str | None,
    stage_status: str | None,
    summary_file: Path | None,
    summary_target: str | None,
    artifact_ids: list[str] | None,
    sync_focus: bool,
    report: bool,
    agent_id: str | None,
    locale: str | None,
) -> dict[str, Any]:
    data = file_spec or {}
    file_summary = None
    if summary_file is None and data.get("summary_file") is not None:
        if finalize_file is None:
            file_summary = Path(str(data["summary_file"]))
        else:
            file_summary = _resolve_file_summary_path(root, finalize_file, str(data["summary_file"]))
    merged = {
        "option_id": option_id or _optional_text(data, "option"),
        "status": status or _optional_text(data, "status"),
        "problem_status": problem_status if problem_status is not None else _optional_text(data, "problem_status"),
        "stage_status": stage_status if stage_status is not None else _optional_text(data, "stage_status"),
        "summary_file": summary_file if summary_file is not None else file_summary,
        "summary_target": summary_target or _optional_text(data, "summary_target") or "report",
        "artifact_ids": artifact_ids if artifact_ids is not None else _string_list(data.get("artifacts"), "artifacts"),
        "sync_focus": sync_focus or bool(data.get("sync_focus", False)),
        "report": report or bool(data.get("report", False)),
        "agent_id": agent_id or _optional_text(data, "agent") or "researcher",
        "locale": locale or _optional_text(data, "locale"),
    }
    if not merged["option_id"]:
        raise ValueError("finalize option is required; provide --option or file field 'option'")
    if not merged["status"]:
        raise ValueError("finalize status is required; provide --status or file field 'status'")
    return merged


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
    resolved_inputs = {
        "summary_file": str(summary_file.resolve()) if summary_file is not None else None,
    }
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
        "resolved_inputs": resolved_inputs,
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
    if dry_run:
        return dry_run_preflight_result(root, result)
    if not changed:
        return result

    finish_mutation(
        root,
        changes,
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
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=FINALIZE_WORKSTREAM_EXAMPLE,
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--file", type=Path, dest="finalize_file")
    parser.add_argument("--print-schema", action="store_true", help="Print the finalize YAML schema example and exit.")
    parser.add_argument("--option", dest="option_id")
    parser.add_argument("--status")
    parser.add_argument("--problem-status")
    parser.add_argument("--stage-status")
    parser.add_argument("--summary-file", type=Path)
    parser.add_argument("--summary-target", choices=sorted(SUMMARY_TARGETS))
    parser.add_argument("--artifact", action="append", dest="artifact_ids")
    parser.add_argument("--sync-focus", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--agent", dest="agent_id")
    parser.add_argument("--locale", choices=["en", "zh"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--build", action="store_true", help="Accepted for readability; build is the default.")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    if args.print_schema:
        safe_print(FINALIZE_WORKSTREAM_EXAMPLE)
        return

    try:
        merged = _merge_finalize_inputs(
            load_finalize_spec(args.finalize_file) if args.finalize_file else None,
            root=args.root,
            finalize_file=args.finalize_file,
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
        )
        result = finalize_workstream(
            args.root,
            option_id=merged["option_id"],
            status=merged["status"],
            problem_status=merged["problem_status"],
            stage_status=merged["stage_status"],
            summary_file=merged["summary_file"],
            summary_target=merged["summary_target"],
            artifact_ids=merged["artifact_ids"],
            sync_focus=merged["sync_focus"],
            report=merged["report"],
            agent_id=merged["agent_id"],
            locale=merged["locale"],
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        if args.compact:
            updated = [result["option_id"]]
            if result.get("problem_id"):
                updated.append(result["problem_id"])
            if result.get("stage_id"):
                updated.append(result["stage_id"])
            if result.get("after", {}).get("focus_next_actions") != result.get("before", {}).get("focus_next_actions"):
                updated.append("current_state")
            emit_json(
                compact_mutation_result(
                    result,
                    command="finalize-workstream",
                    target={"option": result["option_id"], "problem": result.get("problem_id")},
                    root=args.root,
                    updated=updated,
                )
            )
        else:
            emit_json(result)
        return
    verb = "Would finalize" if args.dry_run else "Finalized"
    safe_print(f"{verb} workstream {result['option_id']}")
    if args.show_diff and result.get("diff"):
        safe_print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        safe_print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
