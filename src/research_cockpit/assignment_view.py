from __future__ import annotations

from typing import Any

from research_cockpit.graph_core import derive_focus_path, node_id_by_type_in_path
from research_cockpit.resources import node_artifact_ids


DEFAULT_ASSIGNMENT_STATUSES = ("queued", "running")
DEFAULT_ASSIGNMENT_PRIORITIES = ("critical", "high")


def _as_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _node_ref(nodes: dict[str, Any], node_id: str) -> dict[str, Any]:
    node = nodes[node_id]
    return {
        "id": node.id,
        "title": node.title,
        "type": node.type,
        "status": node.status,
        "priority": node.priority,
    }


def _node_refs(nodes: dict[str, Any], node_ids: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node_id in node_ids:
        if node_id in seen or node_id not in nodes:
            continue
        seen.add(node_id)
        out.append(_node_ref(nodes, node_id))
    return out


def _parent_option(nodes: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    try:
        path = derive_focus_path(nodes, node_id)
    except ValueError:
        return None
    option_id = node_id_by_type_in_path(nodes, path, "option", nearest=True)
    if option_id and option_id in nodes:
        return _node_ref(nodes, option_id)
    return None


def _priority_rank(priority: str | None) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(priority or "").lower(), 9)


def _assignment_sort_key(row: dict[str, Any]) -> tuple[int, str, str, str]:
    status_rank = {"queued": "0", "running": "1"}.get(str(row.get("status") or ""), "9")
    return (
        _priority_rank(row.get("priority")),
        str(row.get("order") or row.get("rank") or ""),
        status_rank,
        str(row.get("id") or ""),
    )


def build_assignment_view(
    nodes: dict[str, Any],
    *,
    statuses: list[str] | tuple[str, ...] | None = None,
    priorities: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    wanted_statuses = {str(status).lower() for status in (statuses or DEFAULT_ASSIGNMENT_STATUSES)}
    wanted_priorities = {str(priority).lower() for priority in (priorities or DEFAULT_ASSIGNMENT_PRIORITIES)}
    rows: list[dict[str, Any]] = []

    for node in nodes.values():
        if node.type != "experiment":
            continue
        if str(node.status).lower() not in wanted_statuses:
            continue
        if str(node.priority or "").lower() not in wanted_priorities:
            continue

        next_actions = _as_list(node.raw.get("next_actions"))
        artifact_ids = _as_list(node.raw.get("linked_artifacts"))
        artifact_ids.extend(str(item) for item in node_artifact_ids(node) if str(item).strip())
        rows.append({
            "id": node.id,
            "title": node.title,
            "status": node.status,
            "priority": node.priority,
            "order": node.raw.get("order"),
            "rank": node.raw.get("rank"),
            "parent_option": _parent_option(nodes, node.id),
            "owner": node.raw.get("owner"),
            "ready_for_agent": node.raw.get("ready_for_agent"),
            "depends_on": _node_refs(nodes, _as_list(node.raw.get("depends_on"))),
            "blocked_by": _node_refs(nodes, _as_list(node.raw.get("blocked_by"))),
            "key_artifacts": _node_refs(nodes, artifact_ids),
            "next_action": next_actions[0] if next_actions else None,
            "next_actions": next_actions,
            "handoff_context": node.raw.get("handoff_context"),
            "summary": node.summary,
        })

    rows = sorted(rows, key=_assignment_sort_key)
    return {
        "schema_version": "assignment_view_v1",
        "filters": {
            "node_type": "experiment",
            "statuses": sorted(wanted_statuses),
            "priorities": sorted(wanted_priorities),
        },
        "count": len(rows),
        "assignments": rows,
    }
