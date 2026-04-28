from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPO_ROOT / "research_cockpit"
sys.path.insert(0, str(REPO_ROOT))

from cockpit.model import (
    ValidationError,
    build_action_suggestions,
    build_context_metadata,
    build_link_rows,
    build_search_index,
    build_search_index_summary,
    focus_node_id_from_current,
    load_nodes,
    load_yaml,
    validate_cockpit,
)
from scripts.build_dashboard import build_dashboard


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _context_paths(root: Path) -> dict[str, dict[str, Any]]:
    dash = root / "dashboards"
    files = {
        "agent_context_pack": dash / "agent_context_pack.json",
        "focus_context_pack": dash / "focus_context_pack.json",
        "graph_view": dash / "graph_view.json",
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


def agent_bootstrap_payload(root: Path = ROOT, *, build: bool = False) -> dict[str, Any]:
    if build:
        build_dashboard(root)

    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    errors = validate_cockpit(root, nodes, current)
    link_rows = build_link_rows(root, nodes)
    suggestions = build_action_suggestions(root, nodes, current, link_rows)
    search_index = build_search_index(root, nodes, current)
    focus_node_id = focus_node_id_from_current(current, nodes)
    metadata = build_context_metadata(root, current)

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
        },
        "context_paths": _context_paths(root),
        "top_suggestions": suggestions[:3],
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
    except (OSError, ValidationError, ValueError) as exc:
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
