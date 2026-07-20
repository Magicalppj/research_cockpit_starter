from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_cockpit.assignment_leases import AssignmentLeaseError
from research_cockpit.evidence_staging import StagedEvidence, stage_final_evidence
from research_cockpit.model import RunRecord, load_yaml
from research_cockpit.mutation_lock import MutationError
from research_cockpit.operation_receipts import (
    OperationIdConflict,
    error_receipt,
    normalized_request_hash,
    replay_or_conflict,
    validate_operation_id,
)
from research_cockpit.run_closeout import complete_run_closeout


WORK_CLOSE_SCHEMA_VERSION = "work_close_v1"
_CONTROL_FIELDS = {
    "schema_version",
    "agent_id",
    "lease_id",
    "lease_epoch",
    "operation_id",
    "input_revision",
    "assignment_result",
    "review_required",
    "evidence_inputs",
}
_CLOSEOUT_FIELDS = {
    "run",
    "experiment",
    "finding",
    "artifact_record",
    "gates",
    "next_experiment",
    "next_actions",
}


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(timezone.utc)


def _parse_work_close(plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("work close input must be a mapping")
    if plan.get("schema_version") != WORK_CLOSE_SCHEMA_VERSION:
        raise ValueError("work close requires schema_version: work_close_v1")
    allowed = _CONTROL_FIELDS | _CLOSEOUT_FIELDS
    unknown = sorted(set(plan) - allowed)
    if unknown:
        raise ValueError("work close input does not support: " + ", ".join(unknown))
    required = {
        "agent_id",
        "lease_id",
        "lease_epoch",
        "operation_id",
        "input_revision",
        "run",
        "assignment_result",
    }
    missing = sorted(field for field in required if field not in plan)
    if missing:
        raise ValueError("work close input is missing: " + ", ".join(missing))
    for field in ("agent_id", "lease_id", "operation_id", "input_revision"):
        if not isinstance(plan.get(field), str) or not plan[field].strip():
            raise ValueError(f"work close {field} must be a non-empty string")
    epoch = plan.get("lease_epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise ValueError("work close lease_epoch must be an integer >= 1")
    for field in ("run", "assignment_result"):
        if not isinstance(plan.get(field), dict):
            raise ValueError(f"work close {field} must be a mapping")
    if "review_required" in plan and not isinstance(plan["review_required"], bool):
        raise ValueError("work close review_required must be a boolean")
    if "evidence_inputs" in plan and not isinstance(plan["evidence_inputs"], dict):
        raise ValueError("work close evidence_inputs must be a mapping")
    if plan.get("evidence_inputs") and plan.get("artifact_record"):
        raise ValueError("work close cannot combine evidence_inputs with artifact_record")
    return deepcopy(plan)


def _operation_scope(assignment_id: str) -> str:
    return f"assignment:{assignment_id}"


def _existing_operation(
    root: Path,
    *,
    assignment_id: str,
    operation_id: str,
    request_hash: str,
) -> dict[str, Any] | None:
    try:
        return replay_or_conflict(
            root,
            scope=_operation_scope(assignment_id),
            operation_id=operation_id,
            request_hash=request_hash,
            operation="work close",
            assignment_id=assignment_id,
        )
    except OperationIdConflict as exc:
        raise AssignmentLeaseError(exc.receipt) from exc


def _run_identity(root: Path, plan: dict[str, Any]) -> tuple[str, str]:
    run_spec = plan["run"]
    run_id = str(run_spec.get("id") or run_spec.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("work close run.id is required")
    path = root / "runs" / f"{run_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(path)
    run = RunRecord.from_dict(load_yaml(path))
    return run_id, run.experiment_id


def _transaction_error(
    exc: MutationError,
    *,
    assignment_id: str,
    operation_id: str,
    lease_id: str,
    input_revision: str,
) -> AssignmentLeaseError:
    stored = exc.payload.get("operation_receipt")
    if isinstance(stored, dict):
        return AssignmentLeaseError(stored)
    status = str(exc.payload.get("status") or "")
    return AssignmentLeaseError(
        error_receipt(
            operation="work close",
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="conflict" if status == "conflict" else "mutation_failed",
            message=str(exc),
            lease_id=lease_id,
            input_revision=input_revision,
            conflict_files=[
                str(item) for item in exc.payload.get("conflict_files", [])
            ],
            rolled_back=bool(exc.payload.get("rolled_back", False)),
            partial_success=bool(exc.payload.get("partial_success", False)),
        )
    )


def close_assignment_work(
    root: Path,
    *,
    assignment_id: str,
    plan: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    parsed = _parse_work_close(plan)
    operation_id = validate_operation_id(str(parsed["operation_id"]))
    run_id, experiment_id = _run_identity(root, parsed)
    staged: StagedEvidence | None = None
    closeout_plan = {
        key: deepcopy(value)
        for key, value in parsed.items()
        if key in _CLOSEOUT_FIELDS
    }
    closeout_plan["schema_version"] = "run_closeout_v1"
    evidence_inputs = parsed.get("evidence_inputs")
    if evidence_inputs:
        staged = stage_final_evidence(
            root,
            assignment_id=assignment_id,
            experiment_id=experiment_id,
            run_id=run_id,
            agent_id=str(parsed["agent_id"]),
            spec=evidence_inputs,
        )
        closeout_plan["artifact_record"] = staged.record_spec
    request_hash = normalized_request_hash(
        {
            **parsed,
            "evidence_snapshot_sha256": staged.content_sha256 if staged else None,
        }
    )
    try:
        replay = _existing_operation(
            root,
            assignment_id=assignment_id,
            operation_id=operation_id,
            request_hash=request_hash,
        )
    except Exception:
        if staged is not None:
            staged.cleanup()
        raise
    if replay is not None:
        if staged is not None:
            staged.cleanup()
        return replay
    closeout_plan["assignment_result"] = deepcopy(parsed["assignment_result"])
    try:
        result = complete_run_closeout(
            root,
            plan=closeout_plan,
            rebuild_dashboard=False,
            assignment_id=assignment_id,
            operation_context={
                "agent_id": str(parsed["agent_id"]),
                "lease_id": str(parsed["lease_id"]),
                "lease_epoch": int(parsed["lease_epoch"]),
                "operation_id": operation_id,
                "request_hash": request_hash,
                "input_revision": str(parsed["input_revision"]),
                "review_required": parsed.get("review_required"),
                "now": _now(now),
                "refresh_commit_clock": now is None,
            },
            staged_moves=[staged.staged_move] if staged else None,
        )
    except MutationError as exc:
        raise _transaction_error(
            exc,
            assignment_id=assignment_id,
            operation_id=operation_id,
            lease_id=str(parsed["lease_id"]),
            input_revision=str(parsed["input_revision"]),
        ) from exc
    finally:
        if staged is not None:
            staged.cleanup()
    receipt = result.get("operation_receipt")
    if not isinstance(receipt, dict):
        raise RuntimeError("work close transaction did not return an operation receipt")
    return receipt
