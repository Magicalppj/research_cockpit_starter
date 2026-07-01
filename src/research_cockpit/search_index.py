from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import re

from research_cockpit.artifact_records import list_artifact_records
from research_cockpit.graph_core import GraphTopology, focus_related_ids
from research_cockpit.storage import load_yaml, normalize_relative_path, relative_to_root
from research_cockpit.types import (
    DEFAULT_RESOURCE_SCAN_SETTINGS,
    RESOURCE_SEARCH_ALLOWED_SUFFIXES,
    ResourceScanSettings,
    SEARCH_NODE_TEXT_FIELDS,
    ResearchNode,
)
from research_cockpit.resources import build_link_rows


def _node_file_paths(root: Path) -> dict[str, str]:
    paths: dict[str, str] = {}
    for path in sorted((root / "graph" / "nodes").glob("*.yaml")):
        data = load_yaml(path)
        node_id = data.get("id")
        if node_id:
            paths[str(node_id)] = relative_to_root(root, path)
    return paths


def _node_note_paths(nodes: dict[str, ResearchNode]) -> dict[str, str]:
    note_paths: dict[str, str] = {}
    for node in nodes.values():
        links = node.raw.get("links")
        if not isinstance(links, dict):
            continue
        note_path = links.get("notes")
        if note_path:
            note_paths[normalize_relative_path(note_path)] = node.id
    return note_paths


def _first_markdown_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    return fallback


def _flatten_search_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        values: list[str] = []
        for key in sorted(value):
            values.extend(_flatten_search_values(value[key]))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_flatten_search_values(item))
        return values
    return [str(value)]


def _node_search_text(node: ResearchNode) -> str:
    parts = [
        node.id,
        node.type,
        node.title,
        node.status,
        node.priority or "",
        node.summary,
        *node.tags,
    ]
    for field_name in SEARCH_NODE_TEXT_FIELDS:
        parts.extend(_flatten_search_values(node.raw.get(field_name)))
    return "\n".join(part for part in parts if part not in (None, ""))


def _artifact_record_search_entries(
    root: Path,
    nodes: dict[str, ResearchNode],
    focus_ids: set[str],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for record in list_artifact_records(root):
        record_id = str(record.get("record_id") or "")
        if not record_id:
            continue
        experiment_id = str(record.get("experiment_id") or "")
        experiment_node = nodes.get(experiment_id) if experiment_id else None
        links = record.get("links") if isinstance(record.get("links"), dict) else {}
        retention = record.get("retention") if isinstance(record.get("retention"), dict) else {}
        text = "\n".join(
            str(value)
            for value in [
                record_id,
                experiment_id,
                record.get("run_id"),
                record.get("title"),
                record.get("summary"),
                record.get("artifact_kind"),
                record.get("status"),
                record.get("stable_path"),
                record.get("promoted_artifact_id"),
                *(links.values() if isinstance(links, dict) else []),
                *(retention.values() if isinstance(retention, dict) else []),
            ]
            if value not in (None, "")
        )
        entries.append({
            "entry_id": f"artifact_record:{record_id}",
            "source": "artifact_record",
            "node_id": experiment_id or None,
            "node_type": experiment_node.type if experiment_node else ("experiment" if experiment_id else None),
            "node_title": experiment_node.title if experiment_node else (experiment_id or None),
            "title": record.get("title") or record_id,
            "path": record.get("stable_path") or record.get("source_file") or "",
            "text": text,
            "updated_at": str(record.get("updated_at") or ""),
            "is_focus_related": bool(experiment_id and experiment_id in focus_ids),
            "artifact_record_id": record_id,
            "run_id": record.get("run_id"),
            "promoted_artifact_id": record.get("promoted_artifact_id"),
        })
    return entries


def _resource_entry_id(node_id: str, label: str, target: str) -> str:
    return f"resource:{node_id}:{label}:{target}"


def _resource_search_entry(
    row: dict[str, Any],
    *,
    path: str,
    text: str = "",
    truncated: bool = False,
    bytes_read: int = 0,
    skip_reason: str = "",
    scan_kind: str = "",
    scan_file_count: int = 0,
    summary_files: list[str] | None = None,
) -> dict[str, Any]:
    node_id = str(row.get("node_id") or "")
    label = str(row.get("label") or row.get("kind") or "")
    title = f"{row.get('node_title') or node_id} / {label}".strip(" /")
    return {
        "entry_id": _resource_entry_id(node_id, label, path or str(row.get("target") or "")),
        "source": "resource",
        "node_id": node_id or None,
        "node_type": row.get("node_type"),
        "node_title": row.get("node_title"),
        "title": title,
        "path": path,
        "text": text,
        "updated_at": "",
        "is_focus_related": False,
        "resource_kind": row.get("kind"),
        "resource_label": label,
        "target": str(row.get("target") or ""),
        "truncated": truncated,
        "bytes_read": bytes_read,
        "skip_reason": skip_reason,
        "scan_kind": scan_kind,
        "scan_file_count": scan_file_count,
        "summary_files": summary_files or [],
    }


def _resource_path_under_root(root: Path, target: str) -> tuple[Path | None, str]:
    normalized = normalize_relative_path(target)
    if not normalized:
        return None, ""
    path = Path(target)
    if path.is_absolute():
        return None, normalized
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None, normalized
    return candidate, normalized


def _matches_skip_pattern(path: str, settings: ResourceScanSettings) -> bool:
    normalized = normalize_relative_path(path).lower()
    name = Path(normalized).name
    for pattern in settings.skip_patterns:
        lowered = pattern.lower()
        if fnmatch.fnmatch(normalized, lowered) or fnmatch.fnmatch(name, lowered):
            return True
    return False


def _resource_skip_reason(
    root: Path,
    row: dict[str, Any],
    normalized_note_paths: set[str],
    settings: ResourceScanSettings,
) -> tuple[str, Path | None, str]:
    target = str(row.get("target") or "")
    normalized = normalize_relative_path(target)
    kind = str(row.get("kind") or "")
    if normalized in normalized_note_paths and normalized.lower().endswith(".md"):
        return "indexed_as_note", None, normalized
    if kind == "run_id":
        return "run_id", None, normalized or target
    if kind == "linked_artifact":
        return "linked_artifact", None, normalized or target
    if kind == "linked_artifact_record":
        return "linked_artifact_record", None, normalized or target
    if Path(target).is_absolute():
        return "absolute_path", None, normalized or target
    if urlparse(target).scheme in {"http", "https"}:
        return "external", None, normalized or target
    path, normalized = _resource_path_under_root(root, target)
    if path is None:
        return "outside_root", None, normalized or target
    if row.get("exists") is False:
        return "missing", path, normalized
    if row.get("exists") is not True:
        return "unknown", path, normalized
    if _matches_skip_pattern(normalized or target, settings):
        return "resource_scan_skip_pattern", None, normalized or target
    if not path.is_file():
        if path.is_dir():
            return "", path, normalized
        return "not_file", path, normalized
    if path.suffix.lower() not in RESOURCE_SEARCH_ALLOWED_SUFFIXES:
        return "unsupported_extension", path, normalized
    return "", path, normalized


def _read_resource_text(path: Path, *, max_bytes: int) -> tuple[str, bool, int]:
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    return data.decode("utf-8", errors="replace"), truncated, len(data)


def _read_directory_summary(
    path: Path,
    *,
    normalized: str,
    settings: ResourceScanSettings,
) -> tuple[str, bool, int, int, list[str], str]:
    chunks: list[str] = []
    bytes_read = 0
    truncated = False
    read_files: list[str] = []
    seen_file_keys: set[tuple[object, ...]] = set()
    max_files = max(0, settings.max_files_per_artifact)
    max_bytes = max(0, settings.max_bytes_per_artifact)
    for relative_name in settings.summary_files:
        if len(read_files) >= max_files or bytes_read >= max_bytes:
            truncated = True
            break
        summary_path = path / relative_name
        if not summary_path.is_file():
            continue
        try:
            stat = summary_path.stat()
        except OSError:
            continue
        file_key = (
            ("stat", stat.st_dev, stat.st_ino)
            if stat.st_ino
            else ("path", str(summary_path.resolve(strict=False)).lower())
        )
        if file_key in seen_file_keys:
            continue
        seen_file_keys.add(file_key)
        summary_normalized = normalize_relative_path(f"{normalized}/{relative_name}")
        if _matches_skip_pattern(summary_normalized, settings):
            continue
        if summary_path.suffix.lower() not in RESOURCE_SEARCH_ALLOWED_SUFFIXES:
            continue
        remaining = max_bytes - bytes_read
        text, file_truncated, file_bytes = _read_resource_text(summary_path, max_bytes=remaining)
        bytes_read += file_bytes
        truncated = truncated or file_truncated
        read_files.append(summary_normalized)
        chunks.append(f"# {relative_name}\n{text}")
    if not read_files:
        return "", False, 0, 0, [], "directory_no_summary_files"
    return "\n\n".join(chunks), truncated, bytes_read, len(read_files), read_files, ""


def _resource_search_entries(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    note_paths: dict[str, str],
    *,
    link_rows: list[dict[str, Any]] | None = None,
    focus_ids: set[str] | None = None,
    topology: GraphTopology | None = None,
    include_resource_text: bool = True,
    resource_scan_settings: ResourceScanSettings | None = None,
) -> list[dict[str, Any]]:
    settings = resource_scan_settings or DEFAULT_RESOURCE_SCAN_SETTINGS
    focus_ids = focus_ids if focus_ids is not None else (
        focus_related_ids(nodes, current, topology=topology) if current else set()
    )
    normalized_note_paths = set(note_paths)
    skip_cache: dict[tuple[str, str, object], tuple[str, Path | None, str]] = {}
    text_cache: dict[str, tuple[str, bool, int]] = {}
    entries: list[dict[str, Any]] = []

    def skip_reason(row: dict[str, Any]) -> tuple[str, Path | None, str]:
        key = (str(row.get("kind") or ""), str(row.get("target") or ""), row.get("exists"))
        if key not in skip_cache:
            skip_cache[key] = _resource_skip_reason(root, row, normalized_note_paths, settings)
        return skip_cache[key]

    def read_resource_text(path: Path, normalized: str) -> tuple[str, bool, int]:
        key = normalized or path.as_posix()
        if key not in text_cache:
            text_cache[key] = _read_resource_text(path, max_bytes=settings.max_bytes_per_artifact)
        return text_cache[key]

    for row in link_rows if link_rows is not None else build_link_rows(root, nodes):
        reason, path, normalized = skip_reason(row)
        if reason == "indexed_as_note":
            continue
        if reason:
            entry = _resource_search_entry(row, path=normalized or str(row.get("target") or ""), skip_reason=reason)
        elif not include_resource_text:
            entry = _resource_search_entry(row, path=normalized, skip_reason="resource_search_disabled")
        else:
            assert path is not None
            if path.is_dir():
                text, truncated, bytes_read, file_count, summary_files, read_skip_reason = _read_directory_summary(
                    path,
                    normalized=normalized,
                    settings=settings,
                )
                if read_skip_reason:
                    entry = _resource_search_entry(
                        row,
                        path=normalized,
                        skip_reason=read_skip_reason,
                        scan_kind="directory",
                    )
                    entry["is_focus_related"] = bool(entry.get("node_id") in focus_ids)
                    entries.append(entry)
                    continue
                entry = _resource_search_entry(
                    row,
                    path=normalized,
                    text=text,
                    truncated=truncated,
                    bytes_read=bytes_read,
                    scan_kind="directory_summary",
                    scan_file_count=file_count,
                    summary_files=summary_files,
                )
                entry["is_focus_related"] = bool(entry.get("node_id") in focus_ids)
                entries.append(entry)
                continue
            text, truncated, bytes_read = read_resource_text(path, normalized)
            entry = _resource_search_entry(
                row,
                path=normalized,
                text=text,
                truncated=truncated,
                bytes_read=bytes_read,
                scan_kind="file",
                scan_file_count=1,
            )
        entry["is_focus_related"] = bool(entry.get("node_id") in focus_ids)
        entries.append(entry)
    return entries


def build_search_index(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None = None,
    *,
    link_rows: list[dict[str, Any]] | None = None,
    topology: GraphTopology | None = None,
    include_resource_text: bool = True,
    resource_scan_settings: ResourceScanSettings | None = None,
    sources: set[str] | list[str] | None = None,
) -> list[dict[str, Any]]:
    current = current or {}
    selected_sources = set(sources or [])

    def include_source(source: str) -> bool:
        return not selected_sources or source in selected_sources

    focus_ids = focus_related_ids(nodes, current, topology=topology) if current else set()
    node_paths = _node_file_paths(root) if include_source("node") else {}
    note_paths = _node_note_paths(nodes) if include_source("note") or include_source("resource") else {}
    entries: list[dict[str, Any]] = []

    if include_source("node"):
        for node in sorted(nodes.values(), key=lambda item: item.id):
            entries.append({
                "entry_id": f"node:{node.id}",
                "source": "node",
                "node_id": node.id,
                "node_type": node.type,
                "node_title": node.title,
                "title": node.title,
                "path": node_paths.get(node.id, ""),
                "text": _node_search_text(node),
                "updated_at": str(node.raw.get("updated_at") or ""),
                "is_focus_related": node.id in focus_ids,
            })

    notes_dir = root / "notes"
    if include_source("note") and notes_dir.exists():
        for path in sorted(notes_dir.glob("**/*.md")):
            rel_path = relative_to_root(root, path)
            normalized = normalize_relative_path(rel_path)
            text = path.read_text(encoding="utf-8", errors="replace")
            node_id = note_paths.get(normalized)
            node = nodes.get(node_id) if node_id else None
            entries.append({
                "entry_id": f"note:{normalized}",
                "source": "note",
                "node_id": node.id if node else None,
                "node_type": node.type if node else None,
                "node_title": node.title if node else None,
                "title": _first_markdown_heading(text, path.stem),
                "path": normalized,
                "text": text,
                "updated_at": str(node.raw.get("updated_at") or "") if node else "",
                "is_focus_related": bool(node and node.id in focus_ids),
            })
    if include_source("artifact_record"):
        entries.extend(_artifact_record_search_entries(root, nodes, focus_ids))
    if include_source("resource"):
        entries.extend(_resource_search_entries(
            root,
            nodes,
            current,
            note_paths,
            link_rows=link_rows,
            focus_ids=focus_ids,
            topology=topology,
            include_resource_text=include_resource_text,
            resource_scan_settings=resource_scan_settings,
        ))
    return entries


def _search_terms(query: str) -> list[str]:
    return [term.lower() for term in query.split() if term.strip()]


def _count_occurrences(text: str, needle: str) -> int:
    if not needle:
        return 0
    return text.count(needle)


def _search_score(entry: dict[str, Any], query: str, terms: list[str]) -> int:
    phrase = query.lower()
    title = str(entry.get("title") or "").lower()
    path = str(entry.get("path") or "").lower()
    text = str(entry.get("text") or "").lower()
    score = 0
    score += 40 * _count_occurrences(title, phrase)
    score += 12 * _count_occurrences(text, phrase)
    score += 4 * _count_occurrences(path, phrase)
    for term in terms:
        score += 10 * _count_occurrences(title, term)
        score += _count_occurrences(text, term)
        score += 2 * _count_occurrences(path, term)
    return score


def make_search_snippet(text: str, query: str, width: int = 180) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return ""
    lower = clean.lower()
    phrase = query.lower().strip()
    terms = _search_terms(query)
    positions = []
    if phrase:
        pos = lower.find(phrase)
        if pos >= 0:
            positions.append(pos)
    for term in terms:
        pos = lower.find(term)
        if pos >= 0:
            positions.append(pos)
    start_at = min(positions) if positions else 0
    start = max(0, start_at - width // 2)
    end = min(len(clean), start + width)
    snippet = clean[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(clean):
        snippet += "..."
    return snippet


def _search_result_from_entry(entry: dict[str, Any], query: str, score: int) -> dict[str, Any]:
    text = str(entry.get("text") or "")
    return {
        "entry_id": entry.get("entry_id"),
        "score": score,
        "source": entry.get("source"),
        "node_id": entry.get("node_id"),
        "node_type": entry.get("node_type"),
        "node_title": entry.get("node_title"),
        "title": entry.get("title"),
        "path": entry.get("path"),
        "snippet": make_search_snippet(text, query),
        "preview": make_search_snippet(text, query, width=700),
        "updated_at": entry.get("updated_at"),
        "is_focus_related": bool(entry.get("is_focus_related")),
        "resource_kind": entry.get("resource_kind"),
        "resource_label": entry.get("resource_label"),
        "target": entry.get("target"),
        "truncated": bool(entry.get("truncated")),
        "bytes_read": entry.get("bytes_read"),
        "skip_reason": entry.get("skip_reason"),
    }


def search_knowledge(
    index: list[dict[str, Any]],
    query: str,
    *,
    sources: set[str] | list[str] | None = None,
    node_types: set[str] | list[str] | None = None,
    limit: int | None = 20,
    focus_only: bool = False,
) -> list[dict[str, Any]]:
    query = query.strip()
    if not query or limit == 0:
        return []
    selected_sources = set(sources or [])
    selected_node_types = set(node_types or [])
    terms = _search_terms(query)
    results: list[dict[str, Any]] = []

    for entry in index:
        if entry.get("skip_reason"):
            continue
        if selected_sources and entry.get("source") not in selected_sources:
            continue
        if selected_node_types and entry.get("node_type") not in selected_node_types:
            continue
        if focus_only and not entry.get("is_focus_related"):
            continue
        score = _search_score(entry, query, terms)
        if score <= 0:
            continue
        results.append(_search_result_from_entry(entry, query, score))

    results.sort(
        key=lambda item: (
            -int(item.get("score") or 0),
            str(item.get("source") or ""),
            str(item.get("node_id") or ""),
            str(item.get("path") or ""),
            str(item.get("entry_id") or ""),
        )
    )
    if limit is None:
        return results
    return results[:max(0, limit)]


def _search_summary_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_id": entry.get("entry_id"),
        "source": entry.get("source"),
        "node_id": entry.get("node_id"),
        "node_type": entry.get("node_type"),
        "title": entry.get("title"),
        "path": entry.get("path"),
    }


def build_search_index_summary(index: list[dict[str, Any]], focus_entry_limit: int = 8) -> dict[str, Any]:
    note_count = 0
    node_count = 0
    artifact_record_count = 0
    resource_count = 0
    resource_unique_count = 0
    resource_truncated_count = 0
    resource_skipped_count = 0
    resource_search_disabled_count = 0
    resource_scan_skip_pattern_count = 0
    resource_directory_summary_count = 0
    resource_directory_no_summary_count = 0
    resource_bytes_read = 0
    resource_skipped_by_reason: dict[str, int] = {}
    resource_seen_paths: set[str] = set()
    focus_resource_count = 0
    unlinked_note_count = 0
    focus_entry_count = 0
    focus_entries: list[dict[str, Any]] = []

    for entry in index:
        source = entry.get("source")
        if source == "note":
            note_count += 1
            if not entry.get("node_id"):
                unlinked_note_count += 1
        if source == "node":
            node_count += 1
        if source == "artifact_record":
            artifact_record_count += 1
        if source == "resource":
            if entry.get("skip_reason"):
                resource_skipped_count += 1
                reason = str(entry.get("skip_reason") or "")
                resource_skipped_by_reason[reason] = resource_skipped_by_reason.get(reason, 0) + 1
                if entry.get("skip_reason") == "resource_search_disabled":
                    resource_search_disabled_count += 1
                if entry.get("skip_reason") == "resource_scan_skip_pattern":
                    resource_scan_skip_pattern_count += 1
                if entry.get("skip_reason") == "directory_no_summary_files":
                    resource_directory_no_summary_count += 1
            else:
                resource_count += 1
                if entry.get("scan_kind") == "directory_summary":
                    resource_directory_summary_count += 1
                resource_path = str(entry.get("path") or entry.get("target") or entry.get("entry_id") or "")
                if resource_path not in resource_seen_paths:
                    resource_seen_paths.add(resource_path)
                    resource_unique_count += 1
                    resource_bytes_read += int(entry.get("bytes_read") or 0)
                if entry.get("truncated"):
                    resource_truncated_count += 1
                if entry.get("is_focus_related"):
                    focus_resource_count += 1
        if entry.get("is_focus_related") and not entry.get("skip_reason"):
            focus_entry_count += 1
            if len(focus_entries) < focus_entry_limit:
                focus_entries.append(_search_summary_entry(entry))

    return {
        "entry_count": len(index),
        "note_count": note_count,
        "node_count": node_count,
        "artifact_record_count": artifact_record_count,
        "resource_count": resource_count,
        "resource_unique_count": resource_unique_count,
        "resource_truncated_count": resource_truncated_count,
        "resource_skipped_count": resource_skipped_count,
        "resource_search_disabled_count": resource_search_disabled_count,
        "resource_scan_skip_pattern_count": resource_scan_skip_pattern_count,
        "resource_directory_summary_count": resource_directory_summary_count,
        "resource_directory_no_summary_count": resource_directory_no_summary_count,
        "resource_bytes_read": resource_bytes_read,
        "resource_skipped_by_reason": resource_skipped_by_reason,
        "focus_resource_count": focus_resource_count,
        "unlinked_note_count": unlinked_note_count,
        "focus_entry_count": focus_entry_count,
        "focus_entries": focus_entries,
    }
