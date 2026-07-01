from __future__ import annotations

import json
from pathlib import Path
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
            scan = _scan_path(resolved, max_files=max_files)
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


def build_maintenance_audit(
    root: Path,
    *,
    repo: Path,
    base: str = "main",
    min_size_bytes: int,
    max_files: int = 1000,
) -> dict[str, Any]:
    active_assignments = _active_assignment_rows(root)
    running_runs = _running_run_rows(root)
    worktree_audit = build_worktree_audit(root, repo=repo)
    branch_audit = build_branch_audit(root, repo=repo, base=base)
    artifact_audit = build_artifact_retention_audit(root, repo=repo, min_size_bytes=min_size_bytes, max_files=max_files)
    from research_cockpit.artifact_compaction import artifact_compaction_plan

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
        next_actions.append("Run compact-artifacts --dry-run before demoting ordinary run-output artifact nodes.")
    if branch_candidates:
        next_actions.append("Review branch candidates; promote useful unmerged work to research/* before deletion.")
    return {
        "ok": True,
        "schema_version": "maintenance_audit_v1",
        "root": str(root),
        "repo": str(repo),
        "base": base,
        "active_assignments": active_assignments,
        "running_runs": running_runs,
        "active_resources": active_resources,
        "worktree_candidates": worktree_candidates,
        "branch_candidates": branch_candidates,
        "blocked_worktrees": blocked_worktrees,
        "blocked_branches": blocked_branches,
        "large_artifact_candidates": artifact_audit["large_artifact_candidates"],
        "large_output_candidates": _large_output_candidates(artifact_audit),
        "artifact_compaction_counts": artifact_compaction["counts"],
        "record_only_candidates": [
            row["artifact_id"]
            for row in artifact_compaction["artifacts"]
            if row.get("classification") == "can_demote"
        ],
        "dashboard_performance_warnings": _dashboard_performance_warnings(root),
        "unsafe_cleanup_blockers": unsafe_blockers,
        "recommended_next_actions": next_actions,
        "artifact_retention_audit": artifact_audit,
        "artifact_compaction_plan": artifact_compaction,
    }


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
                f"research-cockpit set-cursor --root {root} --assignment {assignment_id} "
                "--node <next_node> --no-build"
            ),
        })
    for node_id in target_row.get("active_workstream_node_ids", []):
        updates.append({
            "reason": "active_workstream",
            "command": (
                f"research-cockpit update-workstream-fields --root {root} --option {node_id} "
                "--status reported --no-build"
            ),
        })
    if not evidence_summary["finding_count"] or not evidence_summary["artifact_ids"]:
        updates.append({
            "reason": "missing_finding_or_evidence",
            "command": (
                f"research-cockpit complete-experiment --root {root} --id <experiment_id> "
                "--finding \"<finding>\" --confidence medium --artifact-id <artifact_id> --no-build"
            ),
        })
    for run in missing_run_retention:
        updates.append({
            "reason": "missing_run_retention_policy",
            "command": (
                f"research-cockpit complete-run --root {root} --id {run.run_id} --status completed "
                "--output-retention-file output_retention.yaml --no-build"
            ),
        })
    for artifact_id in missing_artifact_retention:
        updates.append({
            "reason": "missing_artifact_retention_policy",
            "command": (
                f"research-cockpit update-node-fields --root {root} --id {artifact_id} "
                "--metadata-file retention.yaml --no-build"
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
