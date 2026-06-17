from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from research_cockpit.model import (
    ACTIVE_ASSIGNMENT_STATUSES,
    ResearchNode,
    RunRecord,
    load_assignments,
    load_explicit_edges,
    load_nodes,
    load_runs,
    load_yaml,
    validate_cockpit,
)
from research_cockpit.run_summaries import ACTIVE_RUN_STATUSES

ACTIVE_WORKSTREAM_STATUSES = {"claimed", "in_progress", "blocked"}


def _git_output(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise ValueError(f"git -C {repo} {' '.join(args)}: {message}")
    return completed.stdout


def _short_branch(ref: str) -> str:
    prefix = "refs/heads/"
    return ref[len(prefix) :] if ref.startswith(prefix) else ref


def parse_worktree_porcelain(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                rows.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
            current["label"] = Path(value).name
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = _short_branch(value)
        elif key == "bare":
            current["bare"] = True
        elif key == "detached":
            current["detached"] = True
        elif key == "locked":
            current["locked"] = value or True
        elif key == "prunable":
            current["prunable"] = value or True
    if current:
        rows.append(current)
    return rows


def _load_validated(root: Path) -> tuple[dict[str, ResearchNode], dict[str, Any], dict[str, RunRecord]]:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    runs = load_runs(root)
    assignments = load_assignments(root)
    validate_cockpit(root, nodes, current, explicit_edges, runs=runs, assignments=assignments, raise_on_error=True)
    return nodes, assignments, runs


def _children_by_parent(nodes: dict[str, ResearchNode]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {}
    for node in nodes.values():
        if node.parent:
            children.setdefault(node.parent, []).append(node.id)
    return children


def _subtree_ids(nodes: dict[str, ResearchNode], root_id: str) -> set[str]:
    children = _children_by_parent(nodes)
    seen: set[str] = set()
    stack = [root_id]
    while stack:
        node_id = stack.pop()
        if node_id in seen or node_id not in nodes:
            continue
        seen.add(node_id)
        stack.extend(children.get(node_id, []))
    return seen


def _matches_branch_or_label(record: dict[str, Any], *, branch: str | None, label: str | None) -> bool:
    return bool((branch and record.get("branch") == branch) or (label and record.get("label") == label))


def _matching_assignments(assignments: dict[str, Any], *, branch: str | None, label: str | None) -> list[Any]:
    return [
        assignment
        for assignment in assignments.values()
        if _matches_branch_or_label(assignment.worktree, branch=branch, label=label)
    ]


def _option_workstream_nodes(
    nodes: dict[str, ResearchNode],
    *,
    branch: str | None,
    label: str | None,
) -> list[ResearchNode]:
    matches: list[ResearchNode] = []
    for node in nodes.values():
        workstream = node.raw.get("agent_workstream")
        if not isinstance(workstream, dict):
            continue
        record = {
            "branch": workstream.get("git_branch"),
            "label": workstream.get("worktree_label"),
        }
        if _matches_branch_or_label(record, branch=branch, label=label):
            matches.append(node)
    return sorted(matches, key=lambda item: item.id)


def _active_option_workstream_nodes(
    nodes: dict[str, ResearchNode],
    *,
    branch: str | None,
    label: str | None,
) -> list[ResearchNode]:
    return [
        node
        for node in _option_workstream_nodes(nodes, branch=branch, label=label)
        if str(node.raw.get("agent_workstream", {}).get("status", "")) in ACTIVE_WORKSTREAM_STATUSES
    ]


def _run_statuses_for_nodes(
    *,
    nodes: dict[str, ResearchNode],
    runs: dict[str, RunRecord],
    root_node_ids: set[str],
) -> list[dict[str, str]]:
    scoped_ids: set[str] = set()
    for node_id in root_node_ids:
        scoped_ids.update(_subtree_ids(nodes, node_id))
    experiment_ids = {node_id for node_id in scoped_ids if nodes[node_id].type == "experiment"}
    return [
        {"run_id": run.run_id, "status": run.status}
        for run in sorted(runs.values(), key=lambda item: item.run_id)
        if run.experiment_id in experiment_ids
    ]


def _evidence_node_ids(nodes: dict[str, ResearchNode], node_ids: set[str]) -> list[str]:
    evidence: set[str] = set()
    scoped_ids: set[str] = set()
    for node_id in node_ids:
        scoped_ids.update(_subtree_ids(nodes, node_id))
    for node_id in scoped_ids:
        node = nodes.get(node_id)
        if not node:
            continue
        linked = node.raw.get("linked_artifacts")
        if isinstance(linked, list):
            evidence.update(str(item) for item in linked)
    return sorted(evidence)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left == right


def _worktree_dirty(path: Path) -> bool:
    return bool(_git_output(path, "-c", "status.refreshIndex=false", "status", "--porcelain").strip())


def build_worktree_audit(
    root: Path,
    *,
    repo: Path,
    include_nested: list[Path] | None = None,
) -> dict[str, Any]:
    nodes, assignments, runs = _load_validated(root)
    rows = parse_worktree_porcelain(_git_output(repo, "worktree", "list", "--porcelain"))
    worktrees: list[dict[str, Any]] = []
    for row in rows:
        branch = row.get("branch")
        label = row.get("label")
        path = Path(str(row["path"]))
        matching_assignments = _matching_assignments(assignments, branch=branch, label=label)
        active_assignments = [
            assignment
            for assignment in matching_assignments
            if assignment.status in ACTIVE_ASSIGNMENT_STATUSES
        ]
        option_workstreams = _option_workstream_nodes(nodes, branch=branch, label=label)
        root_node_ids = {assignment.root_node for assignment in matching_assignments}
        root_node_ids.update(node.id for node in option_workstreams)
        active_node_ids = sorted(
            {
                node_id
                for assignment in active_assignments
                for node_id in (assignment.root_node, assignment.current_node)
                if node_id
            }
        )
        run_statuses = _run_statuses_for_nodes(nodes=nodes, runs=runs, root_node_ids=root_node_ids)
        blockers: list[str] = []
        dirty = False if row.get("bare") or row.get("locked") or row.get("prunable") else _worktree_dirty(path)
        if _same_path(path, repo):
            blockers.append("primary_worktree")
        if row.get("bare"):
            blockers.append("bare_worktree")
        if row.get("locked"):
            blockers.append("locked_worktree")
        if row.get("prunable"):
            blockers.append("prunable_worktree")
        if dirty:
            blockers.append("dirty_worktree")
        if active_assignments:
            blockers.append("active_assignment")
        active_workstreams = [
            node
            for node in option_workstreams
            if str(node.raw.get("agent_workstream", {}).get("status", "")) in ACTIVE_WORKSTREAM_STATUSES
        ]
        if active_workstreams:
            blockers.append("active_workstream")
        if any(item["status"] in ACTIVE_RUN_STATUSES for item in run_statuses):
            blockers.append("active_run")
        worktrees.append(
            {
                **row,
                "path": str(path),
                "branch": branch,
                "dirty": dirty,
                "active_assignment_ids": [assignment.assignment_id for assignment in active_assignments],
                "assignment_ids": [assignment.assignment_id for assignment in matching_assignments],
                "option_workstream_node_ids": [node.id for node in option_workstreams],
                "active_workstream_node_ids": [node.id for node in active_workstreams],
                "active_node_ids": active_node_ids,
                "run_statuses": run_statuses,
                "blockers": blockers,
                "safe_to_remove": not blockers,
            }
        )

    nested = [
        build_worktree_audit(root, repo=nested_repo, include_nested=[])
        for nested_repo in (include_nested or [])
    ]
    return {
        "ok": True,
        "schema_version": "worktree_audit_v1",
        "root": str(root),
        "repo": str(repo),
        "worktree_count": len(worktrees),
        "worktrees": worktrees,
        "nested": nested,
    }


def _branch_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _branch_class(name: str, base: str) -> str:
    if name == base or name in {"main", "master"}:
        return "main"
    if name.startswith("research/"):
        return "research"
    if name.startswith("agent/"):
        return "agent"
    if name.startswith("codex/"):
        return "temporary"
    if name.startswith("archive/"):
        return "archive"
    return "local"


def _recommended_branch_action(
    *,
    name: str,
    base: str,
    branch_class: str,
    checked_out: bool,
    merged: bool,
    active_assignment_ids: list[str],
    active_workstream_node_ids: list[str],
    option_workstream_node_ids: list[str],
    evidence_count: int,
) -> str:
    if name == base or branch_class == "main":
        return "keep_base"
    if checked_out or active_assignment_ids or active_workstream_node_ids:
        return "keep_active"
    if branch_class == "research":
        return "keep_research"
    if merged and branch_class in {"agent", "temporary", "local"}:
        return "delete_candidate"
    if not merged and (option_workstream_node_ids or evidence_count > 0):
        return "preserve_as_research_candidate"
    if not merged and branch_class in {"agent", "temporary"}:
        return "review_unmerged"
    return "review"


def build_branch_audit(root: Path, *, repo: Path, base: str) -> dict[str, Any]:
    nodes, assignments, _runs = _load_validated(root)
    worktrees = parse_worktree_porcelain(_git_output(repo, "worktree", "list", "--porcelain"))
    checked_out_by: dict[str, list[str]] = {}
    for worktree in worktrees:
        branch = worktree.get("branch")
        if branch:
            checked_out_by.setdefault(branch, []).append(str(worktree.get("path", "")))

    local_branches = _branch_lines(_git_output(repo, "branch", "--format=%(refname:short)"))
    merged = set(_branch_lines(_git_output(repo, "branch", "--merged", base, "--format=%(refname:short)")))
    branches: list[dict[str, Any]] = []
    for branch in sorted(local_branches):
        branch_assignments = [
            assignment
            for assignment in assignments.values()
            if assignment.worktree.get("branch") == branch
        ]
        active_assignment_ids = [
            assignment.assignment_id
            for assignment in branch_assignments
            if assignment.status in ACTIVE_ASSIGNMENT_STATUSES
        ]
        option_workstreams = _option_workstream_nodes(nodes, branch=branch, label=None)
        active_workstreams = _active_option_workstream_nodes(nodes, branch=branch, label=None)
        scoped_node_ids = {assignment.root_node for assignment in branch_assignments}
        scoped_node_ids.update(node.id for node in option_workstreams)
        evidence_ids = _evidence_node_ids(nodes, scoped_node_ids)
        branch_class = _branch_class(branch, base)
        checked_out = branch in checked_out_by
        is_merged = branch in merged
        blockers: list[str] = []
        if checked_out:
            blockers.append("checked_out")
        if active_assignment_ids:
            blockers.append("active_assignment")
        if active_workstreams:
            blockers.append("active_workstream")
        if not is_merged and branch_class in {"agent", "temporary", "local"}:
            blockers.append("unmerged")
        recommended_action = _recommended_branch_action(
            name=branch,
            base=base,
            branch_class=branch_class,
            checked_out=checked_out,
            merged=is_merged,
            active_assignment_ids=active_assignment_ids,
            active_workstream_node_ids=[node.id for node in active_workstreams],
            option_workstream_node_ids=[node.id for node in option_workstreams],
            evidence_count=len(evidence_ids),
        )
        branches.append(
            {
                "name": branch,
                "branch_class": branch_class,
                "checked_out": checked_out,
                "checked_out_worktrees": checked_out_by.get(branch, []),
                "merged": is_merged,
                "assignment_ids": [assignment.assignment_id for assignment in branch_assignments],
                "active_assignment_ids": active_assignment_ids,
                "option_workstream_node_ids": [node.id for node in option_workstreams],
                "active_workstream_node_ids": [node.id for node in active_workstreams],
                "evidence_node_ids": evidence_ids,
                "evidence_count": len(evidence_ids),
                "blockers": blockers,
                "recommended_action": recommended_action,
                "delete_candidate": recommended_action == "delete_candidate",
            }
        )

    return {
        "ok": True,
        "schema_version": "branch_audit_v1",
        "root": str(root),
        "repo": str(repo),
        "base": base,
        "branch_count": len(branches),
        "branches": branches,
    }
