from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from research_cockpit.agent_state import (
    AssignmentRecord,
    load_assignments,
)
from research_cockpit.commands._runtime import stable_payload_revision
from research_cockpit.public_contracts import parse_public_contract
from research_cockpit.validation_index import (
    ASSIGNMENT_PROJECTION_VERSION,
    file_signature,
    is_index_schema_compatible,
    load_validation_index,
)


COORDINATION_SCHEMA_VERSION = "coordination_snapshot_v1"
COORDINATION_MAX_BYTES = 32 * 1024
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
_COLLECTION_LIMIT = 20
_ACTIVE_STATUSES = {"queued", "active", "blocked"}
_WRITER_STATUSES = {"active", "blocked"}
_NON_WRITER_POLICIES = {"review_read_only", "coordinator"}


def _bounded(values: list[Any], *, limit: int = _COLLECTION_LIMIT) -> dict[str, Any]:
    actual_limit = max(0, min(limit, _COLLECTION_LIMIT))
    items = values[:actual_limit]
    return {
        "items": items,
        "limit": actual_limit,
        "total": len(values),
        "omitted": len(values) - len(items),
    }


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _utc_now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(timezone.utc)


def _review_status(assignment: AssignmentRecord) -> str:
    return str(
        assignment.review.get("status")
        or ("pending" if assignment.review.get("required") else "not_required")
    )


def _result_revision(assignment: AssignmentRecord | None) -> str | None:
    if assignment is None:
        return None
    revision = str(assignment.result.get("revision") or "").strip()
    return revision or None


def _lease_state(assignment: AssignmentRecord, *, now: datetime) -> str:
    lease_id = str(assignment.lease.get("lease_id") or "").strip()
    if not lease_id:
        if assignment.status in {"active", "blocked"} and assignment.agent_id:
            return "legacy_unknown"
        return "unclaimed"
    expires_at = _parse_timestamp(assignment.lease.get("expires_at"))
    if expires_at is not None and expires_at <= now:
        return "expired"
    return "active"


def _assignment_from_index_row(row: dict[str, Any]) -> AssignmentRecord:
    root_node = str(row.get("root_node") or "")
    raw: dict[str, Any] = {
        "assignment_id": str(row.get("assignment_id") or ""),
        "agent_id": row.get("agent_id"),
        "status": str(row.get("status") or ""),
        "kind": str(row.get("kind") or "experiment"),
        "root_node": root_node,
        "current_node": str(row.get("current_node") or root_node),
        "allowed_subtree": {
            "root": str(row.get("allowed_root") or root_node),
            "policy": "descendants_only",
        },
        "scope": dict(row.get("scope") or {}),
        "dependencies": list(row.get("dependencies") or []),
        "lease": dict(row.get("lease") or {}),
        "review": dict(row.get("review") or {}),
    }
    if row.get("has_inputs", True):
        raw["inputs"] = dict(row.get("inputs") or {})
        raw["input_revision"] = row.get("input_revision")
    result_revision = str(row.get("result_revision") or "").strip()
    if result_revision:
        raw["result"] = {"revision": result_revision}
    return AssignmentRecord.from_dict(raw)


def _assignment_index_is_fresh(root: Path, index: dict[str, Any] | None) -> bool:
    if (
        not is_index_schema_compatible(index)
        or index is None
        or index.get("assignment_projection_version") != ASSIGNMENT_PROJECTION_VERSION
        or not isinstance(index.get("assignments"), dict)
    ):
        return False
    rows = index["assignments"]
    assignment_dir = root / "assignments"
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in assignment_dir.glob("*.yaml")
        if path.is_file()
    } if assignment_dir.exists() else set()
    indexed_paths = {
        str(row.get("file") or "")
        for row in rows.values()
        if isinstance(row, dict) and row.get("file")
    }
    if actual_paths != indexed_paths or len(rows) != len(indexed_paths):
        return False
    for row in rows.values():
        if not isinstance(row, dict):
            return False
        rel_path = str(row.get("file") or "")
        signature = row.get("file_signature")
        if not rel_path or not isinstance(signature, dict):
            return False
        try:
            stat = (root / rel_path).stat()
        except OSError:
            return False
        if stat.st_size != signature.get("size") or stat.st_mtime_ns != signature.get("mtime_ns"):
            return False
    return True


def _assignment_sources(
    root: Path,
) -> tuple[dict[str, AssignmentRecord], dict[str, Any] | None, dict[str, str], bool]:
    index = load_validation_index(root)
    if _assignment_index_is_fresh(root, index):
        assert index is not None
        records: dict[str, AssignmentRecord] = {}
        fingerprints: dict[str, str] = {}
        for assignment_id, raw_row in sorted(index["assignments"].items()):
            row = dict(raw_row)
            assignment = _assignment_from_index_row(row)
            records[str(assignment_id)] = assignment
            signature = row.get("file_signature") or {}
            fingerprints[str(assignment_id)] = str(signature.get("sha256") or "")
        return records, index, fingerprints, True

    records = load_assignments(root)
    fingerprints = {
        assignment_id: stable_payload_revision(assignment.raw, prefix="assignment-v1")
        for assignment_id, assignment in sorted(records.items())
    }
    return records, index, fingerprints, False


def _dependency_state(
    assignment: AssignmentRecord,
    records: dict[str, AssignmentRecord],
) -> tuple[bool, bool, list[str]]:
    waiting = False
    unknown = False
    stale: list[str] = []
    expected_revisions = assignment.inputs.get("dependency_revisions", {})
    if not isinstance(expected_revisions, dict):
        expected_revisions = {}
    for specification in assignment.dependencies:
        dependency_id = str(specification.get("assignment_id") or "")
        dependency = records.get(dependency_id)
        required_status = specification.get("required_status")
        required_review = specification.get("required_review_status")
        if required_status is None and required_review is None:
            required_status = "completed"
        if dependency is None:
            waiting = True
            current_revision = None
        else:
            status_ok = required_status is None or dependency.status == str(required_status)
            review_ok = required_review is None or _review_status(dependency) == str(required_review)
            waiting = waiting or not (status_ok and review_ok)
            current_revision = _result_revision(dependency)
        expected = expected_revisions.get(dependency_id)
        if not isinstance(expected, str) or not expected:
            unknown = True
        elif current_revision != expected:
            stale.append(
                f"Dependency {dependency_id} changed from {expected} to {current_revision or 'no-result'}."
            )
    return waiting, unknown, stale


def _readiness(
    assignment_id: str,
    assignment: AssignmentRecord,
    records: dict[str, AssignmentRecord],
    index: dict[str, Any] | None,
    *,
    assignment_index_fresh: bool,
) -> tuple[str, list[str]]:
    waiting, unknown, stale = _dependency_state(assignment, records)
    if "inputs" not in assignment.raw:
        unknown = True
    else:
        projection = (
            ((index or {}).get("assignments", {}) or {}).get(assignment_id, {})
            if assignment_index_fresh
            else {}
        )
        if not isinstance(projection, dict) or projection.get("baseline_projection_fresh") is not True:
            unknown = True
        else:
            current_baseline = projection.get("current_baseline_revision")
            expected_baseline = assignment.inputs.get("effective_baseline_revision")
            if current_baseline != expected_baseline:
                stale.append(
                    "Effective baseline changed from "
                    f"{expected_baseline or 'none'} to {current_baseline or 'none'}."
                )
    if unknown:
        return "unknown_inputs", stale
    if stale:
        return "stale_inputs", stale
    if waiting:
        return "waiting_dependencies", stale
    return "ready", stale


def _summary_rows(
    records: dict[str, AssignmentRecord],
    index: dict[str, Any] | None,
    *,
    now: datetime,
    assignment_index_fresh: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for assignment_id, assignment in sorted(records.items()):
        readiness, _stale = _readiness(
            assignment_id,
            assignment,
            records,
            index,
            assignment_index_fresh=assignment_index_fresh,
        )
        rows.append(
            {
                "assignment_id": assignment_id,
                "kind": assignment.kind,
                "status": assignment.status,
                "readiness": readiness,
                "agent_id": assignment.agent_id,
                "root_node": assignment.root_node,
                "review_status": _review_status(assignment),
                "lease_state": _lease_state(assignment, now=now),
                "packet_revision": None,
            }
        )
    return rows


def _is_ancestor_or_self(index: dict[str, Any] | None, ancestor: str, node_id: str) -> bool:
    if ancestor == node_id:
        return True
    if not is_index_schema_compatible(index) or index is None:
        return False
    rows = index.get("nodes", {}) or {}
    current = node_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        row = rows.get(current)
        if not isinstance(row, dict):
            return False
        current = str(row.get("parent") or "")
        if current == ancestor:
            return True
    return False


def _overlap_warnings(
    records: dict[str, AssignmentRecord],
    index: dict[str, Any] | None,
) -> list[str]:
    writers = [
        assignment
        for assignment in records.values()
        if assignment.status in _WRITER_STATUSES
        and assignment.agent_id
        and str(assignment.scope.get("write_policy") or "exclusive") not in _NON_WRITER_POLICIES
    ]
    warnings: list[str] = []
    for position, left in enumerate(sorted(writers, key=lambda item: item.assignment_id)):
        left_policy = str(left.scope.get("write_policy") or "exclusive")
        left_root = str(left.scope.get("root_node") or left.root_node)
        for right in sorted(writers[position + 1 :], key=lambda item: item.assignment_id):
            right_policy = str(right.scope.get("write_policy") or "exclusive")
            if left_policy == right_policy == "append_only":
                continue
            right_root = str(right.scope.get("root_node") or right.root_node)
            if not (
                _is_ancestor_or_self(index, left_root, right_root)
                or _is_ancestor_or_self(index, right_root, left_root)
            ):
                continue
            warnings.append(
                f"Active writer scopes overlap: {left.assignment_id} ({left_root}) and "
                f"{right.assignment_id} ({right_root})."
            )
    return warnings


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    active_rows = [row for row in rows if row["status"] in _ACTIVE_STATUSES]
    return {
        "waiting": sum(row["readiness"] == "waiting_dependencies" for row in active_rows),
        "ready": sum(row["readiness"] == "ready" for row in active_rows),
        "active": sum(row["status"] == "active" for row in rows),
        "blocked": sum(row["status"] == "blocked" for row in rows),
        "stale_inputs": sum(row["readiness"] == "stale_inputs" for row in active_rows),
        "expired_leases": sum(row["lease_state"] == "expired" for row in rows),
        "pending_review": sum(row["review_status"] == "pending" for row in rows),
    }


def _node_fingerprints(
    records: dict[str, AssignmentRecord],
    index: dict[str, Any] | None,
) -> dict[str, str]:
    if not is_index_schema_compatible(index) or index is None:
        return {}
    rows = index.get("nodes", {}) or {}
    selected: set[str] = set()
    for assignment in records.values():
        for start in (assignment.root_node, assignment.current_node):
            current = str(start or "")
            seen: set[str] = set()
            while current and current not in seen:
                seen.add(current)
                selected.add(current)
                row = rows.get(current)
                if not isinstance(row, dict):
                    break
                current = str(row.get("parent") or "")
    return {
        node_id: str(((rows.get(node_id) or {}).get("file_signature") or {}).get("sha256") or "")
        for node_id in sorted(selected)
    }


def _normalize_set(values: set[str] | None) -> set[str]:
    return {str(value) for value in values or set() if str(value)}


def _filtered_rows(
    rows: list[dict[str, Any]],
    *,
    statuses: set[str],
    kinds: set[str],
    agent_id: str | None,
    root_node: str | None,
    review_status: str | None,
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if (not statuses or row["status"] in statuses)
        and (not kinds or row["kind"] in kinds)
        and (agent_id is None or row["agent_id"] == agent_id)
        and (root_node is None or row["root_node"] == root_node)
        and (review_status is None or row["review_status"] == review_status)
    ]


def _page_token(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_page_token(value: str) -> dict[str, Any]:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("page token is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("page token is invalid")
    return payload


def _encoded_size(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _fit_budget(
    payload: dict[str, Any],
    *,
    offset: int,
    query_hash: str,
) -> None:
    assignments = payload["assignments"]
    warnings = payload["overlap_warnings"]

    def update_page() -> None:
        next_offset = offset + len(assignments["items"])
        payload["next_page"] = (
            _page_token(
                {
                    "revision": payload["revision"],
                    "query": query_hash,
                    "offset": next_offset,
                }
            )
            if next_offset < assignments["total"]
            else None
        )

    update_page()
    while _encoded_size(payload) >= COORDINATION_MAX_BYTES:
        if warnings["items"]:
            warnings["items"].pop()
            warnings["omitted"] = warnings["total"] - len(warnings["items"])
        elif len(assignments["items"]) > 1:
            assignments["items"].pop()
            assignments["omitted"] = assignments["total"] - len(assignments["items"])
        else:
            raise ValueError("Coordination Snapshot cannot fit the 32 KiB output budget")
        update_page()


def build_coordination_state(
    root: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _utc_now(now)
    records, index, assignment_fingerprints, assignment_index_fresh = _assignment_sources(root)
    rows = _summary_rows(
        records,
        index,
        now=current,
        assignment_index_fresh=assignment_index_fresh,
    )
    overlap_warnings = _overlap_warnings(records, index)
    current_state_path = root / "current_state.yaml"
    return {
        "rows": rows,
        "counts": _counts(rows),
        "overlap_warnings": overlap_warnings,
        "revision_payload": {
            "assignments": assignment_fingerprints,
            "nodes": _node_fingerprints(records, index),
            "current_state": (
                file_signature(current_state_path) if current_state_path.is_file() else None
            ),
            "rows": rows,
            "overlap_warnings": overlap_warnings,
        },
    }


def build_coordination_snapshot(
    root: Path,
    *,
    limit: int = DEFAULT_LIMIT,
    page: str | None = None,
    since_revision: str | None = None,
    statuses: set[str] | None = None,
    kinds: set[str] | None = None,
    agent_id: str | None = None,
    root_node: str | None = None,
    review_status: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    normalized_statuses = _normalize_set(statuses)
    normalized_kinds = _normalize_set(kinds)
    query = {
        "statuses": sorted(normalized_statuses),
        "kinds": sorted(normalized_kinds),
        "agent_id": agent_id,
        "root_node": root_node,
        "review_status": review_status,
        "limit": limit,
    }
    query_hash = stable_payload_revision(query, prefix="coord-query-v1")
    coordination_state = build_coordination_state(root, now=now)
    rows = coordination_state["rows"]
    overlap_warnings = coordination_state["overlap_warnings"]
    revision_payload = dict(coordination_state["revision_payload"])
    revision_payload["query"] = query
    revision = stable_payload_revision(revision_payload, prefix="coord-v1")
    if since_revision and since_revision == revision:
        return {
            "schema_version": COORDINATION_SCHEMA_VERSION,
            "changed": False,
            "revision": revision,
        }

    offset = 0
    if page:
        token = _decode_page_token(page)
        if token.get("revision") != revision:
            raise ValueError("page token is stale; restart pagination from the first page")
        if token.get("query") != query_hash:
            raise ValueError("page token does not match the current filters")
        offset = token.get("offset")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("page token offset is invalid")

    filtered = _filtered_rows(
        rows,
        statuses=normalized_statuses,
        kinds=normalized_kinds,
        agent_id=agent_id,
        root_node=root_node,
        review_status=review_status,
    )
    page_rows = [dict(row) for row in filtered[offset : offset + limit]]
    payload = {
        "schema_version": COORDINATION_SCHEMA_VERSION,
        "changed": True,
        "revision": revision,
        "counts": _counts(rows),
        "assignments": {
            "items": page_rows,
            "limit": limit,
            "total": len(filtered),
            "omitted": len(filtered) - len(page_rows),
        },
        "overlap_warnings": _bounded(overlap_warnings),
        "next_page": None,
    }
    _fit_budget(payload, offset=offset, query_hash=query_hash)
    parse_public_contract(payload)
    return payload
