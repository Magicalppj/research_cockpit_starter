from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from research_cockpit.assignment_leases import AssignmentLeaseError
from research_cockpit.commands.accept_decision import accept_decision
from research_cockpit.commands.promote_decision import promote_decision
from research_cockpit.commands.set_baseline import set_baseline
from research_cockpit.commands.update_decision_checklist import update_decision_checklist
from research_cockpit.commands.update_decision_evidence import update_decision_evidence
from research_cockpit.mutation_lock import MutationError
from research_cockpit.operation_receipts import (
    OperationIdConflict,
    error_receipt,
    normalized_request_hash,
    replay_or_conflict,
    success_receipt,
    validate_operation_id,
)


_ACTION_FIELDS: dict[str, tuple[set[str], set[str]]] = {
    "promote": (
        {
            "decision_id",
            "option_id",
            "title",
            "summary",
            "status",
            "supporting_experiments",
            "alternatives",
            "consequences",
            "next_required_actions",
            "evidence_strength",
            "auto_evidence",
            "locale",
            "force_accept",
        },
        {"decision_id", "option_id", "title", "summary"},
    ),
    "refresh_evidence": ({"decision_id", "locale"}, {"decision_id"}),
    "update_checklist": (
        {
            "decision_id",
            "alternatives",
            "consequences",
            "next_required_actions",
            "evidence_summary",
        },
        {"decision_id"},
    ),
    "accept": ({"decision_id", "force_accept"}, {"decision_id"}),
    "set_baseline": (
        {"node_id", "option_id", "decision_id", "artifacts", "reason", "clear"},
        {"node_id"},
    ),
}


_BOOLEAN_PARAMETERS = {"auto_evidence", "force_accept", "clear"}
_LIST_PARAMETERS = {
    "supporting_experiments",
    "alternatives",
    "consequences",
    "next_required_actions",
    "artifacts",
}
_STRING_PARAMETERS = {
    "decision_id",
    "option_id",
    "title",
    "summary",
    "status",
    "evidence_strength",
    "locale",
    "evidence_summary",
    "node_id",
    "reason",
}


def _validate_parameter_types(action: str, parameters: dict[str, Any]) -> None:
    for field in sorted(set(parameters) & _BOOLEAN_PARAMETERS):
        if not isinstance(parameters[field], bool):
            raise ValueError(f"coord decide {action} {field} must be boolean")
    for field in sorted(set(parameters) & _LIST_PARAMETERS):
        value = parameters[field]
        if value is None:
            continue
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(
                f"coord decide {action} {field} must be a list of strings or null"
            )
    for field in sorted(set(parameters) & _STRING_PARAMETERS):
        value = parameters[field]
        if value is not None and not isinstance(value, str):
            raise ValueError(
                f"coord decide {action} {field} must be a string or null"
            )


def parse_coord_decide_input(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != "coord_decide_v1":
        raise ValueError("coord decide input requires schema_version: coord_decide_v1")
    unknown = sorted(set(payload) - {"schema_version", "operation_id", "action", "parameters"})
    if unknown:
        raise ValueError("coord decide input contains unknown fields: " + ", ".join(unknown))
    operation_id = payload.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise ValueError("coord decide input operation_id must be a non-empty string")
    action = payload.get("action")
    if action not in _ACTION_FIELDS:
        raise ValueError(
            "coord decide input action must be one of: " + ", ".join(sorted(_ACTION_FIELDS))
        )
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("coord decide input parameters must be a mapping")
    allowed, required = _ACTION_FIELDS[action]
    unknown_parameters = sorted(set(parameters) - allowed)
    if unknown_parameters:
        raise ValueError(
            f"coord decide {action} contains unknown parameters: "
            + ", ".join(unknown_parameters)
        )
    missing = sorted(required - set(parameters))
    if missing:
        raise ValueError(
            f"coord decide {action} is missing parameters: " + ", ".join(missing)
        )
    normalized = deepcopy(parameters)
    for field in required:
        value = normalized[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"coord decide {action} {field} must be a non-empty string")
        normalized[field] = value.strip()
    _validate_parameter_types(action, normalized)
    return {
        "schema_version": "coord_decide_v1",
        "operation_id": operation_id.strip(),
        "action": action,
        "parameters": normalized,
    }


def _operation_error(operation_id: str, message: str) -> AssignmentLeaseError:
    return AssignmentLeaseError(
        error_receipt(
            operation="coord decide",
            assignment_id=None,
            operation_id=operation_id,
            code="conflict",
            message=message,
            retry_kind="manual_recovery",
            retry_command="research-cockpit coord overview --root <data-root> --json --compact",
        )
    )


def apply_coord_decision(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_coord_decide_input(plan)
    operation_id = validate_operation_id(parsed["operation_id"])
    request_hash = normalized_request_hash(parsed)
    try:
        replay = replay_or_conflict(
            root,
            scope="coordinator",
            operation_id=operation_id,
            request_hash=request_hash,
            operation="coord decide",
            assignment_id=None,
        )
    except OperationIdConflict as exc:
        raise AssignmentLeaseError(exc.receipt) from exc
    if replay is not None:
        return replay

    action = parsed["action"]
    parameters = parsed["parameters"]
    target_id = str(parameters.get("decision_id") or parameters.get("node_id") or "")
    receipt = success_receipt(
        operation="coord decide",
        assignment_id=None,
        operation_id=operation_id,
        changed=True,
        packet_revision=None,
        allowed_operations=[],
    )
    receipt["entities"] = {"action": action, "target_id": target_id}
    operation_request = {
        "scope": "coordinator",
        "operation_id": operation_id,
        "request_hash": request_hash,
        "receipt": receipt,
        "operation": "coord decide",
        "assignment_id": None,
        "command": "research-cockpit coord decide",
    }

    handlers: dict[str, Callable[..., Any]] = {
        "promote": promote_decision,
        "refresh_evidence": update_decision_evidence,
        "update_checklist": update_decision_checklist,
        "accept": accept_decision,
        "set_baseline": set_baseline,
    }
    try:
        handlers[action](
            root,
            **parameters,
            rebuild_dashboard=False,
            operation_request=operation_request,
        )
    except MutationError as exc:
        stored = exc.payload.get("operation_receipt")
        if isinstance(stored, dict):
            raise AssignmentLeaseError(stored) from exc
        raise _operation_error(operation_id, str(exc)) from exc
    return receipt
