from __future__ import annotations

import argparse
import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import dry_run_preflight_result, finish_mutation, yaml_change_diff
from research_cockpit.commands.update_status import find_node_file
from research_cockpit.model import ResearchNode, ValidationError, load_yaml, script_command, validate_cockpit
from research_cockpit.mutation_runtime import load_validated_state


def _baseline_data(
    *,
    option_id: str,
    decision_id: str | None = None,
    artifacts: list[str] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"option": option_id}
    if decision_id:
        data["decision"] = decision_id
    clean_artifacts = [str(item) for item in artifacts or [] if str(item).strip()]
    if clean_artifacts:
        data["artifacts"] = clean_artifacts
    if reason:
        data["reason"] = reason
    return data


def _human_result_verb(*, clear: bool, dry_run: bool) -> str:
    if clear:
        return "Would clear" if dry_run else "Cleared"
    return "Would set" if dry_run else "Set"


def set_baseline(
    root: Path,
    *,
    node_id: str,
    option_id: str | None = None,
    decision_id: str | None = None,
    artifacts: list[str] | None = None,
    reason: str | None = None,
    clear: bool = False,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    operation_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if clear and any([option_id, decision_id, artifacts, reason]):
        raise ValueError("--clear cannot be combined with baseline fields")
    if not clear and not option_id:
        raise ValueError("--option is required unless --clear is used")

    state = load_validated_state(root)
    nodes = state.nodes
    if node_id not in nodes:
        raise ValueError(f"Node does not exist: {node_id}")

    path = find_node_file(root, node_id)
    before_data = load_yaml(path)
    data = copy.deepcopy(before_data)
    before = copy.deepcopy(data.get("baseline"))
    if clear:
        data.pop("baseline", None)
    else:
        data["baseline"] = _baseline_data(
            option_id=str(option_id),
            decision_id=decision_id,
            artifacts=artifacts,
            reason=reason,
        )
    data["updated_at"] = str(date.today())

    candidate = dict(nodes)
    candidate[node_id] = ResearchNode.from_dict(data)
    validate_cockpit(root, candidate, state.current, state.explicit_edges, raise_on_error=True)

    after = copy.deepcopy(data.get("baseline"))
    changed = before != after
    result: dict[str, Any] = {
        "node_id": node_id,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "path": str(path),
        "before": before,
        "after": after,
        "baseline": after,
        "changed_files": [str(path)] if changed else [],
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(path, before_data, data)]) if changed else ""
    if dry_run:
        return dry_run_preflight_result(root, result)
    if not changed and operation_request is None:
        return result

    finish_mutation(
        root,
        [(path, before_data, data)],
        interaction={
            "kind": "set_baseline",
            "actor": "researcher",
            "node_id": node_id,
            "command": f"{script_command('set_baseline.py')} --node {node_id}",
            "before": before,
            "after": after,
        },
        rebuild_dashboard=rebuild_dashboard,
        operation_request=operation_request,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--node", required=True, dest="node_id")
    parser.add_argument("--option")
    parser.add_argument("--decision")
    parser.add_argument("--artifact", action="append", dest="artifacts")
    parser.add_argument("--reason")
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = set_baseline(
            args.root,
            node_id=args.node_id,
            option_id=args.option,
            decision_id=args.decision,
            artifacts=args.artifacts,
            reason=args.reason,
            clear=args.clear,
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
    verb = _human_result_verb(clear=args.clear, dry_run=args.dry_run)
    print(f"{verb} baseline for {args.node_id}")
    if args.show_diff and result.get("diff"):
        print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")


if __name__ == "__main__":
    main()
