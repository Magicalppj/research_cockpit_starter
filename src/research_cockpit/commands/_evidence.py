from __future__ import annotations

from pathlib import Path
from typing import Any

from research_cockpit.resources import build_link_rows
from research_cockpit.types import ResearchNode


def append_unique(existing: Any, additions: list[str], field_name: str) -> tuple[list[str], list[str]]:
    if existing is None:
        existing = []
    if not isinstance(existing, list):
        raise ValueError(f"{field_name} must be a list")
    out = [str(item) for item in existing if str(item).strip()]
    seen = set(out)
    added: list[str] = []
    for item in additions:
        text = str(item).strip()
        if not text or text in seen:
            continue
        out.append(text)
        added.append(text)
        seen.add(text)
    return out, added


def parse_link_values(values: list[str] | None) -> dict[str, str]:
    links: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"--link must use key=value form: {value}")
        key, target = value.split("=", 1)
        key = key.strip()
        target = target.strip()
        if not key or not target:
            raise ValueError(f"--link must include non-empty key and value: {value}")
        links[key] = target
    return links


def validate_node_refs(nodes: dict[str, ResearchNode], node_ids: list[str], field_name: str) -> None:
    for node_id in node_ids:
        if node_id not in nodes:
            raise ValueError(f"{field_name} references missing node {node_id}")


def validate_artifact_ids(nodes: dict[str, ResearchNode], artifact_ids: list[str]) -> None:
    for artifact_id in artifact_ids:
        if artifact_id not in nodes:
            raise ValueError(f"Artifact node id does not exist: {artifact_id}")
        if nodes[artifact_id].type != "artifact":
            raise ValueError(f"Artifact node id {artifact_id} must be artifact, got {nodes[artifact_id].type}")


def linked_resource_rows(root: Path, nodes: dict[str, ResearchNode], node_ids: list[str]) -> list[dict[str, Any]]:
    wanted = set(node_ids)
    return [row for row in build_link_rows(root, nodes) if row.get("node_id") in wanted]
