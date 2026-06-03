from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Iterable

from research_cockpit.graph_core import GraphTopology
from research_cockpit.model import (
    ACTIVE_ASSIGNMENT_STATUSES,
    AssignmentRecord,
    ResearchNode,
    ValidationError,
    load_assignments,
)


class AssignmentScopeError(ValueError):
    def __init__(self, error: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.payload: dict[str, Any] = {
            "ok": False,
            "error": error,
            "message": message,
            "validation": {"ok": False, "errors": [message]},
        }
        self.payload.update(extra)


@dataclass
class AssignmentScopeContext:
    root: Path
    assignment_id: str | None = None
    assignment: AssignmentRecord | None = None
    allowed_root: str | None = None
    _allowed_subtree_cache: dict[int, set[str]] = field(default_factory=dict)
    _allowed_node_cache: dict[int, set[str]] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.assignment_id is not None and self.assignment is not None

    def check_nodes(
        self,
        nodes: dict[str, ResearchNode],
        target_node_ids: Iterable[str | None] = (),
    ) -> str | None:
        if not self.active:
            return None
        assert self.assignment is not None
        allowed_ids = self.allowed_node_ids(nodes)
        for node_id in target_node_ids:
            if not node_id:
                continue
            text_id = str(node_id)
            if text_id not in nodes:
                raise AssignmentScopeError(
                    "node_not_found",
                    f"Node does not exist: {text_id}",
                    assignment_id=self.assignment_id,
                    node_id=text_id,
                    allowed_root=self.allowed_root,
                )
            if text_id not in allowed_ids:
                raise AssignmentScopeError(
                    "node_out_of_assignment_scope",
                    f"Node {text_id} is outside assignment {self.assignment_id} scope rooted at {self.allowed_root}.",
                    assignment_id=self.assignment_id,
                    node_id=text_id,
                    allowed_root=self.allowed_root,
                )
        return self.assignment_id

    def forbid_set_focus(self, node_id: str | None = None) -> None:
        if not self.active:
            return
        raise AssignmentScopeError(
            "assignment_set_focus_forbidden",
            "Assignment-scoped workers should use set-cursor instead of --set-focus.",
            assignment_id=self.assignment_id,
            node_id=node_id,
        )

    def check_created_artifacts_linked(
        self,
        nodes: dict[str, ResearchNode],
        artifact_ids: Iterable[str | None] = (),
    ) -> str | None:
        if not self.active:
            return None
        allowed_ids = self.allowed_subtree_ids(nodes)
        for artifact_id in artifact_ids:
            if not artifact_id:
                continue
            text_id = str(artifact_id)
            node = nodes.get(text_id)
            if node is None:
                raise AssignmentScopeError(
                    "node_not_found",
                    f"Node does not exist: {text_id}",
                    assignment_id=self.assignment_id,
                    node_id=text_id,
                    allowed_root=self.allowed_root,
                )
            if node.type != "artifact":
                continue
            linked_by = [
                node_id
                for node_id in sorted(allowed_ids)
                if text_id in [str(item) for item in (nodes[node_id].raw.get("linked_artifacts", []) or [])]
            ]
            if not linked_by:
                raise AssignmentScopeError(
                    "artifact_not_linked_in_assignment_scope",
                    (
                        f"Artifact {text_id} must be linked from a node inside assignment "
                        f"{self.assignment_id} scope rooted at {self.allowed_root}."
                    ),
                    assignment_id=self.assignment_id,
                    node_id=text_id,
                    allowed_root=self.allowed_root,
                )
        return self.assignment_id

    def allowed_subtree_ids(self, nodes: dict[str, ResearchNode]) -> set[str]:
        if not self.active:
            return set(nodes)
        cache_key = id(nodes)
        if cache_key not in self._allowed_subtree_cache:
            assert self.assignment is not None
            self._allowed_subtree_cache[cache_key] = _allowed_subtree_ids(nodes, self.assignment)
        return self._allowed_subtree_cache[cache_key]

    def allowed_node_ids(self, nodes: dict[str, ResearchNode]) -> set[str]:
        if not self.active:
            return set(nodes)
        cache_key = id(nodes)
        if cache_key not in self._allowed_node_cache:
            assert self.assignment is not None
            self._allowed_node_cache[cache_key] = _allowed_node_ids(nodes, self.assignment)
        return self._allowed_node_cache[cache_key]


def resolve_mutation_assignment_id(
    assignment_id: str | None = None,
    *,
    coordinator: bool = False,
) -> str | None:
    if coordinator:
        return None
    return assignment_id or os.environ.get("RESEARCH_COCKPIT_ASSIGNMENT_ID")


def _load_assignment(root: Path, assignment_id: str) -> AssignmentRecord:
    try:
        assignments = load_assignments(root)
    except ValidationError as exc:
        raise AssignmentScopeError(
            "assignment_load_error",
            str(exc),
            assignment_id=assignment_id,
        ) from exc
    assignment = assignments.get(assignment_id)
    if assignment is None:
        raise AssignmentScopeError(
            "assignment_not_found",
            f"Assignment does not exist: {assignment_id}",
            assignment_id=assignment_id,
        )
    if assignment.status not in ACTIVE_ASSIGNMENT_STATUSES:
        raise AssignmentScopeError(
            "assignment_not_mutable",
            f"Assignment {assignment_id} status {assignment.status!r} does not allow mutation.",
            assignment_id=assignment_id,
            assignment_status=assignment.status,
        )
    return assignment


def resolve_assignment_scope(
    root: Path,
    nodes: dict[str, ResearchNode],
    *,
    assignment_id: str | None = None,
    coordinator: bool = False,
) -> AssignmentScopeContext:
    resolved_assignment_id = resolve_mutation_assignment_id(assignment_id, coordinator=coordinator)
    if not resolved_assignment_id:
        return AssignmentScopeContext(root=root)
    assignment = _load_assignment(root, resolved_assignment_id)
    allowed_root = str(assignment.allowed_subtree.get("root") or assignment.root_node)
    if allowed_root not in nodes:
        raise AssignmentScopeError(
            "assignment_scope_invalid",
            f"Assignment {assignment.assignment_id} root_node {allowed_root!r} does not exist.",
            assignment_id=assignment.assignment_id,
            allowed_root=allowed_root,
        )
    return AssignmentScopeContext(
        root=root,
        assignment_id=resolved_assignment_id,
        assignment=assignment,
        allowed_root=allowed_root,
    )


def _allowed_subtree_ids(nodes: dict[str, ResearchNode], assignment: AssignmentRecord) -> set[str]:
    root_node = str(assignment.allowed_subtree.get("root") or assignment.root_node)
    if root_node not in nodes:
        raise AssignmentScopeError(
            "assignment_scope_invalid",
            f"Assignment {assignment.assignment_id} root_node {root_node!r} does not exist.",
            assignment_id=assignment.assignment_id,
            allowed_root=root_node,
        )
    topology = GraphTopology.from_nodes(nodes)
    return {root_node, *topology.descendant_ids(root_node)}


def _allowed_node_ids(nodes: dict[str, ResearchNode], assignment: AssignmentRecord) -> set[str]:
    allowed_ids = _allowed_subtree_ids(nodes, assignment)
    linked_artifact_ids: set[str] = set()
    for node_id in allowed_ids:
        node = nodes.get(node_id)
        if not node:
            continue
        linked_artifact_ids.update(
            str(item)
            for item in (node.raw.get("linked_artifacts", []) or [])
            if str(item).strip()
        )
    return {node_id for node_id in [*allowed_ids, *linked_artifact_ids] if node_id in nodes}


def ensure_assignment_scope(
    root: Path,
    nodes: dict[str, ResearchNode],
    *,
    assignment_id: str | None = None,
    coordinator: bool = False,
    target_node_ids: Iterable[str | None] = (),
) -> str | None:
    scope = resolve_assignment_scope(root, nodes, assignment_id=assignment_id, coordinator=coordinator)
    return scope.check_nodes(nodes, target_node_ids)


def ensure_created_artifacts_linked_in_scope(
    root: Path,
    nodes: dict[str, ResearchNode],
    *,
    assignment_id: str | None = None,
    coordinator: bool = False,
    artifact_ids: Iterable[str | None] = (),
) -> str | None:
    scope = resolve_assignment_scope(root, nodes, assignment_id=assignment_id, coordinator=coordinator)
    return scope.check_created_artifacts_linked(nodes, artifact_ids)
