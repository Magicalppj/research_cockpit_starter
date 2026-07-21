from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_cockpit.assignment_leases import (
    DEFAULT_LEASE_SECONDS,
    AssignmentLeaseError,
    plan_assignment_lease_renewal,
)
from research_cockpit.commands._runs import RUN_OPTIONAL_FIELDS
from research_cockpit.commands.create_run import create_run
from research_cockpit.mutation_lock import MutationError
from research_cockpit.operation_receipts import (
    OperationIdConflict,
    error_receipt,
    normalized_request_hash,
    replay_or_conflict,
    success_receipt,
    validate_operation_id,
)
from research_cockpit.runtime_ids import generate_runtime_id
from research_cockpit.work_packets import build_work_packet_for_assignment


_START_RUN_FIELDS = frozenset(RUN_OPTIONAL_FIELDS) - {"started_at", "finished_at"}


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(timezone.utc)


def _scope(assignment_id: str) -> str:
    return f"assignment:{assignment_id}"


def _lease_error(
    *,
    assignment_id: str,
    operation_id: str,
    code: str,
    message: str,
    lease_id: str | None = None,
    input_revision: str | None = None,
    latest_packet_revision: str | None = None,
    conflict_files: list[str] | None = None,
) -> AssignmentLeaseError:
    return AssignmentLeaseError(
        error_receipt(
            operation="work start",
            assignment_id=assignment_id,
            operation_id=operation_id,
            code=code,
            message=message,
            lease_id=lease_id,
            input_revision=input_revision,
            latest_packet_revision=latest_packet_revision,
            conflict_files=conflict_files,
            retry_command=(
                f"research-cockpit work open --root <data-root> "
                f"--assignment {assignment_id} --compact --json"
            ),
        )
    )


def _normalized_run_fields(run_fields: dict[str, Any] | None) -> dict[str, Any]:
    fields = deepcopy(run_fields or {})
    unknown = sorted(set(fields) - _START_RUN_FIELDS)
    if unknown:
        raise ValueError("work start run fields are unsupported: " + ", ".join(unknown))
    return fields


def _expand_run_placeholders(
    value: Any,
    *,
    run_id: str,
    experiment_id: str,
    assignment_id: str,
) -> Any:
    if isinstance(value, str):
        return (
            value.replace("{run_id}", run_id)
            .replace("{experiment_id}", experiment_id)
            .replace("{assignment_id}", assignment_id)
        )
    if isinstance(value, dict):
        return {
            key: _expand_run_placeholders(
                item,
                run_id=run_id,
                experiment_id=experiment_id,
                assignment_id=assignment_id,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _expand_run_placeholders(
                item,
                run_id=run_id,
                experiment_id=experiment_id,
                assignment_id=assignment_id,
            )
            for item in value
        ]
    return value


def start_assignment_run(
    root: Path,
    *,
    assignment_id: str,
    agent_id: str,
    lease_id: str,
    lease_epoch: int,
    operation_id: str,
    input_revision: str | None = None,
    experiment_id: str | None = None,
    slug_hint: str | None = None,
    run_fields: dict[str, Any] | None = None,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict[str, Any]:
    operation_id = validate_operation_id(operation_id)
    normalized_run_fields = _normalized_run_fields(run_fields)
    request_hash = normalized_request_hash(
        {
            "operation": "work start",
            "assignment_id": assignment_id,
            "agent_id": agent_id,
            "lease_id": lease_id,
            "lease_epoch": lease_epoch,
            "input_revision": input_revision,
            "experiment_id": experiment_id,
            "slug_hint": slug_hint,
            "run": normalized_run_fields,
            "lease_seconds": lease_seconds,
        }
    )
    try:
        replay = replay_or_conflict(
            root,
            scope=_scope(assignment_id),
            operation_id=operation_id,
            request_hash=request_hash,
            operation="work start",
            assignment_id=assignment_id,
        )
    except OperationIdConflict as exc:
        raise AssignmentLeaseError(exc.receipt) from exc
    if replay is not None:
        return replay

    current = _now(now)
    lease_changes, candidate_assignment, _lease_before, _lease_after = (
        plan_assignment_lease_renewal(
            root,
            assignment_id=assignment_id,
            agent_id=agent_id,
            lease_id=lease_id,
            lease_epoch=lease_epoch,
            operation="work start",
            operation_id=operation_id,
            now=current,
            lease_seconds=lease_seconds,
        )
    )
    packet = build_work_packet_for_assignment(root, candidate_assignment, now=current)
    allowed = {
        str(item) for item in packet.get("allowed_operations", {}).get("items", [])
    }
    if "start" not in allowed:
        raise _lease_error(
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="assignment_not_ready",
            message=(
                f"Assignment readiness {packet.get('readiness')!r} does not allow work start."
            ),
            lease_id=lease_id,
        )
    expected_input_revision = packet.get("input_revision")
    if expected_input_revision and not input_revision:
        raise _lease_error(
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="input_revision_required",
            message="work start must bind the current Work Packet input_revision.",
            lease_id=lease_id,
            latest_packet_revision=str(packet["revision"]),
        )
    if input_revision != expected_input_revision and (
        input_revision is not None or expected_input_revision is not None
    ):
        raise _lease_error(
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="stale_inputs",
            message="work start input_revision does not match the current Work Packet.",
            lease_id=lease_id,
            input_revision=input_revision,
            latest_packet_revision=str(packet["revision"]),
        )

    target_experiment = str(experiment_id or candidate_assignment.current_node)
    run_id = generate_runtime_id("run", scope_hint=assignment_id, slug_hint=slug_hint)
    expanded_run_fields = _expand_run_placeholders(
        normalized_run_fields,
        run_id=run_id,
        experiment_id=target_experiment,
        assignment_id=assignment_id,
    )
    receipt = success_receipt(
        operation="work start",
        assignment_id=assignment_id,
        operation_id=operation_id,
        changed=True,
        packet_revision=str(packet["revision"]),
        allowed_operations=["record", "close"],
    )
    receipt["entities"] = {"run_id": run_id, "experiment_id": target_experiment}
    operation_request = {
        "scope": _scope(assignment_id),
        "operation_id": operation_id,
        "request_hash": request_hash,
        "receipt": receipt,
        "operation": "work start",
        "assignment_id": assignment_id,
    }
    interaction = {
        "kind": "assignment_run_started",
        "actor": agent_id,
        "node_id": target_experiment,
        "command": f"research-cockpit work start --assignment {assignment_id}",
        "after": {
            "assignment_id": assignment_id,
            "run_id": run_id,
            "experiment_id": target_experiment,
            "lease_epoch": lease_epoch,
            "lease_renewed": True,
        },
    }
    try:
        result = create_run(
            root,
            run_id=run_id,
            experiment_id=target_experiment,
            status="running",
            start_experiment=True,
            started_at=_timestamp(current),
            rebuild_dashboard=False,
            assignment_id=assignment_id,
            additional_yaml_changes=lease_changes,
            interaction_override=interaction,
            operation_request=operation_request,
            run_extra_fields={
                "assignment_id": assignment_id,
                "operation_id": operation_id,
                "lease_epoch": lease_epoch,
            },
            **expanded_run_fields,
        )
    except MutationError as exc:
        stored = exc.payload.get("operation_receipt")
        if isinstance(stored, dict):
            raise AssignmentLeaseError(stored) from exc
        raise _lease_error(
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
