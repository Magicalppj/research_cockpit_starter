from __future__ import annotations

from cockpit.model import script_command, search_knowledge


def ordered_tab_labels(text: dict[str, str]) -> list[str]:
    keys = [
        "research_graph",
        "dashboard",
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
    return [text[key] for key in keys if key in text]


def default_detail_node_id(graph: dict, node_ids: list[str]) -> str:
    focus_node_id = graph.get("current_focus_node")
    if focus_node_id in node_ids:
        return focus_node_id
    return node_ids[0] if node_ids else ""


def format_node_option(nodes: dict, node_id: str) -> str:
    node = nodes.get(node_id)
    if not node:
        return node_id
    return f"{node.title} | {node.id} | {node.type}/{node.status}"


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
        for key in ("owner", "workstream_status", "objective", "recommendation", "report_summary", "latest_finding"):
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


def default_selected_statuses(graph: dict, all_statuses: list[str]) -> list[str]:
    hidden = set((graph.get("focus_mode") or {}).get("hide_statuses", []))
    defaults = [status for status in all_statuses if status not in hidden]
    return defaults or all_statuses


def filter_graph_for_view(
    graph: dict,
    view_mode: str,
    selected_types: set[str],
    selected_statuses: set[str],
) -> dict:
    max_depth = {"focus_depth_1": 1, "focus_depth_2": 2}.get(view_mode)
    visible_nodes = []
    included = set()

    for node in graph["nodes"]:
        if selected_types and node["type"] not in selected_types:
            continue
        if selected_statuses and node["status"] not in selected_statuses:
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
