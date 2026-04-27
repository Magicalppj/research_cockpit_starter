from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import yaml
import networkx as nx


STATUS_COLORS = {
    "planned": "#D9E8FF",
    "open": "#D9E8FF",
    "queued": "#D9E8FF",
    "active": "#FFE9A8",
    "running": "#FFE9A8",
    "promising": "#E8F5D8",
    "accepted": "#CFEAD6",
    "done": "#CFEAD6",
    "resolved": "#CFEAD6",
    "rejected": "#F3D0D0",
    "failed": "#F3D0D0",
    "blocked": "#F6C6C6",
    "parked": "#E5E5E5",
    "paused": "#E5E5E5",
    "proposed": "#E7D8F6",
    "superseded": "#E5E5E5",
}

TYPE_SHAPES = {
    "stage": "box",
    "problem": "diamond",
    "option": "box",
    "experiment": "ellipse",
    "decision": "hexagon",
    "artifact": "database",
}


@dataclass
class ResearchNode:
    id: str
    type: str
    title: str
    status: str = "open"
    summary: str = ""
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    priority: str | None = None
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchNode":
        return cls(
            id=str(data["id"]),
            type=str(data.get("type", "node")),
            title=str(data.get("title", data["id"])),
            status=str(data.get("status", "open")),
            summary=str(data.get("summary", "")),
            parent=data.get("parent"),
            children=[str(x) for x in data.get("children", []) or []],
            priority=data.get("priority"),
            tags=[str(x) for x in data.get("tags", []) or []],
            raw=data,
        )


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def load_nodes(root: Path) -> dict[str, ResearchNode]:
    node_dir = root / "graph" / "nodes"
    nodes: dict[str, ResearchNode] = {}
    for path in sorted(node_dir.glob("*.yaml")):
        data = load_yaml(path)
        if not data:
            continue
        node = ResearchNode.from_dict(data)
        nodes[node.id] = node
    return nodes


def build_graph(nodes: dict[str, ResearchNode]) -> nx.DiGraph:
    g = nx.DiGraph()
    for node in nodes.values():
        g.add_node(node.id, **node.raw)
    for node in nodes.values():
        if node.parent and node.parent in nodes:
            g.add_edge(node.parent, node.id, relation="parent")
        for child in node.children:
            if child in nodes:
                g.add_edge(node.id, child, relation="child")
    return g


def graph_to_json(nodes: dict[str, ResearchNode], current_focus_path: list[str] | None = None) -> dict[str, Any]:
    current_focus_path = current_focus_path or []
    focus_set = set(current_focus_path)

    out_nodes = []
    out_edges = []
    for node in nodes.values():
        color = STATUS_COLORS.get(node.status, "#EEEEEE")
        out_nodes.append({
            "id": node.id,
            "label": node.title,
            "title": node.summary,
            "type": node.type,
            "status": node.status,
            "priority": node.priority,
            "color": color,
            "shape": TYPE_SHAPES.get(node.type, "box"),
            "is_focus": node.id in focus_set,
            "raw": node.raw,
        })
        if node.parent and node.parent in nodes:
            out_edges.append({"from": node.parent, "to": node.id, "relation": "parent"})
        for child in node.children:
            if child in nodes:
                out_edges.append({"from": node.id, "to": child, "relation": "child"})
    return {"nodes": out_nodes, "edges": out_edges, "current_focus_path": current_focus_path}


def build_agent_context(root: Path, nodes: dict[str, ResearchNode]) -> dict[str, Any]:
    current = load_yaml(root / "current_state.yaml")
    path = current.get("current_focus_path", []) or []
    linked_nodes = []
    for node_id in path:
        if node_id in nodes:
            n = nodes[node_id]
            linked_nodes.append({
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "status": n.status,
                "summary": n.summary,
                "next_actions": n.raw.get("next_actions", []),
                "blockers": n.raw.get("blockers", []),
            })

    active_problems = [
        n for n in nodes.values()
        if n.type == "problem" and n.status in {"active", "open", "blocked"}
    ]
    active_options = [
        n for n in nodes.values()
        if n.type == "option" and n.status in {"active", "promising", "open"}
    ]
    recent_decisions = [
        n for n in nodes.values()
        if n.type == "decision" and n.status in {"accepted", "proposed"}
    ]

    return {
        "project_name": "Audio Edit Research Cockpit",
        "current_stage": current.get("current_stage"),
        "current_problem": current.get("current_problem"),
        "current_option": current.get("current_option"),
        "current_hypothesis": current.get("current_hypothesis"),
        "current_focus_path": path,
        "open_risks": current.get("open_risks", []),
        "next_actions": current.get("next_actions", []),
        "linked_nodes": linked_nodes,
        "active_problems": [
            {"id": n.id, "title": n.title, "priority": n.priority, "summary": n.summary}
            for n in active_problems
        ],
        "active_options": [
            {"id": n.id, "title": n.title, "status": n.status, "summary": n.summary}
            for n in active_options
        ],
        "recent_decisions": [
            {"id": n.id, "title": n.title, "status": n.status, "summary": n.summary}
            for n in recent_decisions
        ],
    }


def write_dashboard_markdown(root: Path, context: dict[str, Any]) -> None:
    lines = []
    lines.append("# Research Dashboard\n")
    lines.append("## Current Focus\n")
    lines.append(f"- **Stage:** `{context.get('current_stage')}`")
    lines.append(f"- **Problem:** `{context.get('current_problem')}`")
    lines.append(f"- **Option:** `{context.get('current_option')}`")
    lines.append("")
    lines.append("## Current Hypothesis\n")
    lines.append(str(context.get("current_hypothesis") or ""))
    lines.append("")
    lines.append("## Open Risks\n")
    for item in context.get("open_risks", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Next Actions\n")
    for item in context.get("next_actions", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Active Problems\n")
    for p in context.get("active_problems", []):
        lines.append(f"- **{p['title']}** (`{p['id']}`): {p.get('summary','')}")
    lines.append("")
    lines.append("## Recent Decisions\n")
    for d in context.get("recent_decisions", []):
        lines.append(f"- **{d['title']}** (`{d['status']}`): {d.get('summary','')}")
    (root / "dashboards" / "current_state.md").write_text("\n".join(lines), encoding="utf-8")
