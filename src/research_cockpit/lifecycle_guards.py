from __future__ import annotations

from pathlib import Path
from typing import Any

from research_cockpit.graph_core import GraphTopology
from research_cockpit.types import ResearchNode


TERMINAL_PARENT_ACTIVE_DESCENDANTS_ERROR = "terminal_parent_has_active_descendants"

PARENT_TERMINAL_STATUSES = {
    "problem": {"resolved", "parked"},
    "option": {"accepted", "rejected", "paused", "parked"},
}

ACTIVE_DOWNSTREAM_STATUSES = {
    "problem": {"open", "active", "blocked"},
    "option": {"open", "active", "promising"},
    "experiment": {"planned", "queued", "running"},
}


class LifecycleGuardError(ValueError):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__(_guard_message(payload))


def _guard_message(payload: dict[str, Any]) -> str:
    node_id = payload.get("node_id")
    target_status = payload.get("target_status")
    blockers = payload.get("blocking_descendants") or []
    blocker_ids = ", ".join(str(item.get("id")) for item in blockers)
    return (
        f"{node_id}: {TERMINAL_PARENT_ACTIVE_DESCENDANTS_ERROR} for status "
        f"{target_status!r}; active descendants: {blocker_ids}"
    )


def is_terminal_parent_status(node_type: str, status: str) -> bool:
    return status in PARENT_TERMINAL_STATUSES.get(node_type, set())


def ordered_descendant_ids(topology: GraphTopology, parent_id: str) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    stack = list(reversed(topology.child_ids(parent_id)))
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        ordered.append(node_id)
        stack.extend(reversed(topology.child_ids(node_id)))
    return ordered


def active_descendant_blockers(
    nodes: dict[str, ResearchNode],
    parent_id: str,
    target_status: str,
    *,
    topology: GraphTopology | None = None,
) -> list[dict[str, Any]]:
    parent = nodes[parent_id]
    if not is_terminal_parent_status(parent.type, target_status):
        return []

    topology = topology or GraphTopology.from_nodes(nodes)
    blockers: list[dict[str, Any]] = []
    for descendant_id in ordered_descendant_ids(topology, parent_id):
        descendant = nodes.get(descendant_id)
        if not descendant:
            continue
        if descendant.status not in ACTIVE_DOWNSTREAM_STATUSES.get(descendant.type, set()):
            continue
        blockers.append(
            {
                "id": descendant.id,
                "type": descendant.type,
                "status": descendant.status,
                "path": topology.safe_path(descendant.id),
            }
        )
    return blockers


def terminal_parent_guard_failure(
    nodes: dict[str, ResearchNode],
    parent_id: str,
    target_status: str,
    *,
    topology: GraphTopology | None = None,
) -> dict[str, Any] | None:
    blockers = active_descendant_blockers(nodes, parent_id, target_status, topology=topology)
    if not blockers:
        return None
    return {
        "ok": False,
        "error": TERMINAL_PARENT_ACTIVE_DESCENDANTS_ERROR,
        "node_id": parent_id,
        "target_status": target_status,
        "blocking_descendants": blockers,
    }


def terminal_parent_guard_failures(
    nodes: dict[str, ResearchNode],
    *,
    topology: GraphTopology | None = None,
) -> list[dict[str, Any]]:
    topology = topology or GraphTopology.from_nodes(nodes)
    failures: list[dict[str, Any]] = []
    for node in nodes.values():
        failure = terminal_parent_guard_failure(
            nodes,
            node.id,
            node.status,
            topology=topology,
        )
        if failure:
            failure["parent_type"] = node.type
            failure["parent_status"] = node.status
            failures.append(failure)
    return failures


def terminal_parent_transition_failures(
    before_nodes: dict[str, ResearchNode],
    after_nodes: dict[str, ResearchNode],
    node_ids: list[str] | set[str],
) -> list[dict[str, Any]]:
    topology = GraphTopology.from_nodes(after_nodes)
    failures: list[dict[str, Any]] = []
    for node_id in sorted(set(node_ids)):
        after_node = after_nodes.get(node_id)
        if not after_node:
            continue
        before_node = before_nodes.get(node_id)
        if before_node and before_node.status == after_node.status:
            continue
        failure = terminal_parent_guard_failure(
            after_nodes,
            node_id,
            after_node.status,
            topology=topology,
        )
        if not failure:
            continue
        failure["parent_type"] = after_node.type
        failure["parent_status"] = after_node.status
        failure["before_status"] = before_node.status if before_node else None
        failures.append(failure)
    return failures


def lifecycle_guard_payload(root: Path, failures: list[dict[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": TERMINAL_PARENT_ACTIVE_DESCENDANTS_ERROR,
        "lifecycle_errors": failures,
        "suggested_commands": [
            (
                f"research-cockpit close-branch --root {root} --id {failure['node_id']} "
                "--downstream-status parked --dry-run --json --show-diff"
            )
            for failure in failures
        ],
    }
    if len(failures) == 1:
        failure = failures[0]
        payload.update(
            {
                "node_id": failure["node_id"],
                "target_status": failure["target_status"],
                "blocking_descendants": failure["blocking_descendants"],
            }
        )
    return payload


def raise_for_terminal_parent_transitions(
    root: Path,
    before_nodes: dict[str, ResearchNode],
    after_nodes: dict[str, ResearchNode],
    node_ids: list[str] | set[str],
) -> None:
    failures = terminal_parent_transition_failures(before_nodes, after_nodes, node_ids)
    if failures:
        raise LifecycleGuardError(lifecycle_guard_payload(root, failures))
