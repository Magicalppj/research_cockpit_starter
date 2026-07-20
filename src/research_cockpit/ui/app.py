from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd
import streamlit as st

try:
    from research_cockpit.paths import default_data_root
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from research_cockpit.paths import default_data_root

RESEARCH_ROOT = default_data_root()
COMMAND_LANGUAGE = "bash"

from research_cockpit.context_packs import build_agent_context, build_dashboard_read_models
from research_cockpit.coordination import build_coordination_snapshot
from research_cockpit.baselines import (
    build_accepted_decision_rows,
    build_accepted_option_rows,
    build_baseline_overview_rows,
    build_set_baseline_command,
)
from research_cockpit.model import (
    build_experiment_matrix,
    build_search_index_summary,
    graph_to_json,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
)
from research_cockpit.decisions import (
    build_decision_acceptance_checklist,
    build_decision_evidence_summary,
    build_decision_rows,
    build_decision_trace,
)
from research_cockpit.graph_views import load_graph_views, upsert_graph_view
from research_cockpit.option_workstreams import build_branch_comparison
from research_cockpit.suggestions import (
    build_action_suggestions,
    build_suggestion_lifecycle_rows,
    build_suggestion_lifecycle_summary,
)
from research_cockpit.ui.graph_component import graph_component_build_available, render_research_graph_component
from research_cockpit.ui.pyvis_renderer import build_pyvis_html, render_pyvis_graph
from research_cockpit.ui.text import get_text
from research_cockpit.ui.view_helpers import (
    DEFAULT_GRAPH_VIEW_MODE,
    DEFAULT_HIDE_INACTIVE_OPTION_BRANCHES,
    baseline_command_problem_ids,
    build_apply_suggestion_command,
    build_accept_decision_command,
    build_check_decision_acceptance_command,
    build_claim_option_command,
    build_cleanup_suggestion_lifecycle_command,
    build_create_note_command,
    build_node_overview,
    build_option_workstream_context_command,
    build_promote_decision_command,
    build_record_finding_command,
    build_report_option_workstream_command,
    build_set_focus_command,
    build_update_decision_checklist_command,
    build_update_suggestion_state_command,
    context_rows,
    build_graph_component_base_payload,
    build_graph_component_payload_from_base,
    default_detail_node_id,
    default_show_baseline_lens,
    default_selected_node_types,
    default_selected_statuses,
    filter_action_suggestions,
    filter_graph_for_view_with_visibility,
    filter_node_ids,
    filter_search_results,
    graph_component_event_id,
    graph_component_selected_node_id,
    graph_view_state_from_saved_view,
    graph_filter_options,
    format_action_suggestion_rows,
    format_comparison_rows,
    format_decision_checklist,
    format_decision_repair_hints,
    format_evidence_summary,
    format_finding_rows,
    format_node_option,
    format_option_workstream_rows,
    format_resource_index_rows,
    format_resource_rows,
    format_search_result_rows,
    format_suggestion_lifecycle_rows,
    graph_component_payload_cache_key,
    graph_filter_cache_key,
    ordered_tab_keys,
    revealable_child_ids_for_view,
    reset_global_graph_filter_state,
)
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.commands.set_focus import set_focus as save_current_focus
from research_cockpit.commands.apply_suggestion import apply_suggestion as queue_suggestion
from research_cockpit.commands.update_suggestion_state import update_suggestion_state as set_suggestion_state


st.set_page_config(page_title="Research Cockpit", layout="wide")


_DASHBOARD_JSON_FILES = {
    "graph": "graph_view.json",
    "context": "agent_context_pack.json",
    "link_rows": "linked_resources.json",
    "action_suggestions": "next_action_suggestions.json",
    "search_index": "search_index.json",
    "option_workstreams": "option_workstreams.json",
}


def _baseline_ref_label(ref: object) -> str:
    if isinstance(ref, dict):
        node_id = str(ref.get("id") or "")
        title = str(ref.get("title") or "")
        if node_id and title:
            return f"{title} | {node_id}"
        return title or node_id
    return ""


def _truth_source_revision(root: Path) -> tuple[tuple[str, int, int], ...]:
    paths: list[Path] = []
    current_state = root / "current_state.yaml"
    if current_state.is_file():
        paths.append(current_state)
    for directory, pattern in (
        (root / "graph", "**/*.yaml"),
        (root / "notes", "**/*.md"),
        (root / "runs", "**/*.yaml"),
        (root / "gate_results", "**/*.yaml"),
        (root / "gate_results", "**/*.json"),
    ):
        if directory.exists():
            paths.extend(path for path in directory.glob(pattern) if path.is_file())

    revision = []
    for path in sorted(paths):
        stat = path.stat()
        revision.append((path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size))
    return tuple(revision)


def _dashboard_revision(root: Path) -> tuple[tuple[str, int, int], ...]:
    dash = root / "dashboards"
    revision = []
    for filename in sorted(_DASHBOARD_JSON_FILES.values()):
        path = dash / filename
        if path.is_file():
            stat = path.stat()
            revision.append((path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size))
    return tuple(revision)


def dashboard_staleness(root: Path) -> dict[str, Any]:
    truth_revision = _truth_source_revision(root)
    dashboard_revision = _dashboard_revision(root)
    dash = root / "dashboards"
    missing = [
        f"dashboards/{filename}"
        for filename in sorted(_DASHBOARD_JSON_FILES.values())
        if not (dash / filename).is_file()
    ]
    latest_truth_mtime = max((item[1] for item in truth_revision), default=0)
    oldest_dashboard_mtime = min((item[1] for item in dashboard_revision), default=0)
    available = not missing
    stale = available and latest_truth_mtime > oldest_dashboard_mtime
    return {
        "available": available,
        "stale": stale,
        "missing": missing,
        "latest_truth_mtime_ns": latest_truth_mtime,
        "oldest_dashboard_mtime_ns": oldest_dashboard_mtime,
        "recommended_command": f"research-cockpit build --root {root}",
    }


def _load_dashboard_json_files(root: Path) -> dict[str, Any] | None:
    dash = root / "dashboards"
    payloads: dict[str, Any] = {}
    try:
        for key, filename in _DASHBOARD_JSON_FILES.items():
            payloads[key] = json.loads((dash / filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payloads


def _load_graph_data(root: Path):
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validation_errors = validate_cockpit(root, nodes, current, explicit_edges)
    dashboard_status = dashboard_staleness(root)
    dashboard_payloads = None if dashboard_status["stale"] else _load_dashboard_json_files(root)
    if dashboard_payloads is not None:
        graph = dashboard_payloads["graph"]
        context = dashboard_payloads["context"]
        link_rows = dashboard_payloads["link_rows"]
        action_suggestions = dashboard_payloads["action_suggestions"]
        search_index = dashboard_payloads["search_index"]
        option_workstreams = dashboard_payloads["option_workstreams"]
    else:
        read_models = build_dashboard_read_models(root, nodes, current)
        graph = graph_to_json(
            nodes,
            current.get("current_focus_path", []),
            current,
            explicit_edges,
            topology=read_models.topology,
            include_raw=False,
        )
        context = build_agent_context(root, nodes, current=current, read_models=read_models)
        link_rows = read_models.linked_resources
        action_suggestions = read_models.action_suggestions
        search_index = read_models.search_index
        option_workstreams = read_models.option_workstreams
    saved_graph_views = load_graph_views(root)
    all_action_suggestions = build_action_suggestions(
        root,
        nodes,
        current,
        link_rows,
        include_inactive=True,
    )
    return (
        nodes,
        current,
        graph,
        context,
        validation_errors,
        link_rows,
        action_suggestions,
        all_action_suggestions,
        search_index,
        option_workstreams,
        saved_graph_views,
        dashboard_status,
    )


@st.cache_data(show_spinner=False)
def _load_graph_data_cached(
    root_value: str,
    truth_revision: tuple[tuple[str, int, int], ...],
    dashboard_revision: tuple[tuple[str, int, int], ...],
):
    root = Path(root_value)
    return _load_graph_data(root)


def load_graph_data():
    return _load_graph_data_cached(
        str(RESEARCH_ROOT),
        _truth_source_revision(RESEARCH_ROOT),
        _dashboard_revision(RESEARCH_ROOT),
    )


@st.cache_data(show_spinner=False, max_entries=64)
def _filter_graph_for_view_cached(
    cache_key: tuple,
    _graph: dict,
    view_mode: str,
    selected_types: tuple[str, ...],
    selected_statuses: tuple[str, ...],
    selected_stages: tuple[str, ...],
    selected_focus_roles: tuple[str, ...],
    selected_workstreams: tuple[str, ...],
    only_blocking: bool,
    only_next_actions: bool,
    only_missing_evidence: bool,
    hide_inactive_option_branches: bool,
    collapsed_branch_roots: tuple[str, ...],
    revealed_child_roots: tuple[str, ...],
) -> tuple[dict, dict[str, object]]:
    return filter_graph_for_view_with_visibility(
        _graph,
        view_mode,
        set(selected_types),
        set(selected_statuses),
        selected_stages=set(selected_stages),
        selected_focus_roles=set(selected_focus_roles),
        selected_workstreams=set(selected_workstreams),
        only_blocking=only_blocking,
        only_next_actions=only_next_actions,
        only_missing_evidence=only_missing_evidence,
        hide_inactive_option_branches=hide_inactive_option_branches,
        collapsed_branch_roots=set(collapsed_branch_roots),
        revealed_child_roots=set(revealed_child_roots),
    )


@st.cache_data(show_spinner=False, max_entries=64)
def _build_graph_component_base_payload_cached(
    cache_key: tuple,
    _filtered_graph: dict,
    show_baseline_lens: bool,
) -> dict[str, object]:
    return build_graph_component_base_payload(
        _filtered_graph,
        show_baseline_lens=show_baseline_lens,
    )


def clear_graph_data_cache() -> None:
    _load_graph_data_cached.clear()
    _filter_graph_for_view_cached.clear()
    _build_graph_component_base_payload_cached.clear()


def render_decision_repair_hints(checklist: dict, text: dict[str, str]) -> None:
    rows = format_decision_repair_hints(checklist)
    if not rows:
        return

    st.write(text["decision_repair_hints"])
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    command_rows = [row for row in rows if row.get("suggested_command")]
    if command_rows:
        with st.expander(text["decision_repair_commands"]):
            for row in command_rows:
                st.caption(f"{row['check_id']}: {row['label']}")
                st.code(row["suggested_command"], language=COMMAND_LANGUAGE)


def render_effective_baseline(node: object, effective_baseline: dict, text: dict[str, str]) -> None:
    st.write(text["effective_baseline"])
    option = effective_baseline.get("option") if isinstance(effective_baseline, dict) else None
    if not option:
        st.caption(text["no_effective_baseline"])
        st.code(
            f"research-cockpit set-baseline --node {getattr(node, 'id', '')} "
            "--option <option_id> --dry-run --json --show-diff",
            language=COMMAND_LANGUAGE,
        )
        st.code(
            f"research-cockpit context --id {getattr(node, 'id', '')} --compact --json",
            language=COMMAND_LANGUAGE,
        )
        return

    if getattr(node, "type", "") == "experiment":
        st.caption(text["experiment_uses_baseline"])
    decision = effective_baseline.get("decision") or {}
    artifacts = effective_baseline.get("artifacts") or []
    artifact_ids = [
        str(item.get("id"))
        for item in artifacts
        if isinstance(item, dict) and item.get("id")
    ]
    rows = [
        {"field": text["baseline_source"], "value": str(effective_baseline.get("source_node_id") or "")},
        {"field": text["baseline_source_kind"], "value": str(effective_baseline.get("source_kind") or "")},
        {"field": text["baseline_option"], "value": _baseline_ref_label(option)},
        {"field": text["baseline_decision"], "value": _baseline_ref_label(decision)},
        {"field": text["baseline_artifacts"], "value": ", ".join(artifact_ids)},
        {"field": text["baseline_reason"], "value": str(effective_baseline.get("reason") or "")},
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.write(text["baseline_context_command"])
    st.code(
        f"research-cockpit context --id {option.get('id')} --with-artifacts --compact --json",
        language=COMMAND_LANGUAGE,
    )


def render_node_detail(
    nodes: dict,
    node_id: str,
    text: dict[str, str],
    current: dict | None = None,
    link_rows: list[dict] | None = None,
    action_suggestions: list[dict] | None = None,
) -> None:
    if not node_id:
        st.info(text["select_node_hint"])
        return

    node = nodes[node_id]
    overview = build_node_overview(node, nodes, current, link_rows, action_suggestions)
    st.subheader(node.title)
    chips = [node.type, node.status]
    if node.priority:
        chips.append(node.priority)
    st.caption(" · ".join(chips))
    st.caption(f"ID: `{node.id}`")
    overview_tab, evidence_tab, resources_tab, relations_tab, actions_tab, agent_tab = st.tabs([
        text["overview_tab"],
        text["evidence_tab"],
        text["resources_tab"],
        text["relations_tab"],
        text["actions_tab"],
        text["agent_tab"],
    ])

    with overview_tab:
        st.write(text["node_purpose"])
        st.write(overview["purpose"] or text["no_summary"])
        st.write(text["current_state"])
        current_state = overview["current_state"]
        state_items = current_state.get("items", [])
        if state_items:
            for item in state_items:
                if current_state.get("kind") == "blockers":
                    st.warning(item)
                else:
                    st.write(item)
        else:
            st.caption(text["none"])
        st.write(text["next_actions"])
        if overview["next"]:
            for item in overview["next"]:
                st.write(f"- {item}")
        else:
            st.caption(text["no_next_actions"])
        render_effective_baseline(node, overview.get("effective_baseline") or {}, text)
        st.write(text["key_resources"])
        if overview["key_resources"]:
            st.dataframe(
                pd.DataFrame(format_resource_rows(overview["key_resources"])),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption(text["no_resources"])
        if node.tags:
            st.write(text["tags"])
            st.write(", ".join(node.tags))

    with evidence_tab:
        evidence_fields = [
            (text["evidence_strength"], node.raw.get("evidence_strength")),
            (text["evidence_summary"], node.raw.get("evidence_summary")),
            (text["result_summary"], node.raw.get("result_summary")),
            (text["outcome"], node.raw.get("outcome")),
        ]
        for label, value in evidence_fields:
            if value:
                st.write(label)
                st.write(value)
        for label, field in (
            (text["supporting_experiments"], "supporting_experiments"),
            (text["contradicting_experiments"], "contradicting_experiments"),
            (text["supporting_decisions"], "supporting_decisions"),
            (text["linked_artifacts"], "linked_artifacts"),
        ):
            values = node.raw.get(field, [])
            if values:
                st.write(label)
                for item in values:
                    st.write(f"- {item}")
        findings = node.raw.get("findings", []) or []
        if findings:
            st.write(text["findings"])
            st.dataframe(pd.DataFrame(format_finding_rows(findings)), use_container_width=True, hide_index=True)
        if node.type == "decision":
            checklist = build_decision_acceptance_checklist(nodes, node_id)
            st.write(text["decision_acceptance_checklist"])
            if checklist["ready"]:
                st.success(text["decision_ready"])
            else:
                st.warning(text["decision_not_ready"])
            st.dataframe(
                pd.DataFrame(format_decision_checklist(checklist)),
                use_container_width=True,
                hide_index=True,
            )
            render_decision_repair_hints(checklist, text)

    with resources_tab:
        node_link_rows = [row for row in (link_rows or []) if row.get("node_id") == node_id]
        if node_link_rows:
            st.dataframe(pd.DataFrame(format_resource_rows(node_link_rows)), use_container_width=True, hide_index=True)
        else:
            st.caption(text["no_resources"])

    with relations_tab:
        st.write(text["parent"])
        if overview["relations"]["parent"]:
            for row in overview["relations"]["parent"]:
                st.write(row["label"])
        else:
            st.caption(text["none"])
        st.write(text["children"])
        if overview["relations"]["children"]:
            for row in overview["relations"]["children"]:
                st.write(f"- {row['label']}")
        else:
            st.caption(text["none"])

    with actions_tab:
        if current:
            if st.button(text["set_as_focus"], key=f"set_focus_{node_id}"):
                try:
                    save_current_focus(RESEARCH_ROOT, focus_node=node_id)
                    st.session_state["graph_view_mode"] = text["focus_depth_2"]
                    st.session_state["graph_detail_node"] = node_id
                    st.session_state["graph_pending_detail_select"] = node_id
                    st.session_state["graph_pending_node_search"] = ""
                    st.session_state["graph_workstreams"] = []
                    st.session_state.pop("graph_focus_roles", None)
                    st.session_state.pop("graph_component_processed_event_id", None)
                    st.success(text["focus_updated"])
                    st.rerun()
                except Exception as exc:
                    st.error(f"{text['focus_update_failed']} {exc}")
            st.write(text["set_focus_command"])
            st.code(build_set_focus_command(current, node_id), language=COMMAND_LANGUAGE)
            st.caption(text["set_focus_command_hint"])
        if node.type == "experiment":
            st.write(text["record_finding_command"])
            st.code(build_record_finding_command(node_id), language=COMMAND_LANGUAGE)
        if node.type == "option":
            st.write(text["promote_decision_command"])
            st.code(build_promote_decision_command(node_id), language=COMMAND_LANGUAGE)
            st.write(text["claim_option_command"])
            st.code(build_claim_option_command(node_id), language=COMMAND_LANGUAGE)
            st.write(text["option_context_command"])
            st.code(build_option_workstream_context_command(node_id), language=COMMAND_LANGUAGE)
            st.write(text["report_option_command"])
            st.code(build_report_option_workstream_command(node_id), language=COMMAND_LANGUAGE)
        if node.type == "decision":
            st.write(text["check_decision_command"])
            st.code(build_check_decision_acceptance_command(node_id), language=COMMAND_LANGUAGE)
            st.write(text["update_decision_checklist_command"])
            st.code(build_update_decision_checklist_command(node_id), language=COMMAND_LANGUAGE)
            st.write(text["accept_decision_command"])
            st.code(build_accept_decision_command(node_id), language=COMMAND_LANGUAGE)
        if node.type in {"problem", "option", "experiment", "decision"}:
            st.write(text["create_note_command"])
            st.code(build_create_note_command(node_id), language=COMMAND_LANGUAGE)
        blockers = node.raw.get("blockers", [])
        if blockers:
            st.write(text["blockers"])
            for item in blockers:
                st.warning(item)
        for label, field in (
            (text["next_actions"], "next_actions"),
            (text["implementation_steps"], "implementation_steps"),
            (text["success_criteria"], "success_criteria"),
        ):
            values = node.raw.get(field, [])
            if values:
                st.write(label)
                for item in values:
                    st.write(f"- {item}")

    with agent_tab:
        agent_context = node.raw.get("agent_context") or {}
        if agent_context:
            for label, field in (
                (text["agent_key_files"], "key_files"),
                (text["agent_key_questions"], "key_questions"),
            ):
                values = agent_context.get(field, [])
                if values:
                    st.write(label)
                    for item in values:
                        st.write(f"- {item}")
            if agent_context.get("next_action_hint"):
                st.write(text["next_action_hint"])
                st.info(agent_context["next_action_hint"])
            st.json(agent_context)
        else:
            st.caption(text["none"])

    with st.expander(text["raw_yaml"]):
        st.json(node.raw)


def _session_node_id_set(key: str) -> set[str]:
    raw = st.session_state.get(key, [])
    values = [raw] if isinstance(raw, str) else list(raw or [])
    return {str(value) for value in values if value not in (None, "")}


def _store_session_node_ids(key: str, values: set[str], all_node_ids: list[str]) -> None:
    st.session_state[key] = [node_id for node_id in all_node_ids if node_id in values]


def render_branch_visibility_controls(
    graph: dict,
    node_id: str,
    text: dict[str, str],
    *,
    all_node_ids: list[str],
    view_mode: str,
    selected_types: set[str],
    selected_statuses: set[str],
    selected_stages: set[str],
    selected_focus_roles: set[str],
    selected_workstreams: set[str],
    only_blocking: bool,
    only_next_actions: bool,
    only_missing_evidence: bool,
    hide_inactive_option_branches: bool,
    collapsed_branch_roots: set[str],
    revealed_child_roots: set[str],
    visibility_context: dict[str, object],
) -> None:
    revealable_child_ids = revealable_child_ids_for_view(
        graph,
        node_id,
        view_mode,
        selected_types,
        selected_statuses,
        selected_stages=selected_stages,
        selected_focus_roles=selected_focus_roles,
        selected_workstreams=selected_workstreams,
        only_blocking=only_blocking,
        only_next_actions=only_next_actions,
        only_missing_evidence=only_missing_evidence,
        hide_inactive_option_branches=hide_inactive_option_branches,
        collapsed_branch_roots=collapsed_branch_roots,
        included_node_ids=visibility_context.get("included_node_ids"),
        hidden_by_collapse=visibility_context.get("hidden_by_collapse"),
        child_ids_by_parent=visibility_context.get("child_ids_by_parent"),
    )
    is_collapsed = node_id in collapsed_branch_roots
    is_revealed = node_id in revealed_child_roots

    with st.expander(text["branch_visibility"], expanded=False):
        branch_left, branch_right = st.columns(2)
        if is_collapsed:
            if branch_left.button(
                text["expand_branch"],
                key=f"graph_expand_branch_{node_id}",
                use_container_width=True,
            ):
                collapsed = _session_node_id_set("graph_collapsed_branch_roots")
                collapsed.discard(node_id)
                _store_session_node_ids("graph_collapsed_branch_roots", collapsed, all_node_ids)
                st.rerun()
        else:
            if branch_left.button(
                text["collapse_branch"],
                key=f"graph_collapse_branch_{node_id}",
                use_container_width=True,
            ):
                collapsed = _session_node_id_set("graph_collapsed_branch_roots")
                revealed = _session_node_id_set("graph_revealed_child_roots")
                collapsed.add(node_id)
                revealed.discard(node_id)
                _store_session_node_ids("graph_collapsed_branch_roots", collapsed, all_node_ids)
                _store_session_node_ids("graph_revealed_child_roots", revealed, all_node_ids)
                st.rerun()

        reveal_label = (
            text["hide_revealed_children"]
            if is_revealed
            else text["reveal_hidden_children"].format(count=len(revealable_child_ids))
        )
        reveal_disabled = is_collapsed or (not is_revealed and not revealable_child_ids)
        if branch_right.button(
            reveal_label,
            key=f"graph_reveal_children_{node_id}",
            disabled=reveal_disabled,
            use_container_width=True,
        ):
            revealed = _session_node_id_set("graph_revealed_child_roots")
            if is_revealed:
                revealed.discard(node_id)
            else:
                revealed.add(node_id)
            _store_session_node_ids("graph_revealed_child_roots", revealed, all_node_ids)
            st.rerun()


def render_dashboard(
    context: dict,
    validation_errors: list[str],
    text: dict[str, str],
    action_suggestions: list[dict],
) -> None:
    c1, c2, c3 = st.columns(3)
    c1.metric(text["active_problems"], len(context.get("active_problems", [])))
    c2.metric(text["active_options"], len(context.get("active_options", [])))
    c3.metric(text["next_actions"], len(context.get("next_actions", [])))

    st.subheader(text["current_focus"])
    focus_rows = [
        {text["level"]: text["stage"], "ID": context.get("current_stage"), text["title"]: context.get("current_stage_title")},
        {text["level"]: text["problem"], "ID": context.get("current_problem"), text["title"]: context.get("current_problem_title")},
        {text["level"]: text["option"], "ID": context.get("current_option"), text["title"]: context.get("current_option_title")},
    ]
    st.dataframe(pd.DataFrame(focus_rows), use_container_width=True, hide_index=True)

    st.subheader(text["current_hypothesis"])
    st.write(context.get("current_hypothesis") or text["no_hypothesis"])

    left, right = st.columns(2)
    with left:
        st.subheader(text["open_risks"])
        risks = context.get("open_risks", [])
        if risks:
            for risk in risks:
                st.warning(risk)
        else:
            st.caption(text["no_open_risks"])

    with right:
        st.subheader(text["next_actions"])
        actions = context.get("next_actions", [])
        if actions:
            for index, action in enumerate(actions):
                st.checkbox(action, value=False, key=f"dashboard_action_{index}")
        else:
            st.caption(text["no_next_actions"])

    st.subheader(text["top_suggestions"])
    top_suggestions = action_suggestions[:3]
    if top_suggestions:
        st.dataframe(
            pd.DataFrame(format_action_suggestion_rows(top_suggestions)),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption(text["no_action_suggestions"])

    if validation_errors:
        st.error(text["data_health_warning"].format(count=len(validation_errors)))


def render_baselines(nodes: dict, current: dict, text: dict[str, str]) -> None:
    baseline_rows = build_baseline_overview_rows(nodes, current)
    accepted_options = build_accepted_option_rows(nodes, current)
    accepted_decisions = build_accepted_decision_rows(nodes)

    st.subheader(text["default_baselines"])
    if baseline_rows:
        st.dataframe(pd.DataFrame(baseline_rows), use_container_width=True, hide_index=True)
    else:
        st.info(text["no_baselines"])

    option_tab, decision_tab, command_tab = st.tabs([
        text["accepted_options"],
        text["accepted_decisions"],
        text["baseline_commands"],
    ])

    with option_tab:
        if accepted_options:
            st.dataframe(pd.DataFrame(accepted_options), use_container_width=True, hide_index=True)
        else:
            st.info(text["no_accepted_options"])

    with decision_tab:
        if accepted_decisions:
            st.dataframe(pd.DataFrame(accepted_decisions), use_container_width=True, hide_index=True)
        else:
            st.info(text["no_accepted_decisions"])

    with command_tab:
        baseline_by_problem = {
            row["problem_id"]: row
            for row in baseline_rows
            if row.get("problem_id")
        }
        problem_ids = baseline_command_problem_ids(baseline_rows)
        if problem_ids:
            problem_id = st.selectbox(
                text["baseline_target"],
                problem_ids,
                format_func=lambda value: format_node_option(nodes, value),
                key="baseline_target_problem",
            )
            baseline_row = baseline_by_problem.get(problem_id, {})
            baseline_option_id = str(baseline_row.get("baseline_option_id") or "")
            st.write(text["clear_baseline_command"])
            st.code(build_set_baseline_command(problem_id, clear=True), language=COMMAND_LANGUAGE)
            if baseline_option_id:
                st.write(text["baseline_context_command"])
                st.code(
                    f"research-cockpit context --id {baseline_option_id} --with-artifacts --compact --json",
                    language=COMMAND_LANGUAGE,
                )
            option_ids = [
                row["id"]
                for row in accepted_options
                if row.get("id") and row.get("problem_id") == problem_id
            ]
            if option_ids:
                option_id = st.selectbox(
                    text["baseline_option"],
                    option_ids,
                    format_func=lambda value: format_node_option(nodes, value),
                    key="baseline_option",
                )
                decision_ids = [""] + [
                    row["id"]
                    for row in accepted_decisions
                    if row.get("id") and row.get("option_id") == option_id
                ]
                decision_id = st.selectbox(
                    text["baseline_decision"],
                    decision_ids,
                    format_func=lambda value: text["none"] if not value else format_node_option(nodes, value),
                    key="baseline_decision",
                )
                if option_id in nodes:
                    st.caption(nodes[option_id].summary or text["no_summary"])
                if decision_id and decision_id in nodes:
                    st.caption(nodes[decision_id].summary or text["no_summary"])
                reason = st.text_input(text["baseline_reason"], value="", key="baseline_reason")
                st.write(text["set_baseline_command"])
                st.code(
                    build_set_baseline_command(
                        problem_id,
                        option_id,
                        decision_id=decision_id,
                        reason=reason,
                    ),
                    language=COMMAND_LANGUAGE,
                )
                if decision_id:
                    st.write(text["inspect_decision"])
                    st.code(
                        f"research-cockpit node-context --id {decision_id} --compact --json",
                        language=COMMAND_LANGUAGE,
                    )
            else:
                st.info(text["no_accepted_options"])
        else:
            st.info(text["no_baseline_command_targets"])


def render_graph_tab(
    nodes: dict,
    graph: dict,
    current: dict,
    text: dict[str, str],
    link_rows: list[dict],
    saved_graph_views: list[dict],
    action_suggestions: list[dict] | None = None,
) -> None:
    options = graph_filter_options(graph)
    all_types = options["types"] or sorted({node["type"] for node in graph["nodes"]})
    all_statuses = options["statuses"] or sorted({node["status"] for node in graph["nodes"]})
    all_stages = options["stages"]
    all_focus_roles = options["focus_roles"]
    all_workstreams = options["workstreams"]
    all_node_ids = sorted(nodes.keys())
    mode_label_to_value = {
        text["focus_depth_2"]: "focus_depth_2",
        text["focus_depth_1"]: "focus_depth_1",
        text["current_branch"]: "current_branch",
        text["option_workstream_view"]: "option_workstream",
        text["global_graph"]: "global",
    }
    mode_value_to_label = {value: label for label, value in mode_label_to_value.items()}
    default_view_label = mode_value_to_label.get(DEFAULT_GRAPH_VIEW_MODE, text["global_graph"])
    renderer_options = ["React Flow", "PyVis legacy"]

    def sync_graph_view_mode_filters() -> None:
        next_view_label = st.session_state.get("graph_view_mode", default_view_label)
        next_view_mode = mode_label_to_value.get(next_view_label, DEFAULT_GRAPH_VIEW_MODE)
        reset_global_graph_filter_state(
            st.session_state,
            next_view_mode,
            all_types=all_types,
            all_statuses=all_statuses,
            all_stages=all_stages,
            all_focus_roles=all_focus_roles,
        )
        st.session_state["graph_show_baseline_lens"] = default_show_baseline_lens(next_view_mode)
        st.session_state.setdefault(
            "graph_hide_inactive_option_branches",
            DEFAULT_HIDE_INACTIVE_OPTION_BRANCHES,
        )

    def selected_values(key: str, available: list[str], default: list[str]) -> list[str]:
        raw = st.session_state[key] if key in st.session_state else default
        raw_values = {raw} if isinstance(raw, str) else set(raw or [])
        return [value for value in available if value in raw_values]

    message = st.session_state.pop("graph_view_message", None)
    header_left, header_right = st.columns([4, 1])
    header_left.subheader(text["research_graph"])
    header_right.button(
        "刷新图谱 / Refresh",
        key="graph_refresh_data",
        use_container_width=True,
        on_click=clear_graph_data_cache,
    )
    if message:
        st.success(message)

    view_label = st.session_state.get("graph_view_mode", default_view_label)
    if view_label not in mode_label_to_value:
        view_label = default_view_label
    view_mode = mode_label_to_value.get(view_label, DEFAULT_GRAPH_VIEW_MODE)
    reset_global_graph_filter_state(
        st.session_state,
        view_mode,
        all_types=all_types,
        all_statuses=all_statuses,
        all_stages=all_stages,
        all_focus_roles=all_focus_roles,
    )
    selected_types = set(selected_values(
        "graph_node_types",
        all_types,
        default_selected_node_types(all_types),
    ))
    selected_statuses = set(selected_values(
        "graph_statuses",
        all_statuses,
        default_selected_statuses(graph, all_statuses),
    ))
    selected_stages = set(selected_values("graph_stages", all_stages, all_stages))
    selected_focus_roles = set(selected_values("graph_focus_roles", all_focus_roles, all_focus_roles))
    default_workstreams = [
        current.get("current_option")
        for _ in [0]
        if current.get("current_option") in all_workstreams
    ]
    if view_mode == "option_workstream" and not default_workstreams and all_workstreams:
        default_workstreams = [all_workstreams[0]]
    selected_workstreams = set(selected_values(
        "graph_workstreams",
        all_workstreams,
        default_workstreams if view_mode == "option_workstream" else [],
    ))
    if view_mode == "option_workstream" and not selected_workstreams and default_workstreams:
        selected_workstreams = set(default_workstreams)
        st.session_state["graph_workstreams"] = list(default_workstreams)
    only_blocking = bool(st.session_state.get("graph_only_blocking", False))
    only_next_actions = bool(st.session_state.get("graph_only_next_actions", False))
    only_missing_evidence = bool(st.session_state.get("graph_only_missing_evidence", False))
    hide_inactive_option_branches = bool(
        st.session_state.get(
            "graph_hide_inactive_option_branches",
            DEFAULT_HIDE_INACTIVE_OPTION_BRANCHES,
        )
    )
    show_baseline_lens = bool(
        st.session_state.get("graph_show_baseline_lens", default_show_baseline_lens(view_mode))
    )
    collapsed_branch_roots = set(selected_values("graph_collapsed_branch_roots", all_node_ids, []))
    revealed_child_roots = set(selected_values("graph_revealed_child_roots", all_node_ids, []))
    if revealed_child_roots.intersection(collapsed_branch_roots):
        revealed_child_roots.difference_update(collapsed_branch_roots)
        st.session_state["graph_revealed_child_roots"] = [
            node_id for node_id in all_node_ids if node_id in revealed_child_roots
        ]
    renderer_label = st.session_state.get("graph_renderer", "React Flow")
    if renderer_label not in renderer_options:
        renderer_label = "React Flow"

    selected_types_key = tuple(sorted(selected_types))
    selected_statuses_key = tuple(sorted(selected_statuses))
    selected_stages_key = tuple(sorted(selected_stages))
    selected_focus_roles_key = tuple(sorted(selected_focus_roles))
    selected_workstreams_key = tuple(sorted(selected_workstreams))
    collapsed_branch_roots_key = tuple(sorted(collapsed_branch_roots))
    revealed_child_roots_key = tuple(sorted(revealed_child_roots))
    graph_view_key = graph_filter_cache_key(
        graph,
        view_mode,
        selected_types_key,
        selected_statuses_key,
        selected_stages=selected_stages_key,
        selected_focus_roles=selected_focus_roles_key,
        selected_workstreams=selected_workstreams_key,
        only_blocking=only_blocking,
        only_next_actions=only_next_actions,
        only_missing_evidence=only_missing_evidence,
        hide_inactive_option_branches=hide_inactive_option_branches,
        collapsed_branch_roots=collapsed_branch_roots_key,
        revealed_child_roots=revealed_child_roots_key,
    )
    filtered_graph, graph_visibility_context = _filter_graph_for_view_cached(
        graph_view_key,
        graph,
        view_mode,
        selected_types_key,
        selected_statuses_key,
        selected_stages_key,
        selected_focus_roles_key,
        selected_workstreams_key,
        only_blocking,
        only_next_actions,
        only_missing_evidence,
        hide_inactive_option_branches,
        collapsed_branch_roots_key,
        revealed_child_roots_key,
    )
    visible_node_ids = [node["id"] for node in filtered_graph["nodes"] if node["id"] in nodes]
    select_key = "graph_detail_select"
    pending_detail_select = st.session_state.pop("graph_pending_detail_select", None)
    pending_node_search = st.session_state.pop("graph_pending_node_search", None)
    if pending_detail_select in visible_node_ids:
        st.session_state["graph_detail_node"] = pending_detail_select
        st.session_state[select_key] = pending_detail_select
    if pending_node_search is not None:
        st.session_state["graph_node_search"] = str(pending_node_search)
    selected_from_select = st.session_state.get(select_key)
    current_detail_node = st.session_state.get("graph_detail_node")
    if selected_from_select in visible_node_ids and selected_from_select != current_detail_node:
        current_detail_node = selected_from_select
        st.session_state["graph_detail_node"] = selected_from_select
    elif current_detail_node not in visible_node_ids:
        current_detail_node = default_detail_node_id(graph, visible_node_ids)
        if current_detail_node:
            st.session_state["graph_detail_node"] = current_detail_node
            st.session_state[select_key] = current_detail_node
    elif current_detail_node:
        st.session_state[select_key] = current_detail_node

    graph_area, detail = st.columns([2, 1])
    with graph_area:
        use_react_flow = renderer_label == "React Flow" and graph_component_build_available()
        if use_react_flow:
            base_payload = _build_graph_component_base_payload_cached(
                graph_component_payload_cache_key(
                    graph_view_key,
                    show_baseline_lens=show_baseline_lens,
                ),
                filtered_graph,
                show_baseline_lens,
            )
            payload = build_graph_component_payload_from_base(base_payload, current_detail_node)
            component_value = render_research_graph_component(
                payload,
                selected_node_id=current_detail_node,
                key="research_graph_component",
            )
            clicked_node_id = graph_component_selected_node_id(component_value, visible_node_ids)
            clicked_event_id = graph_component_event_id(component_value)
            processed_event_id = st.session_state.get("graph_component_processed_event_id")
            new_component_click = (
                bool(clicked_event_id) and clicked_event_id != processed_event_id
            ) or (
                clicked_event_id is None and clicked_node_id != st.session_state.get("graph_detail_node")
            )
            if clicked_node_id and new_component_click:
                if clicked_event_id:
                    st.session_state["graph_component_processed_event_id"] = clicked_event_id
                st.session_state["graph_detail_node"] = clicked_node_id
                st.session_state["graph_detail_select"] = clicked_node_id
                st.session_state["graph_node_search"] = ""
        else:
            if renderer_label == "React Flow":
                st.caption("React Flow build missing; using PyVis fallback.")
            render_pyvis_graph(filtered_graph, set(), set(), current_detail_node or graph.get("current_focus_node"))

        controls = st.expander("图谱控制 / Graph Controls", expanded=False)
        with controls:
            view_label = st.radio(
                text["view_mode"],
                list(mode_label_to_value),
                index=list(mode_label_to_value).index(view_label),
                horizontal=True,
                key="graph_view_mode",
                on_change=sync_graph_view_mode_filters,
            )
            view_mode = mode_label_to_value.get(view_label or default_view_label, DEFAULT_GRAPH_VIEW_MODE)
            filter_left, filter_right = st.columns(2)
            selected_types = set(filter_left.multiselect(
                text["node_types"],
                all_types,
                default=[value for value in all_types if value in selected_types],
                key="graph_node_types",
            ))
            selected_statuses = set(
                filter_right.multiselect(
                    text["statuses"],
                    all_statuses,
                    default=[value for value in all_statuses if value in selected_statuses],
                    key="graph_statuses",
                )
            )
            advanced_left, advanced_mid, advanced_right = st.columns(3)
            selected_stages = set(advanced_left.multiselect(
                text["stages"],
                all_stages,
                default=[value for value in all_stages if value in selected_stages],
                key="graph_stages",
            ))
            selected_focus_roles = set(advanced_mid.multiselect(
                text["focus_roles"],
                all_focus_roles,
                default=[value for value in all_focus_roles if value in selected_focus_roles],
                key="graph_focus_roles",
            ))
            selected_workstreams = set(advanced_right.multiselect(
                text["workstreams_filter"],
                all_workstreams,
                default=[value for value in all_workstreams if value in selected_workstreams],
                key="graph_workstreams",
            ))
            flag_left, flag_mid, flag_right = st.columns(3)
            only_blocking = flag_left.checkbox(text["only_blocking"], value=only_blocking, key="graph_only_blocking")
            only_next_actions = flag_mid.checkbox(
                text["only_next_actions"],
                value=only_next_actions,
                key="graph_only_next_actions",
            )
            only_missing_evidence = flag_right.checkbox(
                text["only_missing_evidence"],
                value=only_missing_evidence,
                key="graph_only_missing_evidence",
            )
            hide_inactive_option_branches = st.checkbox(
                text["hide_inactive_option_branches"],
                value=hide_inactive_option_branches,
                key="graph_hide_inactive_option_branches",
            )
            show_baseline_lens = st.checkbox(
                text["show_baseline_lens"],
                value=show_baseline_lens,
                key="graph_show_baseline_lens",
            )
            if st.button(text["reset_branch_visibility"], key="graph_reset_branch_visibility"):
                st.session_state["graph_collapsed_branch_roots"] = []
                st.session_state["graph_revealed_child_roots"] = []
                st.rerun()
            renderer_label = st.radio(
                "Graph Renderer",
                renderer_options,
                index=renderer_options.index(renderer_label),
                horizontal=True,
                key="graph_renderer",
            )
            save_left, save_right = st.columns([3, 1])
            view_title = save_left.text_input(
                text["graph_view_title"],
                value="",
                placeholder=text["graph_view_title_placeholder"],
                key="graph_view_save_title",
            )
            if save_right.button(text["save_current_view"], key="graph_save_current_view", use_container_width=True):
                if not view_title.strip():
                    st.warning(text["graph_view_title_required"])
                else:
                    view = {
                        "title": view_title.strip(),
                        "scope": view_mode,
                        "filters": {
                            "node_types": [value for value in all_types if value in selected_types],
                            "statuses": [value for value in all_statuses if value in selected_statuses],
                            "stages": [value for value in all_stages if value in selected_stages],
                            "focus_roles": [value for value in all_focus_roles if value in selected_focus_roles],
                            "workstreams": [value for value in all_workstreams if value in selected_workstreams],
                            "collapsed_branch_roots": [
                                value for value in all_node_ids if value in collapsed_branch_roots
                            ],
                            "revealed_child_roots": [
                                value for value in all_node_ids if value in revealed_child_roots
                            ],
                            "only_blocking": only_blocking,
                            "only_next_actions": only_next_actions,
                            "only_missing_evidence": only_missing_evidence,
                            "hide_inactive_option_branches": hide_inactive_option_branches,
                            "show_baseline_lens": show_baseline_lens,
                        },
                        "saved_focus_node_id": graph.get("current_focus_node"),
                        "saved_focus_path": current.get("current_focus_path", []) or [],
                    }
                    try:
                        upsert_graph_view(RESEARCH_ROOT, view)
                        build_dashboard(RESEARCH_ROOT)
                        st.session_state["graph_view_message"] = text["graph_view_saved"]
                        st.rerun()
                    except Exception as exc:
                        st.error(f"{text['graph_view_save_failed']} {exc}")
            st.write(f"**{text['legend']}**")
            legend_left, legend_right = st.columns(2)
            legend_left.write(text["legend_current"])
            legend_left.write(text["legend_path"])
            legend_right.write(text["legend_depth_1"])
            legend_right.write(text["legend_depth_2"])

    with detail:
        search_query = st.text_input(text["search_nodes"], value="", key="graph_node_search")
        search_scope = {node_id: nodes[node_id] for node_id in (visible_node_ids or sorted(nodes.keys()))}
        detail_options = filter_node_ids(search_scope, search_query)
        selected_detail_node = st.session_state.get("graph_detail_node")
        default_id = selected_detail_node if selected_detail_node in detail_options else default_detail_node_id(graph, detail_options)
        if not detail_options:
            st.info(text["select_node_hint"])
            return
        default_index = detail_options.index(default_id) if default_id in detail_options else 0
        if st.session_state.get(select_key) not in detail_options:
            st.session_state[select_key] = detail_options[default_index]
        node_id = st.selectbox(
            text["inspect_node"],
            detail_options,
            index=default_index,
            format_func=lambda value: format_node_option(nodes, value),
            key=select_key,
        )
        if node_id != st.session_state.get("graph_detail_node"):
            st.session_state["graph_detail_node"] = node_id
        render_branch_visibility_controls(
            graph,
            node_id,
            text,
            all_node_ids=all_node_ids,
            view_mode=view_mode,
            selected_types=selected_types,
            selected_statuses=selected_statuses,
            selected_stages=selected_stages,
            selected_focus_roles=selected_focus_roles,
            selected_workstreams=selected_workstreams,
            only_blocking=only_blocking,
            only_next_actions=only_next_actions,
            only_missing_evidence=only_missing_evidence,
            hide_inactive_option_branches=hide_inactive_option_branches,
            collapsed_branch_roots=collapsed_branch_roots,
            revealed_child_roots=revealed_child_roots,
            visibility_context=graph_visibility_context,
        )
        render_node_detail(nodes, node_id, text, current, link_rows, action_suggestions)

    saved_view_by_id = {str(view.get("id")): view for view in saved_graph_views if view.get("id")}
    if saved_view_by_id:
        load_left, load_right = st.columns([3, 1])
        selected_view_id = load_left.selectbox(
            text["saved_graph_views"],
            [""] + sorted(saved_view_by_id),
            format_func=lambda value: text["none"] if not value else (
                f"{saved_view_by_id[value].get('title')} | {value}"
            ),
            key="graph_saved_view_selected",
        )
        if load_right.button(
            text["load_saved_view"],
            disabled=not selected_view_id,
            key="graph_load_saved_view",
            use_container_width=True,
        ):
            state = graph_view_state_from_saved_view(
                saved_view_by_id[selected_view_id],
                options,
                mode_value_to_label,
                all_node_ids,
            )
            for key, value in state.items():
                st.session_state[key] = value
            st.session_state["graph_skip_global_filter_reset"] = True
            st.session_state["graph_view_message"] = text["graph_view_loaded"]
            st.rerun()
    else:
        st.caption(text["no_saved_graph_views"])

def render_branch_comparison(nodes: dict, current: dict, text: dict[str, str]) -> None:
    problem_options = sorted(node.id for node in nodes.values() if node.type == "problem")
    if not problem_options:
        st.info(text["no_options"])
        return

    current_problem = current.get("current_problem")
    default_index = problem_options.index(current_problem) if current_problem in problem_options else 0
    problem_id = st.selectbox(
        text["select_problem"],
        problem_options,
        index=default_index,
        format_func=lambda value: format_node_option(nodes, value),
        key="branch_comparison_problem",
    )
    rows = build_branch_comparison(nodes, problem_id, current)
    if not rows:
        st.info(text["no_options"])
        return

    display_rows = format_comparison_rows(rows)
    st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)


def render_decision_trace(nodes: dict, text: dict[str, str]) -> None:
    decision_ids = sorted(node.id for node in nodes.values() if node.type == "decision")
    if not decision_ids:
        st.info(text["no_decisions"])
        return

    decision_id = st.selectbox(
        text["inspect_decision"],
        decision_ids,
        format_func=lambda value: format_node_option(nodes, value),
        key="decision_trace_decision",
    )
    trace = build_decision_trace(nodes, decision_id)

    st.subheader(text["trace_chain"])
    chain = [
        trace_item
        for trace_item in (trace.get("stage"), trace.get("problem"), trace.get("option"), trace.get("decision"))
        if trace_item
    ]
    st.dataframe(pd.DataFrame(context_rows(chain)), use_container_width=True, hide_index=True)

    experiments = trace.get("supporting_experiments", [])
    if experiments:
        st.subheader(text["supporting_evidence"])
        st.dataframe(pd.DataFrame(context_rows(experiments)), use_container_width=True, hide_index=True)

    alternatives = trace.get("alternatives_considered", [])
    if alternatives:
        st.subheader(text["alternatives_considered"])
        st.dataframe(pd.DataFrame(context_rows(alternatives)), use_container_width=True, hide_index=True)

    consequences = trace.get("consequences", [])
    if consequences:
        st.subheader(text["consequences"])
        for item in consequences:
            st.write(f"- {item}")

    st.subheader(text["evidence_summary"])
    st.dataframe(
        pd.DataFrame([format_evidence_summary(trace.get("evidence_summary") or build_decision_evidence_summary(nodes, decision_id))]),
        use_container_width=True,
        hide_index=True,
    )
    checklist = build_decision_acceptance_checklist(nodes, decision_id)
    st.subheader(text["decision_acceptance_checklist"])
    if checklist["ready"]:
        st.success(text["decision_ready"])
    else:
        st.warning(text["decision_not_ready"])
    st.dataframe(pd.DataFrame(format_decision_checklist(checklist)), use_container_width=True, hide_index=True)
    render_decision_repair_hints(checklist, text)


def render_action_guidance(action_suggestions: list[dict], text: dict[str, str]) -> None:
    if not action_suggestions:
        st.info(text["no_action_suggestions"])
        return

    kinds = sorted({suggestion.get("kind", "") for suggestion in action_suggestions if suggestion.get("kind")})
    priorities = sorted({suggestion.get("priority", "") for suggestion in action_suggestions if suggestion.get("priority")})
    states = ["active", "dismissed", "completed"]
    kind_filter, priority_filter, state_filter, focus_filter = st.columns(4)
    selected_kinds = set(kind_filter.multiselect(
        text["suggestion_kind"],
        kinds,
        default=kinds,
        key="action_guidance_kinds",
    ))
    selected_priorities = set(priority_filter.multiselect(
        text["suggestion_priority"],
        priorities,
        default=priorities,
        key="action_guidance_priorities",
    ))
    selected_states = set(
        state_filter.multiselect(
            text["suggestion_state"],
            states,
            default=["active"],
            format_func=lambda value: text.get(value, value),
            key="action_guidance_states",
        )
    )
    focus_only = focus_filter.checkbox(text["focus_related"], value=False, key="action_guidance_focus_only")

    filtered = filter_action_suggestions(
        action_suggestions,
        selected_kinds,
        selected_priorities,
        selected_states,
        focus_only,
    )
    if not filtered:
        st.info(text["no_action_suggestions"])
        return
    st.dataframe(
        pd.DataFrame(format_action_suggestion_rows(filtered)),
        use_container_width=True,
        hide_index=True,
    )

    commands = [item for item in filtered if item.get("suggested_command")]
    if commands:
        selected = st.selectbox(
            text["set_focus_command"],
            commands,
            format_func=lambda item: f"{item.get('kind')} | {item.get('source_node_id')}",
            key="action_guidance_command",
        )
        st.code(selected["suggested_command"], language=COMMAND_LANGUAGE)

    selected_suggestion = st.selectbox(
        text["queue_suggestion"],
        filtered,
        format_func=lambda item: f"{item.get('id')} | {item.get('kind')} | {item.get('source_node_id')}",
        key="action_guidance_selected_suggestion",
    )
    selected_state = selected_suggestion.get("lifecycle_state", "active")
    selected_key = selected_suggestion.get("key") or selected_suggestion["id"]
    reason = st.text_input(text["suggestion_reason"], value="", key=f"reason_{selected_key}")
    lifecycle_cols = st.columns(3)
    with lifecycle_cols[0]:
        st.code(build_update_suggestion_state_command(str(selected_key), "dismissed"), language=COMMAND_LANGUAGE)
        if st.button(
            text["dismiss_suggestion"],
            key=f"dismiss_{selected_key}",
            disabled=selected_state == "dismissed",
        ):
            try:
                set_suggestion_state(
                    RESEARCH_ROOT,
                    suggestion_id=str(selected_key),
                    state="dismissed",
                    reason=reason,
                )
                st.success(text["suggestion_state_updated"])
                st.rerun()
            except Exception as exc:
                st.error(f"{text['suggestion_state_failed']} {exc}")
    with lifecycle_cols[1]:
        st.code(build_update_suggestion_state_command(str(selected_key), "completed"), language=COMMAND_LANGUAGE)
        if st.button(
            text["complete_suggestion"],
            key=f"complete_{selected_key}",
            disabled=selected_state == "completed",
        ):
            try:
                set_suggestion_state(
                    RESEARCH_ROOT,
                    suggestion_id=str(selected_key),
                    state="completed",
                    reason=reason,
                )
                st.success(text["suggestion_state_updated"])
                st.rerun()
            except Exception as exc:
                st.error(f"{text['suggestion_state_failed']} {exc}")
    with lifecycle_cols[2]:
        st.code(build_update_suggestion_state_command(str(selected_key), "active"), language=COMMAND_LANGUAGE)
        if st.button(
            text["restore_suggestion"],
            key=f"restore_{selected_key}",
            disabled=selected_state == "active",
        ):
            try:
                set_suggestion_state(RESEARCH_ROOT, suggestion_id=str(selected_key), state="active")
                st.success(text["suggestion_state_updated"])
                st.rerun()
            except Exception as exc:
                st.error(f"{text['suggestion_state_failed']} {exc}")

    inactive = selected_state != "active"
    if inactive:
        st.caption(text["inactive_queue_disabled"])
    current_col, node_col = st.columns(2)
    with current_col:
        st.code(build_apply_suggestion_command(selected_suggestion["id"], "current"), language=COMMAND_LANGUAGE)
        if selected_suggestion.get("queued_in_current"):
            st.caption(text["queued_current"])
        if st.button(
            text["queue_current"],
            key=f"queue_current_{selected_suggestion['id']}",
            disabled=inactive or bool(selected_suggestion.get("queued_in_current")),
        ):
            try:
                queue_suggestion(RESEARCH_ROOT, suggestion_id=selected_suggestion["id"], target="current")
                st.success(text["queue_updated"])
                st.rerun()
            except Exception as exc:
                st.error(f"{text['queue_failed']} {exc}")
    with node_col:
        st.code(build_apply_suggestion_command(selected_suggestion["id"], "node"), language=COMMAND_LANGUAGE)
        if selected_suggestion.get("queued_in_node"):
            st.caption(text["queued_node"])
        if st.button(
            text["queue_node"],
            key=f"queue_node_{selected_suggestion['id']}",
            disabled=inactive or bool(selected_suggestion.get("queued_in_node")),
        ):
            try:
                queue_suggestion(RESEARCH_ROOT, suggestion_id=selected_suggestion["id"], target="node")
                st.success(text["queue_updated"])
                st.rerun()
            except Exception as exc:
                st.error(f"{text['queue_failed']} {exc}")


def render_option_workstreams(option_workstreams: list[dict], text: dict[str, str]) -> None:
    if not option_workstreams:
        st.info(text["no_option_workstreams"])
        return

    st.dataframe(
        pd.DataFrame(format_option_workstream_rows(option_workstreams)),
        use_container_width=True,
        hide_index=True,
    )
    options = [row for row in option_workstreams if row.get("option_id")]
    selected = st.selectbox(
        text["option"],
        options,
        format_func=lambda row: f"{row.get('option_title')} | {row.get('option_id')}",
        key="option_workstream_selected",
    )
    if not selected:
        return

    option_id = selected["option_id"]
    st.write(text["option_context_command"])
    st.code(build_option_workstream_context_command(option_id), language=COMMAND_LANGUAGE)
    st.write(text["claim_option_command"])
    st.code(build_claim_option_command(option_id), language=COMMAND_LANGUAGE)
    st.write(text["report_option_command"])
    st.code(build_report_option_workstream_command(option_id), language=COMMAND_LANGUAGE)


def render_search(search_index: list[dict], nodes: dict, text: dict[str, str]) -> None:
    summary = build_search_index_summary(search_index)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(text["node_entries"], summary["node_count"])
    m2.metric(text["note_entries"], summary["note_count"])
    m3.metric(text["resource_entries"], summary["resource_count"])
    m4.metric(text["unlinked_notes"], summary["unlinked_note_count"])

    sources = sorted({entry.get("source", "") for entry in search_index if entry.get("source")})
    node_types = sorted({entry.get("node_type", "") for entry in search_index if entry.get("node_type")})
    query = st.text_input(text["search_query"], value="", key="knowledge_search_query")
    c1, c2, c3, c4 = st.columns(4)
    selected_sources = set(c1.multiselect(
        text["search_source"],
        sources,
        default=sources,
        key="knowledge_search_sources",
    ))
    selected_node_types = set(c2.multiselect(
        text["search_node_type"],
        node_types,
        default=node_types,
        key="knowledge_search_node_types",
    ))
    focus_only = c3.checkbox(text["focus_related"], value=False, key="knowledge_search_focus_only")
    limit = int(c4.number_input(
        text["search_limit"],
        min_value=1,
        max_value=100,
        value=20,
        step=1,
        key="knowledge_search_limit",
    ))

    results = filter_search_results(
        search_index,
        query,
        selected_sources,
        set() if selected_node_types == set(node_types) else selected_node_types,
        focus_only=focus_only,
        limit=limit,
    )
    if not results:
        st.info(text["no_search_results"])
        return

    st.subheader(text["search_results"])
    st.dataframe(pd.DataFrame(format_search_result_rows(results)), use_container_width=True, hide_index=True)
    selected = st.selectbox(
        text["search_preview"],
        results,
        format_func=lambda item: f"{item.get('score')} | {item.get('source')} | {item.get('title')}",
        key="knowledge_search_selected_result",
    )
    st.text_area(text["search_preview"], selected.get("preview") or selected.get("snippet") or "", height=220)

    node_id = selected.get("node_id")
    node = nodes.get(node_id) if node_id else None
    if node:
        st.subheader(text["related_node"])
        st.dataframe(
            pd.DataFrame([{
                "id": node.id,
                "type": node.type,
                "status": node.status,
                "title": node.title,
                "summary": node.summary,
            }]),
            use_container_width=True,
            hide_index=True,
        )


def render_resources(link_rows: list[dict], search_index: list[dict], text: dict[str, str]) -> None:
    if not link_rows:
        st.info(text["no_resources"])
        return

    rows = format_resource_index_rows(link_rows, search_index)
    node_types = sorted({row.get("node_type", "") for row in rows if row.get("node_type")})
    resource_types = sorted({row.get("kind", "") for row in rows if row.get("kind")})
    exists_values = ["yes", "missing", "unknown"]

    type_filter, resource_filter, exists_filter = st.columns(3)
    selected_node_types = set(type_filter.multiselect(
        text["node_types"],
        node_types,
        default=node_types,
        key="resources_node_types",
    ))
    selected_resource_types = set(
        resource_filter.multiselect(
            text["resource_type"],
            resource_types,
            default=resource_types,
            key="resources_resource_types",
        )
    )
    selected_exists = set(exists_filter.multiselect(
        text["resource_exists"],
        exists_values,
        default=exists_values,
        key="resources_exists",
    ))

    filtered_rows = [
        row
        for row in rows
        if row.get("node_type") in selected_node_types
        and row.get("kind") in selected_resource_types
        and row.get("exists") in selected_exists
    ]
    if not filtered_rows:
        st.info(text["no_resources"])
        return
    st.dataframe(pd.DataFrame(filtered_rows), use_container_width=True, hide_index=True)


def render_experiment_matrix(nodes: dict, text: dict[str, str]) -> None:
    rows = build_experiment_matrix(nodes)
    if not rows:
        st.info(text["no_experiments"])
        return
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_decisions(nodes: dict, current: dict, text: dict[str, str], link_rows: list[dict]) -> None:
    rows = build_decision_rows(nodes)
    if not rows:
        st.info(text["no_decisions"])
        return
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    decision_options = [""] + [row["id"] for row in rows]
    decision_id = st.selectbox(
        text["inspect_decision"],
        decision_options,
        format_func=lambda value: text["none"] if not value else format_node_option(nodes, value),
        key="decisions_detail_decision",
    )
    if decision_id:
        render_node_detail(nodes, decision_id, text, current, link_rows)


def render_agent_context(context: dict, text: dict[str, str]) -> None:
    st.subheader(text["agent_context_pack"])
    st.json(context)


def render_data_health(
    nodes: dict,
    graph: dict,
    current: dict,
    validation_errors: list[str],
    text: dict[str, str],
    link_rows: list[dict],
    action_suggestions: list[dict],
    all_action_suggestions: list[dict],
    search_index: list[dict],
) -> None:
    if validation_errors:
        st.error(text["validation_failed"])
        for error in validation_errors:
            st.write(f"- {error}")
    else:
        st.success(text["all_valid"])

    rows = []
    for node in nodes.values():
        rows.append({
            "id": node.id,
            "type": node.type,
            "status": node.status,
            "parent": node.parent,
            "children": len(node.children),
        })
    st.subheader(text["node_inventory"])
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.subheader(text["graph_summary"])
    c1, c2 = st.columns(2)
    c1.metric(text["nodes"], len(graph["nodes"]))
    c2.metric(text["edges"], len(graph["edges"]))

    st.subheader(text["resources"])
    missing_rows = [row for row in link_rows if row.get("exists") is False]
    unknown_rows = [row for row in link_rows if row.get("exists") is None]
    fix_resource_count = len([item for item in action_suggestions if item.get("kind") == "fix_resource"])
    r1, r2, r3, r4 = st.columns(4)
    r1.metric(text["resources"], len(link_rows))
    r2.metric("Missing", len(missing_rows))
    r3.metric("Unknown", len(unknown_rows))
    r4.metric(text["action_guidance"], fix_resource_count)
    if missing_rows:
        st.warning(f"{len(missing_rows)} linked resource(s) are missing.")
        st.dataframe(pd.DataFrame(format_resource_rows(missing_rows)), use_container_width=True, hide_index=True)

    st.subheader(text["suggestion_lifecycle"])
    lifecycle_summary = build_suggestion_lifecycle_summary(current, all_action_suggestions)
    s1, s2, s3, s4 = st.columns(4)
    s1.metric(text["active"], lifecycle_summary["active"])
    s2.metric(text["dismissed"], lifecycle_summary["dismissed"])
    s3.metric(text["completed"], lifecycle_summary["completed"])
    s4.metric(text["orphan_suggestions"], lifecycle_summary["orphan"])
    if lifecycle_summary["orphan"]:
        st.warning(f"{lifecycle_summary['orphan']} suggestion lifecycle record(s) no longer match current suggestions.")
        lifecycle_rows = build_suggestion_lifecycle_rows(current, all_action_suggestions)
        orphan_rows = [row for row in lifecycle_rows if row.get("orphan")]
        st.caption(text["orphan_details"])
        st.dataframe(
            pd.DataFrame(format_suggestion_lifecycle_rows(orphan_rows)),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(text["cleanup_lifecycle_dry_run"])
        st.code(build_cleanup_suggestion_lifecycle_command(dry_run=True), language=COMMAND_LANGUAGE)
        st.caption(text["cleanup_lifecycle_apply"])
        st.code(build_cleanup_suggestion_lifecycle_command(dry_run=False), language=COMMAND_LANGUAGE)

    st.subheader(text["search_index"])
    search_summary = build_search_index_summary(search_index)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric(text["search_index"], search_summary["entry_count"])
    k2.metric(text["node_entries"], search_summary["node_count"])
    k3.metric(text["note_entries"], search_summary["note_count"])
    k4.metric(text["unlinked_notes"], search_summary["unlinked_note_count"])
    r1, r2, r3, r4 = st.columns(4)
    r1.metric(text["resource_entries"], search_summary["resource_count"])
    r2.metric(text["resource_truncated"], search_summary["resource_truncated_count"])
    r3.metric(text["resource_skipped"], search_summary["resource_skipped_count"])
    r4.metric(text["focus_resources"], search_summary["focus_resource_count"])


def main() -> None:
    with st.sidebar:
        language = st.selectbox("界面语言 / Language", ["中文", "English"], index=0)
    text = get_text(language)
    page_keys = ordered_tab_keys(text)
    default_page_key = "research_graph" if "research_graph" in page_keys else page_keys[0]
    if st.session_state.get("main_page") not in page_keys:
        st.session_state["main_page"] = default_page_key

    with st.sidebar:
        page_key = st.radio(
            "页面 / Page",
            page_keys,
            index=page_keys.index(st.session_state["main_page"]),
            format_func=lambda key: text[key],
            key="main_page",
        )

    if page_key == "coordination":
        st.title(text["page_title"])
        st.caption(text["page_caption"])
        st.header(text["coordination"])
        render_coordination(text)
        return

    (
        nodes,
        current,
        graph,
        context,
        validation_errors,
        link_rows,
        action_suggestions,
        all_action_suggestions,
        search_index,
        option_workstreams,
        saved_graph_views,
        dashboard_status,
    ) = load_graph_data()

    with st.sidebar:
        st.divider()
        st.header(text["current_focus"])
        st.write(f"{text['stage']}:", current.get("current_stage"))
        st.write(f"{text['problem']}:", current.get("current_problem"))
        st.write(f"{text['option']}:", current.get("current_option"))
        st.write(f"{text['focus_node']}:", graph.get("current_focus_node"))
        st.divider()
        st.header(text["data_health"])
        if validation_errors:
            st.error(f"{len(validation_errors)} {text['issues']}")
        else:
            st.success(text["valid"])
        if dashboard_status.get("stale"):
            st.warning(
                "Generated dashboard files are older than truth-source files. "
                f"Run `{dashboard_status['recommended_command']}` to refresh."
            )

    if page_key != "research_graph":
        st.title(text["page_title"])
        st.caption(text["page_caption"])

    if page_key == "research_graph":
        render_graph_tab(nodes, graph, current, text, link_rows, saved_graph_views, action_suggestions)
    elif page_key == "dashboard":
        render_dashboard(context, validation_errors, text, action_suggestions)
    elif page_key == "baselines":
        render_baselines(nodes, current, text)
    elif page_key == "branch_comparison":
        render_branch_comparison(nodes, current, text)
    elif page_key == "decision_trace":
        render_decision_trace(nodes, text)
    elif page_key == "action_guidance":
        render_action_guidance(all_action_suggestions, text)
    elif page_key == "option_workstreams":
        render_option_workstreams(option_workstreams, text)
    elif page_key == "search":
        render_search(search_index, nodes, text)
    elif page_key == "resources":
        render_resources(link_rows, search_index, text)
    elif page_key == "experiment_matrix":
        render_experiment_matrix(nodes, text)
    elif page_key == "decisions":
        render_decisions(nodes, current, text, link_rows)
    elif page_key == "agent_context":
        render_agent_context(context, text)
    elif page_key == "data_health":
        render_data_health(
            nodes,
            graph,
            current,
            validation_errors,
            text,
            link_rows,
            action_suggestions,
            all_action_suggestions,
            search_index,
        )


def _load_coordination_snapshot(
    root: Path,
    *,
    statuses: set[str] | None = None,
    page: str | None = None,
) -> dict[str, Any]:
    return build_coordination_snapshot(
        root,
        limit=100,
        page=page,
        statuses=statuses,
    )


def render_coordination(text: dict[str, str]) -> None:
    statuses = st.multiselect(
        text["coord_status_filter"],
        ["queued", "active", "blocked", "completed", "cancelled", "retired"],
        key="coordination_status_filter",
    )
    filter_signature = tuple(sorted(statuses))
    if st.session_state.get("coordination_filter_signature") != filter_signature:
        st.session_state["coordination_filter_signature"] = filter_signature
        st.session_state["coordination_page"] = None
    page = st.session_state.get("coordination_page")
    try:
        snapshot = _load_coordination_snapshot(
            RESEARCH_ROOT,
            statuses=set(statuses),
            page=page,
        )
    except ValueError:
        st.session_state["coordination_page"] = None
        snapshot = _load_coordination_snapshot(
            RESEARCH_ROOT,
            statuses=set(statuses),
        )
        page = None

    counts = snapshot["counts"]
    metric_columns = st.columns(5)
    metric_columns[0].metric(text["coord_ready"], counts["ready"])
    metric_columns[1].metric(text["coord_waiting"], counts["waiting"])
    metric_columns[2].metric(text["coord_stale_inputs"], counts["stale_inputs"])
    metric_columns[3].metric(text["coord_pending_review"], counts["pending_review"])
    metric_columns[4].metric(text["coord_expired_leases"], counts["expired_leases"])

    rows = snapshot["assignments"]["items"]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info(text["coord_no_assignments"])

    warnings = snapshot["overlap_warnings"]["items"]
    if warnings:
        st.warning(text["coord_overlap_warnings"] + "\n\n" + "\n".join(f"- {item}" for item in warnings))

    first_column, next_column = st.columns(2)
    if page and first_column.button(text["coord_first_page"], key="coordination_first_page"):
        st.session_state["coordination_page"] = None
        st.rerun()
    next_page = snapshot.get("next_page")
    if next_page and next_column.button(text["coord_next_page"], key="coordination_next_page"):
        st.session_state["coordination_page"] = next_page
        st.rerun()


if __name__ == "__main__":
    main()
