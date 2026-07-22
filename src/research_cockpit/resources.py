from __future__ import annotations

from pathlib import Path
import os
from typing import Any
from urllib.parse import unquote, urlparse

from research_cockpit.artifact_records import list_artifact_records
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


def _unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


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
    return _unique_preserving_order(artifact_ids)


def node_artifact_record_ids(node: ResearchNode) -> list[str]:
    record_ids: list[str] = []
    record_ids.extend(str(item) for item in node.raw.get("linked_artifact_records", []) or [] if str(item).strip())
    findings = node.raw.get("findings")
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            record_ids.extend(
                str(item)
                for item in finding.get("linked_artifact_records", []) or []
                if str(item).strip()
            )
    return _unique_preserving_order(record_ids)


def _is_external_target(target: str) -> bool:
    parsed = urlparse(target)
    return bool(parsed.scheme and parsed.scheme not in {"", "file"})

def _file_uri_path(target: str) -> Path | None:
    parsed = urlparse(target)
    if parsed.scheme.casefold() != "file" or parsed.query or parsed.fragment:
        return None
    path_text = unquote(parsed.path)
    netloc = unquote(parsed.netloc)
    if netloc and netloc.casefold() != "localhost":
        path_text = f"//{netloc}{path_text}"
    elif os.name == "nt" and (
        len(path_text) >= 3
        and path_text[0] == "/"
        and path_text[1].isalpha()
        and path_text[2] == ":"
    ):
        path_text = path_text[1:]
    return Path(path_text)



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


def _target_resolution(
    root: Path,
    kind: str,
    target: str,
    nodes: dict[str, ResearchNode],
    *,
    artifact_record_ids: set[str] | None = None,
) -> dict[str, Any]:
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
    if kind == "linked_artifact_record":
        record_ids = artifact_record_ids
        if record_ids is None:
            record_ids = {str(record["record_id"]) for record in list_artifact_records(root)}
        return {
            "exists": target in record_ids,
            "resolved_target": target,
            "resolution_base": "artifact_records",
            "resolution_attempts": [target],
        }
    file_uri = _file_uri_path(target)
    if file_uri is not None:
        resolved = file_uri.resolve(strict=False)
        return {
            "exists": resolved.exists(),
            "resolved_target": resolved.as_posix(),
            "resolution_base": "file_uri",
            "resolution_attempts": [resolved.as_posix()],
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


def build_link_rows(
    root: Path,
    nodes: dict[str, ResearchNode],
    *,
    artifact_records: dict[str, dict[str, Any]] | list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    resolution_cache: dict[tuple[str, str], dict[str, Any]] = {}
    artifact_records_cache: dict[str, dict[str, Any]] | None = None
    artifact_record_id_cache: set[str] | None = None

    def normalize_artifact_records() -> dict[str, dict[str, Any]] | None:
        if artifact_records is None:
            return None
        if isinstance(artifact_records, dict):
            return {str(record_id): record for record_id, record in artifact_records.items()}
        return {
            str(record["record_id"]): record
            for record in artifact_records
            if str(record.get("record_id") or "").strip()
        }

    def scoped_artifact_records_by_id() -> dict[str, dict[str, Any]] | None:
        wanted_by_experiment: dict[str, set[str]] = {}
        linked_record_ids: set[str] = set()
        for node in nodes.values():
            record_ids = node_artifact_record_ids(node)
            if not record_ids:
                continue
            linked_record_ids.update(record_ids)
            if node.type == "experiment":
                wanted_by_experiment.setdefault(node.id, set()).update(record_ids)
        if not linked_record_ids:
            return {}
        records: dict[str, dict[str, Any]] = {}
        for experiment_id, wanted_ids in wanted_by_experiment.items():
            for record in list_artifact_records(root, experiment_id=experiment_id):
                record_id = str(record.get("record_id") or "")
                if record_id in wanted_ids and record_id not in records:
                    records[record_id] = record
        missing = linked_record_ids - set(records)
        if missing:
            for record in list_artifact_records(root):
                record_id = str(record.get("record_id") or "")
                if record_id in missing and record_id not in records:
                    records[record_id] = record
        return records

    def artifact_records_by_id() -> dict[str, dict[str, Any]]:
        nonlocal artifact_records_cache
        if artifact_records_cache is None:
            provided = normalize_artifact_records()
            scoped = scoped_artifact_records_by_id() if provided is None else None
            artifact_records_cache = provided if provided is not None else scoped if scoped is not None else {
                str(record["record_id"]): record
                for record in list_artifact_records(root)
                if str(record.get("record_id") or "").strip()
            }
        return artifact_records_cache

    def artifact_record_ids() -> set[str]:
        nonlocal artifact_record_id_cache
        if artifact_record_id_cache is None:
            artifact_record_id_cache = set(artifact_records_by_id())
        return artifact_record_id_cache

    def resolve_target(kind: str, target: str) -> dict[str, Any]:
        key = (kind, target)
        if key not in resolution_cache:
            resolution_cache[key] = _target_resolution(
                root,
                kind,
                target,
                nodes,
                artifact_record_ids=artifact_record_ids() if kind == "linked_artifact_record" else None,
            )
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
        for record_id in node_artifact_record_ids(node):
            entries.append({"kind": "linked_artifact_record", "label": "linked_artifact_record", "target": str(record_id)})

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
            if kind == "linked_artifact_record":
                record = artifact_records_by_id().get(target)
                if not record:
                    continue
                record_entries = []
                if record.get("stable_path"):
                    record_entries.append({"kind": "artifact_record_path", "label": "path", "target": str(record["stable_path"])})
                links = record.get("links")
                if isinstance(links, dict):
                    for label, link_target in links.items():
                        if link_target not in (None, ""):
                            record_entries.append({"kind": "artifact_record_link", "label": str(label), "target": str(link_target)})
                for record_entry in record_entries:
                    record_resolution = resolve_target(record_entry["kind"], record_entry["target"])
                    rows.append({
                        "node_id": node.id,
                        "node_title": node.title,
                        "node_type": node.type,
                        "artifact_record_id": target,
                        "kind": record_entry["kind"],
                        "label": f"{target}:{record_entry['label']}",
                        "target": record_entry["target"],
                        **row_resolution(record_resolution),
                    })
    return rows
