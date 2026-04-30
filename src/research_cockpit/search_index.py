from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import re

from research_cockpit.graph_core import focus_related_ids
from research_cockpit.storage import load_yaml, normalize_relative_path, relative_to_root
from research_cockpit.types import (
    RESOURCE_SEARCH_ALLOWED_SUFFIXES,
    RESOURCE_SEARCH_MAX_BYTES,
    SEARCH_NODE_TEXT_FIELDS,
    ResearchNode,
)
from research_cockpit.model import build_link_rows


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


def _resource_skip_reason(
    root: Path,
    row: dict[str, Any],
    normalized_note_paths: set[str],
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
    if path.suffix.lower() not in RESOURCE_SEARCH_ALLOWED_SUFFIXES:
        return "unsupported_extension", path, normalized
    if not path.is_file():
        return "not_file", path, normalized
    return "", path, normalized


def _read_resource_text(path: Path) -> tuple[str, bool, int]:
    with path.open("rb") as handle:
        data = handle.read(RESOURCE_SEARCH_MAX_BYTES + 1)
    truncated = len(data) > RESOURCE_SEARCH_MAX_BYTES
    data = data[:RESOURCE_SEARCH_MAX_BYTES]
    return data.decode("utf-8", errors="replace"), truncated, len(data)


def _resource_search_entries(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any],
    note_paths: dict[str, str],
) -> list[dict[str, Any]]:
    focus_ids = focus_related_ids(nodes, current) if current else set()
    normalized_note_paths = set(note_paths)
    entries: list[dict[str, Any]] = []
    for row in build_link_rows(root, nodes):
        skip_reason, path, normalized = _resource_skip_reason(root, row, normalized_note_paths)
        if skip_reason == "indexed_as_note":
            continue
        if skip_reason:
            entry = _resource_search_entry(row, path=normalized or str(row.get("target") or ""), skip_reason=skip_reason)
        else:
            assert path is not None
            text, truncated, bytes_read = _read_resource_text(path)
            entry = _resource_search_entry(
                row,
                path=normalized,
                text=text,
                truncated=truncated,
                bytes_read=bytes_read,
            )
        entry["is_focus_related"] = bool(entry.get("node_id") in focus_ids)
        entries.append(entry)
    return entries


def build_search_index(
    root: Path,
    nodes: dict[str, ResearchNode],
    current: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    current = current or {}
    focus_ids = focus_related_ids(nodes, current) if current else set()
    node_paths = _node_file_paths(root)
    note_paths = _node_note_paths(nodes)
    entries: list[dict[str, Any]] = []

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
    if notes_dir.exists():
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
    entries.extend(_resource_search_entries(root, nodes, current, note_paths))
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
    resource_count = 0
    resource_truncated_count = 0
    resource_skipped_count = 0
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
        if source == "resource":
            if entry.get("skip_reason"):
                resource_skipped_count += 1
            else:
                resource_count += 1
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
        "resource_count": resource_count,
        "resource_truncated_count": resource_truncated_count,
        "resource_skipped_count": resource_skipped_count,
        "focus_resource_count": focus_resource_count,
        "unlinked_note_count": unlinked_note_count,
        "focus_entry_count": focus_entry_count,
        "focus_entries": focus_entries,
    }
