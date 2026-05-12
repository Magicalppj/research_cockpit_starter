from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from research_cockpit.commands._runtime import compact_mutation_result, load_validated_state
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.commands.complete_experiment import complete_experiment
from research_cockpit.commands.set_agent_focus import set_agent_focus
from research_cockpit.commands.set_focus import set_focus_result
from research_cockpit.model import ValidationError, load_yaml
from research_cockpit.paths import default_data_root

ROOT = default_data_root()

TERMINAL_FOCUS_STATUSES = {
    "accepted",
    "archived",
    "cancelled",
    "done",
    "failed",
    "parked",
    "rejected",
    "resolved",
}


def _agent_ids_to_sync(current: dict[str, Any], experiment_id: str, sync_agent: str | None) -> list[str]:
    if not sync_agent:
        return []
    focuses = current.get("agent_focuses") if isinstance(current.get("agent_focuses"), dict) else {}
    if sync_agent == "all":
        return [
            str(agent_id)
            for agent_id, focus in focuses.items()
            if isinstance(focus, dict) and focus.get("current_focus_node") == experiment_id
        ]
    return [sync_agent]


def _node_next_actions(node: Any) -> list[str]:
    actions = node.raw.get("next_actions")
    if not isinstance(actions, list):
        return []
    return [str(action) for action in actions if str(action).strip()]


def _resolved_focus_warnings(next_focus: str | None, agent_ids: list[str]) -> set[str]:
    if not next_focus:
        return set()
    return {"current_focus_node_is_terminal", *(f"agent_focus_is_terminal:{agent_id}" for agent_id in agent_ids)}


def _resolved_focus_command(command: str, next_focus: str | None, agent_ids: list[str]) -> bool:
    if not next_focus:
        return False
    if command.startswith("research-cockpit set-focus "):
        return True
    if command.startswith("research-cockpit set-agent-focus "):
        return any(f"--agent {agent_id}" in command for agent_id in agent_ids)
    return False


def close_current_experiment(
    root: Path,
    *,
    experiment_id: str,
    finding: str,
    confidence: str,
    outcome: str | None = None,
    metrics: list[str] | None = None,
    artifact_ids: list[str] | None = None,
    result_summary: str | None = None,
    next_focus: str | None = None,
    sync_agent: str | None = None,
    rebuild_dashboard: bool = True,
) -> dict[str, Any]:
    state = load_validated_state(root)
    if experiment_id not in state.nodes:
        raise ValueError(f"Experiment node does not exist: {experiment_id}")
    if state.nodes[experiment_id].type != "experiment":
        raise ValueError(f"Node {experiment_id} must be experiment")
    if next_focus and next_focus not in state.nodes:
        raise ValueError(f"Next focus node does not exist: {next_focus}")
    if next_focus == experiment_id:
        raise ValueError("Next focus cannot be the experiment being closed")
    if next_focus and state.nodes[next_focus].status in TERMINAL_FOCUS_STATUSES:
        raise ValueError(
            f"Next focus node {next_focus} has terminal status {state.nodes[next_focus].status!r}"
        )
    before_current = load_yaml(root / "current_state.yaml")
    agent_ids = _agent_ids_to_sync(before_current, experiment_id, sync_agent)
    focus_next_actions = _node_next_actions(state.nodes[next_focus]) if next_focus else None

    completed = complete_experiment(
        root,
        experiment_id=experiment_id,
        finding=finding,
        confidence=confidence,
        outcome=outcome,
        metrics=metrics,
        artifact_ids=artifact_ids,
        result_summary=result_summary,
        rebuild_dashboard=False,
    )
    focus_result = None
    if next_focus:
        focus_result = set_focus_result(
            root,
            focus_node=next_focus,
            next_actions=focus_next_actions,
            rebuild_dashboard=False,
        )
        for agent_id in agent_ids:
            set_agent_focus(
                root,
                agent_id=agent_id,
                node_id=next_focus,
                next_actions=focus_next_actions,
                rebuild_dashboard=False,
            )

    if rebuild_dashboard:
        build_dashboard(root)

    resolved_warning_ids = _resolved_focus_warnings(next_focus, agent_ids)
    warnings = [
        warning
        for warning in completed.get("warnings", [])
        if warning not in resolved_warning_ids
    ]
    recommended_commands = [
        command
        for command in completed.get("recommended_commands", [])
        if not _resolved_focus_command(str(command), next_focus, agent_ids)
    ]

    return {
        "ok": True,
        "dry_run": False,
        "changed": True,
        "experiment_id": experiment_id,
        "next_focus": next_focus,
        "synced_agents": agent_ids,
        "completed_experiment": completed,
        "focus": focus_result,
        "warnings": warnings,
        "recommended_commands": recommended_commands,
        "updated_nodes": [experiment_id, *(["current_state"] if next_focus else [])],
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit close-current-experiment")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="experiment_id")
    parser.add_argument("--finding", required=True)
    parser.add_argument("--confidence", required=True)
    parser.add_argument("--outcome")
    parser.add_argument("--metric", action="append", dest="metrics")
    parser.add_argument("--artifact-id", action="append", dest="artifact_ids")
    parser.add_argument("--result-summary")
    parser.add_argument("--next-focus", help="Non-terminal node to use as the new global focus.")
    parser.add_argument(
        "--sync-agent",
        help="Agent id to move with --next-focus, or 'all' to move agents currently focused on the closed experiment.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = close_current_experiment(
            args.root,
            experiment_id=args.experiment_id,
            finding=args.finding,
            confidence=args.confidence,
            outcome=args.outcome,
            metrics=args.metrics,
            artifact_ids=args.artifact_ids,
            result_summary=args.result_summary,
            next_focus=args.next_focus,
            sync_agent=args.sync_agent,
            rebuild_dashboard=not args.no_build,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        payload = compact_mutation_result(
            result,
            command="close-current-experiment",
            target=args.experiment_id,
            root=args.root,
            updated=result.get("updated_nodes", []),
        ) if args.compact else result
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"Closed experiment {args.experiment_id}")


if __name__ == "__main__":
    main()
