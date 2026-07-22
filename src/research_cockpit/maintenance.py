from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Any
from urllib.parse import urlparse

from research_cockpit.model import (
    ACTIVE_ASSIGNMENT_STATUSES,
    AssignmentRecord,
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
RETENTION_CLEANUP_CLASSES = {"disposable_cache", "reproducible_output", "deprecated_payload"}
DEFAULT_GIT_STATUS_BYTES = 16 * 1024
DEFAULT_AUDIT_CANDIDATE_LIMIT = 10
MAX_AUDIT_CANDIDATE_LIMIT = 50
COMPACT_AUDIT_RESULT_BYTES = 15 * 1024
AUDIT_CANDIDATE_CLASSIFICATIONS = {
    "must_keep",
    "can_migrate",
    "can_quarantine",
    "needs_review",
}
_AUDIT_CLASSIFICATION_ORDER = {
    "needs_review": 0,
    "can_migrate": 1,
    "must_keep": 2,
    "can_quarantine": 3,
}
_MAX_AUDIT_TEXT_BYTES = 160
_MAX_AUDIT_DIMENSIONS = 12
_MAX_AUDIT_REASONS = 8
WORKTREE_CLOSEOUT_CLASSIFICATIONS = {
    "merge_to_main",
    "preserve_as_research_branch",
    "extract_partial",
    "discard_after_recording",
}


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


def _git_limited_output(
    repo: Path,
    *args: str,
    max_bytes: int,
) -> tuple[bytes, bool]:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    process = subprocess.Popen(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    output = process.stdout.read(max_bytes + 1)
    truncated = len(output) > max_bytes
    if truncated:
        process.terminate()
        process.communicate()
        return output[:max_bytes], True
    _stdout, stderr = process.communicate()
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="replace").strip() or "git command failed"
        raise ValueError(f"git -C {repo} {' '.join(args)}: {message}")
    return output, False


def _git_pathspec(repo_root: Path, path: Path) -> str | None:
    try:
        relative = path.resolve(strict=False).relative_to(repo_root.resolve(strict=False))
    except ValueError:
        return None
    return relative.as_posix() or "."


def _containing_worktree(
    path: Path,
    worktrees: list[dict[str, Any]],
) -> tuple[Path, str, str] | None:
    matches: list[tuple[int, Path, str, str]] = []
    for row in worktrees:
        raw_path = row.get("path")
        if not raw_path:
            continue
        worktree_path = Path(str(raw_path))
        pathspec = _git_pathspec(worktree_path, path)
        if pathspec is None:
            continue
        label = str(row.get("label") or worktree_path.name)
        matches.append((len(worktree_path.resolve(strict=False).parts), worktree_path, label, pathspec))
    if not matches:
        return None
    _depth, worktree_path, label, pathspec = max(matches, key=lambda item: item[0])
    return worktree_path, label, pathspec


def _git_check_ignore(repo: Path, pathspec: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "--no-index", "-q", "--", pathspec],
        capture_output=True,
        check=False,
    )
    if completed.returncode in {0, 1}:
        return completed.returncode == 0
    message = completed.stderr.decode("utf-8", errors="replace").strip() or "git command failed"
    raise ValueError(f"git -C {repo} check-ignore: {message}")


def _git_has_tracked_path(repo: Path, pathspec: str) -> bool:
    output, _truncated = _git_limited_output(
        repo,
        "ls-files",
        "-z",
        "--",
        pathspec,
        max_bytes=1,
    )
    return bool(output)


def _collapse_pathspecs(paths: list[str]) -> list[str]:
    selected: list[str] = []
    for pathspec in sorted(set(paths), key=lambda value: (value.count("/"), value)):
        if pathspec == ".":
            return [pathspec]
        if any(pathspec == existing or pathspec.startswith(f"{existing.rstrip('/')}/") for existing in selected):
            continue
        selected.append(pathspec)
    return selected


def _status_count_payload(count: int, *, exact: bool) -> dict[str, Any]:
    return {
        "count": count,
        "exact": exact,
        "lower_bound": not exact,
    }


def _bounded_git_status(
    repo: Path,
    *,
    pathspecs: list[str],
    max_bytes: int,
    deep: bool,
) -> dict[str, Any]:
    if not pathspecs:
        empty = _status_count_payload(0, exact=True)
        return {
            "deep": deep,
            "truncated": False,
            "bytes_read": 0,
            "byte_limit": None if deep else max_bytes,
            "untracked": dict(empty),
            "ignored": dict(empty),
            "tracked_modified": dict(empty),
        }
    args = (
        "-c",
        "status.showUntrackedFiles=normal",
        "status",
        "--porcelain=v1",
        "-z",
        "--ignored=matching",
        "--untracked-files=normal",
        "--",
        *pathspecs,
    )
    if deep:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", errors="replace").strip() or "git command failed"
            raise ValueError(f"git -C {repo} {' '.join(args)}: {message}")
        output = completed.stdout
        truncated = False
    else:
        output, truncated = _git_limited_output(repo, *args, max_bytes=max_bytes)
    counts = {"untracked": 0, "ignored": 0, "tracked_modified": 0}
    records = output.split(b"\0")
    complete_records = records if output.endswith(b"\0") else records[:-1]
    for record in complete_records:
        if len(record) < 2:
            continue
        prefix = record[:2]
        if prefix == b"??":
            counts["untracked"] += 1
        elif prefix == b"!!":
            counts["ignored"] += 1
        elif prefix[:1] not in {b"?", b"!"}:
            counts["tracked_modified"] += 1
    exact = not truncated
    return {
        "deep": deep,
        "truncated": truncated,
        "bytes_read": len(output),
        "byte_limit": None if deep else max_bytes,
        "untracked": _status_count_payload(counts["untracked"], exact=exact),
        "ignored": _status_count_payload(counts["ignored"], exact=exact),
        "tracked_modified": _status_count_payload(
            counts["tracked_modified"],
            exact=exact,
        ),
    }


def _bounded_git_status_groups(
    groups: dict[Path, list[str]],
    *,
    max_bytes: int,
    deep: bool,
) -> dict[str, Any]:
    if not groups:
        return _bounded_git_status(
            Path("."),
            pathspecs=[],
            max_bytes=max_bytes,
            deep=deep,
        )
    results: list[dict[str, Any]] = []
    remaining = max_bytes
    skipped_group_count = 0
    grouped = sorted(groups.items(), key=lambda item: str(item[0]))
    for index, (worktree, pathspecs) in enumerate(grouped):
        if not deep and remaining <= 0:
            skipped_group_count = len(grouped) - index
            break
        groups_left = len(grouped) - index
        budget = max_bytes if deep else max(1, remaining // groups_left)
        result = _bounded_git_status(
            worktree,
            pathspecs=_collapse_pathspecs(pathspecs),
            max_bytes=budget,
            deep=deep,
        )
        results.append(result)
        if not deep:
            remaining = max(0, remaining - int(result["bytes_read"]))
    truncated = skipped_group_count > 0 or any(bool(row.get("truncated")) for row in results)
    counts: dict[str, int] = {
        name: sum(_non_negative_count(_mapping(row.get(name)).get("count")) for row in results)
        for name in ("untracked", "ignored", "tracked_modified")
    }
    exact = not truncated and all(
        bool(_mapping(row.get(name)).get("exact"))
        for row in results
        for name in ("untracked", "ignored", "tracked_modified")
    )
    return {
        "deep": deep,
        "truncated": truncated,
        "bytes_read": sum(_non_negative_count(row.get("bytes_read")) for row in results),
        "byte_limit": None if deep else max_bytes,
        "worktree_group_count": len(grouped),
        "skipped_worktree_group_count": skipped_group_count,
        "untracked": _status_count_payload(counts["untracked"], exact=exact),
        "ignored": _status_count_payload(counts["ignored"], exact=exact),
        "tracked_modified": _status_count_payload(counts["tracked_modified"], exact=exact),
    }


def build_git_hygiene_summary(
    root: Path,
    *,
    repo: Path,
    max_status_bytes: int = DEFAULT_GIT_STATUS_BYTES,
    deep: bool = False,
) -> dict[str, Any]:
    if max_status_bytes <= 0:
        raise ValueError("max_status_bytes must be positive")
    repo_root = Path(_git_output(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    worktrees = parse_worktree_porcelain(_git_output(repo, "worktree", "list", "--porcelain"))
    if not any(_same_path(Path(str(row.get("path") or "")), repo_root) for row in worktrees):
        worktrees.append({"path": str(repo_root), "label": repo_root.name})
    from research_cockpit.storage_layout import resolve_storage_layout

    layout = resolve_storage_layout(root)
    configured_roots: list[tuple[str, Path]] = [
        ("state", root.resolve()),
        ("legacy_artifacts", layout.legacy_artifact_root),
    ]
    if layout.managed_artifact_root is not None:
        configured_roots.append(("managed_artifacts", layout.managed_artifact_root))

    storage_roots: list[dict[str, Any]] = []
    status_groups: dict[Path, list[str]] = {}
    for kind, path in configured_roots:
        containing_worktree = _containing_worktree(path, worktrees)
        inside_worktree = containing_worktree is not None
        overlapping_worktrees = [
            str(row.get("label") or Path(str(row.get("path") or "")).name)
            for row in worktrees
            if row.get("path") and _paths_overlap(path, Path(str(row["path"])))
        ]
        ignored: bool | None = None
        tracked: bool | None = None
        risks: list[str] = []
        recommended_ignore = None
        if inside_worktree:
            assert containing_worktree is not None
            inspection_repo, inspection_label, pathspec = containing_worktree
            status_groups.setdefault(inspection_repo, []).append(pathspec)
            ignored = _git_check_ignore(inspection_repo, pathspec)
            tracked = _git_has_tracked_path(inspection_repo, pathspec)
            risks.append("inside_git_worktree")
            if tracked:
                risks.append("tracked_storage_root")
            if not ignored:
                risks.append("unignored_storage_root")
                if pathspec != ".":
                    recommended_ignore = f"{pathspec.rstrip('/')}/"
            if kind == "managed_artifacts":
                risks.append("managed_payload_in_worktree")
        if overlapping_worktrees:
            risks.append("worktree_overlap")
        storage_roots.append(
            {
                "kind": kind,
                "path": str(path),
                "inside_git_worktree": inside_worktree,
                "git_worktree": inspection_label if inside_worktree else None,
                "overlapping_worktrees": sorted(set(overlapping_worktrees)),
                "ignore": {
                    "checked": inside_worktree,
                    "ignored": ignored,
                    "tracked": tracked,
                    "coverage": (
                        "outside_worktree"
                        if not inside_worktree
                        else "tracked"
                        if tracked
                        else "ignored"
                        if ignored
                        else "unignored"
                    ),
                },
                "risks": sorted(set(risks)),
                "recommended_ignore": recommended_ignore,
            }
        )
    status = _bounded_git_status_groups(
        status_groups,
        max_bytes=max_status_bytes,
        deep=deep,
    )
    return {
        "ok": True,
        "schema_version": "git_hygiene_v1",
        "repo": str(repo),
        "repo_root": str(repo_root),
        "storage_roots": storage_roots,
        "status": status,
        "risks": sorted(
            {
                risk
                for item in storage_roots
                for risk in item.get("risks", [])
            }
        ),
    }


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


def _evidence_summary(nodes: dict[str, ResearchNode], node_ids: set[str]) -> dict[str, Any]:
    scoped_ids: set[str] = set()
    for node_id in node_ids:
        scoped_ids.update(_subtree_ids(nodes, node_id))
    artifact_ids: set[str] = set()
    finding_count = 0
    for node_id in scoped_ids:
        node = nodes.get(node_id)
        if not node:
            continue
        if node.type == "artifact":
            artifact_ids.add(node.id)
        linked = node.raw.get("linked_artifacts")
        if isinstance(linked, list):
            artifact_ids.update(str(item) for item in linked)
        findings = node.raw.get("findings")
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            finding_count += 1
            linked_artifacts = finding.get("linked_artifacts")
            if isinstance(linked_artifacts, list):
                artifact_ids.update(str(item) for item in linked_artifacts)
    return {
        "scoped_node_ids": sorted(scoped_ids),
        "finding_count": finding_count,
        "artifact_ids": sorted(artifact_ids),
    }


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


def _is_external_target(target: str) -> bool:
    if Path(target).is_absolute():
        return False
    parsed = urlparse(target)
    if len(parsed.scheme) == 1:
        return False
    return bool(parsed.scheme and parsed.scheme != "file")


def _resolve_local_path(root: Path, repo: Path, target: Any) -> Path | None:
    if target in (None, ""):
        return None
    text = str(target)
    path = Path(text)
    if path.is_absolute():
        return path
    if _is_external_target(text):
        return None
    candidates = [repo / path, root / path, root.parent / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _within_allowed_roots(path: Path, *, root: Path, repo: Path) -> bool:
    resolved = path.resolve(strict=False)
    allowed_roots = [root.resolve(strict=False), root.parent.resolve(strict=False), repo.resolve(strict=False)]
    for allowed in allowed_roots:
        try:
            resolved.relative_to(allowed)
            return True
        except ValueError:
            continue
    return False


def _paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = left.resolve(strict=False)
    right_resolved = right.resolve(strict=False)
    try:
        right_resolved.relative_to(left_resolved)
        return True
    except ValueError:
        pass
    try:
        left_resolved.relative_to(right_resolved)
        return True
    except ValueError:
        return False


def _scan_path(path: Path, *, max_files: int) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "file_count": 0,
            "size_bytes": 0,
            "truncated": False,
        }
    if path.is_file():
        return {
            "exists": True,
            "file_count": 1,
            "size_bytes": path.stat().st_size,
            "truncated": False,
        }
    if not path.is_dir():
        return {
            "exists": True,
            "file_count": 0,
            "size_bytes": 0,
            "truncated": False,
        }
    size_bytes = 0
    file_count = 0
    truncated = False
    for child in path.rglob("*"):
        if child.is_symlink() or not child.is_file():
            continue
        file_count += 1
        if file_count > max_files:
            truncated = True
            break
        size_bytes += child.stat().st_size
    return {
        "exists": True,
        "file_count": min(file_count, max_files),
        "size_bytes": size_bytes,
        "truncated": truncated,
    }


_STATE_YAML_DIRECTORIES = (
    "agents",
    "assignments",
    "runs",
    "artifact_records",
    "artifact_migrations",
    "handoffs",
)


def _bounded_state_statistics(root: Path, *, max_files: int) -> dict[str, Any]:
    """Measure control-state bytes without traversing payload or dashboard roots."""

    limit = max(1, max_files)
    entry_limit = max(limit * 2, limit + 1)
    file_count = 0
    size_bytes = 0
    truncated = False
    unsafe_entry_count = 0
    entries_scanned = 0

    def add_file(path: Path) -> bool:
        nonlocal file_count, size_bytes, truncated, unsafe_entry_count
        try:
            metadata = path.lstat()
        except OSError:
            unsafe_entry_count += 1
            return True
        if not stat.S_ISREG(metadata.st_mode):
            unsafe_entry_count += 1
            return True
        if file_count >= limit:
            truncated = True
            return False
        file_count += 1
        size_bytes += int(metadata.st_size)
        return True

    for name in (
        "current_state.yaml",
        "coordinator_state.yaml",
        "storage.yaml",
        "graph/edges.yaml",
        "graph/interaction_log.yaml",
    ):
        path = root / name
        if path.is_symlink():
            unsafe_entry_count += 1
            continue
        if path.is_file() and not add_file(path):
            break

    scopes: list[tuple[Path, set[str] | None]] = [
        (root / name, {".yaml", ".yml"}) for name in _STATE_YAML_DIRECTORIES
    ]
    scopes.extend(
        [
            (root / "graph" / "nodes", {".yaml", ".yml"}),
            (root / "graph" / "interaction_events", None),
            (root / "gate_results", {".yaml", ".yml", ".json"}),
        ]
    )
    for directory, suffixes in scopes:
        if truncated:
            continue
        if directory.is_symlink():
            unsafe_entry_count += 1
            continue
        if not directory.exists():
            continue
        if not directory.is_dir():
            unsafe_entry_count += 1
            continue
        stack = [directory]
        while stack and not truncated:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        entries_scanned += 1
                        if entries_scanned > entry_limit:
                            truncated = True
                            break
                        try:
                            if entry.is_symlink():
                                unsafe_entry_count += 1
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                unsafe_entry_count += 1
                                continue
                        except OSError:
                            unsafe_entry_count += 1
                            continue
                        if suffixes is not None and Path(entry.name).suffix.lower() not in suffixes:
                            continue
                        if not add_file(Path(entry.path)):
                            break
            except OSError:
                unsafe_entry_count += 1
                continue
    exact = not truncated and unsafe_entry_count == 0
    return {
        "file_count": file_count,
        "size_bytes": size_bytes,
        "exact": exact,
        "lower_bound": not exact,
        "truncated": truncated,
        "unsafe_entry_count": unsafe_entry_count,
        "file_limit": limit,
        "entries_scanned": entries_scanned,
        "entry_limit": entry_limit,
    }


def _resource_strings(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_resource_strings(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_resource_strings(item))
        return strings
    return []


def _active_resource_references(
    *,
    root: Path,
    repo: Path,
    artifact_paths: list[Path],
    runs: dict[str, RunRecord],
) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for run in sorted(runs.values(), key=lambda item: item.run_id):
        if run.status not in ACTIVE_RUN_STATUSES:
            continue
        raw_paths: list[tuple[str, Any]] = [
            ("output_root", run.output_root),
            ("log_root", run.log_root),
            ("progress_file", run.progress_file),
            ("config_file", run.config_file),
        ]
        raw_paths.extend(("resources", item) for item in _resource_strings(run.raw.get("resources")))
        for source, target in raw_paths:
            resolved = _resolve_local_path(root, repo, target)
            if resolved is None:
                continue
            if any(_paths_overlap(path, resolved) for path in artifact_paths):
                references.append({
                    "run_id": run.run_id,
                    "status": run.status,
                    "source": source,
                    "target": str(target),
                })
                break
    return references


def _artifact_targets(node: ResearchNode) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    path = node.raw.get("path")
    if path not in (None, ""):
        targets.append(("path", str(path)))
    links = node.raw.get("links")
    if isinstance(links, dict):
        for label, target in links.items():
            if target not in (None, ""):
                targets.append((f"links.{label}", str(target)))
    return targets


def _artifact_reference_index(nodes: dict[str, ResearchNode]) -> dict[str, list[dict[str, str]]]:
    references: dict[str, list[dict[str, str]]] = {}
    for node in sorted(nodes.values(), key=lambda item: item.id):
        linked = node.raw.get("linked_artifacts")
        if isinstance(linked, list):
            for artifact_id in linked:
                references.setdefault(str(artifact_id), []).append({
                    "node_id": node.id,
                    "node_type": node.type,
                    "source": "linked_artifacts",
                })
        baseline = node.raw.get("baseline")
        if isinstance(baseline, dict):
            artifacts = baseline.get("artifacts")
            if isinstance(artifacts, list):
                for artifact_id in artifacts:
                    references.setdefault(str(artifact_id), []).append({
                        "node_id": node.id,
                        "node_type": node.type,
                        "source": "baseline.artifacts",
                    })
        findings = node.raw.get("findings")
        if isinstance(findings, list):
            for index, finding in enumerate(findings):
                if not isinstance(finding, dict):
                    continue
                for artifact_id in finding.get("linked_artifacts", []) or []:
                    references.setdefault(str(artifact_id), []).append({
                        "node_id": node.id,
                        "node_type": node.type,
                        "source": f"findings[{index}].linked_artifacts",
                    })
    return references


def _active_assignment_references(
    *,
    nodes: dict[str, ResearchNode],
    assignments: dict[str, AssignmentRecord],
    artifact_id: str,
) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    for assignment in sorted(assignments.values(), key=lambda item: item.assignment_id):
        if assignment.status not in ACTIVE_ASSIGNMENT_STATUSES:
            continue
        scoped_ids = _subtree_ids(nodes, assignment.root_node)
        if artifact_id in scoped_ids:
            references.append({
                "assignment_id": assignment.assignment_id,
                "node_id": artifact_id,
                "source": "subtree_node",
            })
        for node_id in sorted(scoped_ids):
            node = nodes.get(node_id)
            if not node:
                continue
            linked = node.raw.get("linked_artifacts")
            if isinstance(linked, list) and artifact_id in {str(item) for item in linked}:
                references.append({
                    "assignment_id": assignment.assignment_id,
                    "node_id": node_id,
                    "source": "linked_artifacts",
                })
            findings = node.raw.get("findings")
            if not isinstance(findings, list):
                continue
            for index, finding in enumerate(findings):
                if not isinstance(finding, dict):
                    continue
                if artifact_id in {str(item) for item in finding.get("linked_artifacts", []) or []}:
                    references.append({
                        "assignment_id": assignment.assignment_id,
                        "node_id": node_id,
                        "source": f"findings[{index}].linked_artifacts",
                    })
    return references


def _retention_class(node: ResearchNode) -> tuple[str | None, list[str]]:
    retention = node.raw.get("retention")
    if retention is None:
        return None, []
    if not isinstance(retention, dict):
        return None, ["invalid_retention"]
    value = retention.get("class")
    return (None if value in (None, "") else str(value)), []


def build_artifact_retention_audit(
    root: Path,
    *,
    repo: Path,
    min_size_bytes: int,
    max_files: int = 1000,
) -> dict[str, Any]:
    from research_cockpit.artifact_inventory import (
        cached_retention_target_scan,
        ensure_artifact_inventory,
        patch_retention_target_scans,
    )

    inventory_result = ensure_artifact_inventory(root)
    inventory = inventory_result["inventory"]
    scan_updates: list[dict[str, Any]] = []
    nodes, assignments, runs = _load_validated(root)
    artifact_references = _artifact_reference_index(nodes)
    artifacts: list[dict[str, Any]] = []
    for node in sorted(nodes.values(), key=lambda item: item.id):
        if node.type != "artifact":
            continue
        warnings: list[str] = []
        retention_class, retention_warnings = _retention_class(node)
        warnings.extend(retention_warnings)
        target_rows: list[dict[str, Any]] = []
        local_paths: list[Path] = []
        for label, target in _artifact_targets(node):
            resolved = _resolve_local_path(root, repo, target)
            if resolved is None:
                target_rows.append({
                    "label": label,
                    "target": target,
                    "local": False,
                    "exists": None,
                    "resolved_path": None,
                    "size_bytes": 0,
                    "file_count": 0,
                    "truncated": False,
                })
                continue
            if not _within_allowed_roots(resolved, root=root, repo=repo):
                warnings.append("external_path")
                target_rows.append({
                    "label": label,
                    "target": target,
                    "local": True,
                    "external": True,
                    "exists": resolved.exists(),
                    "resolved_path": str(resolved),
                    "size_bytes": 0,
                    "file_count": 0,
                    "truncated": False,
                })
                continue
            scan = cached_retention_target_scan(
                inventory,
                repo=repo,
                max_files=max_files,
                artifact_id=node.id,
                label=label,
                target=target,
                resolved_path=resolved,
            )
            if scan is None:
                scan = _scan_path(resolved, max_files=max_files)
                scan_updates.append({
                    "artifact_id": node.id,
                    "label": label,
                    "target": target,
                    "resolved_path": resolved,
                    "scan": scan,
                })
            if not scan["exists"]:
                warnings.append("missing_path")
            target_rows.append({
                "label": label,
                "target": target,
                "local": True,
                "resolved_path": str(resolved),
                **scan,
            })
            local_paths.append(resolved)
        total_size = sum(int(target["size_bytes"]) for target in target_rows)
        large = total_size >= min_size_bytes and any(target.get("exists") for target in target_rows)
        missing_retention = large and retention_class is None
        if missing_retention:
            warnings.append("missing_retention")
        active_refs = _active_resource_references(root=root, repo=repo, artifact_paths=local_paths, runs=runs)
        blockers: list[str] = []
        linked_refs = artifact_references.get(node.id, [])
        active_assignment_refs = _active_assignment_references(
            nodes=nodes,
            assignments=assignments,
            artifact_id=node.id,
        )
        if linked_refs:
            blockers.append("linked_reference")
        if active_assignment_refs:
            blockers.append("active_assignment")
        if any(target.get("external") for target in target_rows):
            blockers.append("external_path")
        if active_refs:
            blockers.append("active_resource")
        cleanup_candidate = (
            retention_class in RETENTION_CLEANUP_CLASSES
            and large
            and not blockers
            and any(target.get("exists") for target in target_rows)
        )
        artifacts.append({
            "artifact_id": node.id,
            "title": node.title,
            "path": node.raw.get("path"),
            "retention_class": retention_class,
            "targets": target_rows,
            "total_size_bytes": total_size,
            "file_count": sum(int(target["file_count"]) for target in target_rows),
            "large": large,
            "missing_retention": missing_retention,
            "active_resource_references": active_refs,
            "linked_references": linked_refs,
            "active_assignment_references": active_assignment_refs,
            "blockers": blockers,
            "warnings": sorted(set(warnings)),
            "cleanup_candidate": cleanup_candidate,
        })

    if scan_updates:
        patch_retention_target_scans(
            root,
            repo=repo,
            max_files=max_files,
            scans=scan_updates,
        )

    return {
        "ok": True,
        "schema_version": "artifact_retention_audit_v1",
        "root": str(root),
        "repo": str(repo),
        "min_size_bytes": min_size_bytes,
        "max_files": max_files,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "large_artifact_candidates": [item["artifact_id"] for item in artifacts if item["large"]],
        "cleanup_candidates": [item["artifact_id"] for item in artifacts if item["cleanup_candidate"]],
        "warnings": sorted({warning for item in artifacts for warning in item["warnings"]}),
        "artifact_inventory": {
            "status": inventory_result["status"],
            "aggregates": inventory.get("aggregates", {}),
            "scan": inventory.get("scan", {}),
        },
    }


def _active_assignment_rows(root: Path) -> list[dict[str, Any]]:
    assignments = load_assignments(root)
    return [
        {
            "assignment_id": assignment.assignment_id,
            "agent_id": assignment.agent_id,
            "status": assignment.status,
            "root_node": assignment.root_node,
            "current_node": assignment.current_node,
            "worktree": assignment.worktree,
        }
        for assignment in sorted(assignments.values(), key=lambda item: item.assignment_id)
        if assignment.status in ACTIVE_ASSIGNMENT_STATUSES
    ]


def _running_run_rows(root: Path) -> list[dict[str, Any]]:
    runs = load_runs(root)
    return [
        {
            "run_id": run.run_id,
            "status": run.status,
            "experiment_id": run.experiment_id,
            "output_root": run.output_root,
            "log_root": run.log_root,
            "progress_file": run.progress_file,
            "config_file": run.config_file,
            "resources": run.raw.get("resources"),
        }
        for run in sorted(runs.values(), key=lambda item: item.run_id)
        if run.status in ACTIVE_RUN_STATUSES
    ]


def _large_output_candidates(artifact_audit: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for artifact in artifact_audit.get("artifacts", []):
        if not artifact.get("large"):
            continue
        for target in artifact.get("targets", []):
            if target.get("exists") and target.get("target"):
                out.append(str(target["target"]))
    return sorted(set(out))


def _dashboard_performance_warnings(root: Path) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    profile_path = root / "dashboards" / "build_profile.json"
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            profile = {}
        for warning in profile.get("warnings", []) if isinstance(profile, dict) else []:
            if not isinstance(warning, dict):
                continue
            warnings.append({
                **warning,
                "source": "build_profile",
            })
    node_count = len(load_nodes(root))
    if node_count > 500:
        warnings.append({
            "code": "large_graph",
            "node_count": node_count,
            "message": "Graph has more than 500 nodes; prefer compact context and profile dashboard builds.",
        })
    return warnings


def _build_maintenance_audit_detail(
    root: Path,
    *,
    repo: Path,
    base: str = "main",
    min_size_bytes: int,
    max_files: int = 1000,
    deep_git: bool = False,
) -> dict[str, Any]:
    active_assignments = _active_assignment_rows(root)
    running_runs = _running_run_rows(root)
    worktree_audit = build_worktree_audit(root, repo=repo)
    branch_audit = build_branch_audit(root, repo=repo, base=base)
    artifact_audit = build_artifact_retention_audit(root, repo=repo, min_size_bytes=min_size_bytes, max_files=max_files)
    git_hygiene = build_git_hygiene_summary(root, repo=repo, deep=deep_git)
    from research_cockpit.artifact_inventory import ensure_artifact_inventory
    from research_cockpit.artifact_compaction import artifact_compaction_plan

    inventory = ensure_artifact_inventory(root)["inventory"]
    state_statistics = _bounded_state_statistics(root, max_files=max_files)
    artifact_compaction = artifact_compaction_plan(root)
    active_resources = [
        {
            "run_id": run["run_id"],
            "status": run["status"],
            "output_root": run.get("output_root"),
            "log_root": run.get("log_root"),
            "progress_file": run.get("progress_file"),
            "config_file": run.get("config_file"),
            "resources": run.get("resources"),
        }
        for run in running_runs
    ]
    worktree_candidates = [
        row for row in worktree_audit["worktrees"] if row.get("safe_to_remove")
    ]
    branch_candidates = [
        row for row in branch_audit["branches"] if row.get("delete_candidate")
    ]
    blocked_worktrees = [
        row
        for row in worktree_audit["worktrees"]
        if not row.get("safe_to_remove") and "primary_worktree" not in row.get("blockers", [])
    ]
    blocked_branches = [
        row
        for row in branch_audit["branches"]
        if row.get("recommended_action") not in {"keep_base", "delete_candidate"}
    ]
    unsafe_blockers = sorted(
        {
            blocker
            for row in blocked_worktrees
            for blocker in row.get("blockers", [])
        }
        | {
            blocker
            for row in blocked_branches
            for blocker in row.get("blockers", [])
        }
        | {
            blocker
            for row in artifact_audit["artifacts"]
            for blocker in row.get("blockers", [])
        }
    )
    next_actions: list[str] = []
    if unsafe_blockers:
        next_actions.append("Clear unsafe cleanup blockers before removing worktrees, branches, or artifacts.")
    if any(item.get("missing_retention") for item in artifact_audit["artifacts"]):
        next_actions.append("Add retention metadata for large artifacts before cleanup decisions.")
    if any(item.get("cleanup_candidate") for item in artifact_audit["artifacts"]):
        next_actions.append("Review cleanup candidates and preserve summaries before deleting payloads.")
    if artifact_compaction["counts"].get("can_demote"):
        next_actions.append("Run maintenance compact with execute: false before demoting a run-output artifact node.")
    if branch_candidates:
        next_actions.append("Review branch candidates; promote useful unmerged work to research/* before deletion.")
    return {
        "ok": True,
        "schema_version": "maintenance_audit_detail_v1",
        "root": str(root),
        "repo": str(repo),
        "base": base,
        "state_statistics": state_statistics,
        "worktree_audit": worktree_audit,
        "branch_audit": branch_audit,
        "active_assignments": active_assignments,
        "running_runs": running_runs,
        "active_resources": active_resources,
        "worktree_candidates": worktree_candidates,
        "branch_candidates": branch_candidates,
        "blocked_worktrees": blocked_worktrees,
        "blocked_branches": blocked_branches,
        "large_artifact_candidates": artifact_audit["large_artifact_candidates"],
        "large_output_candidates": _large_output_candidates(artifact_audit),
        "artifact_inventory": artifact_audit.get("artifact_inventory", {}),
        "_artifact_inventory_records": inventory.get("records", {}),
        "artifact_compaction_counts": artifact_compaction["counts"],
        "record_only_candidates": [
            row["artifact_id"]
            for row in artifact_compaction["artifacts"]
            if row.get("classification") == "can_demote"
        ],
        "dashboard_performance_warnings": _dashboard_performance_warnings(root),
        "git_hygiene": git_hygiene,
        "unsafe_cleanup_blockers": unsafe_blockers,
        "recommended_next_actions": next_actions,
        "artifact_retention_audit": artifact_audit,
        "artifact_compaction_plan": artifact_compaction,
    }


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _non_negative_count(value: Any) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _bounded_audit_text(value: Any, *, max_bytes: int = _MAX_AUDIT_TEXT_BYTES) -> str:
    text = str(value or "")
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    clipped = encoded[: max(0, max_bytes - 3)].decode("utf-8", errors="ignore")
    return f"{clipped}..."


def _bounded_audit_strings(value: Any, *, limit: int = _MAX_AUDIT_REASONS) -> list[str]:
    values = sorted(
        {
            _bounded_audit_text(item, max_bytes=96)
            for item in _list(value)
            if str(item or "").strip()
        }
    )
    return values[:limit]


def _compact_count_dimensions(value: Any) -> dict[str, Any]:
    rows = _mapping(value)
    ordered = sorted(
        (
            (_bounded_audit_text(key, max_bytes=96), _non_negative_count(count))
            for key, count in rows.items()
        ),
        key=lambda item: (-item[1], item[0]),
    )
    selected = ordered[:_MAX_AUDIT_DIMENSIONS]
    omitted = ordered[_MAX_AUDIT_DIMENSIONS:]
    return {
        "values": [{"value": key, "count": count} for key, count in selected],
        "truncated": bool(omitted),
        "omitted_value_count": len(omitted),
        "omitted_record_count": sum(count for _key, count in omitted),
    }


def _compact_inventory_statistics(value: Any) -> dict[str, Any]:
    statistics = _mapping(value)
    lower_bound = bool(statistics.get("lower_bound"))
    exact = bool(statistics.get("exact")) and not lower_bound
    return {
        "size_bytes": _non_negative_count(statistics.get("size_bytes")),
        "file_count": _non_negative_count(statistics.get("file_count")),
        "exact": exact,
        "lower_bound": not exact,
        "unknown_or_incomplete_count": _non_negative_count(
            statistics.get("unknown_or_incomplete_count")
        ),
    }


def _compact_state_statistics(value: Any) -> dict[str, Any]:
    statistics = _mapping(value)
    lower_bound = bool(statistics.get("lower_bound"))
    exact = bool(statistics.get("exact")) and not lower_bound
    return {
        "size_bytes": _non_negative_count(statistics.get("size_bytes")),
        "file_count": _non_negative_count(statistics.get("file_count")),
        "exact": exact,
        "lower_bound": not exact,
        "truncated": bool(statistics.get("truncated")),
        "unsafe_entry_count": _non_negative_count(statistics.get("unsafe_entry_count")),
        "file_limit": _non_negative_count(statistics.get("file_limit")),
        "entries_scanned": _non_negative_count(statistics.get("entries_scanned")),
        "entry_limit": _non_negative_count(statistics.get("entry_limit")),
    }


def _compact_inventory_summary(value: Any) -> dict[str, Any]:
    aggregates = _mapping(value)
    records = _mapping(aggregates.get("records"))
    graph_artifacts = _mapping(aggregates.get("graph_artifacts"))
    managed_payloads = _mapping(aggregates.get("managed_payloads"))
    managed_orphans = _mapping(aggregates.get("managed_orphans"))
    return {
        "records": {
            "count": _non_negative_count(records.get("count")),
            "statistics": _compact_inventory_statistics(records.get("statistics")),
            "by_storage_mode": _compact_count_dimensions(records.get("by_storage_mode")),
            "by_ownership": _compact_count_dimensions(records.get("by_ownership")),
            "by_retention_class": _compact_count_dimensions(records.get("by_retention_class")),
            "by_integrity_level": _compact_count_dimensions(records.get("by_integrity_level")),
            "by_availability_status": _compact_count_dimensions(records.get("by_availability_status")),
        },
        "graph_artifacts": {
            "count": _non_negative_count(graph_artifacts.get("count")),
            "by_retention_class": _compact_count_dimensions(
                graph_artifacts.get("by_retention_class")
            ),
        },
        "managed_payloads": {
            "count": _non_negative_count(managed_payloads.get("count")),
            "count_exact": bool(managed_payloads.get("count_exact")),
            "count_lower_bound": bool(managed_payloads.get("count_lower_bound")),
            "statistics": _compact_inventory_statistics(managed_payloads.get("statistics")),
        },
        "managed_orphans": {
            "count": _non_negative_count(managed_orphans.get("count")),
            "count_exact": bool(managed_orphans.get("count_exact")),
            "count_lower_bound": bool(managed_orphans.get("count_lower_bound")),
            "statistics": _compact_inventory_statistics(managed_orphans.get("statistics")),
        },
    }


def _compact_git_status_count(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    lower_bound = bool(row.get("lower_bound"))
    exact = bool(row.get("exact")) and not lower_bound
    return {
        "count": _non_negative_count(row.get("count")),
        "exact": exact,
        "lower_bound": not exact,
    }


def _compact_git_hygiene(value: Any) -> dict[str, Any]:
    hygiene = _mapping(value)
    roots: list[dict[str, Any]] = []
    for raw in _list(hygiene.get("storage_roots"))[:3]:
        row = _mapping(raw)
        ignore = _mapping(row.get("ignore"))
        roots.append(
            {
                "kind": _bounded_audit_text(row.get("kind"), max_bytes=48),
                "path": _bounded_audit_text(row.get("path"), max_bytes=256),
                "inside_git_worktree": bool(row.get("inside_git_worktree")),
                "git_worktree": _bounded_audit_text(row.get("git_worktree"), max_bytes=96),
                "overlapping_worktrees": _bounded_audit_strings(
                    row.get("overlapping_worktrees"),
                    limit=4,
                ),
                "ignore_coverage": _bounded_audit_text(
                    ignore.get("coverage"),
                    max_bytes=48,
                ),
                "risks": _bounded_audit_strings(row.get("risks"), limit=6),
            }
        )
    status = _mapping(hygiene.get("status"))
    return {
        "schema_version": _bounded_audit_text(hygiene.get("schema_version"), max_bytes=48),
        "roots": roots,
        "status": {
            "deep": bool(status.get("deep")),
            "truncated": bool(status.get("truncated")),
            "bytes_read": _non_negative_count(status.get("bytes_read")),
            "byte_limit": (
                _non_negative_count(status.get("byte_limit"))
                if status.get("byte_limit") is not None
                else None
            ),
            "worktree_group_count": _non_negative_count(status.get("worktree_group_count")),
            "skipped_worktree_group_count": _non_negative_count(
                status.get("skipped_worktree_group_count")
            ),
            "untracked": _compact_git_status_count(status.get("untracked")),
            "ignored": _compact_git_status_count(status.get("ignored")),
            "tracked_modified": _compact_git_status_count(status.get("tracked_modified")),
        },
        "risks": _bounded_audit_strings(hygiene.get("risks"), limit=8),
    }


def _audit_candidate(
    *,
    kind: str,
    identifier: Any,
    classification: str,
    reasons: Any,
    **fields: Any,
) -> dict[str, Any]:
    text_id = _bounded_audit_text(identifier, max_bytes=128)
    row: dict[str, Any] = {
        "key": f"{kind}:{text_id}",
        "kind": kind,
        "id": text_id,
        "classification": classification,
        "reasons": _bounded_audit_strings(reasons),
    }
    for key, value in fields.items():
        if isinstance(value, bool):
            row[key] = value
        elif isinstance(value, int):
            row[key] = _non_negative_count(value)
        elif isinstance(value, list):
            row[key] = _bounded_audit_strings(value)
        else:
            row[key] = _bounded_audit_text(value)
    return row


def _maintenance_candidates(detail: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    artifact_audit = _mapping(detail.get("artifact_retention_audit"))
    for raw in _list(artifact_audit.get("artifacts")):
        artifact = _mapping(raw)
        identifier = artifact.get("artifact_id")
        if not identifier:
            continue
        blockers = _bounded_audit_strings(artifact.get("blockers"))
        warnings = _bounded_audit_strings(artifact.get("warnings"))
        retention_class = _bounded_audit_text(artifact.get("retention_class"), max_bytes=80)
        if blockers or retention_class == "must_keep":
            classification = "must_keep"
            reasons = blockers or ["retention_must_keep"]
        elif artifact.get("cleanup_candidate"):
            classification = "can_quarantine"
            reasons = ["requires_ownership_and_integrity_verification"]
        else:
            classification = "needs_review"
            reasons = warnings
            if not reasons:
                reasons = ["retention_or_ownership_review"]
        candidates.append(
            _audit_candidate(
                kind="artifact",
                identifier=identifier,
                classification=classification,
                reasons=reasons,
                retention_class=retention_class,
                size_bytes=_non_negative_count(artifact.get("total_size_bytes")),
                file_count=_non_negative_count(artifact.get("file_count")),
                large=bool(artifact.get("large")),
            )
        )

    records = _mapping(detail.get("_artifact_inventory_records"))
    for record_id, raw in records.items():
        record = _mapping(raw)
        storage = _mapping(record.get("storage"))
        mode = _bounded_audit_text(storage.get("mode"), max_bytes=48)
        ownership = _bounded_audit_text(storage.get("ownership"), max_bytes=48)
        availability = _bounded_audit_text(record.get("availability_status"), max_bytes=48)
        if mode == "legacy":
            classification = "can_migrate"
            reasons = ["legacy_payload_requires_explicit_migration"]
        elif mode == "reference" or ownership == "external":
            classification = "must_keep"
            reasons = ["external_payload_not_owned_by_cockpit"]
        elif availability in {"missing", "quarantined", "deleted", "unknown"}:
            classification = "needs_review"
            reasons = [f"availability_{availability or 'unknown'}"]
        else:
            continue
        inventory = _mapping(record.get("inventory"))
        candidates.append(
            _audit_candidate(
                kind="artifact_record",
                identifier=record_id,
                classification=classification,
                reasons=reasons,
                storage_mode=mode,
                ownership=ownership,
                availability=availability,
                size_bytes=_non_negative_count(inventory.get("size_bytes")),
                file_count=_non_negative_count(inventory.get("file_count")),
            )
        )

    worktree_audit = _mapping(detail.get("worktree_audit"))
    for raw in _list(worktree_audit.get("worktrees")):
        worktree = _mapping(raw)
        identifier = worktree.get("label") or worktree.get("path")
        if not identifier:
            continue
        blockers = _bounded_audit_strings(worktree.get("blockers"))
        if worktree.get("safe_to_remove"):
            classification = "needs_review"
            reasons = ["safe_to_remove_requires_confirmation"]
        elif blockers:
            classification = "must_keep"
            reasons = blockers
        else:
            continue
        candidates.append(
            _audit_candidate(
                kind="worktree",
                identifier=identifier,
                classification=classification,
                reasons=reasons,
                branch=worktree.get("branch"),
            )
        )

    branch_audit = _mapping(detail.get("branch_audit"))
    base = str(detail.get("base") or "")
    for raw in _list(branch_audit.get("branches")):
        branch = _mapping(raw)
        identifier = branch.get("name")
        if not identifier or identifier == base:
            continue
        blockers = _bounded_audit_strings(branch.get("blockers"))
        if branch.get("delete_candidate"):
            classification = "needs_review"
            reasons = ["delete_candidate_requires_confirmation"]
        elif blockers:
            classification = "must_keep"
            reasons = blockers
        else:
            classification = "needs_review"
            reasons = [branch.get("recommended_action") or "branch_review"]
        candidates.append(
            _audit_candidate(
                kind="branch",
                identifier=identifier,
                classification=classification,
                reasons=reasons,
                branch_class=branch.get("branch_class"),
                merged=bool(branch.get("merged")),
            )
        )

    return sorted(
        candidates,
        key=lambda row: (
            _AUDIT_CLASSIFICATION_ORDER.get(str(row.get("classification")), 99),
            str(row.get("kind")),
            str(row.get("key")),
        ),
    )


def _cursor_fingerprint(candidates: list[dict[str, Any]]) -> str:
    payload = [
        [str(row.get("classification")), str(row.get("key"))]
        for row in candidates
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _encode_audit_cursor(
    *,
    classification: str,
    candidate_id: str | None,
    offset: int,
    fingerprint: str,
) -> str:
    payload = {
        "v": 1,
        "classification": classification,
        "id": candidate_id,
        "offset": offset,
        "fingerprint": fingerprint,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_audit_cursor(cursor: str) -> dict[str, Any]:
    if len(cursor) > 1024:
        raise ValueError("invalid maintenance audit cursor")
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(f"{cursor}{padding}".encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeEncodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid maintenance audit cursor") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError("invalid maintenance audit cursor")
    classification = payload.get("classification")
    candidate_id = payload.get("id")
    offset = payload.get("offset")
    fingerprint = payload.get("fingerprint")
    if (
        classification not in {"all", *AUDIT_CANDIDATE_CLASSIFICATIONS}
        or candidate_id is not None and not isinstance(candidate_id, str)
        or not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
        or not isinstance(fingerprint, str)
    ):
        raise ValueError("invalid maintenance audit cursor")
    return payload


def _validate_audit_page_request(
    *,
    limit: int,
    classification: str | None,
    candidate_id: str | None,
    cursor: str | None,
) -> tuple[str, str | None, int, str | None]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_AUDIT_CANDIDATE_LIMIT:
        raise ValueError(
            f"maintenance audit --limit must be between 1 and {MAX_AUDIT_CANDIDATE_LIMIT}"
        )
    requested_classification = classification or "all"
    if requested_classification not in {"all", *AUDIT_CANDIDATE_CLASSIFICATIONS}:
        allowed = ", ".join(["all", *sorted(AUDIT_CANDIDATE_CLASSIFICATIONS)])
        raise ValueError(f"invalid maintenance audit classification; expected one of: {allowed}")
    requested_id = str(candidate_id) if candidate_id not in (None, "") else None
    if not cursor:
        return requested_classification, requested_id, 0, None
    parsed = _decode_audit_cursor(cursor)
    cursor_classification = str(parsed["classification"])
    cursor_id = parsed.get("id")
    if classification is not None and requested_classification != cursor_classification:
        raise ValueError("maintenance audit cursor classification does not match --classification")
    if requested_id is not None and requested_id != cursor_id:
        raise ValueError("maintenance audit cursor id does not match --id")
    return cursor_classification, cursor_id, int(parsed["offset"]), str(parsed["fingerprint"])


def _candidate_page(
    candidates: list[dict[str, Any]],
    *,
    limit: int,
    classification: str | None,
    candidate_id: str | None,
    cursor: str | None,
) -> dict[str, Any]:
    selected_classification, selected_id, offset, expected_fingerprint = _validate_audit_page_request(
        limit=limit,
        classification=classification,
        candidate_id=candidate_id,
        cursor=cursor,
    )
    filtered = [
        row
        for row in candidates
        if (selected_classification == "all" or row.get("classification") == selected_classification)
        and (
            selected_id is None
            or selected_id in {str(row.get("id") or ""), str(row.get("key") or "")}
        )
    ]
    fingerprint = _cursor_fingerprint(filtered)
    if expected_fingerprint is not None and expected_fingerprint != fingerprint:
        raise ValueError("maintenance audit cursor is stale; rerun the summary")
    if offset > len(filtered):
        raise ValueError("maintenance audit cursor is outside the current candidate page")
    items = filtered[offset : offset + limit]
    page: dict[str, Any] = {
        "classification": selected_classification,
        "id": selected_id,
        "offset": offset,
        "limit": limit,
        "total_count": len(filtered),
        "items": items,
        "next_cursor": None,
        "_cursor_fingerprint": fingerprint,
    }
    _refresh_page_cursor(page)
    return page


def _refresh_page_cursor(page: dict[str, Any]) -> None:
    next_offset = int(page.get("offset") or 0) + len(_list(page.get("items")))
    total_count = int(page.get("total_count") or 0)
    if next_offset >= total_count:
        page["next_cursor"] = None
        return
    page["next_cursor"] = _encode_audit_cursor(
        classification=str(page.get("classification") or "all"),
        candidate_id=page.get("id") if isinstance(page.get("id"), str) else None,
        offset=next_offset,
        fingerprint=str(page.get("_cursor_fingerprint") or ""),
    )


def _compact_warning_counts(value: Any) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for raw in _list(value):
        row = _mapping(raw)
        code = _bounded_audit_text(row.get("code") or "unknown", max_bytes=96)
        counts[code] = counts.get(code, 0) + 1
    return _compact_count_dimensions(counts)


def _summary_protected_path_count(value: Any) -> int:
    paths = {
        str(row.get(field) or "")
        for raw in _list(value)
        for row in [_mapping(raw)]
        for field in ("output_root", "log_root", "progress_file", "config_file")
        if row.get(field)
    }
    return len(paths)


def _fit_compact_audit_result(payload: dict[str, Any]) -> None:
    page = _mapping(payload.get("candidate_page"))
    while (
        len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        > COMPACT_AUDIT_RESULT_BYTES
        and _list(page.get("items"))
    ):
        page["items"].pop()
        page["truncated_to_fit"] = True
        _refresh_page_cursor(page)
    if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > COMPACT_AUDIT_RESULT_BYTES:
        raise ValueError("maintenance audit compact summary exceeded its fixed output budget")


def _summarize_maintenance_audit(
    detail: dict[str, Any],
    *,
    limit: int = DEFAULT_AUDIT_CANDIDATE_LIMIT,
    cursor: str | None = None,
    classification: str | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    candidates = _maintenance_candidates(detail)
    counts = {name: 0 for name in AUDIT_CANDIDATE_CLASSIFICATIONS}
    for candidate in candidates:
        candidate_classification = str(candidate.get("classification") or "")
        if candidate_classification in counts:
            counts[candidate_classification] += 1
    artifact_audit = _mapping(detail.get("artifact_retention_audit"))
    inventory = _mapping(artifact_audit.get("artifact_inventory"))
    managed_scan = _mapping(_mapping(inventory.get("scan")).get("managed_store"))
    payload: dict[str, Any] = {
        "ok": bool(detail.get("ok")),
        "schema_version": "maintenance_audit_v2",
        "root": _bounded_audit_text(detail.get("root"), max_bytes=256),
        "repo": _bounded_audit_text(detail.get("repo"), max_bytes=256),
        "base": _bounded_audit_text(detail.get("base"), max_bytes=96),
        "summary": {
            "state": _compact_state_statistics(detail.get("state_statistics")),
            "active": {
                "assignment_count": len(_list(detail.get("active_assignments"))),
                "run_count": len(_list(detail.get("running_runs"))),
                "protected_path_count": _summary_protected_path_count(
                    detail.get("active_resources")
                ),
            },
            "artifact_inventory": {
                "status": _bounded_audit_text(inventory.get("status"), max_bytes=48),
                "managed_scan_truncated": bool(managed_scan.get("truncated")),
                "storage": _compact_inventory_summary(inventory.get("aggregates")),
            },
            "git_hygiene": _compact_git_hygiene(detail.get("git_hygiene")),
            "candidate_counts": {
                "total": len(candidates),
                "by_classification": counts,
                "worktree_removal_candidates": sum(
                    1
                    for row in candidates
                    if row.get("kind") == "worktree"
                    and "safe_to_remove_requires_confirmation" in row.get("reasons", [])
                ),
                "branch_removal_candidates": sum(
                    1
                    for row in candidates
                    if row.get("kind") == "branch"
                    and "delete_candidate_requires_confirmation" in row.get("reasons", [])
                ),
                "large_artifact_count": len(_list(detail.get("large_artifact_candidates"))),
            },
            "unsafe_cleanup_blockers": _compact_count_dimensions(
                {
                    value: 1
                    for value in _bounded_audit_strings(
                        detail.get("unsafe_cleanup_blockers"),
                        limit=_MAX_AUDIT_DIMENSIONS,
                    )
                }
            ),
            "dashboard_warning_codes": _compact_warning_counts(
                detail.get("dashboard_performance_warnings")
            ),
        },
        "candidate_page": _candidate_page(
            candidates,
            limit=limit,
            classification=classification,
            candidate_id=candidate_id,
            cursor=cursor,
        ),
        "recommended_next_actions": _bounded_audit_strings(
            detail.get("recommended_next_actions"),
            limit=6,
        ),
    }
    _fit_compact_audit_result(payload)
    _mapping(payload.get("candidate_page")).pop("_cursor_fingerprint", None)
    return payload


def build_maintenance_audit(
    root: Path,
    *,
    repo: Path,
    base: str = "main",
    min_size_bytes: int,
    max_files: int = 1000,
    limit: int = DEFAULT_AUDIT_CANDIDATE_LIMIT,
    cursor: str | None = None,
    classification: str | None = None,
    candidate_id: str | None = None,
    deep_git: bool = False,
) -> dict[str, Any]:
    detail = _build_maintenance_audit_detail(
        root,
        repo=repo,
        base=base,
        min_size_bytes=min_size_bytes,
        max_files=max_files,
        deep_git=deep_git,
    )
    return _summarize_maintenance_audit(
        detail,
        limit=limit,
        cursor=cursor,
        classification=classification,
        candidate_id=candidate_id,
    )


def _find_worktree_row(worktrees: list[dict[str, Any]], target: Path) -> dict[str, Any]:
    for row in worktrees:
        if _same_path(Path(str(row["path"])), target):
            return row
    raise ValueError(f"Worktree not found in git worktree list: {target}")


def _classification_commands(
    *,
    repo: Path,
    worktree: Path,
    branch: str | None,
    base: str,
    classification: str,
    delete_branch: bool = True,
) -> list[str]:
    if not branch:
        return [f"git -C {repo} worktree remove {worktree}"]
    remove = f"git -C {repo} worktree remove {worktree}"
    delete = f"git -C {repo} branch -d {branch}"
    if classification == "merge_to_main":
        return [
            f"git -C {repo} checkout {base}",
            f"git -C {repo} merge --no-ff {branch}",
            remove,
            *([delete] if delete_branch else []),
        ]
    if classification == "preserve_as_research_branch":
        target_branch = branch if branch.startswith("research/") else f"research/{Path(worktree).name}"
        commands = []
        if delete_branch and target_branch != branch:
            commands.append(f"git -C {repo} branch -m {branch} {target_branch}")
        commands.append(remove)
        return commands
    if classification == "extract_partial":
        return [
            f"git -C {repo} diff {base}...{branch} > closeout.patch",
            remove,
            *([delete] if delete_branch else []),
        ]
    return [remove, *([delete] if delete_branch else [])]


def _closeout_rc_updates(
    *,
    root: Path,
    target_row: dict[str, Any],
    evidence_summary: dict[str, Any],
    missing_run_retention: list[RunRecord],
    missing_artifact_retention: list[str],
) -> list[dict[str, str]]:
    updates: list[dict[str, str]] = []
    for assignment_id in target_row.get("active_assignment_ids", []):
        updates.append({
            "reason": "active_assignment",
            "command": (
                f"research-cockpit work close --root {root} --assignment {assignment_id} "
                "--file <closeout.yaml> --json --compact"
            ),
        })
    for node_id in target_row.get("active_workstream_node_ids", []):
        updates.append({
            "reason": "active_workstream",
            "command": (
                f"research-cockpit coord assign --root {root} "
                f"--file <coord_assign_{node_id}.yaml> --json --compact"
            ),
        })
    if not evidence_summary["finding_count"] or not evidence_summary["artifact_ids"]:
        updates.append({
            "reason": "missing_finding_or_evidence",
            "command": (
                f"research-cockpit work close --root {root} --assignment <assignment_id> "
                "--file <closeout.yaml> --json --compact"
            ),
        })
    for run in missing_run_retention:
        updates.append({
            "reason": "missing_run_retention_policy",
            "command": (
                f"research-cockpit work close --root {root} --assignment <assignment_id> "
                f"--file <closeout_for_{run.run_id}.yaml> --json --compact"
            ),
        })
    for artifact_id in missing_artifact_retention:
        updates.append({
            "reason": "missing_artifact_retention_policy",
            "command": (
                f"research-cockpit coord assign --root {root} "
                f"--file <coord_assign_{artifact_id}.yaml> --json --compact"
            ),
        })
    return updates


def build_worktree_closeout_plan(
    root: Path,
    *,
    repo: Path,
    worktree: Path,
    classification: str,
    base: str = "main",
    min_size_bytes: int,
    max_files: int = 1000,
    include_nested: list[Path] | None = None,
) -> dict[str, Any]:
    if classification not in WORKTREE_CLOSEOUT_CLASSIFICATIONS:
        allowed = ", ".join(sorted(WORKTREE_CLOSEOUT_CLASSIFICATIONS))
        raise ValueError(f"Invalid classification {classification!r}; allowed: {allowed}")
    resolved_worktree = worktree if worktree.is_absolute() else repo / worktree
    nodes, assignments, runs = _load_validated(root)
    worktree_audit = build_worktree_audit(root, repo=repo)
    branch_audit = build_branch_audit(root, repo=repo, base=base)
    artifact_audit = build_artifact_retention_audit(
        root,
        repo=repo,
        min_size_bytes=min_size_bytes,
        max_files=max_files,
    )
    target_row = _find_worktree_row(worktree_audit["worktrees"], resolved_worktree)
    branch = target_row.get("branch")
    branch_row = next(
        (row for row in branch_audit["branches"] if row.get("name") == branch),
        None,
    )
    delete_branch = not (
        branch in {base, "main", "master"}
        or (branch_row and branch_row.get("recommended_action") == "keep_base")
    )
    root_node_ids = {
        assignments[assignment_id].root_node
        for assignment_id in target_row.get("assignment_ids", [])
        if assignment_id in assignments
    }
    root_node_ids.update(str(node_id) for node_id in target_row.get("option_workstream_node_ids", []))
    evidence = _evidence_summary(nodes, root_node_ids)
    related_runs = [
        run
        for run in sorted(runs.values(), key=lambda item: item.run_id)
        if run.experiment_id in set(evidence["scoped_node_ids"])
    ]
    missing_run_retention = [
        run
        for run in related_runs
        if run.status == "completed" and run.output_root and run.output_retention is None
    ]
    artifact_by_id = {
        str(row["artifact_id"]): row
        for row in artifact_audit.get("artifacts", [])
    }
    missing_artifact_retention = [
        artifact_id
        for artifact_id in evidence["artifact_ids"]
        if artifact_by_id.get(artifact_id, {}).get("missing_retention")
    ]
    active_resource_refs = _active_resource_references(
        root=root,
        repo=repo,
        artifact_paths=[resolved_worktree],
        runs=runs,
    )
    blockers = list(target_row.get("blockers", []))
    if _worktree_dirty(repo):
        blockers.append("dirty_outer_repo")
    if not delete_branch:
        blockers.append("base_branch")
    nested_dirty = [
        str(nested)
        for nested in (include_nested or [])
        if _worktree_dirty(nested)
    ]
    if nested_dirty:
        blockers.append("dirty_nested_repo")
    if active_resource_refs:
        blockers.append("active_resource")
    if not evidence["finding_count"] or not evidence["artifact_ids"]:
        blockers.append("missing_finding_or_evidence")
    if missing_run_retention:
        blockers.append("missing_run_retention_policy")
    if missing_artifact_retention:
        blockers.append("missing_artifact_retention_policy")
    unique_blockers = sorted(set(blockers))
    return {
        "ok": not unique_blockers,
        "schema_version": "worktree_closeout_v1",
        "root": str(root),
        "repo": str(repo),
        "base": base,
        "dry_run": True,
        "classification": classification,
        "safe_to_closeout": not unique_blockers,
        "blockers": unique_blockers,
        "target_worktree": target_row,
        "branch": branch_row,
        "assignment_ids": target_row.get("assignment_ids", []),
        "option_workstream_node_ids": target_row.get("option_workstream_node_ids", []),
        "run_ids": [run.run_id for run in related_runs],
        "active_resource_references": active_resource_refs,
        "nested_dirty_repos": nested_dirty,
        "evidence_summary": evidence,
        "missing_retention": {
            "runs": [run.run_id for run in missing_run_retention],
            "artifacts": missing_artifact_retention,
        },
        "rc_state_updates_needed": _closeout_rc_updates(
            root=root,
            target_row=target_row,
            evidence_summary=evidence,
            missing_run_retention=missing_run_retention,
            missing_artifact_retention=missing_artifact_retention,
        ),
        "execution_commands": _classification_commands(
            repo=repo,
            worktree=resolved_worktree,
            branch=branch,
            base=base,
            classification=classification,
            delete_branch=delete_branch,
        ),
    }
