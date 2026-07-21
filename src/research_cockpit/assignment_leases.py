from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from research_cockpit.agent_state import (
    AgentRecord,
    AssignmentRecord,
    assignment_contract_errors,
    load_agents,
    load_assignment,
    load_assignments,
)
from research_cockpit.graph_core import GraphTopology
from research_cockpit.model import load_nodes, load_runs
from research_cockpit.mutation_lock import MutationError
from research_cockpit.mutation_runtime import execute_mutation_transaction
from research_cockpit.operation_receipts import (
    OperationIdConflict,
    error_receipt,
    normalized_request_hash,
    replay_or_conflict,
    success_receipt,
    validate_operation_id,
)
from research_cockpit.run_summaries import ACTIVE_RUN_STATUSES
from research_cockpit.runtime_ids import generate_runtime_id
from research_cockpit.types import ValidationError
from research_cockpit.work_packets import build_work_packet_for_assignment


DEFAULT_LEASE_SECONDS = 900
DEFAULT_HEARTBEAT_SECONDS = 300
_ACTIVE_ASSIGNMENT_STATUSES = {"active", "blocked"}


class AssignmentLeaseError(ValueError):
    def __init__(self, receipt: dict[str, Any]) -> None:
        message = receipt.get("error", {}).get("message") or "assignment lease operation failed"
        super().__init__(str(message))
        self.receipt = receipt


def _utc_now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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


def _assignment_path(root: Path, assignment_id: str) -> Path:
    return root / "assignments" / f"{assignment_id}.yaml"


def _agent_path(root: Path, agent_id: str) -> Path:
    return root / "agents" / f"{agent_id}.yaml"


def _scope(assignment_id: str) -> str:
    return f"assignment:{assignment_id}"


def _reopen_command(assignment_id: str) -> str:
    return (
        f"research-cockpit work open --root <data-root> --assignment {assignment_id} "
        "--compact --json"
    )


def _fail(
    *,
    operation: str,
    assignment_id: str,
    operation_id: str,
    code: str,
    message: str,
    lease_id: str | None = None,
    conflict_files: list[str] | None = None,
    retry_kind: str = "reopen_packet",
) -> AssignmentLeaseError:
    return AssignmentLeaseError(
        error_receipt(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            code=code,
            message=message,
            lease_id=lease_id,
            conflict_files=conflict_files,
            retry_kind=retry_kind,
            retry_command=_reopen_command(assignment_id) if retry_kind == "reopen_packet"
            else None,
        )
    )


def _existing_replay(
    root: Path,
    *,
    operation: str,
    assignment_id: str,
    operation_id: str,
    request_hash: str,
) -> dict[str, Any] | None:
    try:
        return replay_or_conflict(
            root,
            scope=_scope(assignment_id),
            operation_id=operation_id,
            request_hash=request_hash,
            operation=operation,
            assignment_id=assignment_id,
        )
    except OperationIdConflict as exc:
        raise AssignmentLeaseError(exc.receipt) from exc


def _lease_epoch_counter(assignment: AssignmentRecord) -> int:
    raw_counter = assignment.raw.get("lease_epoch_counter", 0)
    if isinstance(raw_counter, bool) or not isinstance(raw_counter, int) or raw_counter < 0:
        raise ValidationError([
            f"{assignment.assignment_id}: lease_epoch_counter must be an integer >= 0"
        ])
    current = assignment.lease.get("lease_epoch", 0)
    if isinstance(current, bool) or not isinstance(current, int):
        current = 0
    return max(raw_counter, current)


def _lease_is_expired(assignment: AssignmentRecord, *, now: datetime) -> bool:
    expires_at = _parse_timestamp(assignment.lease.get("expires_at"))
    return bool(assignment.lease.get("lease_id") and expires_at and expires_at <= now)


def _agent_has_recent_heartbeat(
    agent: AgentRecord | None,
    *,
    now: datetime,
) -> bool:
    if agent is None:
        return False
    last_seen = _parse_timestamp(agent.last_seen_at)
    return bool(last_seen and now - last_seen <= timedelta(seconds=DEFAULT_HEARTBEAT_SECONDS))


def _assignment_subtree_ids(
    root: Path,
    assignment: AssignmentRecord,
    *,
    topology: GraphTopology | None = None,
) -> set[str]:
    topology = topology or GraphTopology.from_nodes(load_nodes(root))
    root_id = str(assignment.scope.get("root_node") or assignment.root_node)
    if root_id not in topology.parent_by_node:
        return {root_id}
    return {root_id, *topology.descendant_ids(root_id)}


def _has_active_run(root: Path, assignment: AssignmentRecord) -> bool:
    subtree = _assignment_subtree_ids(root, assignment)
    for run in load_runs(root).values():
        if run.status not in ACTIVE_RUN_STATUSES or run.finished_at:
            continue
        run_assignment = str(run.raw.get("assignment_id") or "")
        if run_assignment:
            if run_assignment == assignment.assignment_id:
                return True
            continue
        if run.experiment_id in subtree:
            return True
    return False


def _assert_no_scope_conflict(
    root: Path,
    candidate: AssignmentRecord,
    assignments: dict[str, AssignmentRecord],
    *,
    operation_id: str,
) -> None:
    candidate_policy = str(candidate.scope.get("write_policy") or "exclusive")
    if candidate_policy in {"review_read_only", "coordinator"}:
        return
    topology = GraphTopology.from_nodes(load_nodes(root))
    candidate_subtree = _assignment_subtree_ids(
        root, candidate, topology=topology
    )
    for other in assignments.values():
        if other.assignment_id == candidate.assignment_id:
            continue
        if other.status not in _ACTIVE_ASSIGNMENT_STATUSES or not other.agent_id:
            continue
        other_policy = str(other.scope.get("write_policy") or "exclusive")
        if other_policy in {"review_read_only", "coordinator"}:
            continue
        if candidate_policy == other_policy == "append_only":
            continue
        if candidate_subtree & _assignment_subtree_ids(root, other, topology=topology):
            raise _fail(
                operation="work claim",
                assignment_id=candidate.assignment_id,
                operation_id=operation_id,
                code="assignment_scope_conflict",
                message=(
                    f"Assignment scope overlaps active writer {other.assignment_id} "
                    f"({other_policy})."
                ),
            )


def _agent_after(
    agent: AgentRecord,
    *,
    assignment_id: str,
    now: datetime,
    add: bool,
) -> dict[str, Any]:
    data = deepcopy(agent.raw)
    active = [item for item in agent.active_assignment_ids if item != assignment_id]
    if add:
        active.append(assignment_id)
    data.update(
        {
            "agent_id": agent.agent_id,
            "status": "active" if active else "idle",
            "last_seen_at": _timestamp(now),
            "active_assignment_ids": sorted(set(active)),
        }
    )
    return data


def _receipt_from_packet(
    *,
    operation: str,
    operation_id: str,
    packet: dict[str, Any],
) -> dict[str, Any]:
    allowed = packet.get("allowed_operations", {}).get("items", [])
    receipt = success_receipt(
        operation=operation,
        assignment_id=str(packet["assignment_id"]),
        operation_id=operation_id,
        changed=True,
        packet_revision=str(packet["revision"]),
        readiness=str(packet.get("readiness") or "not_applicable"),
        allowed_operations=[str(item) for item in allowed],
    )
    receipt["packet"] = packet
    return receipt


def _stored_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(receipt)


def _transaction_result(
    root: Path,
    *,
    changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]],
    interaction: dict[str, Any],
    operation: str,
    assignment_id: str,
    operation_id: str,
    request_hash: str,
    receipt: dict[str, Any],
    commit_validators: list[Callable[[], None]] | None = None,
) -> dict[str, Any]:
    try:
        result = execute_mutation_transaction(
            root,
            changes,
            interactions=[interaction],
            rebuild_dashboard=False,
            operation_request={
                "scope": _scope(assignment_id),
                "operation_id": operation_id,
                "request_hash": request_hash,
                "receipt": _stored_receipt(receipt),
                "operation": operation,
                "assignment_id": assignment_id,
            },
            commit_validators=commit_validators,
        )
    except MutationError as exc:
        operation_receipt = exc.payload.get("operation_receipt")
        if isinstance(operation_receipt, dict):
            raise AssignmentLeaseError(operation_receipt) from exc
        conflict_files = [str(item) for item in exc.payload.get("conflict_files", [])]
        raise _fail(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="conflict",
            message=str(exc),
            conflict_files=conflict_files,
        ) from exc
    replayed = result.get("operation_receipt")
    if result.get("replayed") and isinstance(replayed, dict):
        return deepcopy(replayed)
    return receipt


def claim_assignment(
    root: Path,
    *,
    assignment_id: str,
    agent_id: str,
    operation_id: str,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    coordinator: bool = False,
    reassign: bool = False,
) -> dict[str, Any]:
    operation = "work claim"
    operation_id = validate_operation_id(operation_id)
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    request_hash = normalized_request_hash(
        {
            "operation": operation,
            "assignment_id": assignment_id,
            "agent_id": agent_id,
            "lease_seconds": lease_seconds,
            "coordinator": coordinator,
            "reassign": reassign,
        }
    )
    replay = _existing_replay(
        root,
        operation=operation,
        assignment_id=assignment_id,
        operation_id=operation_id,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay

    now = _utc_now(now)
    assignment = load_assignment(root, assignment_id)
    errors = assignment_contract_errors(assignment)
    if errors:
        raise ValidationError(errors)
    agents = load_agents(root)
    agent = agents.get(agent_id)
    if agent is None:
        raise _fail(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="agent_not_found",
            message=f"Agent does not exist: {agent_id}",
        )

    unclaimed = assignment.status == "queued" and assignment.agent_id is None
    expired = _lease_is_expired(assignment, now=now)
    if not unclaimed:
        if not (expired and reassign and coordinator):
            raise _fail(
                operation=operation,
                assignment_id=assignment_id,
                operation_id=operation_id,
                code="assignment_not_claimable",
                message=(
                    f"Assignment {assignment_id} is not queued/unclaimed; expired leases require "
                    "explicit coordinator reassignment."
                ),
                lease_id=assignment.lease.get("lease_id"),
            )
        previous_agent = agents.get(str(assignment.agent_id or ""))
        if _has_active_run(root, assignment):
            raise _fail(
                operation=operation,
                assignment_id=assignment_id,
                operation_id=operation_id,
                code="active_run_blocks_reassignment",
                message=f"Assignment {assignment_id} still has an active run.",
                lease_id=assignment.lease.get("lease_id"),
            )
        if _agent_has_recent_heartbeat(previous_agent, now=now):
            raise _fail(
                operation=operation,
                assignment_id=assignment_id,
                operation_id=operation_id,
                code="active_heartbeat_blocks_reassignment",
                message=f"Assignment {assignment_id} owner still has a recent heartbeat.",
                lease_id=assignment.lease.get("lease_id"),
            )
    elif reassign:
        raise _fail(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="reassignment_not_required",
            message=f"Assignment {assignment_id} is already unclaimed.",
        )

    next_epoch = _lease_epoch_counter(assignment) + 1
    lease_id = generate_runtime_id("lease", scope_hint=assignment_id)
    timestamp = _timestamp(now)
    assignment_after = assignment.to_dict(
        agent_id=agent_id,
        status="active",
        lease={
            "lease_id": lease_id,
            "owner_agent_id": agent_id,
            "lease_epoch": next_epoch,
            "heartbeat_at": timestamp,
            "expires_at": _timestamp(now + timedelta(seconds=lease_seconds)),
        },
        lease_epoch_counter=next_epoch,
        updated_at=timestamp,
    )
    candidate = AssignmentRecord.from_dict(assignment_after)
    contract_errors = assignment_contract_errors(candidate)
    if contract_errors:
        raise ValidationError(contract_errors)
    _assert_no_scope_conflict(
        root,
        candidate,
        load_assignments(root),
        operation_id=operation_id,
    )

    changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]] = [
        (_assignment_path(root, assignment_id), assignment.raw, assignment_after),
        (_agent_path(root, agent_id), agent.raw, _agent_after(agent, assignment_id=assignment_id, now=now, add=True)),
    ]
    if assignment.agent_id and assignment.agent_id != agent_id:
        previous = agents.get(assignment.agent_id)
        if previous is not None:
            changes.append(
                (
                    _agent_path(root, previous.agent_id),
                    previous.raw,
                    _agent_after(previous, assignment_id=assignment_id, now=now, add=False),
                )
            )
    packet = build_work_packet_for_assignment(root, candidate, now=now)
    receipt = _receipt_from_packet(operation=operation, operation_id=operation_id, packet=packet)
    return _transaction_result(
        root,
        changes=changes,
        interaction={
            "kind": "assignment_claimed",
            "actor": agent_id,
            "node_id": assignment.current_node,
            "command": f"research-cockpit work claim --assignment {assignment_id}",
            "before": {"agent_id": assignment.agent_id, "status": assignment.status, "lease": assignment.lease},
            "after": {"agent_id": agent_id, "status": "active", "lease": assignment_after["lease"]},
        },
        operation=operation,
        assignment_id=assignment_id,
        operation_id=operation_id,
        request_hash=request_hash,
        receipt=receipt,
        commit_validators=[
            lambda: _assert_no_scope_conflict(
                root,
                candidate,
                load_assignments(root),
                operation_id=operation_id,
            )
        ],
    )


def _assert_lease(
    assignment: AssignmentRecord,
    *,
    agent_id: str,
    lease_id: str,
    lease_epoch: int,
    operation: str,
    operation_id: str,
) -> None:
    lease = assignment.lease
    if (
        assignment.agent_id != agent_id
        or lease.get("owner_agent_id") != agent_id
        or lease.get("lease_id") != lease_id
        or lease.get("lease_epoch") != lease_epoch
    ):
        raise _fail(
            operation=operation,
            assignment_id=assignment.assignment_id,
            operation_id=operation_id,
            code="lease_mismatch",
            message="Assignment owner, lease id, or lease epoch does not match the current lease.",
            lease_id=lease_id,
        )
    if assignment.status not in _ACTIVE_ASSIGNMENT_STATUSES:
        raise _fail(
            operation=operation,
            assignment_id=assignment.assignment_id,
            operation_id=operation_id,
            code="assignment_not_mutable",
            message=f"Assignment status {assignment.status!r} does not allow lease mutation.",
            lease_id=lease_id,
        )


def plan_assignment_lease_renewal(
    root: Path,
    *,
    assignment_id: str,
    agent_id: str,
    lease_id: str,
    lease_epoch: int,
    operation: str,
    operation_id: str,
    now: datetime,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    allow_expired: bool = False,
) -> tuple[
    list[tuple[Path, dict[str, Any] | None, dict[str, Any]]],
    AssignmentRecord,
    dict[str, Any],
    dict[str, Any],
]:
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    current = _utc_now(now)
    assignment = load_assignment(root, assignment_id)
    _assert_lease(
        assignment,
        agent_id=agent_id,
        lease_id=lease_id,
        lease_epoch=lease_epoch,
        operation=operation,
        operation_id=operation_id,
    )
    expires_at = _parse_timestamp(assignment.lease.get("expires_at"))
    if expires_at is None:
        raise ValidationError([f"{assignment_id}: lease.expires_at is invalid"])
    if expires_at <= current and not allow_expired:
        raise _fail(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="lease_expired",
            message=(
                "Assignment lease expired; reopen the packet and recover or reassign it first."
            ),
            lease_id=lease_id,
        )
    agents = load_agents(root)
    agent = agents.get(agent_id)
    if agent is None:
        raise _fail(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="agent_not_found",
            message=f"Agent does not exist: {agent_id}",
            lease_id=lease_id,
        )
    timestamp = _timestamp(current)
    lease_before = deepcopy(assignment.lease)
    lease_after = deepcopy(lease_before)
    lease_after.update(
        {
            "heartbeat_at": timestamp,
            "expires_at": _timestamp(current + timedelta(seconds=lease_seconds)),
        }
    )
    assignment_after = assignment.to_dict(lease=lease_after, updated_at=timestamp)
    candidate = AssignmentRecord.from_dict(assignment_after)
    errors = assignment_contract_errors(candidate)
    if errors:
        raise ValidationError(errors)
    changes = [
        (_assignment_path(root, assignment_id), assignment.raw, assignment_after),
        (
            _agent_path(root, agent_id),
            agent.raw,
            _agent_after(agent, assignment_id=assignment_id, now=current, add=True),
        ),
    ]
    return changes, candidate, lease_before, lease_after


def renew_assignment(
    root: Path,
    *,
    assignment_id: str,
    agent_id: str,
    lease_id: str,
    lease_epoch: int,
    operation_id: str,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict[str, Any]:
    operation = "work renew"
    operation_id = validate_operation_id(operation_id)
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    request_hash = normalized_request_hash(
        {
            "operation": operation,
            "assignment_id": assignment_id,
            "agent_id": agent_id,
            "lease_id": lease_id,
            "lease_epoch": lease_epoch,
            "lease_seconds": lease_seconds,
        }
    )
    replay = _existing_replay(
        root,
        operation=operation,
        assignment_id=assignment_id,
        operation_id=operation_id,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay

    now = _utc_now(now)
    changes, candidate, lease_before, lease_after = plan_assignment_lease_renewal(
        root,
        assignment_id=assignment_id,
        agent_id=agent_id,
        lease_id=lease_id,
        lease_epoch=lease_epoch,
        operation=operation,
        operation_id=operation_id,
        now=now,
        lease_seconds=lease_seconds,
        allow_expired=True,
    )
    packet = build_work_packet_for_assignment(root, candidate, now=now)
    receipt = _receipt_from_packet(operation=operation, operation_id=operation_id, packet=packet)
    return _transaction_result(
        root,
        changes=changes,
        interaction={
            "kind": "assignment_lease_renewed",
            "actor": agent_id,
            "node_id": candidate.current_node,
            "command": f"research-cockpit work renew --assignment {assignment_id}",
            "before": {"lease": lease_before},
            "after": {"lease": lease_after},
        },
        operation=operation,
        assignment_id=assignment_id,
        operation_id=operation_id,
        request_hash=request_hash,
        receipt=receipt,
    )


def heartbeat_assignment_lease(
    root: Path,
    *,
    assignment_id: str,
    agent_id: str,
    lease_id: str,
    lease_epoch: int,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict[str, Any]:
    current = _utc_now(now)
    operation_id = (
        f"heartbeat:{lease_id}:{lease_epoch}:"
        f"{int(current.timestamp()) // DEFAULT_HEARTBEAT_SECONDS}"
    )
    return renew_assignment(
        root,
        assignment_id=assignment_id,
        agent_id=agent_id,
        lease_id=lease_id,
        lease_epoch=lease_epoch,
        operation_id=operation_id,
        now=current,
        lease_seconds=lease_seconds,
    )


def release_assignment(
    root: Path,
    *,
    assignment_id: str,
    agent_id: str,
    lease_id: str,
    lease_epoch: int,
    operation_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    operation = "work release"
    operation_id = validate_operation_id(operation_id)
    request_hash = normalized_request_hash(
        {
            "operation": operation,
            "assignment_id": assignment_id,
            "agent_id": agent_id,
            "lease_id": lease_id,
            "lease_epoch": lease_epoch,
        }
    )
    replay = _existing_replay(
        root,
        operation=operation,
        assignment_id=assignment_id,
        operation_id=operation_id,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay

    now = _utc_now(now)
    assignment = load_assignment(root, assignment_id)
    _assert_lease(
        assignment,
        agent_id=agent_id,
        lease_id=lease_id,
        lease_epoch=lease_epoch,
        operation=operation,
        operation_id=operation_id,
    )
    if _has_active_run(root, assignment):
        raise _fail(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="active_run_blocks_release",
            message=f"Assignment {assignment_id} still has an active run.",
            lease_id=lease_id,
        )
    agents = load_agents(root)
    agent = agents.get(agent_id)
    if agent is None:
        raise _fail(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="agent_not_found",
            message=f"Agent does not exist: {agent_id}",
            lease_id=lease_id,
        )
    timestamp = _timestamp(now)
    assignment_after = assignment.to_dict(
        agent_id=None,
        status="queued",
        lease={
            "lease_id": None,
            "owner_agent_id": None,
            "lease_epoch": 0,
            "heartbeat_at": None,
            "expires_at": None,
        },
        lease_epoch_counter=max(_lease_epoch_counter(assignment), lease_epoch),
        updated_at=timestamp,
    )
    candidate = AssignmentRecord.from_dict(assignment_after)
    packet = build_work_packet_for_assignment(root, candidate, now=now)
    receipt = _receipt_from_packet(operation=operation, operation_id=operation_id, packet=packet)
    return _transaction_result(
        root,
        changes=[
            (_assignment_path(root, assignment_id), assignment.raw, assignment_after),
            (_agent_path(root, agent_id), agent.raw, _agent_after(agent, assignment_id=assignment_id, now=now, add=False)),
        ],
        interaction={
            "kind": "assignment_released",
            "actor": agent_id,
            "node_id": assignment.current_node,
            "command": f"research-cockpit work release --assignment {assignment_id}",
            "before": {"agent_id": agent_id, "status": assignment.status, "lease": assignment.lease},
            "after": {"agent_id": None, "status": "queued", "lease": assignment_after["lease"]},
        },
        operation=operation,
        assignment_id=assignment_id,
        operation_id=operation_id,
        request_hash=request_hash,
        receipt=receipt,
    )
