from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

from research_cockpit.model import load_nodes, load_yaml, validate_cockpit
from research_cockpit.paths import default_data_root

ROOT = default_data_root()

TERMINAL_STATUSES = {
    "accepted",
    "archived",
    "cancelled",
    "done",
    "failed",
    "parked",
    "rejected",
    "resolved",
}

OPEN_EXPERIMENT_STATUSES = {"active", "planned", "queued", "running"}
NODE_ID_CHARACTERS = "A-Za-z0-9_:-"


def _warning(
    warning_id: str,
    message: str,
    *,
    node_id: str | None = None,
    agent_id: str | None = None,
    command: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": warning_id,
        "severity": "warning",
        "message": message,
    }
    if node_id:
        out["node_id"] = node_id
    if agent_id:
        out["agent_id"] = agent_id
    if command:
        out["command"] = command
    return out


def _next_actions(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _current_work_node_ids(current: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for field in ("current_focus_node", "current_option", "current_problem"):
        node_id = current.get(field)
        if node_id and str(node_id) not in out:
            out.append(str(node_id))
    return out


def _terminal_node_ids(nodes: dict[str, Any]) -> set[str]:
    return {node_id for node_id, node in nodes.items() if node.status in TERMINAL_STATUSES}


def _mentions_node_id(action: str, node_id: str) -> bool:
    pattern = rf"(?<![{NODE_ID_CHARACTERS}]){re.escape(node_id)}(?![{NODE_ID_CHARACTERS}])"
    return re.search(pattern, action) is not None


def semantic_lint(root: Path = ROOT) -> dict[str, Any]:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    validation_errors = validate_cockpit(root, nodes, current)
    warnings: list[dict[str, Any]] = []

    current_focus_node = current.get("current_focus_node")
    if current_focus_node and str(current_focus_node) in nodes:
        focus = nodes[str(current_focus_node)]
        if focus.status in TERMINAL_STATUSES:
            warnings.append(_warning(
                "current_focus_terminal",
                f"current_focus_node {focus.id!r} has terminal status {focus.status!r}.",
                node_id=focus.id,
                command=f"research-cockpit set-focus --root {root} --focus-node <next_node>",
            ))

    agent_focuses = current.get("agent_focuses")
    if isinstance(agent_focuses, dict):
        for agent_id, focus in agent_focuses.items():
            if not isinstance(focus, dict):
                continue
            focus_node = focus.get("current_focus_node")
            if focus_node and str(focus_node) in nodes:
                node = nodes[str(focus_node)]
                if node.status in TERMINAL_STATUSES:
                    warnings.append(_warning(
                        "agent_focus_terminal",
                        f"agent {agent_id!r} focus {node.id!r} has terminal status {node.status!r}.",
                        node_id=node.id,
                        agent_id=str(agent_id),
                        command=(
                            f"research-cockpit set-agent-focus --root {root} "
                            f"--agent {agent_id} --node <next_node>"
                        ),
                    ))

    terminal_ids = _terminal_node_ids(nodes)
    for owner, actions in [("current_state", _next_actions(current.get("next_actions")))]:
        for action in actions:
            for node_id in terminal_ids:
                if _mentions_node_id(action, node_id):
                    warnings.append(_warning(
                        "next_action_references_terminal_node",
                        f"{owner}.next_actions references terminal node {node_id!r}.",
                        node_id=node_id,
                    ))
                    break
    if isinstance(agent_focuses, dict):
        for agent_id, focus in agent_focuses.items():
            if not isinstance(focus, dict):
                continue
            for action in _next_actions(focus.get("next_actions")):
                for node_id in terminal_ids:
                    if _mentions_node_id(action, node_id):
                        warnings.append(_warning(
                            "next_action_references_terminal_node",
                            f"agent {agent_id!r} next_actions references terminal node {node_id!r}.",
                            node_id=node_id,
                            agent_id=str(agent_id),
                        ))
                        break

    current_actions = _next_actions(current.get("next_actions"))
    for node_id in _current_work_node_ids(current):
        node = nodes.get(node_id)
        if not node:
            continue
        node_actions = _next_actions(node.raw.get("next_actions"))
        if node_actions and node_actions != current_actions:
            warnings.append(_warning(
                "current_next_actions_diverge_from_focus_node",
                (
                    "current_state.next_actions differs from a node-local next_actions list; "
                    "dashboard Current Next Actions reads current_state.yaml."
                ),
                node_id=node_id,
                command=f"research-cockpit sync-focus-actions --root {root} --from-node {node_id}",
            ))
            break

    for node in nodes.values():
        if node.type == "experiment" and node.status in OPEN_EXPERIMENT_STATUSES:
            if node.raw.get("result_summary") or node.raw.get("findings"):
                warnings.append(_warning(
                    "open_experiment_has_result",
                    f"experiment {node.id!r} is {node.status!r} but already has result evidence.",
                    node_id=node.id,
                ))
        if node.type == "option":
            workstream = node.raw.get("agent_workstream")
            if not isinstance(workstream, dict):
                continue
            child_experiments = [
                nodes[child_id]
                for child_id in node.children
                if child_id in nodes and nodes[child_id].type == "experiment"
            ]
            if not child_experiments:
                continue
            workstream_status = str(workstream.get("status") or "")
            all_terminal = all(child.status in TERMINAL_STATUSES for child in child_experiments)
            any_open = any(child.status not in TERMINAL_STATUSES for child in child_experiments)
            if workstream_status in {"reported", "released"} and any_open:
                warnings.append(_warning(
                    "workstream_completed_with_open_experiments",
                    f"option {node.id!r} workstream is completed but still has open experiments.",
                    node_id=node.id,
                ))
            elif workstream_status in {"claimed", "in_progress"} and all_terminal:
                warnings.append(_warning(
                    "workstream_active_with_all_experiments_terminal",
                    f"option {node.id!r} workstream is active but all child experiments are terminal.",
                    node_id=node.id,
                ))

    return {
        "ok": not warnings,
        "valid": not validation_errors,
        "validation_errors": validation_errors,
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit lint")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="Run semantic stale-state checks; exits 1 when warnings are present.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not args.semantic:
        raise SystemExit("--semantic is required for now")

    payload = semantic_lint(args.root)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if payload["ok"]:
            print("OK: no semantic warnings")
        else:
            print(f"Semantic warnings: {payload['warning_count']}")
            for warning in payload["warnings"]:
                print(f"- {warning['id']}: {warning['message']}")
    raise SystemExit(0 if payload["ok"] else 1)


if __name__ == "__main__":
    main()
