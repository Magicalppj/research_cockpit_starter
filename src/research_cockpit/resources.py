from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from research_cockpit.types import ResearchNode


def node_link_entries(node: ResearchNode) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    links = node.raw.get("links")
    if isinstance(links, dict):
        for label, target in links.items():
            if target in (None, ""):
                continue
            entries.append({"kind": "link", "label": str(label), "target": str(target)})

    for field_name in ("config_path", "path", "run_id"):
        value = node.raw.get(field_name)
        if value not in (None, ""):
            entries.append({"kind": field_name, "label": field_name, "target": str(value)})
    return entries


def _is_external_target(target: str) -> bool:
    parsed = urlparse(target)
    return bool(parsed.scheme and parsed.scheme not in {"", "file"})


def _target_exists(root: Path, kind: str, target: str, nodes: dict[str, ResearchNode]) -> bool | None:
    if kind == "run_id" or _is_external_target(target):
        return None
    if kind == "linked_artifact":
        return target in nodes
    path = Path(target)
    if path.is_absolute():
        return None
    return (root / target).exists()


def build_link_rows(root: Path, nodes: dict[str, ResearchNode]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in sorted(nodes.values(), key=lambda item: item.id):
        entries = node_link_entries(node)
        for artifact_id in node.raw.get("linked_artifacts", []) or []:
            entries.append({"kind": "linked_artifact", "label": "linked_artifact", "target": str(artifact_id)})

        for entry in entries:
            target = entry["target"]
            kind = entry["kind"]
            rows.append({
                "node_id": node.id,
                "node_title": node.title,
                "node_type": node.type,
                "kind": kind,
                "label": entry["label"],
                "target": target,
                "exists": _target_exists(root, kind, target, nodes),
            })
    return rows
