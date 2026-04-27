from __future__ import annotations

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1] / "research_cockpit"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cockpit.model import load_nodes, load_yaml, graph_to_json, build_agent_context, write_dashboard_markdown


def main() -> None:
    nodes = load_nodes(ROOT)
    current = load_yaml(ROOT / "current_state.yaml")
    graph_json = graph_to_json(nodes, current.get("current_focus_path", []))
    context = build_agent_context(ROOT, nodes)

    dash = ROOT / "dashboards"
    dash.mkdir(parents=True, exist_ok=True)

    (dash / "graph_view.json").write_text(json.dumps(graph_json, indent=2, ensure_ascii=False), encoding="utf-8")
    (dash / "agent_context_pack.json").write_text(json.dumps(context, indent=2, ensure_ascii=False), encoding="utf-8")
    write_dashboard_markdown(ROOT, context)

    print(f"Built dashboard for {len(nodes)} nodes.")
    print(f"Wrote: {dash / 'graph_view.json'}")
    print(f"Wrote: {dash / 'agent_context_pack.json'}")
    print(f"Wrote: {dash / 'current_state.md'}")


if __name__ == "__main__":
    main()
