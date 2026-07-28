from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from research_cockpit.assignment_leases import DEFAULT_LEASE_SECONDS, AssignmentLeaseError
from research_cockpit.baselines import compact_effective_baseline, resolve_effective_baseline
from research_cockpit.commands._runtime import load_validated_state, stable_payload_revision
from research_cockpit.commands.apply_graph_plan import apply_graph_plan
from research_cockpit.commands.start_agent_session import start_agent_session
from research_cockpit.mutation_lock import MutationError
from research_cockpit.model import derive_focus_path, load_assignments
from research_cockpit.operation_receipts import (
    OperationIdConflict,
    bounded,
    error_receipt,
    normalized_request_hash,
    replay_or_conflict,
    success_receipt,
    validate_operation_id,
)
from research_cockpit.runtime_ids import generate_runtime_id
from research_cockpit.run_lifecycle import (
    ActiveRunSnapshot,
    active_run_ids_added_since_snapshot,
    active_run_ids_for_target,
    capture_active_run_snapshot,
)
from research_cockpit.storage import load_yaml
from research_cockpit.validation_index import ensure_validation_index
from research_cockpit.work_packets import assignment_result_revision


_ASSIGN_FIELDS = {"schema_version", "operation_id", "action", "graph_plan", "session"}
TRACKING_REASONS = (
    "parallel_ownership",
    "durable_handoff",
    "independent_review",
    "stage_deliverable",
)
_SESSION_FIELDS = {
    "kind",
    "option_id",
    "experiment_id",
    "producer_assignment_id",
    "objective",
    "branch",
    "worktree",
    "agent_id",
    "assignment_id",
    "label",
    "base",
    "create_worktree",
    "force",
    "tracking_reason",
}


def parse_coord_assign_input(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != "coord_assign_v1":
        raise ValueError("coord assign input requires schema_version: coord_assign_v1")
    unknown = sorted(set(payload) - _ASSIGN_FIELDS)
    if unknown:
        raise ValueError("coord assign input contains unknown fields: " + ", ".join(unknown))
    operation_id = payload.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise ValueError("coord assign input operation_id must be a non-empty string")
    action = payload.get("action")
    if action not in {"graph_plan", "session"}:
        raise ValueError("coord assign input action must be graph_plan or session")
    if action == "graph_plan":
        graph_plan = payload.get("graph_plan")
        if not isinstance(graph_plan, dict):
            raise ValueError("coord assign input graph_plan must be a mapping")
        if payload.get("session") is not None:
            raise ValueError("coord assign graph_plan action cannot include session")
        return {
            "schema_version": "coord_assign_v1",
            "operation_id": operation_id.strip(),
            "action": action,
            "graph_plan": deepcopy(graph_plan),
        }

    session = payload.get("session")
    if not isinstance(session, dict):
        raise ValueError("coord assign input session must be a mapping")
    unknown_session = sorted(set(session) - _SESSION_FIELDS)
    if unknown_session:
        raise ValueError(
            "coord assign session contains unknown fields: " + ", ".join(unknown_session)
        )
    required = ("option_id", "objective", "branch", "worktree", "agent_id", "assignment_id")
    missing = [field for field in required if field not in session]
    if missing:
        raise ValueError("coord assign session is missing fields: " + ", ".join(missing))
    for field in required:
        if not isinstance(session[field], str) or not session[field].strip():
            raise ValueError(f"coord assign session {field} must be a non-empty string")
    if payload.get("graph_plan") is not None:
        raise ValueError("coord assign session action cannot include graph_plan")
    normalized = deepcopy(session)
    kind = normalized.get("kind", "experiment")
    if kind not in {"experiment", "review"}:
        raise ValueError("coord assign session kind must be experiment or review")
    if "kind" in normalized:
        normalized["kind"] = kind
    if kind == "review":
        producer_assignment_id = normalized.get("producer_assignment_id")
        if not isinstance(producer_assignment_id, str) or not producer_assignment_id.strip():
            raise ValueError(
                "coord assign review session producer_assignment_id must be a non-empty string"
            )
        normalized["producer_assignment_id"] = producer_assignment_id.strip()
    for field in required:
        normalized[field] = normalized[field].strip()
    for field in ("label", "base"):
        value = normalized.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"coord assign session {field} must be a non-empty string or null")
    tracking_reason = normalized.get("tracking_reason")
    if tracking_reason is not None:
        if not isinstance(tracking_reason, str) or not tracking_reason.strip():
            raise ValueError(
                "coord assign session tracking_reason must be a non-empty string or null"
            )
        if len(tracking_reason.strip()) > 100:
            raise ValueError("coord assign session tracking_reason exceeds 100 characters")
        normalized["tracking_reason"] = tracking_reason.strip()
    for field in ("create_worktree", "force"):
        value = normalized.get(field, False)
        if not isinstance(value, bool):
            raise ValueError(f"coord assign session {field} must be boolean")
        normalized[field] = value
    experiment_id = normalized.get("experiment_id")
    if kind == "experiment":
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            raise ValueError(
                "coord assign experiment session experiment_id must be a non-empty string"
            )
        normalized["experiment_id"] = experiment_id.strip()
    elif experiment_id is not None:
        raise ValueError("coord assign review session cannot include experiment_id")
    if kind == "experiment" and normalized.get("producer_assignment_id") is not None:
        raise ValueError(
            "coord assign experiment session cannot include producer_assignment_id"
        )
    return {
        "schema_version": "coord_assign_v1",
        "operation_id": operation_id.strip(),
        "action": action,
        "session": normalized,
    }


def assignment_granularity_warning(session: dict[str, Any]) -> dict[str, Any] | None:
    """Return the non-blocking tracking-contract warning for a parsed session."""
    tracking_reason = session.get("tracking_reason")
    if tracking_reason is None:
        return {
            "code": "missing_tracking_reason",
            "allowed_tracking_reasons": list(TRACKING_REASONS),
        }
    if tracking_reason not in TRACKING_REASONS:
        return {
            "code": "unknown_tracking_reason",
            "provided_tracking_reason": tracking_reason,
            "allowed_tracking_reasons": list(TRACKING_REASONS),
        }
    kind = session.get("kind", "experiment")
    if kind == "review" and tracking_reason != "independent_review":
        return {
            "code": "review_tracking_reason_mismatch",
            "provided_tracking_reason": tracking_reason,
            "expected_tracking_reason": "independent_review",
        }
    if kind == "experiment" and tracking_reason == "independent_review":
        return {
            "code": "experiment_tracking_reason_mismatch",
            "provided_tracking_reason": tracking_reason,
            "expected_tracking_reasons": [
                reason for reason in TRACKING_REASONS if reason != "independent_review"
            ],
        }
    return None


def _lease_epoch_counter(payload: dict[str, Any]) -> int:
    values = [payload.get("lease_epoch_counter")]
    lease = payload.get("lease")
    if isinstance(lease, dict):
        values.append(lease.get("lease_epoch"))
    parsed: list[int] = []
    for item in values:
        if item is None:
            continue
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value >= 0:
            parsed.append(value)
    return max(parsed, default=0)


def _operation_error(
    *,
    operation_id: str,
    assignment_id: str | None,
    code: str,
    message: str,
    retry_command: str = "research-cockpit coord overview --root <data-root> --json --compact",
    retry_reason: str | None = None,
    dependency_blockers: list[str] | None = None,
    partial_success: bool = False,
    warnings: list[str] | None = None,
) -> AssignmentLeaseError:
    receipt = error_receipt(
        operation="coord assign",
        assignment_id=assignment_id,
        operation_id=operation_id,
        code=code,
        message=message,
        retry_kind="manual_recovery",
        retry_command=retry_command,
        retry_reason=retry_reason,
        dependency_blockers=dependency_blockers,
        partial_success=partial_success,
    )
    if warnings:
        receipt["warnings"] = bounded(warnings)
    return AssignmentLeaseError(receipt)


def _assert_no_active_run_for_session(
    root: Path,
    *,
    assignment_id: str,
    experiment_id: str,
    operation_id: str,
    active_run_ids: list[str] | None = None,
) -> None:
    if active_run_ids is None:
        active_run_ids = active_run_ids_for_target(
            root,
            assignment_id=assignment_id,
            experiment_id=experiment_id,
        )
    if not active_run_ids:
        return
    raise _operation_error(
        operation_id=operation_id,
        assignment_id=assignment_id,
        code="active_run_blocks_session",
        message=(
            "coord assign cannot replace an experiment session while its assignment "
            "or target has active runs."
        ),
        retry_command=(
            "research-cockpit context --root <data-root> "
            f"--id {experiment_id} --view execution --json --compact"
        ),
        retry_reason="Continue or close the existing active run before assigning a new session.",
        dependency_blockers=[f"active_run:{run_id}" for run_id in active_run_ids],
    )


def apply_coord_assignment(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_coord_assign_input(plan)
    operation_id = validate_operation_id(parsed["operation_id"])
    request_hash = normalized_request_hash(parsed)
    assignment_id = (
        str(parsed["session"]["assignment_id"])
        if parsed["action"] == "session"
        else None
    )
    try:
        replay = replay_or_conflict(
            root,
            scope="coordinator",
            operation_id=operation_id,
            request_hash=request_hash,
            operation="coord assign",
            assignment_id=assignment_id,
        )
    except OperationIdConflict as exc:
        raise AssignmentLeaseError(exc.receipt) from exc
    if replay is not None:
        if parsed["action"] == "session":
            ensure_validation_index(root)
        return replay

    receipt = success_receipt(
        operation="coord assign",
        assignment_id=assignment_id,
        operation_id=operation_id,
        changed=True,
        packet_revision=None,
        allowed_operations=[],
    )
    operation_request = {
        "scope": "coordinator",
        "operation_id": operation_id,
        "request_hash": request_hash,
        "receipt": receipt,
        "operation": "coord assign",
        "assignment_id": assignment_id,
    }
    if parsed["action"] == "graph_plan":
        graph_plan = parsed["graph_plan"]
        created_ids = [
            str(row.get("id"))
            for row in graph_plan.get("nodes", [])
            if isinstance(row, dict) and row.get("id")
        ]
        updated_ids = [
            str(row.get("id"))
            for row in graph_plan.get("updates", [])
            if isinstance(row, dict) and row.get("id")
        ]
        receipt["entities"] = {
            "created_node_ids": created_ids,
            "updated_node_ids": updated_ids,
        }
        receipt["changed"] = bool(created_ids or updated_ids)
        interaction = {
            "kind": "coord_graph_plan_applied",
            "actor": "coordinator",
            "command": "research-cockpit coord assign",
            "after": receipt["entities"],
        }
        try:
            result = apply_graph_plan(
                root,
                plan=graph_plan,
                rebuild_dashboard=False,
                coordinator=True,
                interaction_override=interaction,
                operation_request=operation_request,
            )
        except MutationError as exc:
            stored = exc.payload.get("operation_receipt")
            if isinstance(stored, dict):
                raise AssignmentLeaseError(stored) from exc
            raise _operation_error(
                operation_id=operation_id,
                assignment_id=None,
                code="conflict",
                message=str(exc),
            ) from exc
        transaction = result.get("_operation_transaction", {})
    else:
        session = parsed["session"]
        tracking_reason = session.get("tracking_reason")
        structured_tracking_reason = (
            tracking_reason if tracking_reason in TRACKING_REASONS else None
        )
        granularity_warning = assignment_granularity_warning(session)
        receipt["tracking_reason"] = structured_tracking_reason
        receipt["granularity_warning"] = granularity_warning
        assignment_path = root / "assignments" / f"{session['assignment_id']}.yaml"
        existing_assignment = load_yaml(assignment_path) if assignment_path.is_file() else {}
        session_kind = session.get("kind", "experiment")
        experiment_id = session.get("experiment_id")
        active_run_snapshot: ActiveRunSnapshot | None = None
        if session_kind == "experiment" and experiment_id:
            active_run_snapshot = capture_active_run_snapshot(
                root,
                assignment_id=session["assignment_id"],
                experiment_id=experiment_id,
            )
            _assert_no_active_run_for_session(
                root,
                assignment_id=session["assignment_id"],
                experiment_id=experiment_id,
                operation_id=operation_id,
                active_run_ids=list(active_run_snapshot.occupancy.run_ids),
            )
        timestamp = datetime.now(timezone.utc)
        timestamp_text = timestamp.isoformat(timespec="seconds").replace("+00:00", "Z")
        lease_epoch = _lease_epoch_counter(existing_assignment) + 1
        lease_id = generate_runtime_id("lease", scope_hint=session["assignment_id"])
        state = load_validated_state(root)
        producer = None
        producer_revision = None
        if session_kind == "review":
            producer_id = session["producer_assignment_id"]
            producer = load_assignments(root).get(producer_id)
            if producer is None:
                raise ValueError(f"Producer assignment does not exist: {producer_id}")
            if producer.status != "completed":
                raise ValueError(f"Producer assignment {producer_id} must be completed before review")
            if producer.review.get("status") != "pending":
                raise ValueError(f"Producer assignment {producer_id} does not have a pending review")
            producer_revision = assignment_result_revision(producer)
            if not producer_revision:
                raise ValueError(f"Producer assignment {producer_id} has no result revision")
            if session["option_id"] not in derive_focus_path(state.nodes, producer.current_node):
                raise ValueError(
                    f"Producer assignment {producer_id} is outside option {session['option_id']}"
                )
        elif experiment_id:
            target = state.nodes.get(experiment_id)
            if target is None:
                raise ValueError(f"Experiment node does not exist: {experiment_id}")
            if target.type != "experiment":
                raise ValueError(f"Node {experiment_id} must be experiment, got {target.type}")
            if session["option_id"] not in derive_focus_path(state.nodes, experiment_id):
                raise ValueError(
                    f"Experiment {experiment_id} is outside option {session['option_id']}"
                )
        effective_baseline = compact_effective_baseline(
            resolve_effective_baseline(
                state.nodes,
                session["option_id"],
                state.current,
            )
        )
        baseline_revision = (
            None
            if effective_baseline["source_kind"] == "none"
            else stable_payload_revision(effective_baseline, prefix="exec-v1")
        )
        existing_inputs = existing_assignment.get("inputs")
        if session_kind == "review":
            dependency_revisions = {
                session["producer_assignment_id"]: producer_revision,
            }
        elif isinstance(existing_inputs, dict) and isinstance(
            existing_inputs.get("dependency_revisions", {}), dict
        ):
            dependency_revisions = deepcopy(existing_inputs["dependency_revisions"])
        else:
            dependency_revisions = {}
        captured_inputs = {
            "effective_baseline_revision": baseline_revision,
            "dependency_revisions": dependency_revisions,
        }
        assignment_overrides = {
            "status": "active",
            "agent_id": session["agent_id"],
            "lease_epoch_counter": lease_epoch,
            "inputs": captured_inputs,
            "input_revision": stable_payload_revision(
                captured_inputs,
                prefix="input-v1",
            ),
            "lease": {
                "lease_id": lease_id,
                "owner_agent_id": session["agent_id"],
                "lease_epoch": lease_epoch,
                "heartbeat_at": timestamp_text,
                "expires_at": (
                    timestamp + timedelta(seconds=DEFAULT_LEASE_SECONDS)
                ).isoformat(timespec="seconds").replace("+00:00", "Z"),
            },
            "updated_at": timestamp_text,
        }
        if structured_tracking_reason is not None:
            assignment_overrides["tracking_reason"] = structured_tracking_reason
        if experiment_id:
            assignment_overrides.update({
                "kind": "experiment",
                "current_node": experiment_id,
            })
        if session_kind == "review" and producer is not None:
            assignment_overrides.update({
                "kind": "review",
                "current_node": producer.current_node,
                "allowed_subtree": {
                    "root": session["option_id"],
                    "policy": "descendants_only",
                },
                "scope": {
                    "root_node": session["option_id"],
                    "subtree_policy": "descendants_only",
                    "write_policy": "review_read_only",
                },
                "dependencies": [{
                    "assignment_id": session["producer_assignment_id"],
                    "required_status": "completed",
                }],
                "review": {"required": False, "status": "not_required", "result_revision": None},
            })
        receipt["entities"] = {
            "assignment_id": session["assignment_id"],
            "agent_id": session["agent_id"],
            "option_id": session["option_id"],
            "lease_id": lease_id,
            "lease_epoch": lease_epoch,
            "experiment_id": experiment_id,
            "kind": session_kind,
            "producer_assignment_id": session.get("producer_assignment_id"),
        }
        if structured_tracking_reason is not None:
            receipt["entities"]["tracking_reason"] = structured_tracking_reason
        interaction = {
            "kind": "coord_review_assignment_created" if session_kind == "review" else "coord_assignment_created",
            "actor": "coordinator",
            "node_id": session["option_id"],
            "command": "research-cockpit coord assign",
            "after": receipt["entities"],
        }
        try:
            result = start_agent_session(
                root,
                option_id=session["option_id"],
                objective=session["objective"],
                branch=session["branch"],
                worktree=Path(session["worktree"]),
                agent_id=session["agent_id"],
                assignment_id=session["assignment_id"],
                label=session.get("label"),
                base=session.get("base"),
                create_worktree=session.get("create_worktree", False),
                force=session.get("force", False),
                rebuild_dashboard=False,
                interaction_override=interaction,
                operation_request=operation_request,
                assignment_overrides=assignment_overrides,
                preloaded_state=state,
                claim_option_workstream=session_kind != "review",
                additional_commit_validators=(
                    [
                        lambda: _assert_no_active_run_for_session(
                            root,
                            assignment_id=session["assignment_id"],
                            experiment_id=experiment_id,
                            operation_id=operation_id,
                            active_run_ids=active_run_ids_added_since_snapshot(
                                root,
                                active_run_snapshot,
                            ),
                        )
                    ]
                    if active_run_snapshot is not None
                    else None
                ),
            )
        except MutationError as exc:
            recovery_commands = [
                str(command)
                for command in exc.payload.get("recovery_commands", [])
                if isinstance(command, str) and command.strip()
            ]
            stored = exc.payload.get("operation_receipt")
            if isinstance(stored, dict):
                raise AssignmentLeaseError(stored) from exc
            has_worktree_side_effect = bool(
                exc.payload.get("created_worktree")
                or exc.payload.get("reused_worktree")
            )
            worktree_warnings = []
            if has_worktree_side_effect:
                worktree_warnings.append(
                    "Git worktree exists but coord assign truth was not committed; "
                    "resolve the blocker and retry the same operation_id."
                )
            raise _operation_error(
                operation_id=operation_id,
                assignment_id=assignment_id,
                code="conflict",
                message=str(exc),
                retry_command=(
                    recovery_commands[0]
                    if recovery_commands
                    else "research-cockpit coord overview --root <data-root> --json --compact"
                ),
                retry_reason="Retry the exact same coord assign operation after the conflict is resolved.",
                partial_success=bool(
                    exc.payload.get("partial_success") or has_worktree_side_effect
                ),
                warnings=worktree_warnings,
            ) from exc
        transaction = result.get("_operation_transaction", {})

    if parsed["action"] == "session":
        ensure_validation_index(root)

    replayed = transaction.get("operation_receipt")
    if transaction.get("replayed") and isinstance(replayed, dict):
        return deepcopy(replayed)
    return receipt
