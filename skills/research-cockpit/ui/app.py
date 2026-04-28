from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd
import streamlit as st
from pyvis.network import Network
import streamlit.components.v1 as components

ROOT_DIR = Path(__file__).resolve().parents[1]
RESEARCH_ROOT = ROOT_DIR / "research_cockpit"
sys.path.insert(0, str(ROOT_DIR))

from cockpit.model import (
    build_action_suggestions,
    build_agent_context,
    build_branch_comparison,
    build_decision_acceptance_checklist,
    build_decision_rows,
    build_decision_evidence_summary,
    build_decision_trace,
    build_experiment_matrix,
    build_link_rows,
    build_option_workstream_rows,
    build_search_index,
    build_search_index_summary,
    build_suggestion_lifecycle_rows,
    build_suggestion_lifecycle_summary,
    graph_to_json,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
)
from ui.view_helpers import (
    build_apply_suggestion_command,
    build_accept_decision_command,
    build_check_decision_acceptance_command,
    build_claim_option_command,
    build_cleanup_suggestion_lifecycle_command,
    build_create_note_command,
    build_option_workstream_context_command,
    build_promote_decision_command,
    build_record_finding_command,
    build_report_option_workstream_command,
    build_set_focus_command,
    build_update_decision_checklist_command,
    build_update_suggestion_state_command,
    context_rows,
    default_detail_node_id,
    default_selected_statuses,
    edge_style_for_type,
    filter_action_suggestions,
    filter_graph_for_view,
    filter_node_ids,
    filter_search_results,
    format_action_suggestion_rows,
    format_comparison_rows,
    format_decision_checklist,
    format_evidence_summary,
    format_finding_rows,
    format_node_option,
    format_option_workstream_rows,
    format_resource_index_rows,
    format_resource_rows,
    format_search_result_rows,
    format_suggestion_lifecycle_rows,
    ordered_tab_labels,
)
from scripts.set_focus import set_focus as save_current_focus
from scripts.apply_suggestion import apply_suggestion as queue_suggestion
from scripts.update_suggestion_state import update_suggestion_state as set_suggestion_state


st.set_page_config(page_title="Research Cockpit", layout="wide")


UI_TEXT = {
    "zh": {
        "language": "界面语言",
        "page_title": "Research Cockpit 研究驾驶舱",
        "page_caption": "以仓库为中心的研究图谱状态：阶段、问题、方案、实验和决策。",
        "current_focus": "当前焦点",
        "stage": "阶段",
        "problem": "问题",
        "option": "方案",
        "focus_node": "焦点节点",
        "data_health": "数据健康",
        "resources": "资源",
        "action_guidance": "行动建议",
        "option_workstreams": "方案工作流",
        "valid": "有效",
        "issues": "个问题",
        "dashboard": "总览",
        "research_graph": "研究图谱",
        "experiment_matrix": "实验矩阵",
        "decisions": "决策",
        "branch_comparison": "方案比较",
        "decision_trace": "决策追踪",
        "agent_context": "Agent 上下文",
        "active_problems": "活跃问题",
        "active_options": "活跃方案",
        "next_actions": "下一步行动",
        "level": "层级",
        "title": "标题",
        "current_hypothesis": "当前假设",
        "no_hypothesis": "尚未记录当前假设。",
        "open_risks": "开放风险",
        "no_open_risks": "尚未记录开放风险。",
        "no_next_actions": "尚未记录下一步行动。",
        "data_health_warning": "数据健康存在 {count} 个问题。请打开“数据健康”页查看详情。",
        "select_node_hint": "选择一个节点以查看结构化 YAML 字段。",
        "type": "类型",
        "status": "状态",
        "priority": "优先级",
        "summary": "摘要",
        "tags": "标签",
        "none": "无",
        "no_summary": "暂无摘要。",
        "links": "链接",
        "create_note_command": "创建笔记命令",
        "search_nodes": "搜索节点",
        "resource_type": "资源类型",
        "resource_exists": "存在状态",
        "no_resources": "未找到关联资源。",
        "suggestion_kind": "建议类型",
        "suggestion_priority": "建议优先级",
        "focus_related": "当前焦点相关",
        "top_suggestions": "优先行动建议",
        "no_action_suggestions": "暂无行动建议。",
        "queue_suggestion": "选择建议",
        "queue_current": "写入当前行动队列",
        "queue_node": "写入来源节点行动队列",
        "queued_current": "已在当前行动队列中。",
        "queued_node": "已在来源节点行动队列中。",
        "queue_updated": "行动队列已更新。",
        "queue_failed": "写入行动队列失败。",
        "blockers": "阻塞项",
        "raw_yaml": "原始 YAML 字段",
        "view_mode": "视图范围",
        "focus_depth_1": "Focus 深度 1",
        "focus_depth_2": "Focus 深度 2",
        "global_graph": "全局图谱",
        "legend": "图例",
        "legend_current": "当前焦点：红色粗边框",
        "legend_path": "当前路径：橙色边框",
        "legend_depth_1": "Depth 1：父节点、子节点、兄弟节点",
        "legend_depth_2": "Depth 2：实验、决策、产物",
        "summary_tab": "摘要",
        "evidence_tab": "证据",
        "actions_tab": "行动",
        "agent_tab": "Agent 上下文",
        "set_focus_command": "设为当前焦点命令",
        "set_focus_command_hint": "运行后会更新 current_state.yaml，并重建 dashboard/context。",
        "record_finding_command": "记录实验观察命令",
        "promote_decision_command": "生成决策命令",
        "findings": "实验观察",
        "set_as_focus": "设为当前焦点",
        "focus_updated": "当前焦点已更新。",
        "focus_update_failed": "更新当前焦点失败。",
        "focus_role": "Focus 关系",
        "evidence_strength": "证据强度",
        "evidence_summary": "证据摘要",
        "supporting_experiments": "支持实验",
        "contradicting_experiments": "反向实验",
        "supporting_decisions": "支持决策",
        "linked_artifacts": "关联产物",
        "result_summary": "结果摘要",
        "outcome": "结果方向",
        "implementation_steps": "实施步骤",
        "success_criteria": "成功标准",
        "agent_key_files": "关键文件",
        "agent_key_questions": "关键问题",
        "next_action_hint": "下一步提示",
        "node_types": "节点类型",
        "statuses": "状态",
        "inspect_node": "查看节点",
        "no_experiments": "未找到实验节点。",
        "no_decisions": "未找到决策节点。",
        "inspect_decision": "查看决策",
        "select_problem": "选择问题",
        "no_options": "未找到可比较的方案。",
        "current_best": "当前最佳",
        "latest_result": "最新结果",
        "experiment_count": "实验数量",
        "rejection_reason": "拒绝原因",
        "trace_chain": "决策链路",
        "supporting_evidence": "支持证据",
        "evidence_summary": "证据摘要",
        "findings_count": "观察数量",
        "outcome_counts": "结果分布",
        "alternatives_considered": "备选方案",
        "consequences": "后续影响",
        "agent_context_pack": "Agent 上下文包",
        "validation_failed": "校验失败。",
        "all_valid": "所有节点引用、状态和当前焦点链接均有效。",
        "node_inventory": "节点清单",
        "graph_summary": "图谱摘要",
        "nodes": "节点",
        "edges": "边",
    },
    "en": {
        "language": "Language",
        "page_title": "Research Cockpit",
        "page_caption": "Repo-native graph state for research stages, problems, options, experiments, and decisions.",
        "current_focus": "Current Focus",
        "stage": "Stage",
        "problem": "Problem",
        "option": "Option",
        "focus_node": "Focus Node",
        "data_health": "Data Health",
        "resources": "Resources",
        "action_guidance": "Action Guidance",
        "option_workstreams": "Option Workstreams",
        "valid": "Valid",
        "issues": "issue(s)",
        "dashboard": "Dashboard",
        "research_graph": "Research Graph",
        "experiment_matrix": "Experiment Matrix",
        "decisions": "Decisions",
        "branch_comparison": "Branch Comparison",
        "decision_trace": "Decision Trace",
        "agent_context": "Agent Context",
        "active_problems": "Active Problems",
        "active_options": "Active Options",
        "next_actions": "Next Actions",
        "level": "Level",
        "title": "Title",
        "current_hypothesis": "Current Hypothesis",
        "no_hypothesis": "No current hypothesis recorded.",
        "open_risks": "Open Risks",
        "no_open_risks": "No open risks recorded.",
        "no_next_actions": "No next actions recorded.",
        "data_health_warning": "Data health has {count} issue(s). Open the Data Health tab for details.",
        "select_node_hint": "Select a node to inspect its structured YAML fields.",
        "type": "Type",
        "status": "Status",
        "priority": "Priority",
        "summary": "Summary",
        "tags": "Tags",
        "none": "None",
        "no_summary": "No summary.",
        "links": "Links",
        "create_note_command": "Create Note Command",
        "search_nodes": "Search nodes",
        "resource_type": "Resource Type",
        "resource_exists": "Exists",
        "no_resources": "No linked resources found.",
        "suggestion_kind": "Suggestion Type",
        "suggestion_priority": "Suggestion Priority",
        "focus_related": "Focus Related",
        "top_suggestions": "Top Action Suggestions",
        "no_action_suggestions": "No action suggestions.",
        "queue_suggestion": "Select suggestion",
        "queue_current": "Queue in current actions",
        "queue_node": "Queue in source node actions",
        "queued_current": "Already queued in current actions.",
        "queued_node": "Already queued in source node actions.",
        "queue_updated": "Action queue updated.",
        "queue_failed": "Failed to update action queue.",
        "blockers": "Blockers",
        "raw_yaml": "Raw YAML fields",
        "view_mode": "View Mode",
        "focus_depth_1": "Focus Depth 1",
        "focus_depth_2": "Focus Depth 2",
        "global_graph": "Global Graph",
        "legend": "Legend",
        "legend_current": "Current focus: red border",
        "legend_path": "Focus path: orange border",
        "legend_depth_1": "Depth 1: parents, children, siblings",
        "legend_depth_2": "Depth 2: experiments, decisions, artifacts",
        "summary_tab": "Summary",
        "evidence_tab": "Evidence",
        "actions_tab": "Actions",
        "agent_tab": "Agent Context",
        "set_focus_command": "Set Current Focus Command",
        "set_focus_command_hint": "Running this updates current_state.yaml and rebuilds dashboard/context files.",
        "record_finding_command": "Record Finding Command",
        "promote_decision_command": "Promote Decision Command",
        "findings": "Findings",
        "set_as_focus": "Set as current focus",
        "focus_updated": "Current focus updated.",
        "focus_update_failed": "Failed to update current focus.",
        "focus_role": "Focus Relation",
        "evidence_strength": "Evidence Strength",
        "evidence_summary": "Evidence Summary",
        "supporting_experiments": "Supporting Experiments",
        "contradicting_experiments": "Contradicting Experiments",
        "supporting_decisions": "Supporting Decisions",
        "linked_artifacts": "Linked Artifacts",
        "result_summary": "Result Summary",
        "outcome": "Outcome",
        "implementation_steps": "Implementation Steps",
        "success_criteria": "Success Criteria",
        "agent_key_files": "Key Files",
        "agent_key_questions": "Key Questions",
        "next_action_hint": "Next Action Hint",
        "node_types": "Node types",
        "statuses": "Statuses",
        "inspect_node": "Inspect node",
        "no_experiments": "No experiment nodes found.",
        "no_decisions": "No decision nodes found.",
        "inspect_decision": "Inspect decision",
        "select_problem": "Select problem",
        "no_options": "No comparable options found.",
        "current_best": "Current Best",
        "latest_result": "Latest Result",
        "experiment_count": "Experiment Count",
        "rejection_reason": "Rejection Reason",
        "trace_chain": "Decision Chain",
        "supporting_evidence": "Supporting Evidence",
        "evidence_summary": "Evidence Summary",
        "findings_count": "Findings Count",
        "outcome_counts": "Outcome Counts",
        "alternatives_considered": "Alternatives Considered",
        "consequences": "Consequences",
        "agent_context_pack": "Agent Context Pack",
        "validation_failed": "Validation failed.",
        "all_valid": "All node references, statuses, and current focus links are valid.",
        "node_inventory": "Node Inventory",
        "graph_summary": "Graph Summary",
        "nodes": "Nodes",
        "edges": "Edges",
    },
}


EXTRA_UI_TEXT = {
    "zh": {
        "suggestion_state": "建议状态",
        "lifecycle_state": "生命周期状态",
        "suggestion_reason": "原因",
        "dismiss_suggestion": "忽略建议",
        "complete_suggestion": "标记完成",
        "restore_suggestion": "恢复活跃",
        "suggestion_state_updated": "建议状态已更新。",
        "suggestion_state_failed": "更新建议状态失败：",
        "inactive_queue_disabled": "已忽略或已完成的建议不能写入行动队列。",
        "suggestion_lifecycle": "建议生命周期",
        "orphan_suggestions": "孤立记录",
        "orphan_details": "孤立建议记录明细",
        "cleanup_lifecycle_dry_run": "清理前预览命令",
        "cleanup_lifecycle_apply": "清理孤立记录命令",
        "active": "活跃",
        "dismissed": "已忽略",
        "completed": "已完成",
        "search": "搜索",
        "search_query": "搜索内容",
        "search_source": "来源",
        "search_node_type": "节点类型",
        "search_limit": "结果数量",
        "search_results": "搜索结果",
        "search_preview": "内容预览",
        "no_search_results": "未找到搜索结果。",
        "search_index": "搜索索引",
        "note_entries": "Note 条目",
        "node_entries": "Node 条目",
        "unlinked_notes": "未关联笔记",
        "related_node": "关联节点",
        "resource_entries": "资源正文条目",
        "resource_truncated": "截断资源",
        "resource_skipped": "跳过资源",
        "focus_resources": "Focus 资源",
        "decision_acceptance_checklist": "决策接受质量门",
        "decision_ready": "该 decision 已满足接受条件。",
        "decision_not_ready": "该 decision 尚未满足接受条件。",
        "check_decision_command": "检查决策接受条件命令",
        "accept_decision_command": "接受决策命令",
        "update_decision_checklist_command": "更新决策检查清单命令",
        "claim_option_command": "认领方案工作流命令",
        "option_context_command": "方案工作流上下文命令",
        "report_option_command": "回报方案工作流命令",
        "no_option_workstreams": "暂无方案工作流记录。",
    },
    "en": {
        "suggestion_state": "Suggestion State",
        "lifecycle_state": "Lifecycle State",
        "suggestion_reason": "Reason",
        "dismiss_suggestion": "Dismiss suggestion",
        "complete_suggestion": "Mark completed",
        "restore_suggestion": "Restore active",
        "suggestion_state_updated": "Suggestion state updated.",
        "suggestion_state_failed": "Failed to update suggestion state:",
        "inactive_queue_disabled": "Dismissed or completed suggestions cannot be queued.",
        "suggestion_lifecycle": "Suggestion Lifecycle",
        "orphan_suggestions": "Orphan Records",
        "orphan_details": "Orphan Suggestion Records",
        "cleanup_lifecycle_dry_run": "Cleanup dry-run command",
        "cleanup_lifecycle_apply": "Cleanup orphan records command",
        "active": "Active",
        "dismissed": "Dismissed",
        "completed": "Completed",
        "search": "Search",
        "search_query": "Search Query",
        "search_source": "Source",
        "search_node_type": "Node Type",
        "search_limit": "Result Limit",
        "search_results": "Search Results",
        "search_preview": "Preview",
        "no_search_results": "No search results.",
        "search_index": "Search Index",
        "note_entries": "Note Entries",
        "node_entries": "Node Entries",
        "unlinked_notes": "Unlinked Notes",
        "related_node": "Related Node",
        "resource_entries": "Resource Entries",
        "resource_truncated": "Truncated Resources",
        "resource_skipped": "Skipped Resources",
        "focus_resources": "Focus Resources",
        "decision_acceptance_checklist": "Decision Acceptance Checklist",
        "decision_ready": "This decision is ready for acceptance.",
        "decision_not_ready": "This decision is not ready for acceptance.",
        "check_decision_command": "Check Decision Acceptance Command",
        "accept_decision_command": "Accept Decision Command",
        "update_decision_checklist_command": "Update Decision Checklist Command",
        "claim_option_command": "Claim Option Workstream Command",
        "option_context_command": "Option Workstream Context Command",
        "report_option_command": "Report Option Workstream Command",
        "no_option_workstreams": "No option workstreams recorded.",
    },
}


def get_text(language: str) -> dict[str, str]:
    locale = "zh" if language == "中文" else "en"
    text = dict(UI_TEXT[locale])
    text.update(EXTRA_UI_TEXT[locale])
    return text


def load_graph_data():
    nodes = load_nodes(RESEARCH_ROOT)
    current = load_yaml(RESEARCH_ROOT / "current_state.yaml")
    explicit_edges = load_explicit_edges(RESEARCH_ROOT)
    validation_errors = validate_cockpit(RESEARCH_ROOT, nodes, current, explicit_edges)
    graph = graph_to_json(nodes, current.get("current_focus_path", []), current, explicit_edges)
    context = build_agent_context(RESEARCH_ROOT, nodes)
    link_rows = build_link_rows(RESEARCH_ROOT, nodes)
    search_index = build_search_index(RESEARCH_ROOT, nodes, current)
    option_workstreams = build_option_workstream_rows(nodes)
    action_suggestions = build_action_suggestions(RESEARCH_ROOT, nodes, current, link_rows)
    all_action_suggestions = build_action_suggestions(
        RESEARCH_ROOT,
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
    )


def build_pyvis_html(
    graph: dict,
    selected_types: set[str],
    selected_statuses: set[str],
    focus_node_id: str | None = None,
) -> str:
    net = Network(
        height="680px",
        width="100%",
        bgcolor="#FFFFFF",
        font_color="#111111",
        directed=True,
        cdn_resources="in_line",
    )
    net.toggle_physics(True)
    net.barnes_hut(gravity=-7000, central_gravity=0.18, spring_length=180, spring_strength=0.04)

    included = set()
    for node in graph["nodes"]:
        if selected_types and node["type"] not in selected_types:
            continue
        if selected_statuses and node["status"] not in selected_statuses:
            continue

        is_current_focus = node.get("is_current_focus")
        border_width = 8 if is_current_focus else 5 if node.get("is_focus") else 1
        border_color = "#D93025" if is_current_focus else "#F59E0B" if node.get("is_focus") else "#6B7280"
        node_color = {
            "background": node.get("color", "#EEEEEE"),
            "border": border_color,
            "highlight": {"background": node.get("color", "#EEEEEE"), "border": "#D93025"},
        }
        net.add_node(
            node["id"],
            label=node["label"],
            title=f"{node['type']} | {node['status']}<br>{node.get('title', '')}",
            color=node_color,
            shape=node.get("shape", "box"),
            borderWidth=border_width,
            borderWidthSelected=8,
            size=34 if is_current_focus else 24 if node.get("is_focus") else 18,
            font={"size": 16 if is_current_focus else 15},
        )
        included.add(node["id"])

    for edge in graph["edges"]:
        if edge["from"] in included and edge["to"] in included:
            style = edge_style_for_type(edge.get("type") or edge.get("relation"))
            strength = edge.get("strength")
            try:
                width = 1 + min(4, max(0, float(strength) * 4)) if strength is not None else 1
            except (TypeError, ValueError):
                width = 1
            net.add_edge(
                edge["from"],
                edge["to"],
                color=style["color"],
                dashes=style["dashes"],
                arrows="to",
                label=edge.get("label"),
                width=width,
            )

    html = net.generate_html(notebook=False)
    focus_target = focus_node_id or graph.get("current_focus_node")
    if focus_target in included:
        focus_json = json.dumps(focus_target)
        focus_script = f"""
<script>
setTimeout(function () {{
  if (typeof network !== "undefined") {{
    network.selectNodes([{focus_json}]);
    network.focus({focus_json}, {{
      scale: 1.25,
      animation: {{ duration: 700, easingFunction: "easeInOutQuad" }}
    }});
  }}
}}, 900);
</script>
"""
        html = html.replace("</body>", focus_script + "</body>")
    return html


def render_pyvis_graph(
    graph: dict,
    selected_types: set[str],
    selected_statuses: set[str],
    focus_node_id: str | None = None,
) -> None:
    html = build_pyvis_html(graph, selected_types, selected_statuses, focus_node_id)
    components.html(html, height=720, scrolling=True)


def render_node_detail(
    nodes: dict,
    node_id: str,
    text: dict[str, str],
    current: dict | None = None,
    link_rows: list[dict] | None = None,
) -> None:
    if not node_id:
        st.info(text["select_node_hint"])
        return

    node = nodes[node_id]
    st.subheader(node.title)
    summary_tab, evidence_tab, actions_tab, agent_tab = st.tabs([
        text["summary_tab"],
        text["evidence_tab"],
        text["actions_tab"],
        text["agent_tab"],
    ])

    with summary_tab:
        c1, c2 = st.columns(2)
        c1.write(text["type"])
        c1.code(node.type)
        c2.write(text["status"])
        c2.code(node.status)
        st.write(text["priority"])
        st.code(node.priority or text["none"])
        st.write(text["summary"])
        st.write(node.summary or text["no_summary"])
        if node.tags:
            st.write(text["tags"])
            st.write(", ".join(node.tags))
        node_link_rows = [row for row in (link_rows or []) if row.get("node_id") == node_id]
        if node_link_rows:
            st.write(text["links"])
            st.dataframe(pd.DataFrame(format_resource_rows(node_link_rows)), use_container_width=True, hide_index=True)

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

    with actions_tab:
        if current:
            if st.button(text["set_as_focus"], key=f"set_focus_{node_id}"):
                try:
                    save_current_focus(RESEARCH_ROOT, focus_node=node_id)
                    st.success(text["focus_updated"])
                    st.rerun()
                except Exception as exc:
                    st.error(f"{text['focus_update_failed']} {exc}")
            st.write(text["set_focus_command"])
            st.code(build_set_focus_command(current, node_id), language="powershell")
            st.caption(text["set_focus_command_hint"])
        if node.type == "experiment":
            st.write(text["record_finding_command"])
            st.code(build_record_finding_command(node_id), language="powershell")
        if node.type == "option":
            st.write(text["promote_decision_command"])
            st.code(build_promote_decision_command(node_id), language="powershell")
            st.write(text["claim_option_command"])
            st.code(build_claim_option_command(node_id), language="powershell")
            st.write(text["option_context_command"])
            st.code(build_option_workstream_context_command(node_id), language="powershell")
            st.write(text["report_option_command"])
            st.code(build_report_option_workstream_command(node_id), language="powershell")
        if node.type == "decision":
            st.write(text["check_decision_command"])
            st.code(build_check_decision_acceptance_command(node_id), language="powershell")
            st.write(text["update_decision_checklist_command"])
            st.code(build_update_decision_checklist_command(node_id), language="powershell")
            st.write(text["accept_decision_command"])
            st.code(build_accept_decision_command(node_id), language="powershell")
        if node.type in {"problem", "option", "experiment", "decision"}:
            st.write(text["create_note_command"])
            st.code(build_create_note_command(node_id), language="powershell")
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


def render_graph_tab(nodes: dict, graph: dict, current: dict, text: dict[str, str], link_rows: list[dict]) -> None:
    all_types = sorted({node["type"] for node in graph["nodes"]})
    all_statuses = sorted({node["status"] for node in graph["nodes"]})

    controls, detail = st.columns([2, 1])
    with controls:
        mode_label_to_value = {
            text["focus_depth_2"]: "focus_depth_2",
            text["focus_depth_1"]: "focus_depth_1",
            text["global_graph"]: "global",
        }
        view_label = st.radio(
            text["view_mode"],
            list(mode_label_to_value),
            index=0,
            horizontal=True,
            key="graph_view_mode",
        )
        view_mode = mode_label_to_value.get(view_label or text["focus_depth_2"], "focus_depth_2")
        filter_left, filter_right = st.columns(2)
        selected_types = set(filter_left.multiselect(
            text["node_types"],
            all_types,
            default=all_types,
            key="graph_node_types",
        ))
        selected_statuses = set(
            filter_right.multiselect(
                text["statuses"],
                all_statuses,
                default=default_selected_statuses(graph, all_statuses),
                key="graph_statuses",
            )
        )
        filtered_graph = filter_graph_for_view(graph, view_mode, selected_types, selected_statuses)
        render_pyvis_graph(filtered_graph, set(), set(), graph.get("current_focus_node"))
        with st.expander(text["legend"], expanded=True):
            st.write(text["legend_current"])
            st.write(text["legend_path"])
            st.write(text["legend_depth_1"])
            st.write(text["legend_depth_2"])

    with detail:
        visible_node_ids = [node["id"] for node in filtered_graph["nodes"] if node["id"] in nodes]
        search_query = st.text_input(text["search_nodes"], value="", key="graph_node_search")
        search_scope = {node_id: nodes[node_id] for node_id in (visible_node_ids or sorted(nodes.keys()))}
        detail_options = filter_node_ids(search_scope, search_query)
        default_id = default_detail_node_id(graph, detail_options)
        if not detail_options:
            st.info(text["select_node_hint"])
            return
        default_index = detail_options.index(default_id) if default_id in detail_options else 0
        node_id = st.selectbox(
            text["inspect_node"],
            detail_options,
            index=default_index,
            format_func=lambda value: format_node_option(nodes, value),
            key="graph_detail_node",
        )
        render_node_detail(nodes, node_id, text, current, link_rows)


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
        st.code(selected["suggested_command"], language="powershell")

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
        st.code(build_update_suggestion_state_command(str(selected_key), "dismissed"), language="powershell")
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
        st.code(build_update_suggestion_state_command(str(selected_key), "completed"), language="powershell")
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
        st.code(build_update_suggestion_state_command(str(selected_key), "active"), language="powershell")
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
        st.code(build_apply_suggestion_command(selected_suggestion["id"], "current"), language="powershell")
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
        st.code(build_apply_suggestion_command(selected_suggestion["id"], "node"), language="powershell")
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
    st.code(build_option_workstream_context_command(option_id), language="powershell")
    st.write(text["claim_option_command"])
    st.code(build_claim_option_command(option_id), language="powershell")
    st.write(text["report_option_command"])
    st.code(build_report_option_workstream_command(option_id), language="powershell")


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
        st.code(build_cleanup_suggestion_lifecycle_command(dry_run=True), language="powershell")
        st.caption(text["cleanup_lifecycle_apply"])
        st.code(build_cleanup_suggestion_lifecycle_command(dry_run=False), language="powershell")

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
    ) = load_graph_data()

    with st.sidebar:
        language = st.selectbox("界面语言 / Language", ["中文", "English"], index=0)
    text = get_text(language)

    st.title(text["page_title"])
    st.caption(text["page_caption"])

    with st.sidebar:
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

    tab_labels = ordered_tab_labels(text)
    tabs = st.tabs(tab_labels)

    for label, tab in zip(tab_labels, tabs):
        with tab:
            if label == text["research_graph"]:
                render_graph_tab(nodes, graph, current, text, link_rows)
            elif label == text["dashboard"]:
                render_dashboard(context, validation_errors, text, action_suggestions)
            elif label == text["branch_comparison"]:
                render_branch_comparison(nodes, current, text)
            elif label == text["decision_trace"]:
                render_decision_trace(nodes, text)
            elif label == text["action_guidance"]:
                render_action_guidance(all_action_suggestions, text)
            elif label == text["option_workstreams"]:
                render_option_workstreams(option_workstreams, text)
            elif label == text["search"]:
                render_search(search_index, nodes, text)
            elif label == text["resources"]:
                render_resources(link_rows, search_index, text)
            elif label == text["experiment_matrix"]:
                render_experiment_matrix(nodes, text)
            elif label == text["decisions"]:
                render_decisions(nodes, current, text, link_rows)
            elif label == text["agent_context"]:
                render_agent_context(context, text)
            elif label == text["data_health"]:
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


if __name__ == "__main__":
    main()
