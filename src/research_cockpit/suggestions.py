from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
import hashlib

from research_cockpit.command_registry import cli_command_for_script
from research_cockpit.graph_core import child_ids, focus_node_id_from_current, unique_strings
from research_cockpit.types import ResearchNode


def _workflow_command(script_name: str, *parts: str) -> str:
    return cli_command_for_script(script_name, *parts)


def _priority_rank(priority: str | None) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(priority or "").lower(), 2)


def _suggestion_priority(node: ResearchNode | None, default: str = "medium") -> str:
    if node and str(node.priority or "").lower() in {"critical", "high", "medium", "low"}:
        return str(node.priority).lower()
    return default


def suggestion_key(kind: str, source_node_id: str, action: str) -> str:
    payload = f"{kind}\0{source_node_id}\0{action}".encode("utf-8")
    return f"sg_{hashlib.sha1(payload).hexdigest()[:16]}"


def _focus_related_ids(nodes: dict[str, ResearchNode], current: dict[str, Any]) -> set[str]:
    related = {
        str(node_id)
        for node_id in current.get("current_focus_path", []) or []
        if str(node_id) in nodes
    }
    for key in ("current_stage", "current_problem", "current_option", "current_focus_node"):
        node_id = current.get(key)
        if node_id in nodes:
            related.add(str(node_id))
    for node_id in list(related):
        node = nodes.get(node_id)
        if not node:
            continue
        if node.parent in nodes:
            related.add(str(node.parent))
        related.update(child_id for child_id in child_ids(nodes, node) if child_id in nodes)
    return related


def _make_suggestion(
    *,
    kind: str,
    priority: str,
    action: str,
    reason: str,
    source: ResearchNode,
    related_node_ids: list[str] | None = None,
    suggested_command: str = "",
    focus_ids: set[str] | None = None,
) -> dict[str, Any]:
    related_node_ids = related_node_ids or []
    focus_ids = focus_ids or set()
    return {
        "kind": kind,
        "priority": priority,
        "action": action,
        "reason": reason,
        "source_node_id": source.id,
        "source_node_type": source.type,
        "related_node_ids": unique_strings(related_node_ids),
        "suggested_command": suggested_command,
        "is_focus_related": source.id in focus_ids or any(node_id in focus_ids for node_id in related_node_ids),
    }


def _finalize_suggestions(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for suggestion in suggestions:
        key = (
            str(suggestion.get("kind")),
            str(suggestion.get("source_node_id")),
            str(suggestion.get("action")),
        )
        if key in seen:
            continue
        seen.add(key)
        suggestion["key"] = suggestion_key(key[0], key[1], key[2])
        deduped.append(suggestion)

    kind_rank = {
        "focus_next_action": 0,
        "resolve_blocker": 1,
        "run_experiment": 2,
        "record_finding": 3,
        "review_decision": 4,
        "fix_resource": 5,
    }
    deduped.sort(
        key=lambda item: (
            0 if item.get("is_focus_related") else 1,
            _priority_rank(item.get("priority")),
            kind_rank.get(str(item.get("kind")), 99),
            str(item.get("source_node_id")),
            str(item.get("action")),
        )
    )
    return deduped


def _apply_suggestion_lifecycle(
    suggestions: list[dict[str, Any]],
    current: dict[str, Any],
    *,
    include_inactive: bool,
) -> list[dict[str, Any]]:
    lifecycle = current.get("suggestion_lifecycle")
    lifecycle = lifecycle if isinstance(lifecycle, dict) else {}
    out: list[dict[str, Any]] = []
    for suggestion in suggestions:
        key = str(suggestion.get("key") or "")
        record = lifecycle.get(key)
        state = "active"
        reason = ""
        updated_at = ""
        if isinstance(record, dict):
            state = str(record.get("state") or "active")
            reason = str(record.get("reason") or "")
            updated_at = str(record.get("updated_at") or "")
        suggestion["lifecycle_state"] = state
        suggestion["lifecycle_reason"] = reason
        suggestion["lifecycle_updated_at"] = updated_at
        if state in {"dismissed", "completed"} and not include_inactive:
            continue
        out.append(suggestion)
    return out


def _assign_suggestion_ids(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, suggestion in enumerate(suggestions, start=1):
        display_id = f"next_action_{index:03d}"
        suggestion["display_id"] = display_id
        suggestion["suggestion_id"] = str(suggestion.get("key") or "")
        suggestion["id"] = display_id
    return suggestions


def _mark_queued_suggestions(
    suggestions: list[dict[str, Any]],
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
) -> list[dict[str, Any]]:
    current_actions = {str(action) for action in current.get("next_actions", []) or []}
    for suggestion in suggestions:
        action = str(suggestion.get("action") or "")
        source = nodes.get(str(suggestion.get("source_node_id")))
        node_actions = set()
        if source:
            node_actions = {str(item) for item in source.raw.get("next_actions", []) or []}
        suggestion["queued_in_current"] = action in current_actions
        suggestion["queued_in_node"] = action in node_actions
    return suggestions


def build_action_suggestions(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    link_rows: list[dict[str, Any]] | None = None,
    *,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    focus_ids = _focus_related_ids(nodes, current)
    suggestions: list[dict[str, Any]] = []
    focus_node_id = focus_node_id_from_current(current, nodes)
    focus_node = nodes.get(focus_node_id) if focus_node_id else None

    action_source = focus_node or nodes.get(str(current.get("current_problem"))) or nodes.get(str(current.get("current_stage")))
    if action_source:
        for action in (current.get("next_actions", []) or []) + (action_source.raw.get("next_actions", []) or []):
            if not action:
                continue
            suggestions.append(_make_suggestion(
                kind="focus_next_action",
                priority=_suggestion_priority(action_source, "high"),
                action=str(action),
                reason="Current focus or current_state lists this as a next action.",
                source=action_source,
                related_node_ids=[node_id for node_id in current.get("current_focus_path", []) or [] if node_id in nodes],
                focus_ids=focus_ids,
            ))

    active_statuses = {"active", "open", "blocked"}
    for node in sorted(nodes.values(), key=lambda item: item.id):
        blockers = node.raw.get("blockers", []) or []
        if node.type == "problem" and node.status in active_statuses and blockers:
            for blocker in blockers:
                suggestions.append(_make_suggestion(
                    kind="resolve_blocker",
                    priority=_suggestion_priority(node, "high"),
                    action=f"Resolve blocker: {blocker}",
                    reason=f"{node.id} is active and has an explicit blocker.",
                    source=node,
                    related_node_ids=[node.parent] if node.parent else [],
                    suggested_command=_workflow_command("update_status.py", "--id", node.id, "--status", "blocked"),
                    focus_ids=focus_ids,
                ))

        if node.type == "experiment" and node.status == "planned":
            suggestions.append(_make_suggestion(
                kind="run_experiment",
                priority=_suggestion_priority(nodes.get(str(node.parent)), "medium"),
                action=f"Run planned experiment: {node.title}",
                reason=f"{node.id} is still planned.",
                source=node,
                related_node_ids=[node.parent] if node.parent else [],
                suggested_command=_workflow_command("update_status.py", "--id", node.id, "--status", "running"),
                focus_ids=focus_ids,
            ))

        if node.type == "experiment" and node.status == "done" and not (node.raw.get("findings") or []):
            suggestions.append(_make_suggestion(
                kind="record_finding",
                priority=_suggestion_priority(nodes.get(str(node.parent)), "medium"),
                action=f"Record findings for completed experiment: {node.title}",
                reason=f"{node.id} is done but has no structured findings.",
                source=node,
                related_node_ids=[node.parent] if node.parent else [],
                suggested_command=_workflow_command(
                    "record_finding.py",
                    "--experiment",
                    node.id,
                    '--statement "Describe the finding"',
                    "--confidence",
                    "medium",
                ),
                focus_ids=focus_ids,
            ))

        if node.type == "decision" and node.status == "proposed":
            suggestions.append(_make_suggestion(
                kind="review_decision",
                priority=_suggestion_priority(nodes.get(str(node.parent)), "medium"),
                action=f"Review proposed decision: {node.title}",
                reason=f"{node.id} is proposed and needs acceptance or rejection.",
                source=node,
                related_node_ids=[node.parent] if node.parent else [],
                suggested_command=_workflow_command("update_decision_evidence.py", "--id", node.id),
                focus_ids=focus_ids,
            ))

    if link_rows is None:
        from research_cockpit.resources import build_link_rows

        link_rows = build_link_rows(root, nodes)

    for row in link_rows:
        if row.get("exists") is not False:
            continue
        node_id = str(row.get("node_id"))
        source = nodes.get(node_id)
        if not source:
            continue
        target = str(row.get("target") or "")
        suggestions.append(_make_suggestion(
            kind="fix_resource",
            priority="low",
            action=f"Restore or update missing resource path: {target}",
            reason=f"{node_id} links to a local resource that does not exist.",
            source=source,
            related_node_ids=[],
            suggested_command="",
            focus_ids=focus_ids,
        ))

    suggestions = _finalize_suggestions(suggestions)
    suggestions = _apply_suggestion_lifecycle(suggestions, current, include_inactive=include_inactive)
    suggestions = _assign_suggestion_ids(suggestions)
    return _mark_queued_suggestions(suggestions, nodes, current)


def build_suggestion_lifecycle_summary(current: dict[str, Any], suggestions: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"active": 0, "dismissed": 0, "completed": 0, "orphan": 0}
    suggestion_keys = {str(item.get("key")) for item in suggestions if item.get("key")}
    for suggestion in suggestions:
        state = str(suggestion.get("lifecycle_state") or "active")
        if state in counts:
            counts[state] += 1

    lifecycle = current.get("suggestion_lifecycle")
    if isinstance(lifecycle, dict):
        for key in lifecycle:
            if str(key) not in suggestion_keys:
                counts["orphan"] += 1
    return counts


def _parse_lifecycle_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def build_suggestion_lifecycle_rows(
    current: dict[str, Any],
    suggestions: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    today = today or date.today()
    by_key = {str(item.get("key")): item for item in suggestions if item.get("key")}
    lifecycle = current.get("suggestion_lifecycle")
    if not isinstance(lifecycle, dict):
        return []

    rows: list[dict[str, Any]] = []
    for key, raw_record in sorted(lifecycle.items(), key=lambda item: str(item[0])):
        record = raw_record if isinstance(raw_record, dict) else {}
        key_text = str(key)
        suggestion = by_key.get(key_text)
        updated_at = str(record.get("updated_at") or "")
        updated_date = _parse_lifecycle_date(updated_at)
        rows.append({
            "key": key_text,
            "suggestion_id": str(suggestion.get("id") or "") if suggestion else "",
            "state": str(record.get("state") or ""),
            "reason": str(record.get("reason") or ""),
            "updated_at": updated_at,
            "action": str(record.get("action") or (suggestion or {}).get("action") or ""),
            "kind": str(record.get("kind") or (suggestion or {}).get("kind") or ""),
            "source_node_id": str(record.get("source_node_id") or (suggestion or {}).get("source_node_id") or ""),
            "active_match": suggestion is not None,
            "orphan": suggestion is None,
            "age_days": (today - updated_date).days if updated_date else None,
        })
    return rows
