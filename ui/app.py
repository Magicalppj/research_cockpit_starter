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
    build_agent_context,
    build_branch_comparison,
    build_decision_rows,
    build_decision_trace,
    build_experiment_matrix,
    build_link_rows,
    graph_to_json,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
)
from scripts.set_focus import set_focus as save_current_focus


st.set_page_config(page_title="Audio Edit Research Cockpit", layout="wide")


UI_TEXT = {
    "zh": {
        "language": "界面语言",
        "page_title": "Audio Edit 研究驾驶舱",
        "page_caption": "以仓库为中心的研究图谱状态：阶段、问题、方案、实验和决策。",
        "current_focus": "当前焦点",
        "stage": "阶段",
        "problem": "问题",
        "option": "方案",
        "focus_node": "焦点节点",
        "data_health": "数据健康",
        "resources": "资源",
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
        "page_title": "Audio Edit Research Cockpit",
        "page_caption": "Repo-native graph state for research stages, problems, options, experiments, and decisions.",
        "current_focus": "Current Focus",
        "stage": "Stage",
        "problem": "Problem",
        "option": "Option",
        "focus_node": "Focus Node",
        "data_health": "Data Health",
        "resources": "Resources",
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


def get_text(language: str) -> dict[str, str]:
    return UI_TEXT["zh"] if language == "中文" else UI_TEXT["en"]


def load_graph_data():
    nodes = load_nodes(RESEARCH_ROOT)
    current = load_yaml(RESEARCH_ROOT / "current_state.yaml")
    explicit_edges = load_explicit_edges(RESEARCH_ROOT)
    validation_errors = validate_cockpit(RESEARCH_ROOT, nodes, current, explicit_edges)
    graph = graph_to_json(nodes, current.get("current_focus_path", []), current, explicit_edges)
    context = build_agent_context(RESEARCH_ROOT, nodes)
    link_rows = build_link_rows(RESEARCH_ROOT, nodes)
    return nodes, current, graph, context, validation_errors, link_rows


def ordered_tab_labels(text: dict[str, str]) -> list[str]:
    keys = [
        "research_graph",
        "dashboard",
        "branch_comparison",
        "decision_trace",
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
    parts = [r"D:\Tools\miniconda3\envs\aigc\python.exe", r"scripts\set_focus.py"]
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
        r"D:\Tools\miniconda3\envs\aigc\python.exe scripts\record_finding.py"
        f" --experiment {experiment_id}"
        ' --statement "Describe the finding"'
        " --confidence medium"
        " --outcome inconclusive"
    )


def build_promote_decision_command(option_id: str) -> str:
    return (
        r"D:\Tools\miniconda3\envs\aigc\python.exe scripts\promote_decision.py"
        " --id decision_new"
        f" --option {option_id}"
        ' --title "Decision title"'
        ' --summary "Decision summary"'
        " --status proposed"
    )


def build_create_note_command(node_id: str) -> str:
    return (
        r"D:\Tools\miniconda3\envs\aigc\python.exe scripts\create_note.py"
        f" --node {node_id}"
    )


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


def render_dashboard(context: dict, validation_errors: list[str], text: dict[str, str]) -> None:
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
            for action in actions:
                st.checkbox(action, value=False)
        else:
            st.caption(text["no_next_actions"])

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
        )
        view_mode = mode_label_to_value.get(view_label or text["focus_depth_2"], "focus_depth_2")
        filter_left, filter_right = st.columns(2)
        selected_types = set(filter_left.multiselect(text["node_types"], all_types, default=all_types))
        selected_statuses = set(
            filter_right.multiselect(
                text["statuses"],
                all_statuses,
                default=default_selected_statuses(graph, all_statuses),
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
        search_query = st.text_input(text["search_nodes"], value="")
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
    )
    rows = build_branch_comparison(nodes, problem_id, current)
    if not rows:
        st.info(text["no_options"])
        return

    display_rows = format_comparison_rows(rows)
    st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)


def _context_rows(items: list[dict]) -> list[dict]:
    return [
        {
            "id": item.get("id"),
            "type": item.get("type"),
            "title": item.get("title"),
            "status": item.get("status"),
            "summary": item.get("summary"),
        }
        for item in items
    ]


def render_decision_trace(nodes: dict, text: dict[str, str]) -> None:
    decision_ids = sorted(node.id for node in nodes.values() if node.type == "decision")
    if not decision_ids:
        st.info(text["no_decisions"])
        return

    decision_id = st.selectbox(
        text["inspect_decision"],
        decision_ids,
        format_func=lambda value: format_node_option(nodes, value),
    )
    trace = build_decision_trace(nodes, decision_id)

    st.subheader(text["trace_chain"])
    chain = [
        trace_item
        for trace_item in (trace.get("stage"), trace.get("problem"), trace.get("option"), trace.get("decision"))
        if trace_item
    ]
    st.dataframe(pd.DataFrame(_context_rows(chain)), use_container_width=True, hide_index=True)

    experiments = trace.get("supporting_experiments", [])
    if experiments:
        st.subheader(text["supporting_evidence"])
        st.dataframe(pd.DataFrame(_context_rows(experiments)), use_container_width=True, hide_index=True)

    alternatives = trace.get("alternatives_considered", [])
    if alternatives:
        st.subheader(text["alternatives_considered"])
        st.dataframe(pd.DataFrame(_context_rows(alternatives)), use_container_width=True, hide_index=True)

    consequences = trace.get("consequences", [])
    if consequences:
        st.subheader(text["consequences"])
        for item in consequences:
            st.write(f"- {item}")


def render_resources(link_rows: list[dict], text: dict[str, str]) -> None:
    if not link_rows:
        st.info(text["no_resources"])
        return

    rows = format_resource_rows(link_rows)
    node_types = sorted({row.get("node_type", "") for row in rows if row.get("node_type")})
    resource_types = sorted({row.get("kind", "") for row in rows if row.get("kind")})
    exists_values = ["yes", "missing", "unknown"]

    type_filter, resource_filter, exists_filter = st.columns(3)
    selected_node_types = set(type_filter.multiselect(text["node_types"], node_types, default=node_types))
    selected_resource_types = set(
        resource_filter.multiselect(text["resource_type"], resource_types, default=resource_types)
    )
    selected_exists = set(exists_filter.multiselect(text["resource_exists"], exists_values, default=exists_values))

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
    )
    if decision_id:
        render_node_detail(nodes, decision_id, text, current, link_rows)


def render_agent_context(context: dict, text: dict[str, str]) -> None:
    st.subheader(text["agent_context_pack"])
    st.json(context)


def render_data_health(
    nodes: dict,
    graph: dict,
    validation_errors: list[str],
    text: dict[str, str],
    link_rows: list[dict],
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
    r1, r2, r3 = st.columns(3)
    r1.metric(text["resources"], len(link_rows))
    r2.metric("Missing", len(missing_rows))
    r3.metric("Unknown", len(unknown_rows))
    if missing_rows:
        st.warning(f"{len(missing_rows)} linked resource(s) are missing.")
        st.dataframe(pd.DataFrame(format_resource_rows(missing_rows)), use_container_width=True, hide_index=True)


def main() -> None:
    nodes, current, graph, context, validation_errors, link_rows = load_graph_data()

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
                render_dashboard(context, validation_errors, text)
            elif label == text["branch_comparison"]:
                render_branch_comparison(nodes, current, text)
            elif label == text["decision_trace"]:
                render_decision_trace(nodes, text)
            elif label == text["resources"]:
                render_resources(link_rows, text)
            elif label == text["experiment_matrix"]:
                render_experiment_matrix(nodes, text)
            elif label == text["decisions"]:
                render_decisions(nodes, current, text, link_rows)
            elif label == text["agent_context"]:
                render_agent_context(context, text)
            elif label == text["data_health"]:
                render_data_health(nodes, graph, validation_errors, text, link_rows)


if __name__ == "__main__":
    main()
