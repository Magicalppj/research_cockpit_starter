from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import secrets
import subprocess
from typing import Any

from research_cockpit.agent_sessions import (
    ensure_worktree_boundary,
    nearest_problem_id,
    session_handoff,
    shell_join,
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
    ACTIVE_ASSIGNMENT_STATUSES,
    ACTIVE_WORKSTREAM_STATUSES,
    AgentRecord,
    AssignmentRecord,
    ResearchNode,
    ValidationError,
    load_agents,
    load_assignments,
    load_yaml,
    script_command,
    validate_cockpit,
)
from research_cockpit.mutation_lock import MutationError
from research_cockpit.paths import default_data_root
from research_cockpit.storage import find_node_file

ROOT = default_data_root()
IDENTITY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
DEFAULT_SPARSE_PROFILE = "ml-experiment"
SPARSE_WORKTREE_PROFILES: dict[str, dict[str, object]] = {
    "ml-experiment": {
        "description": "Keep source/config/test/docs context while excluding bulky generated research payloads.",
        "include_patterns": ["/*"],
        "excluded_paths": [
            "/research_cockpit/",
            "/outputs/",
            "/logs/",
            "/data/",
            "/datasets/**/artifacts/",
            "/.venv/",
            "/.venvs/",
            "/venv/",
        ],
    },
}


def _git_worktree_command(
    repo_root: Path,
    branch: str,
    worktree: Path,
    base: str | None,
    *,
    no_checkout: bool = False,
) -> list[str]:
    command = ["git", "-C", str(repo_root), "worktree", "add", "-b", branch, str(worktree)]
    if no_checkout:
        command.insert(5, "--no-checkout")
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


def _command_plan_entry(command: list[str], *, stdin: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "argv": command,
        "shell": shell_join(command),
    }
    if stdin is not None:
        entry["stdin"] = stdin
    return entry


def _sparse_worktree_plan(
    repo_root: Path,
    *,
    branch: str,
    worktree: Path,
    base: str | None,
    profile_name: str,
) -> dict[str, Any]:
    profile = SPARSE_WORKTREE_PROFILES.get(profile_name)
    if profile is None:
        names = ", ".join(sorted(SPARSE_WORKTREE_PROFILES))
        raise ValueError(f"Unknown sparse profile {profile_name!r}; supported profiles: {names}")
    include_patterns = [str(item) for item in profile["include_patterns"]]
    excluded_paths = [str(item) for item in profile["excluded_paths"]]
    sparse_patterns = [*include_patterns, *[f"!{item}" for item in excluded_paths]]
    worktree_add = _git_worktree_command(repo_root, branch, worktree, base, no_checkout=True)
    sparse_set = ["git", "-C", str(worktree), "sparse-checkout", "set", "--no-cone", "--stdin"]
    return {
        "enabled": True,
        "mode": "manual_command_plan",
        "profile": profile_name,
        "description": profile["description"],
        "include_patterns": include_patterns,
        "excluded_paths": excluded_paths,
        "commands": [
            _command_plan_entry(worktree_add),
            _command_plan_entry(["git", "-C", str(worktree), "sparse-checkout", "init", "--no-cone"]),
            _command_plan_entry(sparse_set, stdin="\n".join(sparse_patterns) + "\n"),
            _command_plan_entry(["git", "-C", str(worktree), "checkout"]),
        ],
        "notes": [
            "This is a dry-run command plan; run it manually after review.",
            "Keep RESEARCH_COCKPIT_ROOT pointed at the canonical main checkout research_cockpit directory.",
            "The worktree remains disposable; ingest useful outputs before closeout.",
        ],
    }


def _identity_date() -> str:
    return today().replace("-", "")


def _label_slug(label: str | None, *, fallback: str = "agent") -> str:
    raw = str(label or fallback).strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return slug[:48].strip("_") or fallback


def _safe_identity_id(value: str, field_name: str) -> str:
    if not IDENTITY_ID_RE.fullmatch(value):
        raise ValueError(
            f"{field_name} must contain only letters, numbers, underscores, or hyphens, "
            "and must not contain path separators"
        )
    return value


def _agent_path(root: Path, agent_id: str) -> Path:
    return root / "agents" / f"{_safe_identity_id(agent_id, 'agent_id')}.yaml"


def _assignment_path(root: Path, assignment_id: str) -> Path:
    return root / "assignments" / f"{_safe_identity_id(assignment_id, 'assignment_id')}.yaml"


def _existing_assignment_id(
    assignments: dict[str, AssignmentRecord],
    *,
    agent_id: str,
    option_id: str,
) -> str | None:
    for assignment in assignments.values():
        if (
            assignment.agent_id == agent_id
            and assignment.root_node == option_id
            and assignment.status in ACTIVE_ASSIGNMENT_STATUSES
        ):
            return assignment.assignment_id
    return None


def _active_assignment_id_for_root(assignments: dict[str, AssignmentRecord], option_id: str) -> str | None:
    for assignment in assignments.values():
        if assignment.root_node == option_id and assignment.status in ACTIVE_ASSIGNMENT_STATUSES:
            return assignment.assignment_id
    return None


def _resolve_identity(
    root: Path,
    *,
    agents: dict[str, AgentRecord],
    assignments: dict[str, AssignmentRecord],
    label: str | None,
    agent_id: str | None,
    assignment_id: str | None,
) -> tuple[str, str]:
    date_text = _identity_date()
    if agent_id:
        agent_id = _safe_identity_id(agent_id, "agent_id")
    if assignment_id:
        assignment_id = _safe_identity_id(assignment_id, "assignment_id")
    slug = _label_slug(label or agent_id)
    for _ in range(100):
        token = secrets.token_hex(3)
        candidate_agent_id = agent_id or f"agent_{date_text}_{token}_{slug}"
        candidate_assignment_id = assignment_id or f"assign_{date_text}_{token}"
        agent_collision = (
            agent_id is None
            and (candidate_agent_id in agents or _agent_path(root, candidate_agent_id).exists())
        )
        assignment_collision = (
            assignment_id is None
            and (
                candidate_assignment_id in assignments
                or _assignment_path(root, candidate_assignment_id).exists()
            )
        )
        if not agent_collision and not assignment_collision:
            return candidate_agent_id, candidate_assignment_id
    raise ValueError("Could not generate unique agent and assignment ids")


def _load_existing_record(path: Path) -> dict[str, Any] | None:
    return load_yaml(path) if path.exists() else None


def _startup_command_args(root: Path, assignment_id: str) -> list[str]:
    return [
        "research-cockpit",
        "agent-session-context",
        "--root",
        str(root.resolve()),
        "--assignment",
        assignment_id,
        "--compact",
        "--json",
    ]


def _build_agent_data(
    before_agent: dict[str, Any] | None,
    *,
    agent_id: str,
    assignment_id: str,
    label: str | None,
    today_text: str,
) -> dict[str, Any]:
    agent_data = dict(before_agent or {})
    active_assignment_ids = (
        agent_data.get("active_assignment_ids")
        if isinstance(agent_data.get("active_assignment_ids"), list)
        else []
    )
    if assignment_id not in active_assignment_ids:
        active_assignment_ids = [*active_assignment_ids, assignment_id]
    agent_data.update({
        "agent_id": agent_id,
        "status": "active",
        "created_at": agent_data.get("created_at") or today_text,
        "last_seen_at": today_text,
        "active_assignment_ids": active_assignment_ids,
    })
    if label:
        agent_data.setdefault("label", _label_slug(label))
        agent_data.setdefault("display_name", label)
    return agent_data


def _previous_agent_assignment_update(
    root: Path,
    *,
    before_assignment: dict[str, Any] | None,
    assignment_id: str,
    agent_id: str,
    agent_path: Path,
) -> tuple[str, Path | None, dict[str, Any] | None, dict[str, Any] | None]:
    previous_assignment_agent_id = str((before_assignment or {}).get("agent_id") or "")
    if not previous_assignment_agent_id or previous_assignment_agent_id == agent_id:
        return previous_assignment_agent_id, None, None, None
    previous_agent_path = _agent_path(root, previous_assignment_agent_id)
    if previous_agent_path == agent_path:
        return previous_assignment_agent_id, None, None, None
    before_previous_agent = _load_existing_record(previous_agent_path)
    if not before_previous_agent:
        return previous_assignment_agent_id, previous_agent_path, before_previous_agent, None
    previous_agent_data = dict(before_previous_agent)
    previous_ids = previous_agent_data.get("active_assignment_ids")
    if isinstance(previous_ids, list):
        previous_agent_data["active_assignment_ids"] = [
            item for item in previous_ids if str(item) != assignment_id
        ]
    return previous_assignment_agent_id, previous_agent_path, before_previous_agent, previous_agent_data


def _build_assignment_data(
    before_assignment: dict[str, Any] | None,
    *,
    assignment_id: str,
    agent_id: str,
    option_id: str,
    objective: str,
    branch: str,
    resolved_worktree: Path,
    session_id: str,
    today_text: str,
) -> dict[str, Any]:
    assignment_data = dict(before_assignment or {})
    allowed_subtree = (
        dict(assignment_data["allowed_subtree"])
        if isinstance(assignment_data.get("allowed_subtree"), dict)
        else {}
    )
    allowed_subtree.update({"root": option_id, "policy": "descendants_only"})
    worktree = (
        dict(assignment_data["worktree"])
        if isinstance(assignment_data.get("worktree"), dict)
        else {}
    )
    worktree.update(
        {
            "branch": branch,
            "label": worktree_label(resolved_worktree),
            "session_id": session_id,
        }
    )
    assignment_data.update({
        "assignment_id": assignment_id,
        "agent_id": agent_id,
        "status": "active",
        "root_node": option_id,
        "current_node": assignment_data.get("current_node") or option_id,
        "allowed_subtree": allowed_subtree,
        "objective": objective,
        "worktree": worktree,
        "created_at": assignment_data.get("created_at") or today_text,
        "updated_at": today_text,
    })
    if not isinstance(assignment_data.get("next_actions"), list):
        assignment_data["next_actions"] = []
    return assignment_data


def _build_start_session_result(
    *,
    root: Path,
    dry_run: bool,
    option_id: str,
    agent_id: str,
    assignment_id: str,
    session_id: str,
    branch: str,
    resolved_worktree: Path,
    option_path: Path,
    agent_path: Path,
    assignment_path: Path,
    before_workstream: dict[str, Any] | None,
    before_agent: dict[str, Any] | None,
    before_assignment: dict[str, Any] | None,
    workstream_data: dict[str, Any],
    agent_data: dict[str, Any],
    assignment_data: dict[str, Any],
    boundary: dict[str, Any],
    git_command: list[str],
    sparse_worktree: dict[str, Any] | None,
    show_diff: bool,
    changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]],
) -> dict[str, Any]:
    launch_env = {
        "RESEARCH_COCKPIT_ROOT": str(root.resolve()),
        "RESEARCH_COCKPIT_AGENT_ID": agent_id,
        "RESEARCH_COCKPIT_ASSIGNMENT_ID": assignment_id,
    }
    startup_command_args = _startup_command_args(root, assignment_id)
    result: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "changed": not dry_run,
        "would_change": dry_run,
        "option_id": option_id,
        "agent_id": agent_id,
        "assignment_id": assignment_id,
        "session_id": session_id,
        "git_branch": branch,
        "worktree_label": worktree_label(resolved_worktree),
        "path": str(option_path),
        "agent_path": str(agent_path),
        "assignment_path": str(assignment_path),
        "before": {"agent_workstream": before_workstream, "agent": before_agent, "assignment": before_assignment},
        "after": {"agent_workstream": workstream_data, "agent": agent_data, "assignment": assignment_data},
        "root_boundary": boundary,
        "git_command": git_command,
        "launch_env": launch_env,
        "startup_command": shell_join(startup_command_args),
        "startup_command_args": startup_command_args,
        "handoff": session_handoff(
            root=root,
            agent_id=agent_id,
            assignment_id=assignment_id,
            option_id=option_id,
            worktree=resolved_worktree,
        ),
        "created_worktree": False,
    }
    if sparse_worktree is not None:
        result["sparse_worktree"] = sparse_worktree
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    return result


def start_agent_session(
    root: Path,
    *,
    option_id: str,
    objective: str,
    branch: str,
    worktree: Path,
    agent_id: str | None = None,
    assignment_id: str | None = None,
    label: str | None = None,
    base: str | None = None,
    create_worktree: bool = False,
    force: bool = False,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    sparse: bool = False,
    sparse_profile: str | None = None,
) -> dict[str, Any]:
    requested_agent_id = agent_id
    requested_assignment_id = assignment_id
    reused_assignment_id = False
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
    if sparse_profile and not sparse:
        raise ValueError("--sparse-profile requires --sparse")
    if sparse and not dry_run:
        raise ValueError("--sparse currently provides dry-run command planning only; pass --dry-run --json")
    if create_worktree and resolved_worktree.exists():
        raise ValueError(f"Worktree path already exists: {resolved_worktree}")

    option_path = find_node_file(root, option_id)
    data = load_yaml(option_path)
    before_data = copy.deepcopy(data)
    existing = data.get("agent_workstream") if isinstance(data.get("agent_workstream"), dict) else {}
    before_workstream = dict(existing) if existing else None
    existing_owner = str(existing.get("owner") or "")
    existing_status = str(existing.get("status") or "")
    agents = load_agents(root)
    assignments = load_assignments(root)
    if agent_id:
        existing_assignment_id = _existing_assignment_id(assignments, agent_id=agent_id, option_id=option_id)
        if not assignment_id and existing_assignment_id:
            assignment_id = existing_assignment_id
            reused_assignment_id = True
    if force:
        active_assignment_id = _active_assignment_id_for_root(assignments, option_id)
        if not assignment_id and active_assignment_id:
            assignment_id = active_assignment_id
            reused_assignment_id = True
    agent_id, assignment_id = _resolve_identity(
        root,
        agents=agents,
        assignments=assignments,
        label=label,
        agent_id=agent_id,
        assignment_id=assignment_id,
    )
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

    agent_path = _agent_path(root, agent_id)
    before_agent = _load_existing_record(agent_path)
    agent_data = _build_agent_data(
        before_agent,
        agent_id=agent_id,
        assignment_id=assignment_id,
        label=label,
        today_text=today_text,
    )

    assignment_path = _assignment_path(root, assignment_id)
    before_assignment = _load_existing_record(assignment_path)
    (
        previous_assignment_agent_id,
        previous_agent_path,
        before_previous_agent,
        previous_agent_data,
    ) = _previous_agent_assignment_update(
        root,
        before_assignment=before_assignment,
        assignment_id=assignment_id,
        agent_id=agent_id,
        agent_path=agent_path,
    )
    session_id = data["agent_workstream"]["session_id"]
    assignment_data = _build_assignment_data(
        before_assignment,
        assignment_id=assignment_id,
        agent_id=agent_id,
        option_id=option_id,
        objective=objective,
        branch=branch,
        resolved_worktree=resolved_worktree,
        session_id=session_id,
        today_text=today_text,
    )

    candidate = dict(nodes)
    candidate[option_id] = ResearchNode.from_dict(data)
    candidate_agents = dict(agents)
    candidate_agents[agent_id] = AgentRecord.from_dict(agent_data)
    if previous_agent_data and previous_assignment_agent_id:
        candidate_agents[previous_assignment_agent_id] = AgentRecord.from_dict(previous_agent_data)
    candidate_assignments = dict(assignments)
    candidate_assignments[assignment_id] = AssignmentRecord.from_dict(assignment_data)
    validate_cockpit(
        root,
        candidate,
        state.current,
        state.explicit_edges,
        agents=candidate_agents,
        assignments=candidate_assignments,
        raise_on_error=True,
    )

    sparse_worktree = (
        _sparse_worktree_plan(
            repo_root,
            branch=branch,
            worktree=resolved_worktree,
            base=base,
            profile_name=sparse_profile or DEFAULT_SPARSE_PROFILE,
        )
        if sparse
        else None
    )
    git_command = (
        list(sparse_worktree["commands"][0]["argv"])
        if sparse_worktree is not None
        else _git_worktree_command(repo_root, branch, resolved_worktree, base)
    )
    changes = [
        (option_path, before_data, data),
        (agent_path, before_agent, agent_data),
        (assignment_path, before_assignment, assignment_data),
    ]
    if previous_agent_path and previous_agent_data and before_previous_agent is not None:
        changes.append((previous_agent_path, before_previous_agent, previous_agent_data))
    result = _build_start_session_result(
        root=root,
        dry_run=dry_run,
        option_id=option_id,
        agent_id=agent_id,
        assignment_id=assignment_id,
        session_id=session_id,
        branch=branch,
        resolved_worktree=resolved_worktree,
        option_path=option_path,
        agent_path=agent_path,
        assignment_path=assignment_path,
        before_workstream=before_workstream,
        before_agent=before_agent,
        before_assignment=before_assignment,
        workstream_data=data["agent_workstream"],
        agent_data=agent_data,
        assignment_data=assignment_data,
        boundary=boundary,
        git_command=git_command,
        sparse_worktree=sparse_worktree,
        show_diff=show_diff,
        changes=changes,
    )
    if dry_run:
        result["changed"] = False
        result["identity_preview"] = {
            "preview_only": True,
            "agent_id_reserved": bool(requested_agent_id),
            "assignment_id_reserved": bool(requested_assignment_id or reused_assignment_id),
            "generated_ids_are_not_reserved": not bool(
                requested_agent_id and (requested_assignment_id or reused_assignment_id)
            ),
            "stable_identity_hint": (
                "Dry-run previews generated ids only; pass explicit --agent and --assignment "
                "when executing if you need the same ids."
            ),
        }
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
                    "--assignment",
                    assignment_id,
                    "--branch",
                    branch,
                ),
                "before": result["before"],
                "after": result["after"],
                "extra": {
                    "option_id": option_id,
                    "agent_id": agent_id,
                    "assignment_id": assignment_id,
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
                    "--assignment",
                    assignment_id,
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
    parser.add_argument("--agent", dest="agent_id")
    parser.add_argument(
        "--assignment",
        "--assignment-id",
        dest="assignment_id",
        help="Use an explicit assignment id; useful when executing after a dry-run preview.",
    )
    parser.add_argument("--label")
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
    parser.add_argument("--sparse", action="store_true")
    parser.add_argument("--sparse-profile")
    args = parser.parse_args()

    try:
        payload = start_agent_session(
            args.root,
            option_id=args.option_id,
            agent_id=args.agent_id,
            assignment_id=args.assignment_id,
            label=args.label,
            objective=args.objective,
            branch=args.branch,
            worktree=args.worktree,
            base=args.base,
            create_worktree=args.create_worktree,
            force=args.force,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
            sparse=args.sparse,
            sparse_profile=args.sparse_profile,
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
        safe_print(f"Would start session for {payload['agent_id']} on {args.option_id}.")
        if args.sparse:
            safe_print("Sparse worktree command plan is available in JSON at sparse_worktree.commands; rerun with --json.")
    else:
        safe_print(f"Started session for {payload['agent_id']} on {args.option_id}.")


if __name__ == "__main__":
    main()
