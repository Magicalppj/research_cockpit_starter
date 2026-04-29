from __future__ import annotations

import json

import streamlit.components.v1 as components
from pyvis.network import Network

from research_cockpit.ui.view_helpers import edge_style_for_type


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

