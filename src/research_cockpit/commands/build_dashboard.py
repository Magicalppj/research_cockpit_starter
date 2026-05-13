from __future__ import annotations

import argparse
from pathlib import Path
import time

from research_cockpit.paths import default_data_root
import json

ROOT = default_data_root()

from research_cockpit.context_packs import (
    build_agent_context,
    build_current_state_payload,
    build_focus_context,
    write_dashboard_markdown,
)
from research_cockpit.assignment_view import build_assignment_view
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


def _truth_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    current = root / "current_state.yaml"
    if current.exists():
        files.append(current)
    graph = root / "graph"
    if graph.exists():
        files.extend(path for path in graph.rglob("*.yaml") if path.is_file())
    notes = root / "notes"
    if notes.exists():
        files.extend(path for path in notes.rglob("*.md") if path.is_file())
    return sorted(files)


def truth_source_signature(root: Path) -> tuple[tuple[str, int, int], ...]:
    items: list[tuple[str, int, int]] = []
    for path in _truth_source_files(root):
        stat = path.stat()
        try:
            name = path.relative_to(root).as_posix()
        except ValueError:
            name = str(path)
        items.append((name, stat.st_mtime_ns, stat.st_size))
    return tuple(items)


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
    option_workstreams = build_option_workstream_rows(nodes, current)
    assignment_view = build_assignment_view(nodes)

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
        dash / "assignment_view.json",
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
    save_text(outputs[11], json.dumps(assignment_view, indent=2, ensure_ascii=False))
    return outputs


def build_dashboard_once(root: Path, *, json_output: bool = False) -> dict:
    with mutation_lock(root):
        nodes = load_nodes(root)
        outputs = build_dashboard(root)
    return {
        "ok": True,
        "root": str(root),
        "node_count": len(nodes),
        "written_files": [str(output) for output in outputs],
        "json": json_output,
    }


def watch_dashboard(root: Path, *, interval: float, max_iterations: int | None, json_output: bool) -> None:
    last_signature: tuple[tuple[str, int, int], ...] | None = None
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        signature = truth_source_signature(root)
        if last_signature is None or signature != last_signature:
            payload = build_dashboard_once(root, json_output=json_output)
            payload.update({"watch": True, "iteration": iteration, "truth_source_changed": True})
            last_signature = truth_source_signature(root)
        else:
            payload = {
                "ok": True,
                "root": str(root),
                "watch": True,
                "iteration": iteration,
                "truth_source_changed": False,
                "written_files": [],
            }
        if json_output:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            if payload["truth_source_changed"]:
                print(f"[{iteration}] Built dashboard.")
            else:
                print(f"[{iteration}] No truth-source changes.")
        if max_iterations is not None and iteration >= max_iterations:
            break
        time.sleep(max(0.0, interval))


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit build")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--max-iterations", type=int)
    args = parser.parse_args()

    if args.watch:
        watch_dashboard(
            args.root,
            interval=args.interval,
            max_iterations=args.max_iterations,
            json_output=args.json,
        )
        return

    payload = build_dashboard_once(args.root, json_output=args.json)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print(f"Built dashboard for {payload['node_count']} nodes.")
    for output in payload["written_files"]:
        print(f"Wrote: {output}")


if __name__ == "__main__":
    main()
