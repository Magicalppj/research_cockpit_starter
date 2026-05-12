from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from research_cockpit.agent_sessions import option_for_focus, today
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
from research_cockpit.model import ValidationError, load_yaml, script_command, validate_cockpit
from research_cockpit.mutation_lock import MutationError
from research_cockpit.paths import default_data_root

ROOT = default_data_root()


def set_agent_focus(
    root: Path,
    *,
    agent_id: str,
    node_id: str,
    next_actions: list[str] | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    state = load_validated_state(root)
    if node_id not in state.nodes:
        raise ValueError(f"Focus node does not exist: {node_id}")

    current_path = root / "current_state.yaml"
    current = load_yaml(current_path)
    before_current = copy.deepcopy(current)
    focuses = current.get("agent_focuses") if isinstance(current.get("agent_focuses"), dict) else {}
    existing = focuses.get(agent_id) if isinstance(focuses.get(agent_id), dict) else {}
    path = derive_focus_path(state.nodes, node_id)
    actions = unique_strings(next_actions if next_actions is not None else existing.get("next_actions", []))
    focuses = dict(focuses)
    focuses[agent_id] = {
        "current_focus_node": node_id,
        "current_focus_path": path,
        "current_option": option_for_focus(state.nodes, node_id),
        "next_actions": actions,
        "updated_at": today(),
    }
    current["agent_focuses"] = focuses

    validate_cockpit(root, state.nodes, current, state.explicit_edges, raise_on_error=True)
    changes = [(current_path, before_current, current)]
    changed = before_current != current
    result: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "agent_id": agent_id,
        "node_id": node_id,
        "current_option": focuses[agent_id]["current_option"],
        "path": str(current_path),
        "before": {"agent_focus": existing or None},
        "after": {"agent_focus": focuses[agent_id]},
    }
    if dry_run:
        result["would_change"] = changed
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        result["changed"] = False
        return dry_run_preflight_result(root, result)
    if not changed:
        return result

    finish_mutation(
        root,
        changes,
        interaction={
            "kind": "set_agent_focus",
            "actor": agent_id,
            "node_id": node_id,
            "command": script_command("set_agent_focus.py", "--agent", agent_id, "--node", node_id),
            "before": result["before"],
            "after": result["after"],
            "extra": {
                "agent_id": agent_id,
                "node_id": node_id,
                "current_option": focuses[agent_id]["current_option"],
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit set-agent-focus")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--agent", required=True, dest="agent_id")
    parser.add_argument("--node", required=True, dest="node_id")
    parser.add_argument("--next-action", action="append", dest="next_actions")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        payload = set_agent_focus(
            args.root,
            agent_id=args.agent_id,
            node_id=args.node_id,
            next_actions=args.next_actions,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except MutationError as exc:
        if args.json and exc.payload:
            emit_json(exc.payload)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc
    except (ValidationError, ValueError) as exc:
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
                command="set-agent-focus",
                target={"agent_id": args.agent_id, "node_id": args.node_id},
                root=args.root,
                updated=[args.node_id],
            ) if args.compact else payload
        )
        return
    if args.dry_run:
        safe_print(f"Would set agent focus for {args.agent_id} to {args.node_id}.")
    else:
        safe_print(f"Set agent focus for {args.agent_id} to {args.node_id}.")


if __name__ == "__main__":
    main()
