from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import re
import shlex
from typing import Any

from research_cockpit.graph_core import derive_focus_path, node_id_by_type_in_path
from research_cockpit.model import ACTIVE_ASSIGNMENT_STATUSES, AssignmentRecord, load_assignments
from research_cockpit.option_workstreams import build_option_workstream_context
from research_cockpit.types import ACTIVE_WORKSTREAM_STATUSES, ResearchNode


def stable_session_id(agent_id: str, option_id: str) -> str:
    raw = f"session_{agent_id}_{option_id}"
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    return safe or "session_agent"


def _shell_quote_arg(value: str) -> str:
    if os.name != "nt":
        return shlex.quote(value)
    return "'" + value.replace("'", "''") + "'"


def shell_join(args: list[str]) -> str:
    if not args:
        return ""
    return " ".join([args[0], *[_shell_quote_arg(str(arg)) for arg in args[1:]]])


def worktree_label(worktree: Path | None) -> str | None:
    if worktree is None:
        return None
    text = str(worktree).strip()
    if not text:
        return None
    return Path(text).name


def path_contains(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def worktree_boundary(root: Path, worktree: Path | None) -> dict[str, Any]:
    root_resolved = root.resolve()
    payload: dict[str, Any] = {
        "canonical_root": str(root_resolved),
        "required_root": str(root_resolved),
        "do_not_mutate_worktree_root": True,
    }
    if worktree is None:
        return payload

    worktree_resolved = worktree.resolve()
    payload.update({
        "worktree_path": str(worktree_resolved),
        "worktree_label": worktree_label(worktree),
        "worktree_research_cockpit": str(worktree_resolved / "research_cockpit"),
    })
    if path_contains(worktree_resolved, root_resolved):
        payload["boundary_error"] = (
            "canonical --root is inside the worktree; use the main repository research_cockpit root"
        )
    elif (worktree_resolved / "research_cockpit").exists():
        payload["boundary_error"] = (
            "worktree contains research_cockpit; do not let the downstream agent mutate that local root"
        )
    return payload


def ensure_worktree_boundary(root: Path, worktree: Path | None) -> dict[str, Any]:
    boundary = worktree_boundary(root, worktree)
    if boundary.get("boundary_error"):
        raise ValueError(str(boundary["boundary_error"]))
    return boundary


def nearest_problem_id(nodes: dict[str, ResearchNode], option_id: str) -> str | None:
    for node_id in reversed(derive_focus_path(nodes, option_id)):
        if nodes[node_id].type == "problem":
            return node_id
    return None


def option_for_focus(nodes: dict[str, ResearchNode], node_id: str) -> str | None:
    path = derive_focus_path(nodes, node_id)
    return node_id_by_type_in_path(nodes, path, "option", nearest=True)


def session_handoff(
    *,
    root: Path,
    agent_id: str,
    option_id: str,
    assignment_id: str | None = None,
    worktree: Path | None = None,
) -> dict[str, Any]:
    root_text = str(root.resolve())
    stable_artifact_root = str((root.resolve() / "artifacts"))
    launch_env = {
        "RESEARCH_COCKPIT_ROOT": root_text,
        "RESEARCH_COCKPIT_AGENT_ID": agent_id,
    }
    if assignment_id:
        launch_env["RESEARCH_COCKPIT_ASSIGNMENT_ID"] = assignment_id
    read_context_args = [
        "research-cockpit",
        "agent-session-context",
        "--root",
        root_text,
    ]
    if assignment_id:
        read_context_args.extend(["--assignment", assignment_id])
    else:
        read_context_args.extend(["--agent", agent_id])
    read_context_args.extend(["--compact", "--json"])
    commands = {
        "read_context": shell_join(read_context_args),
        "option_context": shell_join([
            "research-cockpit",
            "option-workstream-context",
            "--root",
            root_text,
            "--id",
            option_id,
            "--compact",
            "--json",
        ]),
        "ingest_artifact": shell_join([
            "research-cockpit",
            "ingest-artifact",
            "--root",
            root_text,
            *(["--assignment", assignment_id] if assignment_id else []),
            "--node",
            "<node_id>",
            "--from",
            "<worktree_output_dir>",
            "--run-id",
            "<run_id>",
            "--agent",
            agent_id,
            "--json",
            "--compact",
        ]),
    }
    if assignment_id:
        commands["set_cursor"] = shell_join([
            "research-cockpit",
            "set-cursor",
            "--root",
            root_text,
            "--assignment",
            assignment_id,
            "--node",
            "<node_id>",
            "--no-build",
        ])
    else:
        commands["set_agent_focus"] = shell_join([
            "research-cockpit",
            "set-agent-focus",
            "--root",
            root_text,
            "--agent",
            agent_id,
            "--node",
            "<node_id>",
            "--no-build",
        ])
    return {
        "launch_env": launch_env,
        "stable_artifact_root": stable_artifact_root,
        "guardrails": [
            "Use the worktree only for code and experiment files.",
            "Do not run research-cockpit init in the worktree.",
            "Do not mutate a worktree-local research_cockpit directory.",
            "Write research state only through the canonical --root shown here.",
            "Do not use worktree paths as long-lived evidence paths; ingest outputs into the stable artifact root first.",
        ],
        "commands": commands,
        "worktree": str(worktree.resolve()) if worktree else None,
    }


def active_session_options(
    nodes: dict[str, ResearchNode],
    *,
    agent_id: str | None = None,
    option_id: str | None = None,
) -> list[str]:
    matches: list[str] = []
    for node in nodes.values():
        if node.type != "option":
            continue
        if option_id and node.id != option_id:
            continue
        workstream = node.raw.get("agent_workstream")
        if not isinstance(workstream, dict):
            continue
        if agent_id and workstream.get("owner") != agent_id:
            continue
        if not option_id and workstream.get("status") not in ACTIVE_WORKSTREAM_STATUSES:
            continue
        matches.append(node.id)
    return sorted(
        matches,
        key=lambda item: str(
            nodes[item].raw.get("agent_workstream", {}).get("updated_at")
            or nodes[item].raw.get("agent_workstream", {}).get("started_at")
            or ""
        ),
        reverse=True,
    )


def build_agent_session_context(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    *,
    agent_id: str | None = None,
    assignment_id: str | None = None,
    option_id: str | None = None,
) -> dict[str, Any]:
    assignment: AssignmentRecord | None = None
    if assignment_id or agent_id:
        assignments = load_assignments(root)
        if assignment_id:
            assignment = assignments.get(assignment_id)
            if assignment is None:
                raise ValueError(f"Assignment does not exist: {assignment_id}")
        elif agent_id:
            matches = [
                item
                for item in assignments.values()
                if item.agent_id == agent_id
                and item.status in ACTIVE_ASSIGNMENT_STATUSES
                and (option_id is None or item.root_node == option_id)
            ]
            if len(matches) > 1:
                raise ValueError(f"Multiple active assignments exist for agent {agent_id!r}; pass --assignment.")
            assignment = matches[0] if matches else None
    if assignment:
        if agent_id and assignment.agent_id != agent_id:
            raise ValueError(f"Assignment {assignment_id} is owned by {assignment.agent_id!r}, not {agent_id!r}")
        if option_id and assignment.root_node != option_id:
            raise ValueError(f"Assignment {assignment_id} root_node is {assignment.root_node!r}, not {option_id!r}")
        agent_id = assignment.agent_id
        option_id = assignment.root_node

    options = active_session_options(nodes, agent_id=agent_id, option_id=option_id)
    if assignment and assignment.root_node in nodes and assignment.root_node not in options:
        options = [assignment.root_node, *options]
    if not options:
        label = f"agent {agent_id!r}" if agent_id else f"option {option_id!r}"
        raise ValueError(f"No active agent session found for {label}")
    selected_option_id = option_id or options[0]
    option = nodes[selected_option_id]
    workstream = option.raw.get("agent_workstream") if isinstance(option.raw.get("agent_workstream"), dict) else {}
    owner = agent_id or workstream.get("owner")
    agent_focus = None
    assignment_cursor = None
    if assignment:
        current_path = derive_focus_path(nodes, assignment.current_node)
        assignment_cursor = {
            "assignment_id": assignment.assignment_id,
            "root_node": assignment.root_node,
            "current_node": assignment.current_node,
            "current_path": current_path,
            "current_option": option_for_focus(nodes, assignment.current_node),
            "next_actions": list(assignment.next_actions),
            "status": assignment.status,
            "updated_at": assignment.updated_at,
            "source": "assignment",
        }
        agent_focus = {
            "source": "assignment",
            "assignment_id": assignment.assignment_id,
            "current_focus_node": assignment.current_node,
            "current_focus_path": current_path,
            "current_option": assignment_cursor["current_option"],
            "next_actions": list(assignment.next_actions),
            "updated_at": assignment.updated_at,
        }
    elif owner:
        focuses = current.get("agent_focuses") if isinstance(current.get("agent_focuses"), dict) else {}
        focus = focuses.get(str(owner))
        agent_focus = focus if isinstance(focus, dict) else None
    context = build_option_workstream_context(root, nodes, current, selected_option_id)
    return {
        "agent_id": owner,
        "assignment_id": assignment.assignment_id if assignment else None,
        "option_id": selected_option_id,
        "matching_options": options,
        "session": workstream,
        "canonical_root": str(root.resolve()),
        "required_root": str(root.resolve()),
        "do_not_mutate_worktree_root": True,
        "stable_artifact_root": str((root.resolve() / "artifacts")),
        "worktree_boundary": {
            "canonical_root": str(root.resolve()),
            "required_root": str(root.resolve()),
            "do_not_mutate_worktree_root": True,
            "worktree_label": workstream.get("worktree_label"),
        },
        "assignment": {
            "assignment_id": assignment.assignment_id,
            "agent_id": assignment.agent_id,
            "status": assignment.status,
            "root_node": assignment.root_node,
            "current_node": assignment.current_node,
            "next_actions": list(assignment.next_actions),
            "objective": assignment.objective,
            "updated_at": assignment.updated_at,
        } if assignment else None,
        "assignment_cursor": assignment_cursor,
        "agent_focus": agent_focus,
        "option_context": context,
        "handoff": session_handoff(
            root=root,
            agent_id=str(owner or "<agent_id>"),
            assignment_id=assignment.assignment_id if assignment else assignment_id,
            option_id=selected_option_id,
        ),
    }


def today() -> str:
    return str(date.today())
