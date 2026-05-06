from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import subprocess
from typing import Any

from research_cockpit.agent_sessions import (
    ensure_worktree_boundary,
    nearest_problem_id,
    session_handoff,
    stable_session_id,
    today,
    worktree_boundary,
    worktree_label,
)
from research_cockpit.commands._runtime import (
    dry_run_preflight_result,
    emit_json,
    finish_mutation,
    load_validated_state,
    preflight_mutation,
    safe_print,
    yaml_change_diff,
)
from research_cockpit.model import (
    ACTIVE_WORKSTREAM_STATUSES,
    ResearchNode,
    ValidationError,
    load_yaml,
    script_command,
    validate_cockpit,
)
from research_cockpit.mutation_lock import MutationError
from research_cockpit.paths import default_data_root
from research_cockpit.storage import find_node_file

ROOT = default_data_root()


def _git_worktree_command(repo_root: Path, branch: str, worktree: Path, base: str | None) -> list[str]:
    command = ["git", "-C", str(repo_root), "worktree", "add", "-b", branch, str(worktree)]
    if base:
        command.append(base)
    return command


def _resolve_worktree(repo_root: Path, worktree: Path) -> Path:
    return worktree if worktree.is_absolute() else repo_root / worktree


def _run_git_worktree_add(command: list[str]) -> None:
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "git worktree add failed"
        raise RuntimeError(message)


def start_agent_session(
    root: Path,
    *,
    option_id: str,
    agent_id: str,
    objective: str,
    branch: str,
    worktree: Path,
    base: str | None = None,
    create_worktree: bool = False,
    force: bool = False,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    state = load_validated_state(root)
    nodes = state.nodes
    if option_id not in nodes:
        raise ValueError(f"Option node does not exist: {option_id}")
    option = nodes[option_id]
    if option.type != "option":
        raise ValueError(f"Node {option_id} must be option, got {option.type}")

    repo_root = root.resolve().parent
    resolved_worktree = _resolve_worktree(repo_root, worktree)
    boundary = ensure_worktree_boundary(root, resolved_worktree)
    if create_worktree and resolved_worktree.exists():
        raise ValueError(f"Worktree path already exists: {resolved_worktree}")

    option_path = find_node_file(root, option_id)
    data = load_yaml(option_path)
    before_data = copy.deepcopy(data)
    existing = data.get("agent_workstream") if isinstance(data.get("agent_workstream"), dict) else {}
    before_workstream = dict(existing) if existing else None
    existing_owner = str(existing.get("owner") or "")
    existing_status = str(existing.get("status") or "")
    if existing_owner and existing_owner != agent_id and existing_status in ACTIVE_WORKSTREAM_STATUSES and not force:
        raise ValueError(
            f"{option_id} is already claimed by {existing_owner} with status {existing_status}; use --force to override"
        )

    today_text = today()
    session_id = existing.get("session_id") if existing_owner == agent_id else None
    report_to_problem = existing.get("report_to_problem") or nearest_problem_id(nodes, option_id)
    data["agent_workstream"] = {
        key: value
        for key, value in {
            "session_id": session_id or stable_session_id(agent_id, option_id),
            "owner": agent_id,
            "status": "in_progress",
            "objective": objective,
            "git_branch": branch,
            "worktree_label": worktree_label(resolved_worktree),
            "report_to_problem": report_to_problem,
            "started_at": existing.get("started_at") if existing_owner == agent_id else today_text,
            "updated_at": today_text,
        }.items()
        if value not in (None, "")
    }
    data["updated_at"] = today_text

    candidate = dict(nodes)
    candidate[option_id] = ResearchNode.from_dict(data)
    validate_cockpit(root, candidate, state.current, state.explicit_edges, raise_on_error=True)

    git_command = _git_worktree_command(repo_root, branch, resolved_worktree, base)
    changes = [(option_path, before_data, data)]
    result: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "changed": not dry_run,
        "would_change": dry_run,
        "option_id": option_id,
        "agent_id": agent_id,
        "session_id": data["agent_workstream"]["session_id"],
        "git_branch": branch,
        "worktree_label": worktree_label(resolved_worktree),
        "path": str(option_path),
        "before": {"agent_workstream": before_workstream},
        "after": {"agent_workstream": data["agent_workstream"]},
        "root_boundary": boundary,
        "git_command": git_command,
        "launch_env": {"RESEARCH_COCKPIT_ROOT": str(root.resolve())},
        "handoff": session_handoff(root=root, agent_id=agent_id, option_id=option_id, worktree=resolved_worktree),
        "created_worktree": False,
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        result["changed"] = False
        return dry_run_preflight_result(root, result)

    if create_worktree:
        preflight_mutation(root)
        _run_git_worktree_add(git_command)
        result["created_worktree"] = True

    try:
        finish_mutation(
            root,
            changes,
            interaction={
                "kind": "start_agent_session",
                "actor": agent_id,
                "node_id": option_id,
                "command": script_command(
                    "start_agent_session.py",
                    "--option",
                    option_id,
                    "--agent",
                    agent_id,
                    "--branch",
                    branch,
                ),
                "before": result["before"],
                "after": result["after"],
                "extra": {
                    "option_id": option_id,
                    "agent_id": agent_id,
                    "session_id": data["agent_workstream"]["session_id"],
                    "git_branch": branch,
                    "worktree_label": worktree_label(resolved_worktree),
                    "created_worktree": create_worktree,
                },
            },
            rebuild_dashboard=rebuild_dashboard,
        )
    except MutationError as exc:
        if result["created_worktree"]:
            payload = dict(exc.payload)
            payload["created_worktree"] = True
            payload["worktree"] = str(resolved_worktree)
            payload["git_command"] = git_command
            recovery = list(payload.get("recovery_commands", []))
            recovery.insert(
                0,
                script_command(
                    "start_agent_session.py",
                    "--root",
                    str(root),
                    "--option",
                    option_id,
                    "--agent",
                    agent_id,
                    "--objective",
                    objective,
                    "--branch",
                    branch,
                    "--worktree",
                    str(resolved_worktree),
                    "--no-build",
                ),
            )
            payload["recovery_commands"] = recovery
            raise MutationError(str(exc), payload) from exc
        raise
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit start-agent-session")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--option", required=True, dest="option_id")
    parser.add_argument("--agent", required=True, dest="agent_id")
    parser.add_argument("--objective", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--worktree", required=True, type=Path)
    parser.add_argument("--base")
    parser.add_argument("--create-worktree", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        payload = start_agent_session(
            args.root,
            option_id=args.option_id,
            agent_id=args.agent_id,
            objective=args.objective,
            branch=args.branch,
            worktree=args.worktree,
            base=args.base,
            create_worktree=args.create_worktree,
            force=args.force,
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
    except (ValidationError, ValueError, RuntimeError, FileNotFoundError) as exc:
        resolved_worktree = _resolve_worktree(args.root.resolve().parent, args.worktree)
        payload = {
            "ok": False,
            "partial_success": False,
            "rolled_back": False,
            "written_files": [],
            "error": str(exc),
            "root_boundary": worktree_boundary(args.root, resolved_worktree),
        }
        if args.json:
            emit_json(payload)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(payload)
        return
    if args.dry_run:
        safe_print(f"Would start session for {args.agent_id} on {args.option_id}.")
    else:
        safe_print(f"Started session for {args.agent_id} on {args.option_id}.")


if __name__ == "__main__":
    main()
