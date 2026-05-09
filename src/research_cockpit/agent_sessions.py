from __future__ import annotations

from datetime import date
from pathlib import Path
import re
from typing import Any

from research_cockpit.graph_core import derive_focus_path, node_id_by_type_in_path
from research_cockpit.option_workstreams import build_option_workstream_context
from research_cockpit.types import ACTIVE_WORKSTREAM_STATUSES, ResearchNode


def stable_session_id(agent_id: str, option_id: str) -> str:
    raw = f"session_{agent_id}_{option_id}"
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    return safe or "session_agent"


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
    worktree: Path | None = None,
) -> dict[str, Any]:
    root_text = str(root.resolve())
    stable_artifact_root = str((root.resolve() / "artifacts"))
    return {
        "launch_env": {"RESEARCH_COCKPIT_ROOT": root_text},
        "stable_artifact_root": stable_artifact_root,
        "guardrails": [
            "Use the worktree only for code and experiment files.",
            "Do not run research-cockpit init in the worktree.",
            "Do not mutate a worktree-local research_cockpit directory.",
            "Write research state only through the canonical --root shown here.",
            "Do not use worktree paths as long-lived evidence paths; ingest outputs into the stable artifact root first.",
        ],
        "commands": {
            "read_context": (
                f"research-cockpit agent-session-context --root {root_text} "
                f"--agent {agent_id} --compact --json"
            ),
            "set_agent_focus": (
                f"research-cockpit set-agent-focus --root {root_text} "
                f"--agent {agent_id} --node <node_id> --no-build"
            ),
            "option_context": (
                f"research-cockpit option-workstream-context --root {root_text} "
                f"--id {option_id} --compact --json"
            ),
            "ingest_artifact": (
                f"research-cockpit ingest-artifact --root {root_text} "
                "--node <node_id> --from <worktree_output_dir> --run-id <run_id> "
                "--agent "
                f"{agent_id} --json --compact"
            ),
        },
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
    option_id: str | None = None,
) -> dict[str, Any]:
    options = active_session_options(nodes, agent_id=agent_id, option_id=option_id)
    if not options:
        label = f"agent {agent_id!r}" if agent_id else f"option {option_id!r}"
        raise ValueError(f"No active agent session found for {label}")
    selected_option_id = option_id or options[0]
    option = nodes[selected_option_id]
    workstream = option.raw.get("agent_workstream") if isinstance(option.raw.get("agent_workstream"), dict) else {}
    owner = agent_id or workstream.get("owner")
    agent_focus = None
    if owner:
        focuses = current.get("agent_focuses") if isinstance(current.get("agent_focuses"), dict) else {}
        focus = focuses.get(str(owner))
        agent_focus = focus if isinstance(focus, dict) else None
    context = build_option_workstream_context(root, nodes, current, selected_option_id)
    return {
        "agent_id": owner,
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
        "agent_focus": agent_focus,
        "option_context": context,
        "handoff": session_handoff(
            root=root,
            agent_id=str(owner or "<agent_id>"),
            option_id=selected_option_id,
        ),
    }


def today() -> str:
    return str(date.today())
