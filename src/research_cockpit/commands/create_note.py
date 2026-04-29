from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.model import ResearchNode, load_nodes, load_yaml, save_yaml, validate_cockpit
from research_cockpit.commands.build_dashboard import build_dashboard


NOTE_DIR_BY_TYPE = {
    "problem": "problems",
    "option": "options",
    "experiment": "experiments",
    "decision": "decisions",
}


def find_node_file(root: Path, node_id: str) -> Path:
    for path in sorted((root / "graph" / "nodes").glob("*.yaml")):
        data = load_yaml(path)
        if str(data.get("id")) == node_id:
            return path
    raise FileNotFoundError(f"Node does not exist: {node_id}")


def _note_text(node: ResearchNode) -> str:
    today = str(date.today())
    title = node.title or node.id
    type_name = node.type.capitalize()
    sections = {
        "problem": ["Question", "Context", "Evidence", "Options", "Next Actions"],
        "option": ["Hypothesis", "Implementation", "Evidence", "Risks", "Decision Notes"],
        "experiment": ["Setup", "Observations", "Metrics", "Findings", "Follow-up"],
        "decision": ["Context", "Decision", "Evidence", "Alternatives", "Consequences"],
    }[node.type]
    lines = [
        f"# {title}",
        "",
        f"- Node: `{node.id}`",
        f"- Template: {type_name} Note",
        f"- Type: `{node.type}`",
        f"- Status: `{node.status}`",
        f"- Created: {today}",
        "",
        "## Summary",
        "",
        node.summary or "",
        "",
    ]
    for section in sections:
        lines.extend([f"## {section}", "", ""])
    return "\n".join(lines).rstrip() + "\n"


def create_note(
    root: Path,
    *,
    node_id: str,
    overwrite: bool = False,
    rebuild_dashboard: bool = True,
) -> Path:
    nodes = load_nodes(root)
    if node_id not in nodes:
        raise FileNotFoundError(f"Node does not exist: {node_id}")
    node = nodes[node_id]
    if node.type not in NOTE_DIR_BY_TYPE:
        allowed = ", ".join(sorted(NOTE_DIR_BY_TYPE))
        raise ValueError(f"Node type {node.type!r} does not support note templates; allowed: {allowed}")

    relative_path = Path("notes") / NOTE_DIR_BY_TYPE[node.type] / f"{node.id}.md"
    note_path = root / relative_path
    if note_path.exists() and not overwrite:
        raise FileExistsError(note_path)

    node_path = find_node_file(root, node_id)
    data = load_yaml(node_path)
    links = data.get("links") or {}
    if not isinstance(links, dict):
        raise ValueError(f"{node_id}: links must be a mapping")
    links["notes"] = relative_path.as_posix()
    data["links"] = links
    data["updated_at"] = str(date.today())

    candidate = dict(nodes)
    candidate[node_id] = ResearchNode.from_dict(data)
    validate_cockpit(root, candidate, load_yaml(root / "current_state.yaml"), raise_on_error=True)

    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(_note_text(candidate[node_id]), encoding="utf-8")
    save_yaml(node_path, data)
    if rebuild_dashboard:
        build_dashboard(root)
    return note_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--node", required=True, dest="node_id")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    out = create_note(
        args.root,
        node_id=args.node_id,
        overwrite=args.overwrite,
        rebuild_dashboard=not args.no_build,
    )
    print(f"Wrote {out}")
    if not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
