from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1] / "research_cockpit"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cockpit.model import (
    ValidationError,
    build_search_index,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    search_knowledge,
    validate_cockpit,
)


def _print_human(results: list[dict]) -> None:
    if not results:
        print("No search results.")
        return
    for result in results:
        node = result.get("node_id") or "unlinked"
        path = result.get("path") or ""
        print(
            f"[{result.get('score')}] {result.get('source')}: "
            f"{result.get('title')} ({node}) {path}"
        )
        snippet = result.get("snippet")
        if snippet:
            print(f"  {snippet}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--query", required=True)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--source", action="append", choices=["note", "node"], dest="sources")
    parser.add_argument("--node-type", action="append", dest="node_types")
    parser.add_argument("--focus-only", action="store_true")
    args = parser.parse_args()

    try:
        nodes = load_nodes(args.root)
        current = load_yaml(args.root / "current_state.yaml")
        explicit_edges = load_explicit_edges(args.root)
        validate_cockpit(args.root, nodes, current, explicit_edges, raise_on_error=True)
        index = build_search_index(args.root, nodes, current)
        results = search_knowledge(
            index,
            args.query,
            sources=args.sources,
            node_types=args.node_types,
            limit=args.limit,
            focus_only=args.focus_only,
        )
    except ValidationError as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.as_json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        _print_human(results)


if __name__ == "__main__":
    main()
