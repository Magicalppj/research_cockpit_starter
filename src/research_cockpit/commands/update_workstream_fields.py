from __future__ import annotations

import argparse
import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.commands._runtime import compact_mutation_result, dry_run_preflight_result, finish_mutation, load_validated_state, yaml_change_diff
from research_cockpit.commands.record_finding import find_node_file
from research_cockpit.model import ResearchNode, VALID_WORKSTREAM_STATUSES, ValidationError, load_yaml, script_command, validate_cockpit
from research_cockpit.paths import default_data_root

ROOT = default_data_root()


def _set_if_present(target: dict[str, Any], field: str, value: Any, touched: list[str]) -> None:
    if value is None:
        return
    target[field] = value if isinstance(value, bool) else str(value)
    touched.append(f"agent_workstream.{field}")


def update_workstream_fields(
    root: Path,
    *,
    option_id: str,
    status: str | None = None,
    objective: str | None = None,
    owner: str | None = None,
    session_id: str | None = None,
    report_to_problem: str | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    if status is not None and status not in VALID_WORKSTREAM_STATUSES:
        allowed = ", ".join(sorted(VALID_WORKSTREAM_STATUSES))
        raise ValueError(f"Invalid agent_workstream.status {status!r}; allowed: {allowed}")
    if all(value is None for value in (status, objective, owner, session_id, report_to_problem)):
        raise ValueError("At least one workstream field update is required")

    state = load_validated_state(root)
    if option_id not in state.nodes:
        raise ValueError(f"Option node does not exist: {option_id}")
    if state.nodes[option_id].type != "option":
        raise ValueError(f"Node {option_id} must be option, got {state.nodes[option_id].type}")

    path = find_node_file(root, option_id)
    data = load_yaml(path)
    before_data = copy.deepcopy(data)
    workstream = data.get("agent_workstream")
    if workstream is None:
        workstream = {}
    if not isinstance(workstream, dict):
        raise ValueError(f"{option_id}: agent_workstream must be a mapping")
    workstream = dict(workstream)
    touched: list[str] = []
    _set_if_present(workstream, "status", status, touched)
    _set_if_present(workstream, "objective", objective, touched)
    _set_if_present(workstream, "owner", owner, touched)
    _set_if_present(workstream, "session_id", session_id, touched)
    _set_if_present(workstream, "report_to_problem", report_to_problem, touched)
    data["agent_workstream"] = workstream
    data["updated_at"] = str(date.today())

    candidate = dict(state.nodes)
    candidate[option_id] = ResearchNode.from_dict(data)
    validate_cockpit(root, candidate, state.current, state.explicit_edges, raise_on_error=True)

    before = {"agent_workstream": before_data.get("agent_workstream")}
    after = {"agent_workstream": data.get("agent_workstream")}
    changed = before != after
    result: dict[str, Any] = {
        "option_id": option_id,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "path": str(path),
        "before": before,
        "after": after,
        "fields": sorted(set(touched)),
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(path, before_data, data)]) if changed else ""
    if dry_run:
        return dry_run_preflight_result(root, result)
    if not changed:
        return result

    finish_mutation(
        root,
        [(path, before_data, data)],
        interaction={
            "kind": "update_workstream_fields",
            "actor": "researcher",
            "node_id": option_id,
            "command": f"{script_command('update_workstream_fields.py')} --option {option_id}",
            "before": before,
            "after": after,
            "extra": {"fields": sorted(set(touched))},
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit update-workstream-fields")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--option", required=True, dest="option_id")
    parser.add_argument("--status", choices=sorted(VALID_WORKSTREAM_STATUSES))
    parser.add_argument("--objective")
    parser.add_argument("--owner")
    parser.add_argument("--session-id")
    parser.add_argument("--report-to-problem")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = update_workstream_fields(
            args.root,
            option_id=args.option_id,
            status=args.status,
            objective=args.objective,
            owner=args.owner,
            session_id=args.session_id,
            report_to_problem=args.report_to_problem,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        payload = compact_mutation_result(
            result,
            command="update-workstream-fields",
            target=args.option_id,
            root=args.root,
            updated=[args.option_id],
        ) if args.compact else result
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    verb = "Would update" if args.dry_run else "Updated"
    print(f"{verb} workstream fields for {args.option_id}")


if __name__ == "__main__":
    main()
