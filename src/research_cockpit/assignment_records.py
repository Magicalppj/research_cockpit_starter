from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any

from research_cockpit.assignment_leases import (
    DEFAULT_LEASE_SECONDS,
    AssignmentLeaseError,
    plan_assignment_lease_renewal,
)
from research_cockpit.commands.ingest_artifact import ingest_artifact
from research_cockpit.mutation_lock import MutationError
from research_cockpit.operation_receipts import (
    OperationIdConflict,
    error_receipt,
    normalized_request_hash,
    replay_or_conflict,
    success_receipt,
    validate_operation_id,
)
from research_cockpit.work_packets import build_work_packet_for_assignment


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(timezone.utc)


def _scope(assignment_id: str) -> str:
    return f"assignment:{assignment_id}"


def _source_digest(source: Path) -> str:
    root = source.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Evidence source is not a directory: {source}")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError(f"Evidence source must not contain symbolic links: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return f"evidence-v1:{digest.hexdigest()}"


def _record_error(
    *,
    assignment_id: str,
    operation_id: str,
    code: str,
    message: str,
    lease_id: str | None = None,
    conflict_files: list[str] | None = None,
) -> AssignmentLeaseError:
    return AssignmentLeaseError(
        error_receipt(
            operation="work record",
            assignment_id=assignment_id,
            operation_id=operation_id,
            code=code,
            message=message,
            lease_id=lease_id,
            conflict_files=conflict_files,
            retry_command=(
                f"research-cockpit work open --root <data-root> "
                f"--assignment {assignment_id} --compact --json"
            ),
        )
    )


def record_assignment_evidence(
    root: Path,
    *,
    assignment_id: str,
    agent_id: str,
    lease_id: str,
    lease_epoch: int,
    operation_id: str,
    run_id: str,
    source_dir: Path,
    node_id: str | None = None,
    record_id: str | None = None,
    title: str | None = None,
    summary: str = "",
    links: dict[str, str] | None = None,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict[str, Any]:
    operation_id = validate_operation_id(operation_id)
    normalized_links = {str(key): str(value) for key, value in (links or {}).items()}
    source_hash = _source_digest(source_dir)
    request_hash = normalized_request_hash(
        {
            "operation": "work record",
            "assignment_id": assignment_id,
            "agent_id": agent_id,
            "lease_id": lease_id,
            "lease_epoch": lease_epoch,
            "run_id": run_id,
            "node_id": node_id,
            "record_id": record_id,
            "title": title,
            "summary": summary,
            "links": normalized_links,
            "source_hash": source_hash,
            "lease_seconds": lease_seconds,
        }
    )
    try:
        replay = replay_or_conflict(
            root,
            scope=_scope(assignment_id),
            operation_id=operation_id,
            request_hash=request_hash,
            operation="work record",
            assignment_id=assignment_id,
        )
    except OperationIdConflict as exc:
        raise AssignmentLeaseError(exc.receipt) from exc
    if replay is not None:
        return replay

    current = _now(now)
    lease_changes, candidate_assignment, _before, _after = plan_assignment_lease_renewal(
        root,
        assignment_id=assignment_id,
        agent_id=agent_id,
        lease_id=lease_id,
        lease_epoch=lease_epoch,
        operation="work record",
        operation_id=operation_id,
        now=current,
        lease_seconds=lease_seconds,
    )
    packet = build_work_packet_for_assignment(root, candidate_assignment, now=current)
    allowed = {str(item) for item in packet.get("allowed_operations", {}).get("items", [])}
    if "record" not in allowed:
        raise _record_error(
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="assignment_not_ready",
            message=f"Assignment readiness {packet.get('readiness')!r} does not allow work record.",
            lease_id=lease_id,
        )

    target_node = str(node_id or candidate_assignment.current_node)
    operation_record_key = hashlib.sha256(
        f"{assignment_id}\0{operation_id}".encode("utf-8")
    ).hexdigest()[:20]
    generated_record_id = record_id or f"record_{operation_record_key}"
    receipt = success_receipt(
        operation="work record",
        assignment_id=assignment_id,
        operation_id=operation_id,
        changed=True,
        packet_revision=str(packet["revision"]),
        allowed_operations=["record", "close"],
    )
    receipt["entities"] = {
        "record_id": generated_record_id,
        "run_id": run_id,
        "node_id": target_node,
    }
    operation_request = {
        "scope": _scope(assignment_id),
        "operation_id": operation_id,
        "request_hash": request_hash,
        "receipt": receipt,
        "operation": "work record",
        "assignment_id": assignment_id,
    }
    interaction = {
        "kind": "assignment_evidence_recorded",
        "actor": agent_id,
        "node_id": target_node,
        "command": f"research-cockpit work record --assignment {assignment_id}",
        "after": {
            "assignment_id": assignment_id,
            "record_id": generated_record_id,
            "run_id": run_id,
            "lease_epoch": lease_epoch,
            "source_hash": source_hash,
        },
    }
    try:
        result = ingest_artifact(
            root,
            node_id=target_node,
            source_dir=source_dir,
            source_content_hash=source_hash,
            run_id=run_id,
            artifact_id=generated_record_id,
            title=title,
            summary=summary,
            agent_id=agent_id,
            links=normalized_links,
            rebuild_dashboard=False,
            assignment_id=assignment_id,
            record_only=True,
            additional_yaml_changes=lease_changes,
            interaction_override=interaction,
            operation_request=operation_request,
        )
    except MutationError as exc:
        stored = exc.payload.get("operation_receipt")
        if isinstance(stored, dict):
            raise AssignmentLeaseError(stored) from exc
        raise _record_error(
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="conflict",
            message=str(exc),
            lease_id=lease_id,
            conflict_files=[str(item) for item in exc.payload.get("conflict_files", [])],
        ) from exc
    transaction = result.get("_operation_transaction", {})
    replayed = transaction.get("operation_receipt")
    if transaction.get("replayed") and isinstance(replayed, dict):
        return deepcopy(replayed)
    return receipt
