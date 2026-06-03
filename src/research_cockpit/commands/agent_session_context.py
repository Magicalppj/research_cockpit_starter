from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.agent_sessions import build_agent_session_context
from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.commands.option_workstream_context import compact_option_workstream_context
from research_cockpit.model import (
    ValidationError,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
)
from research_cockpit.paths import default_data_root

ROOT = default_data_root()


def agent_session_context_payload(
    root: Path,
    *,
    agent_id: str | None = None,
    assignment_id: str | None = None,
    option_id: str | None = None,
    compact: bool = False,
) -> dict[str, Any]:
    if not agent_id and not assignment_id and not option_id:
        raise ValueError("Pass --assignment, --agent, or --option")
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
    payload = build_agent_session_context(
        root,
        nodes,
        current,
        agent_id=agent_id,
        assignment_id=assignment_id,
        option_id=option_id,
    )
    if compact:
        payload = {
            "agent_id": payload.get("agent_id"),
            "assignment_id": payload.get("assignment_id"),
            "option_id": payload.get("option_id"),
            "matching_options": payload.get("matching_options", []),
            "session": payload.get("session", {}),
            "canonical_root": payload.get("canonical_root"),
            "required_root": payload.get("required_root"),
            "do_not_mutate_worktree_root": True,
            "stable_artifact_root": payload.get("stable_artifact_root"),
            "worktree_boundary": payload.get("worktree_boundary", {}),
            "assignment": payload.get("assignment"),
            "assignment_cursor": payload.get("assignment_cursor"),
            "agent_focus": payload.get("agent_focus"),
            "option_context": compact_option_workstream_context(payload["option_context"], nodes),
            "handoff": payload.get("handoff", {}),
        }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit agent-session-context")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--agent", dest="agent_id")
    parser.add_argument("--assignment", dest="assignment_id")
    parser.add_argument("--option", dest="option_id")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        payload = agent_session_context_payload(
            args.root,
            agent_id=args.agent_id,
            assignment_id=args.assignment_id,
            option_id=args.option_id,
            compact=args.compact,
        )
    except (ValidationError, ValueError) as exc:
        if args.json:
            emit_json({"ok": False, "error": str(exc)})
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(payload)
        return

    safe_print(f"Agent: {payload.get('agent_id')}")
    safe_print(f"Option: {payload.get('option_id')}")
    safe_print(f"Required root: {payload.get('required_root')}")
    focus = payload.get("agent_focus") or {}
    if focus.get("current_focus_node"):
        safe_print(f"Agent focus: {focus['current_focus_node']}")


if __name__ == "__main__":
    main()
