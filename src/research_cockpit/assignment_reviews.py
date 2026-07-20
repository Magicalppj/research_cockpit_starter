from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable

from research_cockpit.agent_state import (
    AssignmentRecord,
    assignment_contract_errors,
    load_assignment,
)
from research_cockpit.assignment_leases import (
    AssignmentLeaseError,
    plan_assignment_lease_renewal,
)
from research_cockpit.commands._runtime import stable_payload_revision
from research_cockpit.evidence_bundles import (
    bounded_collection,
    build_evidence_bundle,
    persisted_result,
)
from research_cockpit.mutation_runtime import execute_mutation_transaction, load_targeted_state
from research_cockpit.mutation_lock import MutationError
from research_cockpit.operation_receipts import (
    OperationIdConflict,
    error_receipt,
    normalized_request_hash,
    replay_or_conflict,
    success_receipt,
    validate_operation_id,
)
from research_cockpit.public_contracts import parse_public_contract
from research_cockpit.types import ValidationError
from research_cockpit.validation_index import load_validation_index
from research_cockpit.work_packets import (
    assignment_result_revision,
    build_work_packet_for_assignment,
)


REVIEW_REPORT_SCHEMA_VERSION = "review_report_v1"
COORD_REVIEW_SCHEMA_VERSION = "coord_review_v1"
REVIEW_OPEN_MAX_BYTES = 32 * 1024
_SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
_VERDICT_OUTCOME = {
    "approved": "positive",
    "changes_requested": "negative",
    "inconclusive": "inconclusive",
}


def _utc_now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _review_target(assignment: AssignmentRecord) -> str:
    if assignment.kind != "review":
        raise ValueError(f"{assignment.assignment_id}: review operation requires kind: review")
    if assignment.scope.get("write_policy") != "review_read_only":
        raise ValueError(
            f"{assignment.assignment_id}: review operation requires scope.write_policy: review_read_only"
        )
    if len(assignment.dependencies) != 1:
        raise ValueError(
            f"{assignment.assignment_id}: review assignment requires exactly one producer dependency"
        )
    producer_id = str(assignment.dependencies[0].get("assignment_id") or "").strip()
    if not producer_id or producer_id == assignment.assignment_id:
        raise ValueError(f"{assignment.assignment_id}: review producer dependency is invalid")
    return producer_id


def _producer_result(
    root: Path,
    producer_id: str,
) -> tuple[AssignmentRecord, dict[str, Any], str, tuple[Path, bytes]]:
    path = root / "assignments" / f"{producer_id}.yaml"
    before = path.read_bytes()
    producer = load_assignment(root, producer_id)
    if producer.status != "completed":
        raise ValueError(f"{producer_id}: producer assignment must be completed before review")
    if not producer.result:
        raise ValueError(f"{producer_id}: producer assignment has no Evidence Bundle")
    result = deepcopy(producer.result)
    _validate_persisted_bundle(result, assignment_id=producer_id)
    if result.get("bundle_kind") != "work_result":
        raise ValueError(f"{producer_id}: producer result must be a work_result bundle")
    revision = assignment_result_revision(producer)
    if not revision:
        raise ValueError(f"{producer_id}: producer result has no revision")
    return producer, result, revision, (path, before)


def _validate_persisted_bundle(result: dict[str, Any], *, assignment_id: str) -> None:
    revision = result.get("revision")
    contract = {key: deepcopy(value) for key, value in result.items() if key != "revision"}
    parse_public_contract(contract, mode="mutation")
    expected = stable_payload_revision(contract, prefix="result-v1")
    if revision != expected:
        raise ValueError(f"{assignment_id}: Evidence Bundle revision does not match its content")


def _text(value: Any, *, limit: int = 400) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _evidence_records(
    root: Path,
    *,
    producer: AssignmentRecord,
    result: dict[str, Any],
) -> dict[str, Any]:
    references = [
        str(item)
        for item in result.get("artifact_records", {}).get("items", [])
        if str(item).strip()
    ]
    if not references:
        return bounded_collection([])
    validation_index = load_validation_index(root) or {}
    indexed_records = validation_index.get("artifact_records", {}) or {}
    seed_node_ids = {producer.current_node}
    for record_id in references:
        row = indexed_records.get(record_id, {})
        if not isinstance(row, dict):
            continue
        experiment_id = str(row.get("experiment_id") or "")
        if experiment_id:
            seed_node_ids.add(experiment_id)
    state = load_targeted_state(
        root,
        node_ids=sorted(seed_node_ids),
        include_artifact_records=True,
    )
    by_id = {
        str(record.get("record_id") or ""): record
        for record in state.artifact_records or []
        if isinstance(record, dict)
    }
    rows: list[dict[str, Any]] = []
    for record_id in references:
        record = by_id.get(record_id, {})
        links = record.get("links", {}) if isinstance(record.get("links"), dict) else {}
        rows.append(
            {
                "record_id": record_id,
                "experiment_id": str(record.get("experiment_id") or ""),
                "run_id": str(record.get("run_id") or ""),
                "title": _text(record.get("title")),
                "summary": _text(record.get("summary")),
                "stable_path": str(record.get("stable_path") or ""),
                "links": {
                    str(key): _text(value, limit=500)
                    for key, value in list(links.items())[:10]
                },
            }
        )
    return bounded_collection(rows)


def _fit_review_open_budget(payload: dict[str, Any]) -> None:
    records = payload["evidence_records"]
    while True:
        size = len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        if size + 100 <= REVIEW_OPEN_MAX_BYTES:
            return
        items = records.get("items", [])
        if not items:
            raise ValueError("review open payload exceeds the 32 KiB contract budget")
        items.pop()
        records["omitted"] = records["total"] - len(items)


def open_review_assignment(
    root: Path,
    *,
    assignment_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    assignment = load_assignment(root, assignment_id)
    producer_id = _review_target(assignment)
    packet = build_work_packet_for_assignment(root, assignment, now=_utc_now(now))
    producer, result, revision, _dependency = _producer_result(root, producer_id)
    evidence_records = _evidence_records(
        root,
        producer=producer,
        result=result,
    )
    payload = {
        "schema_version": "review_open_v1",
        "assignment": packet,
        "producer": {
            "assignment_id": producer_id,
            "status": producer.status,
            "result_revision": revision,
            "result": result,
        },
        "evidence_records": evidence_records,
    }
    _fit_review_open_budget(payload)
    payload["revision"] = stable_payload_revision(payload, prefix="review-open-v1")
    return payload


def _non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [_non_empty_string(item, f"{field_name} item") for item in value]


def _parse_review_report(plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("review report input must be a mapping")
    if plan.get("schema_version") != REVIEW_REPORT_SCHEMA_VERSION:
        raise ValueError("review report requires schema_version: review_report_v1")
    allowed = {
        "schema_version",
        "agent_id",
        "lease_id",
        "lease_epoch",
        "operation_id",
        "input_revision",
        "producer_result_revision",
        "verdict",
        "summary",
        "findings",
        "evidence_inspected",
        "validation_performed",
        "delivery",
    }
    unknown = sorted(set(plan) - allowed)
    if unknown:
        raise ValueError("review report input does not support: " + ", ".join(unknown))
    required = allowed - {"delivery"}
    missing = sorted(required - set(plan))
    if missing:
        raise ValueError("review report input is missing: " + ", ".join(missing))
    parsed = deepcopy(plan)
    for field in (
        "agent_id",
        "lease_id",
        "operation_id",
        "input_revision",
        "producer_result_revision",
        "summary",
    ):
        parsed[field] = _non_empty_string(parsed[field], f"review report {field}")
    epoch = parsed["lease_epoch"]
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise ValueError("review report lease_epoch must be an integer >= 1")
    verdict = _non_empty_string(parsed["verdict"], "review report verdict")
    if verdict not in _VERDICT_OUTCOME:
        raise ValueError("review report verdict must be approved, changes_requested, or inconclusive")
    findings = parsed["findings"]
    if not isinstance(findings, list):
        raise ValueError("review report findings must be a list")
    normalized_findings: list[dict[str, Any]] = []
    finding_fields = {"severity", "code", "summary", "evidence_refs"}
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(f"review report findings[{index}] must be a mapping")
        missing_finding = sorted(finding_fields - set(finding))
        unknown_finding = sorted(set(finding) - finding_fields)
        if missing_finding or unknown_finding:
            detail = missing_finding or unknown_finding
            kind = "missing" if missing_finding else "unsupported"
            raise ValueError(
                f"review report findings[{index}] has {kind} fields: " + ", ".join(detail)
            )
        severity = _non_empty_string(finding["severity"], f"review report findings[{index}].severity")
        if severity not in _SEVERITY_RANK:
            raise ValueError(f"review report findings[{index}].severity must be P0, P1, P2, or P3")
        normalized_findings.append(
            {
                "severity": severity,
                "code": _text(
                    _non_empty_string(finding["code"], f"review report findings[{index}].code"),
                    limit=100,
                ),
                "summary": _text(
                    _non_empty_string(
                        finding["summary"],
                        f"review report findings[{index}].summary",
                    ),
                    limit=600,
                ),
                "evidence_refs": bounded_collection(
                    [
                        _text(item, limit=300)
                        for item in _string_list(
                            finding["evidence_refs"],
                            f"review report findings[{index}].evidence_refs",
                        )
                    ]
                ),
            }
        )
    parsed["findings"] = sorted(
        normalized_findings,
        key=lambda item: _SEVERITY_RANK[item["severity"]],
    )
    parsed["evidence_inspected"] = [
        _text(item, limit=500)
        for item in _string_list(
            parsed["evidence_inspected"],
            "review report evidence_inspected",
        )
    ]
    parsed["validation_performed"] = [
        _text(item, limit=500)
        for item in _string_list(
            parsed["validation_performed"],
            "review report validation_performed",
        )
    ]
    parsed["delivery"] = deepcopy(
        parsed.get("delivery")
        or {
            "git_commit": None,
            "changed_files": [],
            "tests": {
                "status": "not_run",
                "summary": "The review report does not modify producer code.",
            },
        }
    )
    return parsed


def _replay(
    root: Path,
    *,
    scope: str,
    operation: str,
    assignment_id: str,
    operation_id: str,
    request_hash: str,
) -> dict[str, Any] | None:
    try:
        return replay_or_conflict(
            root,
            scope=scope,
            operation_id=operation_id,
            request_hash=request_hash,
            operation=operation,
            assignment_id=assignment_id,
        )
    except OperationIdConflict as exc:
        raise AssignmentLeaseError(exc.receipt) from exc


def _fail(
    *,
    operation: str,
    assignment_id: str,
    operation_id: str,
    code: str,
    message: str,
    lease_id: str | None = None,
    input_revision: str | None = None,
    packet_revision: str | None = None,
) -> AssignmentLeaseError:
    return AssignmentLeaseError(
        error_receipt(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            code=code,
            message=message,
            lease_id=lease_id,
            input_revision=input_revision,
            latest_packet_revision=packet_revision,
        )
    )


def _transaction_error(
    exc: MutationError,
    *,
    operation: str,
    assignment_id: str,
    operation_id: str,
    lease_id: str | None = None,
    input_revision: str | None = None,
) -> AssignmentLeaseError:
    stored = exc.payload.get("operation_receipt")
    if isinstance(stored, dict):
        return AssignmentLeaseError(stored)
    status = str(exc.payload.get("status") or "")
    return AssignmentLeaseError(
        error_receipt(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="conflict" if status == "conflict" else "mutation_failed",
            message=str(exc),
            lease_id=lease_id,
            input_revision=input_revision,
            conflict_files=[str(item) for item in exc.payload.get("conflict_files", [])],
            rolled_back=bool(exc.payload.get("rolled_back", False)),
            partial_success=bool(exc.payload.get("partial_success", False)),
        )
    )


def _review_commit_time() -> datetime:
    return datetime.now(timezone.utc)


def _review_freshness_validator(
    root: Path,
    *,
    candidate: AssignmentRecord,
    assignment_before: dict[str, Any],
    packet_revision: str,
    input_revision: str,
    operation_id: str,
    lease_id: str,
    planned_now: datetime,
    refresh_commit_clock: bool,
) -> Callable[[], None]:
    def validate() -> None:
        current = _review_commit_time() if refresh_commit_clock else planned_now
        expires_value = (assignment_before.get("lease") or {}).get("expires_at")
        try:
            expires_at = datetime.fromisoformat(
                str(expires_value or "").replace("Z", "+00:00")
            )
        except ValueError:
            expires_at = None
        if expires_at is None or expires_at.tzinfo is None:
            expires_at = None
        elif expires_at is not None:
            expires_at = expires_at.astimezone(timezone.utc)
        if expires_at is None or expires_at <= current:
            raise _fail(
                operation="review report",
                assignment_id=candidate.assignment_id,
                operation_id=operation_id,
                code="lease_expired",
                message="Assignment lease expired before review report could commit.",
                lease_id=lease_id,
                input_revision=input_revision,
                packet_revision=packet_revision,
            )
        fresh_packet = build_work_packet_for_assignment(root, candidate, now=current)
        expected_input_revision = (
            candidate.input_revision
            or fresh_packet.get("input_revision")
            or fresh_packet.get("revision")
        )
        review_allowed = "review" in fresh_packet.get("allowed_operations", {}).get(
            "items", []
        )
        if (
            str(fresh_packet.get("revision") or "") != packet_revision
            or input_revision != expected_input_revision
            or not review_allowed
        ):
            raise _fail(
                operation="review report",
                assignment_id=candidate.assignment_id,
                operation_id=operation_id,
                code="stale_inputs",
                message="Review Work Packet truth changed before report commit.",
                lease_id=lease_id,
                input_revision=input_revision,
                packet_revision=str(fresh_packet.get("revision") or ""),
            )

    return validate


def report_assignment_review(
    root: Path,
    *,
    assignment_id: str,
    plan: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    refresh_commit_clock = now is None
    parsed = _parse_review_report(plan)
    operation = "review report"
    operation_id = validate_operation_id(parsed["operation_id"])
    request_hash = normalized_request_hash(parsed)
    scope = f"assignment:{assignment_id}"
    replay = _replay(
        root,
        scope=scope,
        operation=operation,
        assignment_id=assignment_id,
        operation_id=operation_id,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay

    current = _utc_now(now)
    changes, candidate, _lease_before, _lease_after = plan_assignment_lease_renewal(
        root,
        assignment_id=assignment_id,
        agent_id=parsed["agent_id"],
        lease_id=parsed["lease_id"],
        lease_epoch=parsed["lease_epoch"],
        operation=operation,
        operation_id=operation_id,
        now=current,
    )
    producer_id = _review_target(candidate)
    packet = build_work_packet_for_assignment(root, candidate, now=current)
    if "review" not in packet.get("allowed_operations", {}).get("items", []):
        raise _fail(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="assignment_not_ready",
            message=f"Assignment readiness {packet.get('readiness')!r} does not allow review report.",
            lease_id=parsed["lease_id"],
            input_revision=parsed["input_revision"],
            packet_revision=packet.get("revision"),
        )
    expected_input_revision = candidate.input_revision or packet.get("input_revision") or packet["revision"]
    if parsed["input_revision"] != expected_input_revision:
        raise _fail(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="stale_inputs",
            message="review report input_revision does not match the current Work Packet.",
            lease_id=parsed["lease_id"],
            input_revision=parsed["input_revision"],
            packet_revision=packet.get("revision"),
        )
    producer, producer_result, producer_revision, producer_dependency = _producer_result(
        root,
        producer_id,
    )
    if parsed["producer_result_revision"] != producer_revision:
        raise _fail(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="stale_producer_result",
            message="review report producer_result_revision does not match the current producer result.",
            lease_id=parsed["lease_id"],
            input_revision=parsed["input_revision"],
            packet_revision=packet.get("revision"),
        )

    review_block = {
        "producer_assignment_id": producer_id,
        "producer_result_revision": producer_revision,
        "findings": bounded_collection(parsed["findings"]),
        "evidence_inspected": bounded_collection(parsed["evidence_inspected"]),
        "validation_performed": bounded_collection(parsed["validation_performed"]),
        "verdict": parsed["verdict"],
    }
    bundle, result_revision = build_evidence_bundle(
        assignment_id=assignment_id,
        operation_id=operation_id,
        input_revision=parsed["input_revision"],
        result_spec={
            "outcome": _VERDICT_OUTCOME[parsed["verdict"]],
            "summary": parsed["summary"],
            "delivery": parsed["delivery"],
            "proposals": [],
        },
        run_ids=list(producer_result.get("runs", {}).get("items", [])),
        finding_ids=list(producer_result.get("findings", {}).get("items", [])),
        artifact_record_ids=list(
            producer_result.get("artifact_records", {}).get("items", [])
        ),
        packet_revision=str(packet["revision"]),
        bundle_kind="review_result",
        review=review_block,
    )
    assignment_path, assignment_before, assignment_after = changes[0]
    agent_path, agent_before, agent_after = changes[1]
    assignment_after = deepcopy(assignment_after)
    agent_after = deepcopy(agent_after)
    assignment_after.update(
        {
            "agent_id": None,
            "status": "completed",
            "result": persisted_result(bundle, result_revision),
            "next_actions": [],
            "lease": {
                "lease_id": None,
                "owner_agent_id": None,
                "lease_epoch": 0,
                "heartbeat_at": None,
                "expires_at": None,
            },
            "updated_at": _timestamp(current),
        }
    )
    active = [
        item
        for item in agent_after.get("active_assignment_ids", [])
        if item != assignment_id
    ]
    agent_after["active_assignment_ids"] = active
    agent_after["status"] = "active" if active else "idle"
    errors = assignment_contract_errors(AssignmentRecord.from_dict(assignment_after))
    if errors:
        raise ValidationError(errors)
    changes = [
        (assignment_path, assignment_before, assignment_after),
        (agent_path, agent_before, agent_after),
    ]
    receipt = success_receipt(
        operation=operation,
        assignment_id=assignment_id,
        operation_id=operation_id,
        changed=True,
        packet_revision=str(packet["revision"]),
        readiness="not_applicable",
        allowed_operations=[],
    )
    receipt.update(
        {
            "result_revision": result_revision,
            "entities": {
                "review_assignment_id": assignment_id,
                "producer_assignment_id": producer.assignment_id,
            },
        }
    )
    try:
        transaction = execute_mutation_transaction(
            root,
            changes,
            interactions=[
                {
                    "kind": "assignment_review_reported",
                    "actor": parsed["agent_id"],
                    "node_id": candidate.current_node,
                    "command": f"research-cockpit review report --assignment {assignment_id}",
                    "after": {
                        "review_assignment_id": assignment_id,
                        "producer_assignment_id": producer_id,
                        "producer_result_revision": producer_revision,
                        "review_result_revision": result_revision,
                        "verdict": parsed["verdict"],
                    },
                }
            ],
            rebuild_dashboard=False,
            read_dependencies=[producer_dependency],
            commit_validators=[
                _review_freshness_validator(
                    root,
                    candidate=candidate,
                    assignment_before=assignment_before,
                    packet_revision=str(packet["revision"]),
                    input_revision=parsed["input_revision"],
                    operation_id=operation_id,
                    lease_id=parsed["lease_id"],
                    planned_now=current,
                    refresh_commit_clock=refresh_commit_clock,
                )
            ],
            operation_request={
                "scope": scope,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "receipt": receipt,
                "operation": operation,
                "assignment_id": assignment_id,
            },
        )
    except MutationError as exc:
        raise _transaction_error(
            exc,
            operation=operation,
            assignment_id=assignment_id,
            operation_id=operation_id,
            lease_id=parsed["lease_id"],
            input_revision=parsed["input_revision"],
        ) from exc
    replayed = transaction.get("operation_receipt")
    return deepcopy(replayed) if isinstance(replayed, dict) else receipt


def _parse_coord_review(plan: dict[str, Any]) -> dict[str, str]:
    if not isinstance(plan, dict):
        raise ValueError("coord review input must be a mapping")
    if plan.get("schema_version") != COORD_REVIEW_SCHEMA_VERSION:
        raise ValueError("coord review requires schema_version: coord_review_v1")
    fields = {
        "schema_version",
        "operation_id",
        "review_assignment_id",
        "review_result_revision",
        "producer_result_revision",
    }
    missing = sorted(fields - set(plan))
    unknown = sorted(set(plan) - fields)
    if missing:
        raise ValueError("coord review input is missing: " + ", ".join(missing))
    if unknown:
        raise ValueError("coord review input does not support: " + ", ".join(unknown))
    return {
        field: _non_empty_string(plan[field], f"coord review {field}")
        for field in fields
    }


def apply_review_result(
    root: Path,
    *,
    producer_assignment_id: str,
    plan: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    parsed = _parse_coord_review(plan)
    operation = "coord review"
    operation_id = validate_operation_id(parsed["operation_id"])
    scope = f"assignment:{producer_assignment_id}"
    request_hash = normalized_request_hash(
        {**parsed, "producer_assignment_id": producer_assignment_id}
    )
    replay = _replay(
        root,
        scope=scope,
        operation=operation,
        assignment_id=producer_assignment_id,
        operation_id=operation_id,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay

    producer = load_assignment(root, producer_assignment_id)
    producer_revision = assignment_result_revision(producer)
    if producer_revision != parsed["producer_result_revision"]:
        raise _fail(
            operation=operation,
            assignment_id=producer_assignment_id,
            operation_id=operation_id,
            code="stale_producer_result",
            message="coord review producer_result_revision does not match the current producer result.",
        )
    review_assignment_id = parsed["review_assignment_id"]
    review_path = root / "assignments" / f"{review_assignment_id}.yaml"
    review_dependency = (review_path, review_path.read_bytes())
    reviewer = load_assignment(root, review_assignment_id)
    if _review_target(reviewer) != producer_assignment_id:
        raise ValueError("coord review reviewer dependency does not target the producer assignment")
    if reviewer.status != "completed" or not reviewer.result:
        raise ValueError("coord review requires a completed reviewer Evidence Bundle")
    review_result = deepcopy(reviewer.result)
    _validate_persisted_bundle(review_result, assignment_id=review_assignment_id)
    review_revision = assignment_result_revision(reviewer)
    if review_revision != parsed["review_result_revision"]:
        raise _fail(
            operation=operation,
            assignment_id=producer_assignment_id,
            operation_id=operation_id,
            code="stale_review_result",
            message="coord review review_result_revision does not match the reviewer result.",
        )
    if review_result.get("bundle_kind") != "review_result":
        raise ValueError("coord review requires a review_result Evidence Bundle")
    review_block = review_result.get("review", {})
    if (
        review_block.get("producer_assignment_id") != producer_assignment_id
        or review_block.get("producer_result_revision") != producer_revision
    ):
        raise ValueError("coord review bundle does not match the producer identity and revision")
    verdict = str(review_block.get("verdict") or "")
    status = "pending" if verdict == "inconclusive" else verdict
    if status not in {"approved", "changes_requested", "pending"}:
        raise ValueError(f"coord review has invalid verdict: {verdict!r}")

    producer_path = root / "assignments" / f"{producer_assignment_id}.yaml"
    producer_after = producer.to_dict(
        review={
            "required": True,
            "status": status,
            "result_revision": None if status == "pending" else review_revision,
        },
        updated_at=_timestamp(_utc_now(now)),
    )
    errors = assignment_contract_errors(AssignmentRecord.from_dict(producer_after))
    if errors:
        raise ValidationError(errors)
    receipt = success_receipt(
        operation=operation,
        assignment_id=producer_assignment_id,
        operation_id=operation_id,
        changed=True,
        packet_revision=producer_revision,
        readiness="not_applicable",
        allowed_operations=[],
    )
    receipt.update(
        {
            "result_revision": review_revision,
            "entities": {
                "producer_assignment_id": producer_assignment_id,
                "review_assignment_id": review_assignment_id,
                "producer_result_revision": producer_revision,
                "review_result_revision": review_revision,
                "verdict": verdict,
            },
        }
    )
    try:
        transaction = execute_mutation_transaction(
            root,
            [(producer_path, producer.raw, producer_after)],
            interactions=[
                {
                    "kind": "coordinator_review_applied",
                    "actor": "coordinator",
                    "node_id": producer.current_node,
                    "command": f"research-cockpit coord review --assignment {producer_assignment_id}",
                    "before": {"review": deepcopy(producer.review)},
                    "after": {
                        "review": deepcopy(producer_after["review"]),
                        "review_assignment_id": review_assignment_id,
                    },
                }
            ],
            rebuild_dashboard=False,
            read_dependencies=[review_dependency],
            operation_request={
                "scope": scope,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "receipt": receipt,
                "operation": operation,
                "assignment_id": producer_assignment_id,
            },
        )
    except MutationError as exc:        raise _transaction_error(
            exc,
            operation=operation,
            assignment_id=producer_assignment_id,
            operation_id=operation_id,
        ) from exc
    replayed = transaction.get("operation_receipt")
    return deepcopy(replayed) if isinstance(replayed, dict) else receipt
