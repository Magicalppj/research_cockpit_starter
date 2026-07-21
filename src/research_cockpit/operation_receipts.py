from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from research_cockpit.interaction_log import iter_interaction_events
from research_cockpit.mutation_lock import mutation_lock
from research_cockpit.public_contracts import parse_public_contract
from research_cockpit.storage import save_text


OPERATION_INDEX_SCHEMA_VERSION = "operation_index_v1"
OPERATION_COLLECTION_LIMIT = 20
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def normalized_request_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        _jsonable(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"request-v1:{hashlib.sha256(encoded).hexdigest()}"


def validate_operation_id(operation_id: str) -> str:
    value = str(operation_id or "").strip()
    if not _OPERATION_ID_RE.fullmatch(value):
        raise ValueError(
            "operation_id must be 1-128 characters using letters, numbers, _, -, ., or :"
        )
    return value


def bounded(values: list[Any], *, limit: int = OPERATION_COLLECTION_LIMIT) -> dict[str, Any]:
    items = list(values[:limit])
    return {
        "items": items,
        "limit": limit,
        "total": len(values),
        "omitted": max(0, len(values) - len(items)),
    }


def success_receipt(
    *,
    operation: str,
    assignment_id: str | None,
    operation_id: str,
    changed: bool,
    packet_revision: str | None,
    readiness: str = "not_applicable",
    allowed_operations: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    receipt = {
        "schema_version": "work_operation_v1",
        "ok": True,
        "operation": operation,
        "assignment_id": assignment_id,
        "operation_id": validate_operation_id(operation_id),
        "changed": bool(changed),
        "packet_revision": packet_revision,
        "readiness": readiness,
        "required_action": {"kind": "none", "command": None, "reason": None},
        "allowed_operations": bounded(allowed_operations or []),
        "verification": {
            "status": "internally_verified",
            "additional_verification_required": False,
            "commands": bounded([]),
        },
        "warnings": bounded(warnings or []),
        "error": None,
        "partial_success": False,
        "rolled_back": False,
    }
    parse_public_contract(receipt)
    return receipt


def error_receipt(
    *,
    operation: str,
    assignment_id: str | None,
    operation_id: str,
    code: str,
    message: str,
    lease_id: str | None = None,
    input_revision: str | None = None,
    latest_packet_revision: str | None = None,
    conflict_files: list[str] | None = None,
    dependency_blockers: list[str] | None = None,
    retry_kind: str = "reopen_packet",
    retry_command: str | None = None,
    retry_reason: str | None = None,
    rolled_back: bool = False,
    partial_success: bool = False,
) -> dict[str, Any]:
    if retry_kind != "none" and retry_command is None:
        retry_command = (
            f"research-cockpit work open --assignment {assignment_id} --compact --json"
            if assignment_id
            else "research-cockpit commands --role coordinator --compact --json"
        )
    receipt = {
        "schema_version": "work_operation_v1",
        "ok": False,
        "operation": operation,
        "assignment_id": assignment_id,
        "operation_id": validate_operation_id(operation_id),
        "changed": False,
        "packet_revision": latest_packet_revision,
        "readiness": "not_applicable",
        "required_action": {
            "kind": retry_kind,
            "command": retry_command,
            "reason": retry_reason or message,
        },
        "allowed_operations": bounded([]),
        "verification": {
            "status": "internally_verified",
            "additional_verification_required": False,
            "commands": bounded([]),
        },
        "warnings": bounded([]),
        "error": {
            "code": code,
            "message": message,
            "context": {
                "assignment_id": assignment_id,
                "lease_id": lease_id,
                "input_revision": input_revision,
                "latest_packet_revision": latest_packet_revision,
            },
            "conflict_files": bounded(conflict_files or []),
            "dependency_blockers": bounded(dependency_blockers or []),
            "retry_action": {
                "kind": retry_kind,
                "command": retry_command,
                "reason": retry_reason or message,
            },
        },
        "partial_success": bool(partial_success),
        "rolled_back": bool(rolled_back),
    }
    parse_public_contract(receipt)
    return receipt


def operation_index_path(root: Path) -> Path:
    return root / "dashboards" / "operation_index.json"


def operation_source_signature(root: Path) -> str:
    graph = root / "graph"
    candidates = [graph / "interaction_log.yaml", graph / "interaction_events" / "manifest.json"]
    event_root = graph / "interaction_events"
    if event_root.exists():
        candidates.extend(sorted(event_root.rglob("*.jsonl")))
    rows: list[tuple[str, int, int]] = []
    for path in candidates:
        if not path.exists():
            continue
        stat = path.stat()
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = path.as_posix()
        rows.append((relative, stat.st_size, stat.st_mtime_ns))
    return normalized_request_hash({"files": rows}).replace("request-v1:", "events-v1:")


def _index_entry(event: dict[str, Any]) -> dict[str, Any] | None:
    operation_id = event.get("operation_id")
    scope = event.get("operation_scope")
    request_hash = event.get("operation_request_hash")
    receipt = event.get("operation_receipt")
    if not all(isinstance(value, str) and value for value in (operation_id, scope, request_hash)):
        return None
    if not isinstance(receipt, dict):
        return None
    return {
        "scope": scope,
        "operation_id": operation_id,
        "request_hash": request_hash,
        "event_id": str(event.get("id") or ""),
        "receipt": deepcopy(receipt),
    }


def _scan_operations(root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    operations: dict[str, dict[str, dict[str, Any]]] = {}
    for event in iter_interaction_events(root, strict=True):
        entry = _index_entry(event)
        if entry is None:
            continue
        scope_rows = operations.setdefault(entry["scope"], {})
        scope_rows[entry["operation_id"]] = {
            key: value for key, value in entry.items() if key != "scope"
        }
    return operations


def _load_index(root: Path) -> dict[str, Any] | None:
    path = operation_index_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != OPERATION_INDEX_SCHEMA_VERSION:
        return None
    if not isinstance(data.get("operations"), dict):
        return None
    return data


def find_operation_receipt(
    root: Path,
    *,
    scope: str,
    operation_id: str,
) -> dict[str, Any] | None:
    operation_id = validate_operation_id(operation_id)
    index = _load_index(root)
    if index is not None and index.get("source_signature") == operation_source_signature(root):
        entry = (index.get("operations", {}).get(scope, {}) or {}).get(operation_id)
        return deepcopy(entry) if isinstance(entry, dict) else None

    rebuilt = rebuild_operation_index(root)
    if rebuilt.get("source_signature") == operation_source_signature(root):
        entry = (rebuilt.get("operations", {}).get(scope, {}) or {}).get(operation_id)
        return deepcopy(entry) if isinstance(entry, dict) else None

    events = list(iter_interaction_events(root, strict=True))
    for event in reversed(events):
        entry = _index_entry(event)
        if entry and entry["scope"] == scope and entry["operation_id"] == operation_id:
            return {key: value for key, value in entry.items() if key != "scope"}
    return None


def _save_index(root: Path, payload: dict[str, Any]) -> None:
    save_text(
        operation_index_path(root),
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _rebuild_operation_index_unlocked(root: Path) -> dict[str, Any]:
    operations: dict[str, dict[str, dict[str, Any]]] = {}
    for _attempt in range(2):
        before = operation_source_signature(root)
        operations = _scan_operations(root)
        after = operation_source_signature(root)
        if before == after:
            payload = {
                "schema_version": OPERATION_INDEX_SCHEMA_VERSION,
                "source_signature": after,
                "operations": operations,
            }
            _save_index(root, payload)
            return payload
    return {
        "schema_version": OPERATION_INDEX_SCHEMA_VERSION,
        "source_signature": None,
        "operations": operations,
    }


def rebuild_operation_index(root: Path) -> dict[str, Any]:
    with mutation_lock(root, lock_name=".operation-index.lock"):
        return _rebuild_operation_index_unlocked(root)


def patch_operation_index(
    root: Path,
    *,
    event: dict[str, Any],
    source_signature_before: str,
    source_signature_after: str,
) -> dict[str, Any]:
    entry = _index_entry(event)
    if entry is None:
        raise ValueError("operation event does not contain a complete receipt index entry")
    with mutation_lock(root, lock_name=".operation-index.lock"):
        current_signature = operation_source_signature(root)
        index = _load_index(root)
        if index is not None and index.get("source_signature") == current_signature:
            return index
        if (
            index is None
            or index.get("source_signature") != source_signature_before
            or current_signature != source_signature_after
        ):
            return _rebuild_operation_index_unlocked(root)

        operations = deepcopy(index["operations"])
        scope_rows = operations.setdefault(entry["scope"], {})
        scope_rows[entry["operation_id"]] = {
            key: value for key, value in entry.items() if key != "scope"
        }
        payload = {
            "schema_version": OPERATION_INDEX_SCHEMA_VERSION,
            "source_signature": source_signature_after,
            "operations": operations,
        }
        _save_index(root, payload)
        return payload


def replay_or_conflict(
    root: Path,
    *,
    scope: str,
    operation_id: str,
    request_hash: str,
    operation: str,
    assignment_id: str | None,
) -> dict[str, Any] | None:
    existing = find_operation_receipt(
        root,
        scope=scope,
        operation_id=operation_id,
    )
    if existing is None:
        return None
    if existing.get("request_hash") == request_hash:
        receipt = existing.get("receipt")
        if isinstance(receipt, dict):
            return deepcopy(receipt)
    raise OperationIdConflict(
        error_receipt(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="idempotency_conflict",
            message="operation_id was already used with a different normalized request payload",
            retry_kind="manual_recovery",
            retry_reason="Choose a new operation_id for the changed request.",
        )
    )


class OperationIdConflict(ValueError):
    def __init__(self, receipt: dict[str, Any]) -> None:
        super().__init__(str(receipt["error"]["message"]))
        self.receipt = receipt
