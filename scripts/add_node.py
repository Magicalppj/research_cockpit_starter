from __future__ import annotations

import argparse
from pathlib import Path
import sys
from datetime import date

ROOT = Path(__file__).resolve().parents[1] / "research_cockpit"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cockpit.model import (
    VALID_NODE_TYPES,
    ResearchNode,
    default_status_for_type,
    load_nodes,
    load_yaml,
    save_yaml,
    validate_cockpit,
    validate_status,
)


def add_node(
    root: Path,
    *,
    node_id: str,
    node_type: str,
    title: str,
    parent: str | None = None,
    status: str | None = None,
    summary: str = "",
) -> Path:
    nodes = load_nodes(root)
    if node_id in nodes:
        raise FileExistsError(root / "graph" / "nodes" / f"{node_id}.yaml")
    if parent and parent not in nodes:
        raise ValueError(f"Parent node does not exist: {parent}")

    node_status = status or default_status_for_type(node_type)
    validate_status(node_type, node_status)

    today = str(date.today())
    data = {
        "id": node_id,
        "type": node_type,
        "title": title,
        "status": node_status,
        "summary": summary,
        "created_at": today,
        "updated_at": today,
    }
    if parent:
        data["parent"] = parent

    candidate = dict(nodes)
    candidate[node_id] = ResearchNode.from_dict(data)
    validate_cockpit(root, candidate, load_yaml(root / "current_state.yaml"), raise_on_error=True)

    out = root / "graph" / "nodes" / f"{node_id}.yaml"
    save_yaml(out, data)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True)
    parser.add_argument("--type", required=True, choices=sorted(VALID_NODE_TYPES))
    parser.add_argument("--title", required=True)
    parser.add_argument("--parent", default=None)
    parser.add_argument("--status", default=None)
    parser.add_argument("--summary", default="")
    args = parser.parse_args()

    out = add_node(
        args.root,
        node_id=args.id,
        node_type=args.type,
        title=args.title,
        parent=args.parent,
        status=args.status,
        summary=args.summary,
    )
    print(f"Created {out}")


if __name__ == "__main__":
    main()
