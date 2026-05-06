from __future__ import annotations

import argparse
import copy
import json
from datetime import date
from pathlib import Path

from research_cockpit.paths import default_data_root
from typing import Any

ROOT = default_data_root()

from research_cockpit.model import (
    ACTIVE_WORKSTREAM_STATUSES,
    ResearchNode,
    ValidationError,
    derive_focus_path,
    load_yaml,
    script_command,
    validate_cockpit,
)
from research_cockpit.commands._runtime import dry_run_preflight_result, finish_mutation, load_validated_state
from research_cockpit.commands.record_finding import find_node_file


VALID_CLAIM_STATUSES = {"claimed", "in_progress"}


def _nearest_problem_id(nodes: dict[str, ResearchNode], option_id: str) -> str | None:
    for node_id in reversed(derive_focus_path(nodes, option_id)):
        if nodes[node_id].type == "problem":
            return node_id
    return None


def claim_option(
    root: Path,
    *,
    option_id: str,
    agent_id: str,
    objective: str | None = None,
    status: str = "claimed",
    force: bool = False,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
) -> Path | dict[str, Any]:
    if status not in VALID_CLAIM_STATUSES:
        allowed = ", ".join(sorted(VALID_CLAIM_STATUSES))
        raise ValueError(f"Invalid claim status {status!r}; allowed: {allowed}")

    state = load_validated_state(root)
    nodes = state.nodes
    if option_id not in nodes:
        raise ValueError(f"Option node does not exist: {option_id}")
    option = nodes[option_id]
    if option.type != "option":
        raise ValueError(f"Node {option_id} must be option, got {option.type}")

    option_path = find_node_file(root, option_id)
    data = load_yaml(option_path)
    before_data = copy.deepcopy(data)
    existing = data.get("agent_workstream") if isinstance(data.get("agent_workstream"), dict) else {}
    before_workstream = dict(existing) if existing else None
    existing_owner = str(existing.get("owner") or "")
    existing_status = str(existing.get("status") or "")
    if (
        existing_owner
        and existing_owner != agent_id
        and existing_status in ACTIVE_WORKSTREAM_STATUSES
        and not force
    ):
        raise ValueError(
            f"{option_id} is already claimed by {existing_owner} with status {existing_status}; use --force to override"
        )

    today = str(date.today())
    report_to_problem = existing.get("report_to_problem") or _nearest_problem_id(nodes, option_id)
    workstream: dict[str, Any] = {
        "owner": agent_id,
        "status": status,
        "objective": objective if objective is not None else existing.get("objective"),
        "report_to_problem": report_to_problem,
        "started_at": existing.get("started_at") if existing_owner == agent_id else today,
        "updated_at": today,
    }
    data["agent_workstream"] = {key: value for key, value in workstream.items() if value not in (None, "")}
    data["updated_at"] = today

    candidate = dict(nodes)
    candidate[option_id] = ResearchNode.from_dict(data)
    validate_cockpit(root, candidate, state.current, state.explicit_edges, raise_on_error=True)
    preview = {
        "dry_run": dry_run,
        "changed": not dry_run,
        "option_id": option_id,
        "agent_id": agent_id,
        "status": status,
        "force": force,
        "path": str(option_path),
        "before": {"agent_workstream": before_workstream},
        "after": {"agent_workstream": data["agent_workstream"]},
    }
    if dry_run:
        preview["changed"] = False
        return dry_run_preflight_result(root, preview)

    command = f"{script_command('claim_option.py')} --option {option_id} --agent {agent_id} --status {status}"
    if force:
        command += " --force"
    finish_mutation(
        root,
        [(option_path, before_data, data)],
        interaction={
            "kind": "claim_option",
            "actor": agent_id,
            "node_id": option_id,
            "command": command,
            "before": {"agent_workstream": before_workstream},
            "after": {"agent_workstream": data["agent_workstream"]},
            "extra": {
                "option_id": option_id,
                "agent_id": agent_id,
                "status": status,
                "force": force,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return option_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--option", required=True, dest="option_id")
    parser.add_argument("--agent", required=True, dest="agent_id")
    parser.add_argument("--objective")
    parser.add_argument("--status", choices=sorted(VALID_CLAIM_STATUSES), default="claimed")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        out = claim_option(
            args.root,
            option_id=args.option_id,
            agent_id=args.agent_id,
            objective=args.objective,
            status=args.status,
            force=args.force,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        if isinstance(out, dict):
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({
                "dry_run": False,
                "changed": True,
                "option_id": args.option_id,
                "agent_id": args.agent_id,
                "status": args.status,
                "force": args.force,
                "path": str(out),
            }, ensure_ascii=False, indent=2))
        return

    if args.dry_run:
        print(f"Would claim {args.option_id} for {args.agent_id} with status {args.status}.")
        return

    print(f"Claimed {args.option_id} for {args.agent_id}: {out}")
    if not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
