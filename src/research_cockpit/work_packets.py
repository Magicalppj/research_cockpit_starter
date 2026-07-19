from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from research_cockpit.agent_state import (
    AssignmentRecord,
    assignment_contract_errors,
    load_assignment,
)
from research_cockpit.baselines import resolve_effective_baseline
from research_cockpit.commands._runtime import stable_payload_revision
from research_cockpit.public_contracts import parse_public_contract
from research_cockpit.root_snapshot import (
    indexed_root_snapshot_source,
    load_indexed_root_snapshot,
)
from research_cockpit.types import ValidationError
from research_cockpit.validation_index import (
    is_index_schema_compatible,
    load_validation_index,
)


WORK_PACKET_SCHEMA_VERSION = "work_packet_v1"
WORK_PACKET_COLLECTION_LIMIT = 20
WORK_PACKET_MAX_BYTES = 8 * 1024
_TEXT_LIMIT = 200
_DEPENDENCY_GRAPH_LIMIT = 200
_ASSIGNMENT_STATUSES = {"queued", "active", "blocked", "completed", "cancelled", "retired"}


def _text(value: Any, *, limit: int = _TEXT_LIMIT) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)] + "..."


def _bounded(
    values: list[Any],
    *,
    item_limit: int = WORK_PACKET_COLLECTION_LIMIT,
) -> dict[str, Any]:
    total = len(values)
    items = values[: min(item_limit, WORK_PACKET_COLLECTION_LIMIT)]
    return {
        "items": items,
        "limit": WORK_PACKET_COLLECTION_LIMIT,
        "total": total,
        "omitted": total - len(items),
    }


def assignment_result_revision(assignment: AssignmentRecord) -> str | None:
    result = assignment.result
    if not result:
        return None
    explicit = result.get("revision")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return stable_payload_revision(result, prefix="result-v1")


def _review_projection(assignment: AssignmentRecord) -> dict[str, Any]:
    review = assignment.review
    required = bool(review.get("required", False))
    status = str(review.get("status") or ("pending" if required else "not_required"))
    result_revision = review.get("result_revision")
    return {
        "required": required,
        "status": status,
        "result_revision": None if result_revision is None else str(result_revision),
    }


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _lease_projection(
    assignment: AssignmentRecord,
    *,
    now: datetime,
) -> tuple[dict[str, Any], str]:
    lease = assignment.lease
    if not lease:
        state = "unclaimed" if assignment.agent_id is None else "legacy_unknown"
        return {
            "owner_agent_id": None,
            "lease_id": None,
            "lease_epoch": 0,
            "heartbeat_at": None,
            "expires_at": None,
            "state": state,
        }, state

    lease_id = lease.get("lease_id")
    projection = {
        "owner_agent_id": lease.get("owner_agent_id"),
        "lease_id": lease_id,
        "lease_epoch": int(lease.get("lease_epoch") or 0),
        "heartbeat_at": lease.get("heartbeat_at"),
        "expires_at": lease.get("expires_at"),
    }
    if lease_id is None:
        state = "unclaimed"
    else:
        expires_at = _parse_utc(str(lease["expires_at"]))
        state = "expired" if expires_at <= now else "active"
    projection["state"] = state
    return projection, state


def _compact_baseline(effective: dict[str, Any]) -> dict[str, Any]:
    def ref(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or not value.get("id"):
            return None
        return {
            key: value[key]
            for key in ("id", "type", "status")
            if value.get(key) not in (None, "")
        }

    return {
        "source_node_id": str(effective.get("source_node_id") or ""),
        "source_kind": str(effective.get("source_kind") or "none"),
        "option": ref(effective.get("option")),
        "decision": ref(effective.get("decision")),
        "artifacts": [
            item
            for item in (ref(value) for value in effective.get("artifacts", []) or [])
            if item is not None
        ][:WORK_PACKET_COLLECTION_LIMIT],
        "reason": _text(effective.get("reason")),
    }


def _baseline_revision(
    root: Path,
    assignment: AssignmentRecord,
    index: dict[str, Any] | None,
) -> tuple[str | None, dict[str, Any]]:
    runtime = {
        "index_fast_path": False,
        "index_fresh": False,
        "used_full_graph": False,
        "nodes_loaded": 0,
        "nodes_total": len((index or {}).get("nodes", {}) or {}),
        "fallback_reason": "validation_index_missing_or_incompatible",
    }
    if not is_index_schema_compatible(index):
        return None, runtime
    assert index is not None
    try:
        snapshot = load_indexed_root_snapshot(
            root,
            node_id=assignment.current_node,
            validation_index=index,
        )
    except RuntimeError as exc:
        runtime["fallback_reason"] = str(exc)
        return None, runtime
    except ValueError as exc:
        raise ValidationError([
            f"{assignment.assignment_id}: scope current_node cannot be projected: {exc}"
        ]) from exc
    if snapshot.validation_errors:
        raise ValidationError(snapshot.validation_errors)
    effective = resolve_effective_baseline(
        snapshot.nodes,
        assignment.current_node,
        snapshot.current,
    )
    compact = _compact_baseline(effective)
    revision = (
        None
        if compact["source_kind"] == "none"
        else stable_payload_revision(compact, prefix="exec-v1")
    )
    runtime.update(
        {
            "index_fast_path": True,
            "index_fresh": True,
            "nodes_loaded": len(snapshot.loaded_node_ids),
            "nodes_total": snapshot.node_count,
            "fallback_reason": "",
        }
    )
    return revision, runtime


def _scope_errors(
    assignment: AssignmentRecord,
    index: dict[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    prefix = assignment.assignment_id
    if assignment.status not in _ASSIGNMENT_STATUSES:
        errors.append(f"{prefix}: invalid assignment status {assignment.status!r}")
    if not assignment.agent_id and assignment.status != "queued":
        errors.append(f"{prefix}: agent_id is required")
    if not assignment.root_node:
        errors.append(f"{prefix}: root_node is required")
    if not assignment.current_node:
        errors.append(f"{prefix}: current_node is required")
    allowed_root = str(assignment.allowed_subtree.get("root") or assignment.root_node)
    if allowed_root != assignment.root_node:
        errors.append(
            f"{prefix}: allowed_subtree.root {allowed_root!r} must match root_node {assignment.root_node!r}"
        )
    policy = str(assignment.allowed_subtree.get("policy") or "descendants_only")
    if policy != "descendants_only":
        errors.append(f"{prefix}: unsupported allowed_subtree.policy {policy!r}")
    if not is_index_schema_compatible(index):
        return errors

    rows = (index or {}).get("nodes", {}) or {}
    for field, node_id in (
        ("root_node", assignment.root_node),
        ("current_node", assignment.current_node),
    ):
        if node_id and node_id not in rows:
            errors.append(f"{prefix}: {field} references missing indexed node {node_id!r}")
    if assignment.root_node in rows and assignment.current_node in rows:
        cursor = assignment.current_node
        seen: set[str] = set()
        while cursor in rows and cursor not in seen and cursor != assignment.root_node:
            seen.add(cursor)
            cursor = str(rows[cursor].get("parent") or "")
        if cursor != assignment.root_node:
            errors.append(
                f"{prefix}: current_node {assignment.current_node!r} is outside scope root {assignment.root_node!r}"
            )
    return errors


def _dependency_cycle_errors(root: Path, assignment: AssignmentRecord) -> list[str]:
    errors: list[str] = []
    visited: set[str] = set()
    visiting: list[str] = []
    records: dict[str, AssignmentRecord] = {assignment.assignment_id: assignment}

    def visit(assignment_id: str) -> None:
        if errors:
            return
        if assignment_id in visiting:
            start = visiting.index(assignment_id)
            cycle = [*visiting[start:], assignment_id]
            errors.append(
                f"{assignment.assignment_id}: dependency cycle detected: {' -> '.join(cycle)}"
            )
            return
        if assignment_id in visited:
            return
        if len(visited) + len(visiting) >= _DEPENDENCY_GRAPH_LIMIT:
            errors.append(
                f"{assignment.assignment_id}: dependency graph exceeds the "
                f"{_DEPENDENCY_GRAPH_LIMIT}-assignment projection limit"
            )
            return
        record = records.get(assignment_id)
        if record is None:
            try:
                record = load_assignment(root, assignment_id)
            except FileNotFoundError:
                visited.add(assignment_id)
                return
            records[assignment_id] = record
        contract_errors = assignment_contract_errors(record)
        if contract_errors:
            errors.extend(contract_errors)
            return
        visiting.append(assignment_id)
        for dependency in record.dependencies:
            dependency_id = str(dependency.get("assignment_id") or "")
            if dependency_id:
                visit(dependency_id)
            if errors:
                break
        visiting.pop()
        visited.add(assignment_id)

    visit(assignment.assignment_id)
    return errors


def _dependency_rows(
    root: Path,
    assignment: AssignmentRecord,
    expected_revisions: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], bool, bool]:
    rows: list[dict[str, Any]] = []
    stale_warnings: list[str] = []
    waiting = False
    unknown = False
    for specification in assignment.dependencies:
        dependency_id = str(specification.get("assignment_id") or "")
        required_status = specification.get("required_status")
        required_review_status = specification.get("required_review_status")
        if required_status is None and required_review_status is None:
            required_status = "completed"
        try:
            dependency = load_assignment(root, dependency_id)
        except FileNotFoundError:
            current_status = "missing"
            review_status = "not_required"
            current_revision = None
            satisfied = False
            blocker = f"Dependency assignment {dependency_id!r} does not exist."
        else:
            dependency_errors = assignment_contract_errors(dependency)
            if dependency_errors:
                raise ValidationError(dependency_errors)
            current_status = dependency.status
            review_status = str(
                dependency.review.get("status")
                or ("pending" if dependency.review.get("required") else "not_required")
            )
            current_revision = assignment_result_revision(dependency)
            status_ok = required_status is None or current_status == str(required_status)
            review_ok = (
                required_review_status is None
                or review_status == str(required_review_status)
            )
            satisfied = status_ok and review_ok
            blocker_parts: list[str] = []
            if not status_ok:
                blocker_parts.append(
                    f"status {current_status!r} does not satisfy {required_status!r}"
                )
            if not review_ok:
                blocker_parts.append(
                    f"review {review_status!r} does not satisfy {required_review_status!r}"
                )
            blocker = "; ".join(blocker_parts)

        expected_revision = expected_revisions.get(dependency_id)
        if not isinstance(expected_revision, str) or not expected_revision:
            unknown = True
        elif current_revision != expected_revision:
            stale_warnings.append(
                f"Dependency {dependency_id} changed from {expected_revision} to {current_revision or 'no-result'}."
            )
        if not satisfied:
            waiting = True
        row = {
            "assignment_id": dependency_id,
            "status": current_status,
            "review_status": review_status,
            "result_revision": current_revision,
            "satisfied": satisfied,
        }
        if required_status is not None:
            row["required_status"] = str(required_status)
        if required_review_status is not None:
            row["required_review_status"] = str(required_review_status)
        if blocker:
            row["blocker"] = _text(blocker)
        rows.append(row)
    return rows, stale_warnings, waiting, unknown


def _allowed_operations(
    assignment: AssignmentRecord,
    *,
    readiness: str,
    lease_state: str,
    legacy_usable: bool,
) -> list[str]:
    if assignment.status in {"completed", "cancelled", "retired"}:
        return []
    if assignment.status == "queued":
        return ["claim"] if assignment.agent_id is None else []
    if lease_state == "expired":
        return []
    if legacy_usable:
        return ["start", "record", "close"]
    if assignment.status == "blocked":
        return ["record", "close"]
    if readiness == "ready" and lease_state in {"active", "legacy_unknown"}:
        return ["start", "record", "close"]
    return []


def _dependency_source_rows(
    root: Path,
    assignment: AssignmentRecord,
) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    pending = [assignment]
    while pending and len(rows) < _DEPENDENCY_GRAPH_LIMIT:
        current = pending.pop()
        if current.assignment_id in rows:
            continue
        rows[current.assignment_id] = current.raw
        for dependency in current.dependencies:
            dependency_id = str(dependency.get("assignment_id") or "")
            if not dependency_id or dependency_id in rows:
                continue
            try:
                pending.append(load_assignment(root, dependency_id))
            except FileNotFoundError:
                rows[dependency_id] = {"missing": True}
    return rows


def _source_revision(
    root: Path,
    assignment: AssignmentRecord,
    index: dict[str, Any] | None,
    *,
    lease_state: str,
) -> str:
    snapshot_source: dict[str, Any]
    if is_index_schema_compatible(index):
        assert index is not None
        snapshot_source = indexed_root_snapshot_source(
            root,
            node_id=assignment.current_node,
            validation_index=index,
        )
    else:
        snapshot_source = {"index_state": "missing_or_incompatible"}
    return stable_payload_revision(
        {
            "assignments": _dependency_source_rows(root, assignment),
            "lease_state": lease_state,
            "snapshot": snapshot_source,
        },
        prefix="packet-v1",
    )


def _encoded_size(packet: dict[str, Any]) -> int:
    return len(
        json.dumps(
            packet,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _fit_budget(packet: dict[str, Any]) -> None:
    collection_paths = [
        packet["success_criteria"],
        packet["cursor"]["next_actions"],
        packet["deliverables"],
        packet["compatibility_warnings"],
        packet["dependencies"],
        packet["stale_inputs"],
    ]
    while _encoded_size(packet) >= WORK_PACKET_MAX_BYTES:
        changed = False
        for collection in collection_paths:
            minimum = 1 if collection is packet["stale_inputs"] and packet["revision_status"] == "stale" else 0
            if len(collection["items"]) > minimum:
                collection["items"].pop()
                collection["omitted"] = collection["total"] - len(collection["items"])
                changed = True
                break
        if changed:
            continue
        if len(packet["objective"]) > 80:
            packet["objective"] = _text(packet["objective"], limit=80)
            continue
        raise ValueError("Work Packet cannot fit the 8 KiB output budget")


def build_work_packet(
    root: Path,
    assignment_id: str,
    *,
    since_revision: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    assignment = load_assignment(root, assignment_id)
    index = load_validation_index(root)
    errors = [
        *assignment_contract_errors(assignment),
        *_scope_errors(assignment, index),
        *_dependency_cycle_errors(root, assignment),
    ]
    if errors:
        raise ValidationError(errors)

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    lease, lease_state = _lease_projection(assignment, now=now)
    source_revision = _source_revision(
        root,
        assignment,
        index,
        lease_state=lease_state,
    )
    if since_revision and since_revision == source_revision:
        return {
            "schema_version": WORK_PACKET_SCHEMA_VERSION,
            "changed": False,
            "revision": source_revision,
            "assignment_id": assignment.assignment_id,
        }
    current_baseline_revision, runtime = _baseline_revision(root, assignment, index)
    captured_inputs = assignment.inputs
    expected_dependency_revisions = captured_inputs.get("dependency_revisions", {})
    dependency_rows, stale_warnings, waiting, dependency_unknown = _dependency_rows(
        root,
        assignment,
        expected_dependency_revisions,
    )

    compatibility_warnings: list[str] = []
    has_input_truth = "inputs" in assignment.raw
    if not has_input_truth:
        compatibility_warnings.append(
            "Legacy assignment has no captured input revision; freshness is unknown."
        )
    if not runtime["index_fresh"]:
        compatibility_warnings.append(
            f"Targeted validation index is unavailable: {runtime['fallback_reason']}."
        )
    expected_baseline_revision = captured_inputs.get("effective_baseline_revision")
    if (
        has_input_truth
        and runtime["index_fresh"]
        and expected_baseline_revision != current_baseline_revision
    ):
        stale_warnings.append(
            "Effective baseline changed from "
            f"{expected_baseline_revision or 'none'} to {current_baseline_revision or 'none'}."
        )

    unknown_inputs = (
        not has_input_truth
        or not runtime["index_fresh"]
        or dependency_unknown
    )
    if unknown_inputs:
        readiness = "unknown_inputs"
        revision_status = "unknown"
        input_revision = None
    else:
        input_revision = assignment.input_revision or stable_payload_revision(
            {
                "effective_baseline_revision": expected_baseline_revision,
                "dependency_revisions": expected_dependency_revisions,
            },
            prefix="input-v1",
        )
        if stale_warnings:
            readiness = "stale_inputs"
            revision_status = "stale"
        elif waiting:
            readiness = "waiting_dependencies"
            revision_status = "fresh"
        else:
            readiness = "ready"
            revision_status = "fresh"

    if lease_state == "legacy_unknown":
        compatibility_warnings.append(
            "Legacy active assignment has no lease and remains usable until explicitly migrated."
        )
    legacy_usable = (
        "inputs" not in assignment.raw
        and "lease" not in assignment.raw
        and assignment.status in {"active", "blocked"}
    )
    allowed_operations = _allowed_operations(
        assignment,
        readiness=readiness,
        lease_state=lease_state,
        legacy_usable=legacy_usable,
    )

    packet: dict[str, Any] = {
        "schema_version": WORK_PACKET_SCHEMA_VERSION,
        "revision": source_revision,
        "revision_status": revision_status,
        "input_revision": input_revision,
        "assignment_id": assignment.assignment_id,
        "agent_id": assignment.agent_id,
        "kind": assignment.kind,
        "status": assignment.status,
        "readiness": readiness,
        "objective": _text(assignment.objective or assignment.assignment_id, limit=400),
        "scope": dict(assignment.scope),
        "dependencies": _bounded(dependency_rows, item_limit=10),
        "inputs": {
            "effective_baseline_revision": expected_baseline_revision,
            "dependency_revisions": {
                str(key): str(value)
                for key, value in expected_dependency_revisions.items()
                if isinstance(value, str) and value
            },
        },
        "stale_inputs": _bounded([_text(item) for item in stale_warnings], item_limit=10),
        "success_criteria": _bounded(
            [_text(item) for item in assignment.success_criteria if _text(item)],
            item_limit=8,
        ),
        "deliverables": _bounded(
            [_text(item) for item in assignment.deliverables if _text(item)],
            item_limit=10,
        ),
        "lease": lease,
        "review": _review_projection(assignment),
        "allowed_operations": _bounded(allowed_operations, item_limit=10),
        "cursor": {
            "current_node": assignment.current_node or None,
            "next_actions": _bounded(
                [_text(item) for item in assignment.next_actions if _text(item)],
                item_limit=5,
            ),
        },
        "compatibility_warnings": _bounded(
            compatibility_warnings,
            item_limit=5,
        ),
        "runtime": runtime,
        "changed": True,
    }
    _fit_budget(packet)
    parse_public_contract(packet)
    if _source_revision(root, assignment, index, lease_state=lease_state) != source_revision:
        raise ValidationError([
            f"{assignment.assignment_id}: Work Packet sources changed during projection; retry work open"
        ])
    return packet
