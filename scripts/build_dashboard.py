from __future__ import annotations

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1] / "research_cockpit"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cockpit.model import (
    build_action_suggestions,
    build_agent_context,
    build_current_state_payload,
    build_experiment_matrix,
    build_focus_context,
    build_link_rows,
    graph_to_json,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
    write_dashboard_markdown,
)


def build_dashboard(root: Path = ROOT) -> list[Path]:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
    graph_json = graph_to_json(nodes, current.get("current_focus_path", []), current, explicit_edges)
    context = build_agent_context(root, nodes)
    focus_context = build_focus_context(root, nodes, current)
    current_payload = build_current_state_payload(root, nodes, current)
    experiment_matrix = build_experiment_matrix(nodes)
    linked_resources = build_link_rows(root, nodes)
    action_suggestions = build_action_suggestions(root, nodes, current, linked_resources)

    dash = root / "dashboards"
    dash.mkdir(parents=True, exist_ok=True)

    outputs = [
        dash / "graph_view.json",
        dash / "agent_context_pack.json",
        dash / "focus_context_pack.json",
        dash / "current_state.md",
        dash / "current_state.json",
        dash / "experiment_matrix.json",
        dash / "linked_resources.json",
        dash / "next_action_suggestions.json",
    ]
    outputs[0].write_text(json.dumps(graph_json, indent=2, ensure_ascii=False), encoding="utf-8")
    outputs[1].write_text(json.dumps(context, indent=2, ensure_ascii=False), encoding="utf-8")
    outputs[2].write_text(json.dumps(focus_context, indent=2, ensure_ascii=False), encoding="utf-8")
    write_dashboard_markdown(root, context)
    outputs[4].write_text(json.dumps(current_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    outputs[5].write_text(json.dumps(experiment_matrix, indent=2, ensure_ascii=False), encoding="utf-8")
    outputs[6].write_text(json.dumps(linked_resources, indent=2, ensure_ascii=False), encoding="utf-8")
    outputs[7].write_text(json.dumps(action_suggestions, indent=2, ensure_ascii=False), encoding="utf-8")
    return outputs


def main() -> None:
    nodes = load_nodes(ROOT)
    outputs = build_dashboard(ROOT)
    print(f"Built dashboard for {len(nodes)} nodes.")
    for output in outputs:
        print(f"Wrote: {output}")


if __name__ == "__main__":
    main()
