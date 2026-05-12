from __future__ import annotations

import argparse
import copy
import json
from datetime import date
from pathlib import Path

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.model import (
    derive_focus_fields,
    load_nodes,
    load_yaml,
    script_command,
    validate_cockpit,
)
from research_cockpit.commands._runtime import compact_mutation_result, dry_run_preflight_result, finish_mutation, yaml_change_diff


def parse_path(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def set_focus(
    root: Path,
    *,
    stage: str | None = None,
    problem: str | None = None,
    option: str | None = None,
    focus_node: str | None = None,
    path: list[str] | None = None,
    hypothesis: str | None = None,
    open_risks: list[str] | None = None,
    next_actions: list[str] | None = None,
    rebuild_dashboard: bool = True,
) -> Path:
    result = set_focus_result(
        root,
        stage=stage,
        problem=problem,
        option=option,
        focus_node=focus_node,
        path=path,
        hypothesis=hypothesis,
        open_risks=open_risks,
        next_actions=next_actions,
        rebuild_dashboard=rebuild_dashboard,
        dry_run=False,
        show_diff=False,
    )
    return Path(str(result["path"]))


def set_focus_result(
    root: Path,
    *,
    stage: str | None = None,
    problem: str | None = None,
    option: str | None = None,
    focus_node: str | None = None,
    path: list[str] | None = None,
    hypothesis: str | None = None,
    open_risks: list[str] | None = None,
    next_actions: list[str] | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, object]:
    nodes = load_nodes(root)
    current_path = root / "current_state.yaml"
    current = load_yaml(current_path)
    before_data = copy.deepcopy(current)
    before = {
        "current_stage": current.get("current_stage"),
        "current_problem": current.get("current_problem"),
        "current_option": current.get("current_option"),
        "current_focus_node": current.get("current_focus_node"),
        "current_focus_path": current.get("current_focus_path", []) or [],
        "next_actions": current.get("next_actions", []) or [],
    }

    focus_node = focus_node or problem
    if not focus_node:
        raise ValueError("Either --focus-node or --problem is required")
    if focus_node not in nodes:
        raise ValueError(f"Focus node does not exist: {focus_node}")

    derived = derive_focus_fields(nodes, focus_node, current)
    stage = stage or derived.get("current_stage")
    problem = problem or derived.get("current_problem")
    option = option or derived.get("current_option")
    path = path if path is not None else derived["current_focus_path"]

    for node_id, expected_type in ((stage, "stage"), (problem, "problem"), (option, "option")):
        if not node_id:
            continue
        if node_id not in nodes:
            raise ValueError(f"Focus node does not exist: {node_id}")
        if nodes[node_id].type != expected_type:
            raise ValueError(f"Focus node {node_id} must be {expected_type}, got {nodes[node_id].type}")

    current["current_stage"] = stage
    current["current_problem"] = problem
    current["current_option"] = option
    current["current_focus_node"] = focus_node
    current["current_focus_path"] = path
    if hypothesis is not None:
        current["current_hypothesis"] = hypothesis
    if open_risks is not None:
        current["open_risks"] = open_risks
    if next_actions is not None:
        current["next_actions"] = next_actions
    current["updated_at"] = str(date.today())

    validate_cockpit(root, nodes, current, raise_on_error=True)
    after = {
        "current_stage": current.get("current_stage"),
        "current_problem": current.get("current_problem"),
        "current_option": current.get("current_option"),
        "current_focus_node": current.get("current_focus_node"),
        "current_focus_path": current.get("current_focus_path", []) or [],
        "next_actions": current.get("next_actions", []) or [],
    }
    changed = before_data != current
    result: dict[str, object] = {
        "focus_node": focus_node,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "path": str(current_path),
        "before": before,
        "after": after,
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(current_path, before_data, current)]) if changed else ""
    if dry_run:
        return dry_run_preflight_result(root, result)
    if not changed:
        return result

    finish_mutation(
        root,
        [(current_path, before_data, current)],
        interaction={
            "kind": "set_focus",
            "actor": "researcher",
            "node_id": focus_node,
            "command": f"{script_command('set_focus.py')} --focus-node {focus_node}",
            "before": before,
            "after": after,
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--stage")
    parser.add_argument("--problem")
    parser.add_argument("--option")
    parser.add_argument("--focus-node")
    parser.add_argument("--path", help="Comma-separated current focus path; derived from --focus-node when omitted")
    parser.add_argument("--hypothesis")
    parser.add_argument("--risk", action="append", dest="open_risks")
    parser.add_argument("--next-action", action="append", dest="next_actions")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true", help="Only update current_state.yaml; do not rebuild dashboards")
    args = parser.parse_args()

    try:
        result = set_focus_result(
            args.root,
            stage=args.stage,
            problem=args.problem,
            option=args.option,
            focus_node=args.focus_node,
            path=parse_path(args.path) if args.path else None,
            hypothesis=args.hypothesis,
            open_risks=args.open_risks,
            next_actions=args.next_actions,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except ValueError as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        payload = compact_mutation_result(
            result,
            command="set-focus",
            target=result["focus_node"],
            root=args.root,
            updated=[str(result["focus_node"])],
        ) if args.compact else result
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    verb = "Would update" if args.dry_run else "Updated"
    print(f"{verb} {result['path']}")
    if args.show_diff and result.get("diff"):
        print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build and result["changed"]:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
