from __future__ import annotations

import argparse
import copy
import difflib
import json
from datetime import date
from pathlib import Path

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.model import ResearchNode, load_nodes, load_yaml, validate_cockpit, script_command
from research_cockpit.commands._runtime import finish_mutation, yaml_change_diff


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
    result = create_note_result(
        root,
        node_id=node_id,
        overwrite=overwrite,
        rebuild_dashboard=rebuild_dashboard,
        dry_run=False,
        show_diff=False,
    )
    return Path(str(result["note_path"]))


def _text_diff(path: Path, before: str | None, after: str) -> str:
    before_text = "" if before is None else before
    return "".join(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{path}:before",
            tofile=f"{path}:after",
        )
    )


def create_note_result(
    root: Path,
    *,
    node_id: str,
    overwrite: bool = False,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, object]:
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
    before_data = copy.deepcopy(data)
    links = data.get("links") or {}
    if not isinstance(links, dict):
        raise ValueError(f"{node_id}: links must be a mapping")
    links["notes"] = relative_path.as_posix()
    data["links"] = links
    data["updated_at"] = str(date.today())

    candidate = dict(nodes)
    candidate[node_id] = ResearchNode.from_dict(data)
    validate_cockpit(root, candidate, load_yaml(root / "current_state.yaml"), raise_on_error=True)

    note_text = _note_text(candidate[node_id])
    note_before = note_path.read_text(encoding="utf-8") if note_path.exists() else None
    changed = before_data != data or note_before != note_text
    result: dict[str, object] = {
        "node_id": node_id,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "note_path": str(note_path),
        "node_path": str(node_path),
        "before": {
            "links": before_data.get("links"),
            "note_exists": note_before is not None,
        },
        "after": {
            "links": data.get("links"),
            "note_exists": True,
        },
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(node_path, before_data, data)]) + _text_diff(note_path, note_before, note_text)
    if dry_run or not changed:
        return result

    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(note_text, encoding="utf-8")
    finish_mutation(
        root,
        [(node_path, data)],
        interaction={
            "kind": "create_note",
            "actor": "researcher",
            "node_id": node_id,
            "command": f"{script_command('create_note.py')} --node {node_id}",
            "before": result["before"],
            "after": result["after"],
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--node", required=True, dest="node_id")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        result = create_note_result(
            args.root,
            node_id=args.node_id,
            overwrite=args.overwrite,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    verb = "Would write" if args.dry_run else "Wrote"
    print(f"{verb} {result['note_path']}")
    if args.show_diff and result.get("diff"):
        print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build and result["changed"]:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
