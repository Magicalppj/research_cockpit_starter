from __future__ import annotations

import argparse
from pathlib import Path

from research_cockpit.paths import default_data_root
import json

ROOT = default_data_root()

from research_cockpit.context_packs import (
    build_agent_context,
    build_current_state_payload,
    build_focus_context,
    write_dashboard_markdown,
)
from research_cockpit.model import (
    build_experiment_matrix,
    build_search_index,
    graph_to_json,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
)
from research_cockpit.resources import build_link_rows
from research_cockpit.decisions import build_decision_acceptance_checklists
from research_cockpit.mutation_lock import mutation_lock
from research_cockpit.option_workstreams import build_option_workstream_rows
from research_cockpit.suggestions import build_action_suggestions
from research_cockpit.storage import save_text


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
    search_index = build_search_index(root, nodes, current)
    decision_checklists = build_decision_acceptance_checklists(nodes)
    option_workstreams = build_option_workstream_rows(nodes)

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
        dash / "search_index.json",
        dash / "decision_acceptance_checklists.json",
        dash / "option_workstreams.json",
    ]
    save_text(outputs[0], json.dumps(graph_json, indent=2, ensure_ascii=False))
    save_text(outputs[1], json.dumps(context, indent=2, ensure_ascii=False))
    save_text(outputs[2], json.dumps(focus_context, indent=2, ensure_ascii=False))
    write_dashboard_markdown(root, context)
    save_text(outputs[4], json.dumps(current_payload, indent=2, ensure_ascii=False))
    save_text(outputs[5], json.dumps(experiment_matrix, indent=2, ensure_ascii=False))
    save_text(outputs[6], json.dumps(linked_resources, indent=2, ensure_ascii=False))
    save_text(outputs[7], json.dumps(action_suggestions, indent=2, ensure_ascii=False))
    save_text(outputs[8], json.dumps(search_index, indent=2, ensure_ascii=False))
    save_text(outputs[9], json.dumps(decision_checklists, indent=2, ensure_ascii=False))
    save_text(outputs[10], json.dumps(option_workstreams, indent=2, ensure_ascii=False))
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit build")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with mutation_lock(args.root):
        nodes = load_nodes(args.root)
        outputs = build_dashboard(args.root)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "root": str(args.root),
                    "node_count": len(nodes),
                    "written_files": [str(output) for output in outputs],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    print(f"Built dashboard for {len(nodes)} nodes.")
    for output in outputs:
        print(f"Wrote: {output}")


if __name__ == "__main__":
    main()
