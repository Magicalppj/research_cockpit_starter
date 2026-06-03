from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Any

from research_cockpit.agent_sessions import option_for_focus, today
from research_cockpit.assignment_scope import AssignmentScopeError, ensure_assignment_scope
from research_cockpit.commands._runtime import (
    compact_mutation_result,
    dry_run_preflight_result,
    emit_json,
    finish_mutation,
    load_validated_state,
    safe_print,
    yaml_change_diff,
)
from research_cockpit.graph_core import derive_focus_path, unique_strings
from research_cockpit.model import (
    AssignmentRecord,
    ValidationError,
    load_assignments,
    load_yaml,
    script_command,
    validate_cockpit,
)
from research_cockpit.mutation_lock import MutationError
from research_cockpit.paths import default_data_root

ROOT = default_data_root()


def _assignment_path(root: Path, assignment_id: str) -> Path:
    return root / "assignments" / f"{assignment_id}.yaml"


def set_cursor(
    root: Path,
    *,
    assignment_id: str,
    node_id: str,
    next_actions: list[str] | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    state = load_validated_state(root)
    ensure_assignment_scope(
        root,
        state.nodes,
        assignment_id=assignment_id,
        target_node_ids=[node_id],
    )
    assignments = load_assignments(root)
    if assignment_id not in assignments:
        raise ValueError(f"Assignment does not exist: {assignment_id}")

    path = _assignment_path(root, assignment_id)
    before_data = load_yaml(path)
    after_data = copy.deepcopy(before_data)
    before = {
        "current_node": before_data.get("current_node"),
        "next_actions": list(before_data.get("next_actions", []) or []),
    }
    after_data["current_node"] = node_id
    if next_actions is not None:
        after_data["next_actions"] = unique_strings(next_actions)
    after_data["updated_at"] = today()

    candidate_assignments = dict(assignments)
    candidate_assignments[assignment_id] = AssignmentRecord.from_dict(after_data)
    validate_cockpit(
        root,
        state.nodes,
        state.current,
        state.explicit_edges,
        assignments=candidate_assignments,
        raise_on_error=True,
    )

    path_ids = derive_focus_path(state.nodes, node_id)
    after = {
        "current_node": after_data.get("current_node"),
        "current_path": path_ids,
        "current_option": option_for_focus(state.nodes, node_id),
        "next_actions": list(after_data.get("next_actions", []) or []),
    }
    changed = before_data != after_data
    result: dict[str, Any] = {
        "ok": True,
        "assignment_id": assignment_id,
        "node_id": node_id,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "path": str(path),
        "before": {"assignment": before},
        "after": {"assignment": after},
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(path, before_data, after_data)]) if changed else ""
    if dry_run:
        return dry_run_preflight_result(root, result)
    if not changed:
        return result

    finish_mutation(
        root,
        [(path, before_data, after_data)],
        interaction={
            "kind": "set_cursor",
            "actor": str(assignments[assignment_id].agent_id),
            "node_id": node_id,
            "command": script_command("set_cursor.py", "--assignment", assignment_id, "--node", node_id),
            "before": result["before"],
            "after": result["after"],
            "extra": {
                "assignment_id": assignment_id,
                "agent_id": assignments[assignment_id].agent_id,
                "node_id": node_id,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit set-cursor")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--assignment", required=True, dest="assignment_id")
    parser.add_argument("--node", required=True, dest="node_id")
    parser.add_argument("--next-action", action="append", dest="next_actions")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        payload = set_cursor(
            args.root,
            assignment_id=args.assignment_id,
            node_id=args.node_id,
            next_actions=args.next_actions,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except AssignmentScopeError as exc:
        if args.json:
            emit_json(exc.payload)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc
    except MutationError as exc:
        if args.json and exc.payload:
            emit_json(exc.payload)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        if args.json:
            emit_json({
                "ok": False,
                "partial_success": False,
                "rolled_back": False,
                "written_files": [],
                "error": str(exc),
            })
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(
            compact_mutation_result(
                payload,
                command="set-cursor",
                target={"assignment_id": args.assignment_id, "node_id": args.node_id},
                root=args.root,
                updated=[args.assignment_id],
            ) if args.compact else payload
        )
        return
    verb = "Would set" if args.dry_run else "Set"
    safe_print(f"{verb} assignment cursor {args.assignment_id} to {args.node_id}.")


if __name__ == "__main__":
    main()
