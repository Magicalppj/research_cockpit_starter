from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile

import pandas as pd
import streamlit as st
from pyvis.network import Network
import streamlit.components.v1 as components

ROOT_DIR = Path(__file__).resolve().parents[1]
RESEARCH_ROOT = ROOT_DIR / "research_cockpit"
sys.path.insert(0, str(ROOT_DIR))

from cockpit.model import load_nodes, load_yaml, graph_to_json, build_agent_context


st.set_page_config(page_title="Audio Edit Research Cockpit", layout="wide")


def load_graph_data():
    nodes = load_nodes(RESEARCH_ROOT)
    current = load_yaml(RESEARCH_ROOT / "current_state.yaml")
    graph = graph_to_json(nodes, current.get("current_focus_path", []))
    context = build_agent_context(RESEARCH_ROOT, nodes)
    return nodes, current, graph, context


def render_pyvis_graph(graph: dict, selected_types: set[str], selected_statuses: set[str]):
    net = Network(height="760px", width="100%", bgcolor="#FFFFFF", font_color="#111111", directed=True)
    net.toggle_physics(True)
    net.barnes_hut(gravity=-7000, central_gravity=0.18, spring_length=180, spring_strength=0.04)

    included = set()
    for n in graph["nodes"]:
        if selected_types and n["type"] not in selected_types:
            continue
        if selected_statuses and n["status"] not in selected_statuses:
            continue

        border_width = 5 if n.get("is_focus") else 1
        color = n.get("color", "#EEEEEE")
        net.add_node(
            n["id"],
            label=n["label"],
            title=f"{n['type']} | {n['status']}<br>{n.get('title','')}",
            color=color,
            shape=n.get("shape", "box"),
            borderWidth=border_width,
            font={"size": 16},
        )
        included.add(n["id"])

    for e in graph["edges"]:
        if e["from"] in included and e["to"] in included:
            net.add_edge(e["from"], e["to"], color="#888888", arrows="to")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
    net.write_html(tmp.name, notebook=False, open_browser=False)
    html = Path(tmp.name).read_text(encoding="utf-8")
    components.html(html, height=800, scrolling=True)


def main():
    nodes, current, graph, context = load_graph_data()

    st.title("Audio Edit Research Cockpit")
    st.caption("Graph-structured research state for datasets, model branches, decisions, and experiments.")

    with st.sidebar:
        st.header("Filters")
        all_types = sorted({n["type"] for n in graph["nodes"]})
        all_statuses = sorted({n["status"] for n in graph["nodes"]})
        selected_types = set(st.multiselect("Node types", all_types, default=all_types))
        selected_statuses = set(st.multiselect("Statuses", all_statuses, default=all_statuses))
        st.divider()
        st.header("Current Focus")
        st.write("Stage:", current.get("current_stage"))
        st.write("Problem:", current.get("current_problem"))
        st.write("Option:", current.get("current_option"))

    top = st.container()
    with top:
        c1, c2, c3 = st.columns(3)
        c1.metric("Nodes", len(graph["nodes"]))
        c2.metric("Active Problems", len(context.get("active_problems", [])))
        c3.metric("Next Actions", len(context.get("next_actions", [])))

        st.subheader("Current Hypothesis")
        st.write(context.get("current_hypothesis", ""))

        if context.get("open_risks"):
            st.subheader("Open Risks")
            for r in context["open_risks"]:
                st.warning(r)

        if context.get("next_actions"):
            st.subheader("Next Actions")
            for a in context["next_actions"]:
                st.checkbox(a, value=False)

    tab_graph, tab_matrix, tab_context, tab_raw = st.tabs(["Research Graph", "Experiment Matrix", "Agent Context", "Raw Nodes"])

    with tab_graph:
        st.subheader("Interactive Problem / Option / Experiment Graph")
        render_pyvis_graph(graph, selected_types, selected_statuses)

        node_id = st.selectbox("Inspect node", [""] + sorted(nodes.keys()))
        if node_id:
            n = nodes[node_id]
            st.subheader(n.title)
            st.write("Type:", n.type)
            st.write("Status:", n.status)
            st.write("Priority:", n.priority)
            st.write("Summary:", n.summary)
            st.json(n.raw)

    with tab_matrix:
        exps = []
        for n in nodes.values():
            if n.type == "experiment":
                exps.append({
                    "id": n.id,
                    "title": n.title,
                    "status": n.status,
                    "dataset": n.raw.get("dataset"),
                    "backbone": n.raw.get("backbone"),
                    "parent": n.parent,
                    "summary": n.summary,
                    "result": n.raw.get("result_summary"),
                })
        st.dataframe(pd.DataFrame(exps), use_container_width=True)

        problems = []
        for n in nodes.values():
            if n.type == "problem":
                problems.append({
                    "id": n.id,
                    "title": n.title,
                    "status": n.status,
                    "priority": n.priority,
                    "current_best_option": n.raw.get("current_best_option"),
                    "summary": n.summary,
                })
        st.subheader("Active Problems")
        st.dataframe(pd.DataFrame(problems), use_container_width=True)

    with tab_context:
        st.subheader("Agent Context Pack")
        st.json(context)

    with tab_raw:
        st.subheader("All Nodes")
        rows = []
        for n in nodes.values():
            rows.append({
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "status": n.status,
                "parent": n.parent,
                "summary": n.summary,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True)


if __name__ == "__main__":
    main()
