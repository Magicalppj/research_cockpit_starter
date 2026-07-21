from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from research_cockpit.assignment_leases import AssignmentLeaseError
from research_cockpit.assignment_reviews import apply_review_result
from research_cockpit.commands.promote_artifact_record import promote_artifact_record
from research_cockpit.mutation_lock import MutationError
from research_cockpit.operation_receipts import (
    OperationIdConflict,
    error_receipt,
    normalized_request_hash,
    replay_or_conflict,
    success_receipt,
    validate_operation_id,
)


_PROMOTION_FIELDS = {
    "schema_version",
    "operation_id",
    "action",
    "record_id",
    "artifact_id",
    "link_to",
    "promotion_reason",
}


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"coord review {field} must be a non-empty string")
    return value.strip()


def _parse_artifact_promotion(plan: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(plan) - _PROMOTION_FIELDS)
    if unknown:
        raise ValueError(
            "coord review promote_artifact does not support: " + ", ".join(unknown)
        )
    required = {
        "schema_version",
        "operation_id",
        "action",
        "record_id",
        "promotion_reason",
    }
    missing = sorted(required - set(plan))
    if missing:
        raise ValueError(
            "coord review promote_artifact is missing: " + ", ".join(missing)
        )
    parsed = {
        "schema_version": "coord_review_v1",
        "operation_id": _non_empty_string(plan["operation_id"], "operation_id"),
        "action": "promote_artifact",
        "record_id": _non_empty_string(plan["record_id"], "record_id"),
        "promotion_reason": _non_empty_string(
            plan["promotion_reason"], "promotion_reason"
        ),
    }
    artifact_id = plan.get("artifact_id")
    if artifact_id is not None:
        parsed["artifact_id"] = _non_empty_string(artifact_id, "artifact_id")
    links = plan.get("link_to", [])
    if not isinstance(links, list):
        raise ValueError("coord review link_to must be a list of node ids")
    parsed["link_to"] = [
        _non_empty_string(item, "link_to item")
        for item in links
    ]
    return parsed


def _promotion_error(
    operation_id: str,
    message: str,
    *,
    conflict_files: list[str] | None = None,
) -> AssignmentLeaseError:
    return AssignmentLeaseError(
        error_receipt(
            operation="coord review",
            assignment_id=None,
            operation_id=operation_id,
            code="conflict",
            message=message,
            conflict_files=conflict_files,
            retry_kind="manual_recovery",
            retry_command=(
                "research-cockpit coord review --root <data-root> "
                "--file <coord_review.yaml> --json --compact"
            ),
        )
    )


def _promote_artifact(
    root: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    parsed = _parse_artifact_promotion(plan)
    operation_id = validate_operation_id(parsed["operation_id"])
    request_hash = normalized_request_hash(parsed)
    try:
        replay = replay_or_conflict(
            root,
            scope="coordinator",
            operation_id=operation_id,
            request_hash=request_hash,
            operation="coord review",
            assignment_id=None,
        )
    except OperationIdConflict as exc:
        raise AssignmentLeaseError(exc.receipt) from exc
    if replay is not None:
        return replay

    artifact_id = str(parsed.get("artifact_id") or parsed["record_id"])
    receipt = success_receipt(
        operation="coord review",
        assignment_id=None,
        operation_id=operation_id,
        changed=True,
        packet_revision=None,
        allowed_operations=[],
    )
    receipt["entities"] = {
        "action": "promote_artifact",
        "record_id": parsed["record_id"],
        "artifact_id": artifact_id,
        "linked_to": list(parsed["link_to"]),
    }
    operation_request = {
        "scope": "coordinator",
        "operation_id": operation_id,
        "request_hash": request_hash,
        "receipt": receipt,
        "operation": "coord review",
        "assignment_id": None,
    }
    try:
        result = promote_artifact_record(
            root,
            record_id=parsed["record_id"],
            artifact_id=artifact_id,
            link_to=parsed["link_to"],
            promotion_reason=parsed["promotion_reason"],
            coordinator=True,
            rebuild_dashboard=False,
            operation_request=operation_request,
            interaction_override={
                "kind": "coordinator_artifact_promoted",
                "actor": "coordinator",
                "node_id": artifact_id,
                "command": "research-cockpit coord review",
                "after": deepcopy(receipt["entities"]),
            },
        )
    except MutationError as exc:
        stored = exc.payload.get("operation_receipt")
        if isinstance(stored, dict):
            raise AssignmentLeaseError(stored) from exc
        raise _promotion_error(
            operation_id,
            str(exc),
            conflict_files=[
                str(item) for item in exc.payload.get("conflict_files", [])
            ],
        ) from exc
    transaction = result.get("_operation_transaction", {})
    replayed = transaction.get("operation_receipt")
    return deepcopy(replayed) if isinstance(replayed, dict) else receipt


def apply_coord_review(
    root: Path,
    *,
    plan: dict[str, Any],
    producer_assignment_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("coord review input must be a mapping")
    if plan.get("schema_version") != "coord_review_v1":
        raise ValueError("coord review requires schema_version: coord_review_v1")
    action = plan.get("action", "assignment_result")
    if action == "assignment_result":
        if not producer_assignment_id:
            raise ValueError(
                "coord review assignment_result requires --assignment"
            )
        assignment_plan = deepcopy(plan)
        assignment_plan.pop("action", None)
        return apply_review_result(
            root,
            producer_assignment_id=producer_assignment_id,
            plan=assignment_plan,
        )
    if action == "promote_artifact":
        if producer_assignment_id:
            raise ValueError(
                "coord review promote_artifact does not accept --assignment"
            )
        return _promote_artifact(root, plan)
    raise ValueError(
        "coord review action must be assignment_result or promote_artifact"
    )
