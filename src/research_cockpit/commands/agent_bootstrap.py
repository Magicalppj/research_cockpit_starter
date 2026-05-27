from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root, display_path, plugin_root

PLUGIN_ROOT = plugin_root()
ROOT = default_data_root()

REQUIRED_MODULES = {
    "networkx": "networkx",
    "yaml": "PyYAML",
}
BATCH_FINISH_COMMANDS = [
    "research-cockpit validate --root <root> --json",
    "research-cockpit build --root <root>",
    "research-cockpit smoke --root <root> --json",
]


def missing_runtime_dependencies(required: dict[str, str] = REQUIRED_MODULES) -> list[str]:
    return [module for module in required if importlib.util.find_spec(module) is None]


def format_dependency_error(missing: list[str]) -> str:
    packages = ", ".join(REQUIRED_MODULES.get(module, module) for module in missing)
    modules = ", ".join(missing)
    return (
        f"Missing Python modules: {modules}. "
        f"From the Research Cockpit plugin root, install the package with `python -m pip install -e .` "
        f"or rerun with an interpreter that already has: {packages}."
    )


_MISSING_DEPENDENCIES = missing_runtime_dependencies()

if not _MISSING_DEPENDENCIES:
    from research_cockpit.baselines import resolve_current_effective_baseline
    from research_cockpit.context_packs import build_context_metadata
    from research_cockpit.gate_result_records import build_gate_overview
    from research_cockpit.hierarchy_policy import hierarchy_policy
    from research_cockpit.model import (
        build_search_index,
        build_search_index_summary,
        focus_node_id_from_current,
        load_nodes,
        load_yaml,
        validate_cockpit,
    )
    from research_cockpit.resources import build_link_rows
    from research_cockpit.run_summaries import build_run_overview
    from research_cockpit.suggestions import build_action_suggestions
    from research_cockpit.commands.build_dashboard import build_dashboard
    from research_cockpit.commands.lint_semantic import semantic_lint


def _display_path(path: Path) -> str:
    return display_path(path, base=Path.cwd())


def _context_paths(root: Path) -> dict[str, dict[str, Any]]:
    dash = root / "dashboards"
    files = {
        "agent_context_pack": dash / "agent_context_pack.json",
        "focus_context_pack": dash / "focus_context_pack.json",
        "graph_view": dash / "graph_view.json",
        "assignment_view": dash / "assignment_view.json",
        "search_index": dash / "search_index.json",
        "next_action_suggestions": dash / "next_action_suggestions.json",
    }
    return {
        name: {
            "path": _display_path(path),
            "exists": path.exists(),
        }
        for name, path in files.items()
    }


def _mutation_guidance(nodes: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    focus_node_id = focus_node_id_from_current(current, nodes)
    focus_node = nodes.get(focus_node_id) if focus_node_id else None
    problem_id = current.get("current_problem")
    problem = nodes.get(str(problem_id)) if problem_id else None
    current_best_option = None
    if problem:
        current_best_option = problem.raw.get("current_best_option") or current.get("current_option")
    elif focus_node:
        current_best_option = focus_node.raw.get("current_best_option") or current.get("current_option")

    pause_candidates: list[str] = []
    if problem:
        for node in nodes.values():
            if node.type == "option" and node.parent == problem.id and node.id != current_best_option:
                if node.status in {"active", "promising", "open"}:
                    pause_candidates.append(node.id)

    return {
        "current_focus_node": focus_node_id,
        "current_best_option": current_best_option,
        "pause_candidate_options": sorted(pause_candidates),
        "batching": "Use --dry-run --json --show-diff first, run mutating commands sequentially with --no-build, then let the coordinator or final handoff run validate, build, and smoke once. A build watcher only refreshes generated dashboards.",
        "multi_agent_batch_mode": {
            "default": "Agents mutate only the canonical root, use --no-build on supported writes, and leave final validate/build/smoke to a coordinator or final handoff. An optional build watcher only refreshes generated dashboards and does not replace validate/smoke.",
            "rules": [
                "Do not parallelize mutating commands against the same data root.",
                "Use commands --json --compact to check supports_no_build and batch_policy before choosing a command.",
                "Run lightweight read checks such as validate --json after local batches when useful.",
                "On mutation conflict, reread compact context and retry the stale command.",
            ],
            "finish_commands": BATCH_FINISH_COMMANDS,
            "examples": {
                "findings": [
                    "research-cockpit record-finding --root <root> --experiment <experiment_id> --statement \"...\" --confidence medium --artifact-id <artifact_id> --no-build",
                    "research-cockpit complete-experiment --root <root> --id <experiment_id> --finding \"...\" --confidence medium --artifact-id <artifact_id> --no-build",
                ],
                "artifacts": [
                    "research-cockpit ingest-artifact --root <root> --node <experiment_id> --from <worktree_output_dir> --run-id <run_id> --agent <agent_id> --no-build",
                    "research-cockpit link-artifact --root <root> --artifact <artifact_id> --to <node_id> --no-build",
                ],
                "runs": [
                    "research-cockpit create-run --root <root> --id <run_id> --experiment <experiment_id> --status running --no-build",
                    "research-cockpit update-run --root <root> --id <run_id> --status running --progress-file artifacts/<experiment_id>/<run_id>/progress.json --no-build",
                    "research-cockpit complete-run --root <root> --id <run_id> --status completed --no-build",
                ],
                "next_actions": [
                    "research-cockpit update-node-fields --root <root> --id <node_id> --clear-next-actions --next-action \"...\" --no-build",
                    "research-cockpit sync-focus-actions --root <root> --from-node <node_id> --no-build",
                    "research-cockpit update-suggestion-state --root <root> --id <suggestion_id> --state completed --reason \"...\" --no-build",
                ],
            },
        },
        "hierarchy_policy": hierarchy_policy(parent_option_id=current.get("current_option") or current_best_option),
        "command_skeletons": [
            "research-cockpit init --root <root> --build --json",
            "research-cockpit context --root <root> --id <node_id> --with-bootstrap --with-artifacts --compact --json",
            "research-cockpit assignment-view --root <root> --json",
            "research-cockpit add-node --root <root> --id <node_id> --type <type> --title \"...\" --parent <parent_id> --no-build",
            "research-cockpit update-node-fields --root <root> --id <node_id> --question \"...\" --tag <tag> --no-build",
            "research-cockpit apply-graph-plan --root <root> --file graph_update.yaml --dry-run --json --show-diff",
            "research-cockpit create-workstream --root <root> --file workstream.yaml --dry-run --json --show-diff",
            "research-cockpit create-artifact --root <root> --id <artifact_id> --title \"...\" --path artifacts/<node_id>/<run_id> --link-to <node_id> --no-build",
            "research-cockpit ingest-artifact --root <root> --node <experiment_id> --from <worktree_output_dir> --run-id <run_id> --agent <agent_id> --dry-run --json --show-diff",
            "research-cockpit record-gate-result --root <root> --id <gate_id> --experiment <experiment_id> --run <run_id> --type smoke_check --passed false --fatal-json \"{}\" --no-build",
            "research-cockpit ingest-gate-result --root <root> --id <gate_id> --file artifacts/<experiment_id>/<run_id>/gate_result.json --run <run_id> --artifact <artifact_id> --no-build",
            "research-cockpit set-baseline --root <root> --node <node_id> --option <option_id> --decision <decision_id> --no-build",
            "research-cockpit complete-experiment --root <root> --id <experiment_id> --finding \"...\" --confidence medium --artifact-id <artifact_id> --no-build",
            "research-cockpit complete-experiments --root <root> --file findings.yaml --no-build",
            "research-cockpit create-followup-experiment --root <root> --from <done_or_running_experiment_id> --id <followup_id> --title \"...\" --priority high --next-action \"...\" --no-build",
            "research-cockpit finalize-workstream --root <root> --file finalize.yaml --dry-run --json --compact",
            "research-cockpit finalize-workstream --root <root> --option <option_id> --status accepted --problem-status resolved --report --no-build",
        ],
    }


def agent_bootstrap_payload(root: Path = ROOT, *, build: bool = False) -> dict[str, Any]:
    if _MISSING_DEPENDENCIES:
        raise RuntimeError(format_dependency_error(_MISSING_DEPENDENCIES))

    if build:
        build_dashboard(root)

    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    errors = validate_cockpit(root, nodes, current)
    link_rows = build_link_rows(root, nodes)
    suggestions = build_action_suggestions(root, nodes, current, link_rows)
    search_index = build_search_index(root, nodes, current)
    focus_node_id = focus_node_id_from_current(current, nodes)
    effective_baseline = resolve_current_effective_baseline(nodes, current)
    metadata = build_context_metadata(root, current)
    semantic = semantic_lint(root)

    return {
        "root": _display_path(root),
        "validation": {
            "ok": not errors,
            "errors": errors,
            "node_count": len(nodes),
        },
        "focus": {
            "current_stage": current.get("current_stage"),
            "current_problem": current.get("current_problem"),
            "current_option": current.get("current_option"),
            "current_focus_node": focus_node_id,
            "current_focus_path": current.get("current_focus_path", []) or [],
            "effective_baseline": effective_baseline,
        },
        "context_paths": _context_paths(root),
        "skill": {
            "path": _display_path(PLUGIN_ROOT),
            "exists": PLUGIN_ROOT.exists(),
        },
        "plugin": {
            "path": _display_path(PLUGIN_ROOT),
            "exists": PLUGIN_ROOT.exists(),
        },
        "top_suggestions": suggestions[:3],
        "semantic_warnings": semantic["warnings"],
        "mutation_guidance": _mutation_guidance(nodes, current),
        "run_overview": build_run_overview(root, nodes),
        "gate_overview": build_gate_overview(root),
        "search_summary": build_search_index_summary(search_index),
        "git": {
            "source_git_commit": metadata["source_git_commit"],
            "worktree_dirty": metadata["worktree_dirty"],
        },
        "metadata": metadata,
    }


def _print_text(payload: dict[str, Any]) -> None:
    validation = payload["validation"]
    state = "OK" if validation["ok"] else "FAILED"
    print(f"Validation: {state} ({validation['node_count']} nodes)")
    for error in validation.get("errors", []):
        print(f"- {error}")
    focus = payload["focus"]
    print(f"Focus: {focus.get('current_focus_node')}")
    print("Context paths:")
    for name, item in payload["context_paths"].items():
        exists = "exists" if item["exists"] else "missing"
        print(f"- {name}: {item['path']} ({exists})")
    if payload["top_suggestions"]:
        print("Top suggestions:")
        for suggestion in payload["top_suggestions"]:
            print(f"- {suggestion['id']} {suggestion['kind']}: {suggestion['action']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true", help="Print machine-readable bootstrap payload")
    parser.add_argument("--build", action="store_true", help="Refresh dashboard/context files before reporting")
    args = parser.parse_args()

    try:
        payload = agent_bootstrap_payload(args.root, build=args.build)
    except (OSError, RuntimeError, ValueError) as exc:
        error_payload = {
            "validation": {"ok": False, "errors": [str(exc)]},
            "error": str(exc),
        }
        if args.json:
            print(json.dumps(error_payload, indent=2, ensure_ascii=False))
        else:
            print(f"FAILED: {exc}")
        raise SystemExit(1)

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_text(payload)


if __name__ == "__main__":
    main()
