from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from research_cockpit.baselines import empty_effective_baseline, resolve_effective_baseline
from research_cockpit.model import script_command, search_knowledge


PRIMARY_GRAPH_NODE_TYPES = ("stage", "problem", "option", "experiment", "decision")
BASELINE_LENS_DEFAULT_MODES = {"focus_depth_1", "focus_depth_2", "current_branch", "option_workstream", "global"}
DEFAULT_HIDDEN_GRAPH_STATUSES = {"done"}
DEFAULT_GRAPH_VIEW_MODE = "global"


def ordered_tab_keys(text: dict[str, str]) -> list[str]:
    keys = [
        "research_graph",
        "dashboard",
        "baselines",
        "branch_comparison",
        "decision_trace",
        "action_guidance",
        "option_workstreams",
        "search",
        "resources",
        "experiment_matrix",
        "decisions",
        "agent_context",
        "data_health",
    ]
    return [key for key in keys if key in text]


def ordered_tab_labels(text: dict[str, str]) -> list[str]:
    keys = ordered_tab_keys(text)
    return [text[key] for key in keys if key in text]


def baseline_command_problem_ids(baseline_rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in baseline_rows:
        problem_id = str(row.get("problem_id") or "")
        if problem_id and problem_id not in seen:
            out.append(problem_id)
            seen.add(problem_id)
    return out


def default_detail_node_id(graph: dict, node_ids: list[str]) -> str:
    focus_node_id = graph.get("current_focus_node")
    if focus_node_id in node_ids:
        return focus_node_id
    return node_ids[0] if node_ids else ""


def default_show_baseline_lens(view_mode: str) -> bool:
    return view_mode in BASELINE_LENS_DEFAULT_MODES


def format_node_option(nodes: dict, node_id: str) -> str:
    node = nodes.get(node_id)
    if not node:
        return node_id
    return f"{node.title} | {node.id} | {node.type}/{node.status}"


def _first_text(*values: object) -> str:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return ""


def build_node_overview(
    node: Any,
    nodes: dict,
    current: dict | None,
    link_rows: list[dict] | None,
    action_suggestions: list[dict] | None,
) -> dict[str, Any]:
    raw = getattr(node, "raw", {}) or {}
    purpose_by_type = {
        "problem": (raw.get("question"), getattr(node, "summary", "")),
        "option": (raw.get("hypothesis"), getattr(node, "summary", "")),
        "experiment": (getattr(node, "summary", ""), raw.get("hypothesis")),
        "decision": (raw.get("decision_summary"), raw.get("rationale"), getattr(node, "summary", "")),
    }
    purpose = _first_text(*(purpose_by_type.get(getattr(node, "type", ""), (getattr(node, "summary", ""),))))

    blockers = [str(item) for item in raw.get("blockers", []) or [] if item not in (None, "")]
    if blockers:
        current_state = {"kind": "blockers", "items": blockers}
    else:
        state_value = _first_text(
            raw.get("result_summary"),
            raw.get("evidence_summary"),
            raw.get("outcome"),
            f"{getattr(node, 'title', getattr(node, 'id', ''))} is currently {getattr(node, 'status', '')}.",
        )
        state_kind = "status"
        for candidate in ("result_summary", "evidence_summary", "outcome"):
            if raw.get(candidate) not in (None, ""):
                state_kind = candidate
                break
        current_state = {"kind": state_kind, "items": [state_value] if state_value else []}

    next_items = [str(item) for item in raw.get("next_actions", []) or [] if item not in (None, "")]
    for suggestion in action_suggestions or []:
        related_ids = [str(item) for item in suggestion.get("related_node_ids", []) or []]
        if suggestion.get("source_node_id") == getattr(node, "id", "") or getattr(node, "id", "") in related_ids:
            action = suggestion.get("action")
            if action not in (None, "") and str(action) not in next_items:
                next_items.append(str(action))

    node_link_rows = [
        row
        for row in (link_rows or [])
        if row.get("node_id") == getattr(node, "id", "")
    ]
    try:
        effective_baseline = resolve_effective_baseline(nodes, getattr(node, "id", ""), current or {})
    except ValueError:
        effective_baseline = empty_effective_baseline()

    def relation_row(node_id: str) -> dict[str, str]:
        return {
            "id": node_id,
            "label": format_node_option(nodes, node_id),
        }

    parent_id = getattr(node, "parent", None)
    child_ids = list(getattr(node, "children", []) or [])
    relations = {
        "parent": [relation_row(str(parent_id))] if parent_id else [],
        "children": [relation_row(str(child_id)) for child_id in child_ids],
    }

    return {
        "purpose": purpose,
        "current_state": current_state,
        "next": next_items,
        "key_resources": node_link_rows[:3],
        "relations": relations,
        "effective_baseline": effective_baseline,
    }


def build_set_focus_command(current: dict, focus_node_id: str) -> str:
    parts = [script_command("set_focus.py")]
    for field, flag in (
        ("current_stage", "--stage"),
        ("current_problem", "--problem"),
        ("current_option", "--option"),
    ):
        value = current.get(field)
        if value:
            parts.extend([flag, str(value)])
    parts.extend(["--focus-node", focus_node_id])
    return " ".join(parts)


def build_record_finding_command(experiment_id: str) -> str:
    return (
        f"{script_command('record_finding.py')}"
        f" --experiment {experiment_id}"
        ' --statement "Describe the finding"'
        " --confidence medium"
        " --outcome inconclusive"
    )


def build_promote_decision_command(option_id: str) -> str:
    return (
        f"{script_command('promote_decision.py')}"
        " --id decision_new"
        f" --option {option_id}"
        ' --title "Decision title"'
        ' --summary "Decision summary"'
        " --status proposed"
    )


def build_check_decision_acceptance_command(decision_id: str) -> str:
    return (
        f"{script_command('check_decision_acceptance.py')}"
        f" --id {decision_id}"
    )


def build_accept_decision_command(decision_id: str) -> str:
    return (
        f"{script_command('accept_decision.py')}"
        f" --id {decision_id}"
    )


def build_update_decision_checklist_command(decision_id: str) -> str:
    return (
        f"{script_command('update_decision_checklist.py')}"
        f" --id {decision_id}"
        " --alternative <option_id>"
        ' --consequence "Describe downstream impact"'
        ' --next-required-action "Describe required follow-up"'
    )


def build_update_decision_evidence_command(decision_id: str) -> str:
    return (
        f"{script_command('update_decision_evidence.py')}"
        f" --id {decision_id}"
    )


def build_create_note_command(node_id: str) -> str:
    return (
        f"{script_command('create_note.py')}"
        f" --node {node_id}"
    )


def build_claim_option_command(option_id: str) -> str:
    return (
        f"{script_command('claim_option.py')}"
        f" --option {option_id}"
        " --agent <agent_id>"
        ' --objective "Describe objective"'
    )


def build_option_workstream_context_command(option_id: str) -> str:
    return (
        f"{script_command('option_workstream_context.py')}"
        f" --option {option_id}"
        " --json"
    )


def build_report_option_workstream_command(option_id: str) -> str:
    return (
        f"{script_command('report_option_workstream.py')}"
        f" --option {option_id}"
        " --agent <agent_id>"
        " --recommend continue"
        ' --summary "Summarize evidence and recommendation"'
    )


def build_apply_suggestion_command(suggestion_id: str, target: str = "current") -> str:
    return (
        f"{script_command('apply_suggestion.py')}"
        f" --id {suggestion_id}"
        f" --target {target}"
    )


def build_update_suggestion_state_command(suggestion_id: str, state: str) -> str:
    return (
        f"{script_command('update_suggestion_state.py')}"
        f" --id {suggestion_id}"
        f" --state {state}"
    )


def build_cleanup_suggestion_lifecycle_command(
    *,
    dry_run: bool = True,
    state: str = "all",
    older_than_days: int | None = None,
) -> str:
    command = script_command("cleanup_suggestion_lifecycle.py")
    if dry_run:
        command += " --dry-run"
    command += f" --state {state}"
    if older_than_days is not None:
        command += f" --older-than-days {older_than_days}"
    return command


def edge_style_for_type(edge_type: str | None) -> dict[str, object]:
    styles: dict[str, dict[str, object]] = {
        "supports": {"color": "#16A34A", "dashes": False},
        "contradicts": {"color": "#DC2626", "dashes": True},
        "validates": {"color": "#2563EB", "dashes": False},
        "contains": {"color": "#888888", "dashes": False},
    }
    return styles.get(str(edge_type or ""), {"color": "#888888", "dashes": False})


def _baseline_badges_for_node(node: dict[str, Any]) -> list[str]:
    badges: list[str] = []
    if node.get("is_current_effective_baseline_option"):
        badges.append("CURRENT BASELINE")
    elif node.get("is_effective_baseline_option"):
        badges.append("BASELINE")
    if node.get("is_baseline_source"):
        badges.append("SOURCE")
    return badges


def _baseline_visual_edges(graph_nodes: list[dict[str, Any]], included: set[str]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str, str]] = set()

    def add_edge(source: str, target: str, edge_type: str, label: str, color: str, width: float) -> None:
        if source not in included or target not in included or source == target:
            return
        key = (source, target, edge_type)
        if key in seen_pairs:
            return
        seen_pairs.add(key)
        edges.append({
            "id": f"{edge_type}--{source}--{target}",
            "source": source,
            "target": target,
            "label": label,
            "type": edge_type,
            "color": color,
            "dashes": True,
            "width": width,
        })

    for node in graph_nodes:
        source = str(node.get("baseline_source_id") or "")
        target = str(node.get("effective_baseline_option_id") or "")
        add_edge(source, target, "baseline", "baseline", "#0F766E", 1.7)

    return edges


def build_graph_component_payload(
    graph: dict,
    selected_node_id: str | None = None,
    *,
    show_baseline_lens: bool = False,
) -> dict[str, Any]:
    nodes = []
    included: set[str] = set()

    for node in graph.get("nodes", []):
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        included.add(node_id)
        payload_node = {
            "id": node_id,
            "label": str(node.get("label") or node.get("title") or node_id),
            "title": str(node.get("title") or node.get("label") or ""),
            "type": str(node.get("type") or ""),
            "status": str(node.get("status") or ""),
            "priority": str(node.get("priority") or ""),
            "color": str(node.get("color") or "#EEEEEE"),
            "is_current_focus": bool(node.get("is_current_focus")),
            "is_focus": bool(node.get("is_focus")),
        }
        if show_baseline_lens:
            baseline_option_id = str(node.get("effective_baseline_option_id") or "")
            if baseline_option_id:
                payload_node["effective_baseline_option_id"] = baseline_option_id
            badges = _baseline_badges_for_node(node)
            if badges:
                payload_node["badges"] = badges
        nodes.append(payload_node)

    edges = []
    for index, edge in enumerate(graph.get("edges", [])):
        source = str(edge.get("from") or edge.get("source") or "")
        target = str(edge.get("to") or edge.get("target") or "")
        if source not in included or target not in included:
            continue
        edge_type = str(edge.get("type") or edge.get("relation") or "")
        style = edge_style_for_type(edge_type)
        strength = edge.get("strength")
        try:
            width = 1 + min(4, max(0, float(strength) * 4)) if strength is not None else 1
        except (TypeError, ValueError):
            width = 1
        edges.append({
            "id": f"{source}--{target}--{edge_type or 'edge'}--{index}",
            "source": source,
            "target": target,
            "label": edge.get("label"),
            "type": edge_type,
            "color": style["color"],
            "dashes": bool(style["dashes"]),
            "width": width,
        })

    selected = str(selected_node_id) if selected_node_id in included else None
    if show_baseline_lens:
        edges.extend(_baseline_visual_edges(graph.get("nodes", []), included))
    return {
        "nodes": nodes,
        "edges": edges,
        "selected_node_id": selected,
    }


def graph_component_selected_node_id(value: object, visible_node_ids: list[str] | set[str]) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        selected = value
    elif isinstance(value, dict):
        selected = value.get("selected_node_id") or value.get("id")
    else:
        selected = getattr(value, "selected_node_id", None) or getattr(value, "id", None)

    if selected in (None, ""):
        return None
    node_id = str(selected)
    return node_id if node_id in set(visible_node_ids) else None


def graph_component_event_id(value: object) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        event_id = value.get("event_id")
    else:
        event_id = getattr(value, "event_id", None)
    if event_id in (None, ""):
        return None
    return str(event_id)


def format_comparison_rows(rows: list[dict]) -> list[dict]:
    formatted = []
    for row in rows:
        next_row = dict(row)
        for key in ("pros", "cons"):
            value = next_row.get(key)
            if isinstance(value, list):
                next_row[key] = "; ".join(str(item) for item in value)
        if "is_current_best" in next_row:
            next_row["is_current_best"] = "yes" if next_row["is_current_best"] else ""
        formatted.append(next_row)
    return formatted


def format_finding_rows(findings: list[dict]) -> list[dict]:
    rows = []
    for finding in findings:
        row = dict(finding)
        for key in ("evidence", "metrics", "linked_artifacts"):
            value = row.get(key)
            if isinstance(value, list):
                row[key] = "; ".join(str(item) for item in value)
        rows.append(row)
    return rows


def format_resource_rows(rows: list[dict]) -> list[dict]:
    labels = {True: "yes", False: "missing", None: "unknown"}
    formatted = []
    for row in rows:
        next_row = dict(row)
        next_row["exists"] = labels.get(next_row.get("exists"), "unknown")
        formatted.append(next_row)
    return formatted


def format_resource_index_rows(rows: list[dict], search_index: list[dict]) -> list[dict]:
    by_resource = {
        (
            str(entry.get("node_id") or ""),
            str(entry.get("resource_label") or ""),
            str(entry.get("target") or ""),
        ): entry
        for entry in search_index
        if entry.get("source") == "resource"
    }
    formatted = format_resource_rows(rows)
    for row in formatted:
        key = (
            str(row.get("node_id") or ""),
            str(row.get("label") or ""),
            str(row.get("target") or ""),
        )
        entry = by_resource.get(key) or {}
        skip_reason = str(entry.get("skip_reason") or "")
        row["indexed"] = "yes" if entry and not skip_reason else ""
        row["truncated"] = "yes" if entry.get("truncated") else ""
        row["skip_reason"] = skip_reason
    return formatted


def format_action_suggestion_rows(suggestions: list[dict]) -> list[dict]:
    formatted = []
    for suggestion in suggestions:
        row = dict(suggestion)
        related = row.get("related_node_ids")
        if isinstance(related, list):
            row["related_node_ids"] = "; ".join(str(item) for item in related)
        row["is_focus_related"] = "yes" if row.get("is_focus_related") else ""
        row["queued_in_current"] = "yes" if row.get("queued_in_current") else ""
        row["queued_in_node"] = "yes" if row.get("queued_in_node") else ""
        row["lifecycle_state"] = row.get("lifecycle_state") or "active"
        row["lifecycle_reason"] = row.get("lifecycle_reason") or ""
        formatted.append(row)
    return formatted


def format_suggestion_lifecycle_rows(rows: list[dict]) -> list[dict]:
    formatted = []
    for row in rows:
        item = dict(row)
        item["active_match"] = "yes" if item.get("active_match") else ""
        item["orphan"] = "yes" if item.get("orphan") else ""
        if item.get("age_days") is None:
            item["age_days"] = ""
        formatted.append(item)
    return formatted


def format_option_workstream_rows(rows: list[dict]) -> list[dict]:
    formatted = []
    for row in rows:
        item = dict(row)
        for key in (
            "owner",
            "session_id",
            "git_branch",
            "worktree_label",
            "agent_focus_node",
            "workstream_status",
            "objective",
            "recommendation",
            "report_summary",
            "latest_finding",
            "last_update",
        ):
            item[key] = item.get(key) or ""
        formatted.append(item)
    return formatted


def filter_action_suggestions(
    suggestions: list[dict],
    selected_kinds: set[str],
    selected_priorities: set[str],
    selected_states: set[str],
    focus_only: bool,
) -> list[dict]:
    return [
        suggestion
        for suggestion in suggestions
        if suggestion.get("kind") in selected_kinds
        and suggestion.get("priority") in selected_priorities
        and suggestion.get("lifecycle_state", "active") in selected_states
        and (not focus_only or suggestion.get("is_focus_related"))
    ]


def filter_search_results(
    search_index: list[dict],
    query: str,
    selected_sources: set[str],
    selected_node_types: set[str],
    *,
    focus_only: bool,
    limit: int,
) -> list[dict]:
    sources = selected_sources or None
    node_types = selected_node_types or None
    return search_knowledge(
        search_index,
        query,
        sources=sources,
        node_types=node_types,
        focus_only=focus_only,
        limit=limit,
    )


def format_search_result_rows(results: list[dict]) -> list[dict]:
    rows = []
    for result in results:
        rows.append({
            "score": result.get("score"),
            "source": result.get("source"),
            "node": result.get("node_id") or "",
            "node_type": result.get("node_type") or "",
            "title": result.get("title") or "",
            "path": result.get("path") or "",
            "truncated": "yes" if result.get("truncated") else "",
            "snippet": result.get("snippet") or "",
        })
    return rows


def format_evidence_summary(summary: dict) -> dict:
    outcome_counts = summary.get("outcome_counts") or {}
    return {
        "evidence_summary": summary.get("summary_text") or summary.get("evidence_summary") or "",
        "experiment_count": summary.get("experiment_count", 0),
        "findings_count": summary.get("findings_count", 0),
        "outcome_counts": "; ".join(f"{key}: {value}" for key, value in sorted(outcome_counts.items())),
        "latest_finding": summary.get("latest_finding") or "",
    }


def format_decision_checklist(checklist: dict) -> list[dict]:
    rows = []
    for item in checklist.get("checks", []) or []:
        related = item.get("related_node_ids") or []
        rows.append({
            "id": item.get("id"),
            "state": item.get("state"),
            "blocking": "yes" if item.get("blocking") else "",
            "label": item.get("label"),
            "reason": item.get("reason"),
            "related_node_ids": "; ".join(str(node_id) for node_id in related),
        })
    return rows


def format_decision_repair_hints(checklist: dict) -> list[dict]:
    decision_id = str(checklist.get("decision_id") or "<decision_id>")
    rows = []

    def append_row(
        item: dict,
        repair_kind: str,
        *,
        suggested_command: str = "",
        target_field: str = "",
        details: str = "",
    ) -> None:
        rows.append({
            "check_id": item.get("id") or "",
            "label": item.get("label") or item.get("id") or "",
            "reason": item.get("reason") or "",
            "repair_kind": repair_kind,
            "suggested_command": suggested_command,
            "target_field": target_field,
            "details": details,
        })

    for item in checklist.get("blocking_failures", []) or []:
        check_id = str(item.get("id") or "")
        related = [str(node_id) for node_id in item.get("related_node_ids", []) or []]
        if check_id == "supporting_experiments":
            reason = str(item.get("reason") or "")
            if "Invalid supporting experiment" in reason:
                append_row(
                    item,
                    "yaml",
                    target_field="supporting_experiments",
                    details="Replace invalid supporting experiment ids with existing experiment node ids.",
                )
            else:
                append_row(
                    item,
                    "command",
                    suggested_command=build_update_decision_evidence_command(decision_id),
                    target_field="supporting_experiments",
                    details=(
                        "Refresh decision evidence if option experiments already exist; "
                        "otherwise add supporting experiment ids to the decision YAML."
                    ),
                )
        elif check_id == "supporting_evidence":
            experiment_id = related[0] if related else "<experiment_id>"
            append_row(
                item,
                "command",
                suggested_command=(
                    f"{build_record_finding_command(experiment_id)}\n"
                    f"{build_update_decision_evidence_command(decision_id)}"
                ),
                target_field="findings",
                details="Record evidence on a supporting experiment, then refresh decision evidence.",
            )
        elif check_id == "evidence_strength":
            append_row(
                item,
                "command",
                suggested_command=build_update_decision_evidence_command(decision_id),
                target_field="evidence_strength",
                details="Refresh derived decision evidence after supporting experiments contain findings.",
            )
        elif check_id == "evidence_summary":
            append_row(
                item,
                "command",
                suggested_command=(
                    f"{build_update_decision_evidence_command(decision_id)}\n"
                    f"{script_command('update_decision_checklist.py')}"
                    f" --id {decision_id}"
                    ' --evidence-summary "Summarize supporting evidence"'
                ),
                target_field="evidence_summary",
                details="Refresh derived evidence, or write a manual evidence summary when needed.",
            )
        elif check_id == "alternatives_considered":
            append_row(
                item,
                "command",
                suggested_command=(
                    f"{script_command('update_decision_checklist.py')}"
                    f" --id {decision_id}"
                    " --alternative <option_id>"
                ),
                target_field="alternatives_considered",
                details="Add at least one alternative option id considered before accepting the decision.",
            )
        elif check_id == "consequences":
            append_row(
                item,
                "command",
                suggested_command=(
                    f"{script_command('update_decision_checklist.py')}"
                    f" --id {decision_id}"
                    ' --consequence "Describe downstream impact"'
                ),
                target_field="consequences",
                details="Record the downstream impact of accepting the decision.",
            )
        elif check_id == "next_required_actions":
            append_row(
                item,
                "command",
                suggested_command=(
                    f"{script_command('update_decision_checklist.py')}"
                    f" --id {decision_id}"
                    ' --next-required-action "Describe required follow-up"'
                ),
                target_field="next_required_actions",
                details="Record the immediate follow-up action required after acceptance.",
            )
        elif check_id == "alternative_refs":
            append_row(
                item,
                "yaml",
                target_field="alternatives_considered",
                details="Replace invalid alternatives with existing option node ids.",
            )
        elif check_id in {"decision_parent", "problem_parent"}:
            append_row(
                item,
                "yaml",
                target_field="parent",
                details="Fix the decision -> option -> problem parent chain in graph node YAML.",
            )
        else:
            append_row(
                item,
                "yaml",
                target_field=check_id,
                details="Review and repair the corresponding decision YAML field.",
            )
    return rows


def filter_node_ids(nodes: dict, query: str) -> list[str]:
    query = query.strip().lower()
    node_ids = sorted(nodes.keys())
    if not query:
        return node_ids

    matches = []
    for node_id in node_ids:
        node = nodes[node_id]
        fields = [
            getattr(node, "id", node_id),
            getattr(node, "title", ""),
            getattr(node, "summary", ""),
            getattr(node, "type", ""),
            getattr(node, "status", ""),
            " ".join(str(tag) for tag in getattr(node, "tags", []) or []),
        ]
        if query in " ".join(str(field).lower() for field in fields):
            matches.append(node_id)
    return matches


def default_graph_statuses(all_statuses: list[str], hidden_statuses: set[str] | None = None) -> list[str]:
    hidden = DEFAULT_HIDDEN_GRAPH_STATUSES | set(hidden_statuses or set())
    defaults = [status for status in all_statuses if status not in hidden]
    return defaults or all_statuses


def default_selected_statuses(graph: dict, all_statuses: list[str]) -> list[str]:
    hidden = set((graph.get("focus_mode") or {}).get("hide_statuses", []))
    return default_graph_statuses(all_statuses, hidden)


def default_selected_node_types(all_types: list[str]) -> list[str]:
    primary_types = set(PRIMARY_GRAPH_NODE_TYPES)
    return [node_type for node_type in all_types if node_type in primary_types]


def graph_filter_options(graph: dict) -> dict[str, list[str]]:
    available = graph.get("available_filters") or {}
    keys = ("types", "statuses", "stages", "focus_roles", "workstreams", "priorities")
    options: dict[str, list[str]] = {}
    for key in keys:
        if key in available:
            options[key] = sorted(str(value) for value in available.get(key, []) if value not in (None, ""))
            continue
        field_name = {
            "types": "type",
            "statuses": "status",
            "stages": "stage_id",
            "focus_roles": "focus_role",
            "workstreams": "option_workstream_id",
            "priorities": "priority",
        }[key]
        options[key] = sorted(
            {
                str(node[field_name])
                for node in graph.get("nodes", [])
                if node.get(field_name) not in (None, "")
            }
        )
    return options


def _saved_view_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _filter_saved_values(values: object, allowed_values: list[str]) -> list[str]:
    if isinstance(values, str):
        raw_values = [values]
    elif isinstance(values, (list, tuple, set)):
        raw_values = values
    else:
        raw_values = []

    allowed = set(allowed_values)
    out: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        item = str(value)
        if item not in allowed or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def graph_view_state_from_saved_view(
    view: dict,
    options: dict[str, list[str]],
    mode_value_to_label: dict[str, str],
) -> dict[str, object]:
    filters = view.get("filters") if isinstance(view.get("filters"), dict) else {}
    scope = str(view.get("scope") or DEFAULT_GRAPH_VIEW_MODE)
    mode_label = (
        mode_value_to_label.get(scope)
        or mode_value_to_label.get(DEFAULT_GRAPH_VIEW_MODE)
        or mode_value_to_label.get("focus_depth_2")
        or next(iter(mode_value_to_label.values()), scope)
    )
    return {
        "graph_view_mode": mode_label,
        "graph_node_types": _filter_saved_values(filters.get("node_types"), options.get("types", [])),
        "graph_statuses": _filter_saved_values(filters.get("statuses"), options.get("statuses", [])),
        "graph_stages": _filter_saved_values(filters.get("stages"), options.get("stages", [])),
        "graph_focus_roles": _filter_saved_values(filters.get("focus_roles"), options.get("focus_roles", [])),
        "graph_workstreams": _filter_saved_values(filters.get("workstreams"), options.get("workstreams", [])),
        "graph_only_blocking": _saved_view_bool(filters.get("only_blocking", False)),
        "graph_only_next_actions": _saved_view_bool(filters.get("only_next_actions", False)),
        "graph_only_missing_evidence": _saved_view_bool(filters.get("only_missing_evidence", False)),
        "graph_show_baseline_lens": (
            _saved_view_bool(filters["show_baseline_lens"])
            if "show_baseline_lens" in filters
            else default_show_baseline_lens(scope)
        ),
    }


def reset_global_graph_filter_state(
    session_state: MutableMapping[str, Any],
    view_mode: str,
    *,
    all_types: list[str],
    all_statuses: list[str],
    all_stages: list[str],
    all_focus_roles: list[str],
) -> bool:
    skip_reset = bool(session_state.pop("graph_skip_global_filter_reset", False))
    previous_view_mode = session_state.get("graph_previous_view_mode")
    session_state["graph_previous_view_mode"] = view_mode
    if skip_reset:
        return False

    changed = False
    if previous_view_mode == "option_workstream" and view_mode != "option_workstream":
        if session_state.get("graph_workstreams"):
            session_state["graph_workstreams"] = []
            changed = True

    if view_mode == "global" and previous_view_mode != "global":
        session_state["graph_node_types"] = default_selected_node_types(all_types)
        session_state["graph_statuses"] = default_graph_statuses(all_statuses)
        session_state["graph_stages"] = list(all_stages)
        session_state["graph_focus_roles"] = list(all_focus_roles)
        session_state["graph_workstreams"] = []
        session_state["graph_only_blocking"] = False
        session_state["graph_only_next_actions"] = False
        session_state["graph_only_missing_evidence"] = False
        changed = True

    return changed


def filter_graph_for_view(
    graph: dict,
    view_mode: str,
    selected_types: set[str],
    selected_statuses: set[str],
    selected_stages: set[str] | None = None,
    selected_focus_roles: set[str] | None = None,
    selected_workstreams: set[str] | None = None,
    *,
    only_blocking: bool = False,
    only_next_actions: bool = False,
    only_missing_evidence: bool = False,
) -> dict:
    max_depth = {"focus_depth_1": 1, "focus_depth_2": 2}.get(view_mode)
    visible_nodes = []
    included = set()
    selected_stages = selected_stages or set()
    selected_focus_roles = selected_focus_roles or set()
    selected_workstreams = selected_workstreams or set()
    if view_mode == "option_workstream" and not selected_workstreams:
        return {
            **graph,
            "nodes": [],
            "edges": [],
        }
    upstream_problem_ids = {
        str(node.get("option_workstream_upstream_problem_id"))
        for node in graph.get("nodes", [])
        if node.get("option_workstream_id") in selected_workstreams
        and node.get("option_workstream_upstream_problem_id")
    }

    for node in graph["nodes"]:
        if selected_types and node["type"] not in selected_types:
            continue
        if selected_statuses and node["status"] not in selected_statuses:
            continue
        if selected_stages and node.get("stage_id") not in selected_stages:
            continue
        if selected_focus_roles and node.get("focus_role") not in selected_focus_roles:
            continue
        if selected_workstreams:
            in_workstream = node.get("option_workstream_id") in selected_workstreams
            is_upstream_problem = node.get("id") in upstream_problem_ids
            if not in_workstream and not is_upstream_problem:
                continue
        if only_blocking and not node.get("has_blockers"):
            continue
        if only_next_actions and not node.get("has_next_actions"):
            continue
        if only_missing_evidence and node.get("has_evidence"):
            continue
        if view_mode == "current_branch" and not node.get("in_current_branch"):
            continue
        if view_mode == "option_workstream" and selected_workstreams:
            in_workstream = node.get("option_workstream_id") in selected_workstreams
            is_upstream_problem = node.get("id") in upstream_problem_ids
            if not in_workstream and not is_upstream_problem:
                continue
        if max_depth is not None:
            depth = node.get("focus_visible_depth")
            if not node.get("is_focus_visible") or depth is None or depth > max_depth:
                continue
        visible_nodes.append(node)
        included.add(node["id"])

    visible_edges = [
        edge
        for edge in graph["edges"]
        if edge["from"] in included and edge["to"] in included
    ]
    return {
        **graph,
        "nodes": visible_nodes,
        "edges": visible_edges,
    }


def context_rows(items: list[dict]) -> list[dict]:
    rows = []
    for item in items:
        rows.append({
            "id": item.get("id"),
            "type": item.get("type"),
            "title": item.get("title"),
            "status": item.get("status"),
            "summary": item.get("summary"),
        })
    return rows
