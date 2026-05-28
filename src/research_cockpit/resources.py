from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from research_cockpit.paths import plugin_root
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


def node_artifact_ids(node: ResearchNode) -> list[str]:
    artifact_ids: list[str] = []
    artifact_ids.extend(str(item) for item in node.raw.get("linked_artifacts", []) or [] if str(item).strip())
    findings = node.raw.get("findings")
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            artifact_ids.extend(
                str(item)
                for item in finding.get("linked_artifacts", []) or []
                if str(item).strip()
            )
    seen: set[str] = set()
    out: list[str] = []
    for artifact_id in artifact_ids:
        if artifact_id in seen:
            continue
        seen.add(artifact_id)
        out.append(artifact_id)
    return out


def _is_external_target(target: str) -> bool:
    parsed = urlparse(target)
    return bool(parsed.scheme and parsed.scheme not in {"", "file"})


def _unique_paths(paths: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
    seen: set[str] = set()
    out: list[tuple[str, Path]] = []
    for label, path in paths:
        key = str(path.resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        out.append((label, path))
    return out


def _display_resolution_path(root: Path, path: Path, label: str) -> str:
    if label == "cwd":
        try:
            return path.relative_to(Path.cwd()).as_posix()
        except ValueError:
            return path.as_posix()

    resolved = path.resolve(strict=False)
    for base in (plugin_root(), root.parent, root):
        try:
            return resolved.relative_to(base.resolve(strict=False)).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def _target_resolution(root: Path, kind: str, target: str, nodes: dict[str, ResearchNode]) -> dict[str, Any]:
    if kind == "run_id" or _is_external_target(target):
        return {
            "exists": None,
            "resolved_target": None,
            "resolution_base": None,
            "resolution_attempts": [],
        }
    if kind == "linked_artifact":
        return {
            "exists": target in nodes,
            "resolved_target": target,
            "resolution_base": "graph",
            "resolution_attempts": [target],
        }
    path = Path(target)
    if path.is_absolute():
        return {
            "exists": path.exists(),
            "resolved_target": path.as_posix(),
            "resolution_base": "absolute",
            "resolution_attempts": [path.as_posix()],
        }
    attempts = _unique_paths(
        [
            ("root_parent", root.parent / target),
            ("root", root / target),
            ("cwd", Path.cwd() / target),
        ]
    )
    for label, candidate in attempts:
        if candidate.exists():
            return {
                "exists": True,
                "resolved_target": _display_resolution_path(root, candidate, label),
                "resolution_base": label,
                "resolution_attempts": [
                    _display_resolution_path(root, item, item_label)
                    for item_label, item in attempts
                ],
            }
    return {
        "exists": False,
        "resolved_target": (
            _display_resolution_path(root, attempts[0][1], attempts[0][0])
            if attempts
            else target
        ),
        "resolution_base": None,
        "resolution_attempts": [
            _display_resolution_path(root, item, item_label)
            for item_label, item in attempts
        ],
    }


def build_link_rows(root: Path, nodes: dict[str, ResearchNode]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    resolution_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def resolve_target(kind: str, target: str) -> dict[str, Any]:
        key = (kind, target)
        if key not in resolution_cache:
            resolution_cache[key] = _target_resolution(root, kind, target, nodes)
        return resolution_cache[key]

    def row_resolution(resolution: dict[str, Any]) -> dict[str, Any]:
        copied = dict(resolution)
        attempts = copied.get("resolution_attempts")
        if isinstance(attempts, list):
            copied["resolution_attempts"] = list(attempts)
        return copied

    for node in sorted(nodes.values(), key=lambda item: item.id):
        entries = node_link_entries(node)
        for artifact_id in node_artifact_ids(node):
            entries.append({"kind": "linked_artifact", "label": "linked_artifact", "target": str(artifact_id)})

        for entry in entries:
            target = entry["target"]
            kind = entry["kind"]
            resolution = resolve_target(kind, target)
            rows.append({
                "node_id": node.id,
                "node_title": node.title,
                "node_type": node.type,
                "kind": kind,
                "label": entry["label"],
                "target": target,
                **row_resolution(resolution),
            })
            if kind == "linked_artifact" and target in nodes and nodes[target].type == "artifact":
                for artifact_entry in node_link_entries(nodes[target]):
                    artifact_resolution = resolve_target(artifact_entry["kind"], artifact_entry["target"])
                    rows.append({
                        "node_id": node.id,
                        "node_title": node.title,
                        "node_type": node.type,
                        "artifact_id": target,
                        "kind": artifact_entry["kind"],
                        "label": f"{target}:{artifact_entry['label']}",
                        "target": artifact_entry["target"],
                        **row_resolution(artifact_resolution),
                    })
    return rows
