from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from research_cockpit.agent_state import (
    AssignmentRecord,
    assignment_contract_errors,
    load_assignment,
)
from research_cockpit.assignment_leases import (
    AssignmentLeaseError,
    DEFAULT_LEASE_SECONDS,
    plan_assignment_lease_renewal,
)
from research_cockpit.commands._runtime import stable_payload_revision
from research_cockpit.operation_receipts import error_receipt
from research_cockpit.public_contracts import parse_public_contract
from research_cockpit.types import ValidationError
from research_cockpit.work_packets import build_work_packet_for_assignment


COLLECTION_LIMIT = 20
EVIDENCE_BUNDLE_MAX_BYTES = 16 * 1024
TEXT_ITEM_LIMIT = 500


def bounded_collection(values: list[Any], *, limit: int = COLLECTION_LIMIT) -> dict[str, Any]:
    items = deepcopy(list(values[:limit]))
    return {
        "items": items,
        "limit": limit,
        "total": len(values),
        "omitted": max(0, len(values) - len(items)),
    }


def _bounded_text(value: Any, *, limit: int = TEXT_ITEM_LIMIT) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _relative_delivery_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError("assignment_result.delivery.changed_files must use repository-relative paths")
    if len(text) > TEXT_ITEM_LIMIT:
        raise ValueError("assignment_result.delivery.changed_files item exceeds 500 characters")
    return path.as_posix()


def _delivery(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("assignment_result.delivery must be a mapping")
    allowed = {"git_commit", "changed_files", "tests"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("assignment_result.delivery does not support: " + ", ".join(unknown))
    changed_files = value.get("changed_files", [])
    if not isinstance(changed_files, list):
        raise ValueError("assignment_result.delivery.changed_files must be a list")
    tests = value.get("tests")
    if not isinstance(tests, dict):
        raise ValueError("assignment_result.delivery.tests must be a mapping")
    if sorted(tests) != ["status", "summary"]:
        raise ValueError("assignment_result.delivery.tests requires only status and summary")
    return {
        "git_commit": None
        if value.get("git_commit") is None
        else _bounded_text(value.get("git_commit"), limit=200),
        "changed_files": bounded_collection(
            [_relative_delivery_path(item) for item in changed_files]
        ),
        "tests": {
            "status": str(tests.get("status") or "").strip(),
            "summary": _bounded_text(tests.get("summary")),
        },
    }


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    rows = [_bounded_text(item) for item in value]
    if any(not item for item in rows):
        raise ValueError(f"{field_name} items must be non-empty strings")
    return rows


def _proposal(value: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"assignment_result.proposals[{index}] must be a mapping")
    required = {
        "kind",
        "title",
        "rationale",
        "parent_candidate",
        "dependencies",
        "success_criteria",
        "expected_deliverables",
    }
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise ValueError(
            f"assignment_result.proposals[{index}] is missing: " + ", ".join(missing)
        )
    if unknown:
        raise ValueError(
            f"assignment_result.proposals[{index}] does not support: " + ", ".join(unknown)
        )
    return {
        "kind": str(value["kind"]).strip(),
        "title": _bounded_text(value["title"], limit=200),
        "rationale": _bounded_text(value["rationale"], limit=800),
        "parent_candidate": _bounded_text(value["parent_candidate"], limit=200),
        "dependencies": bounded_collection(
            _string_list(value["dependencies"], f"assignment_result.proposals[{index}].dependencies")
        ),
        "success_criteria": bounded_collection(
            _string_list(
                value["success_criteria"],
                f"assignment_result.proposals[{index}].success_criteria",
            )
        ),
        "expected_deliverables": bounded_collection(
            _string_list(
                value["expected_deliverables"],
                f"assignment_result.proposals[{index}].expected_deliverables",
            )
        ),
    }


def build_evidence_bundle(
    *,
    assignment_id: str,
    operation_id: str,
    input_revision: str,
    result_spec: dict[str, Any],
    run_ids: list[str],
    finding_ids: list[str],
    artifact_record_ids: list[str],
    packet_revision: str,
    bundle_kind: str = "work_result",
    review: dict[str, Any] | None = None,
    extra_proposals: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str]:
    allowed = {"outcome", "summary", "delivery", "proposals"}
    unknown = sorted(set(result_spec) - allowed)
    if unknown:
        raise ValueError("assignment_result does not support: " + ", ".join(unknown))
    proposals = result_spec.get("proposals", [])
    if not isinstance(proposals, list):
        raise ValueError("assignment_result.proposals must be a list")
    normalized_proposals = [
        _proposal(item, index=index)
        for index, item in enumerate([*proposals, *(extra_proposals or [])])
    ]
    bundle = {
        "schema_version": "evidence_bundle_v1",
        "bundle_kind": bundle_kind,
        "assignment_id": assignment_id,
        "operation_id": operation_id,
        "input_revision": str(input_revision or "").strip(),
        "outcome": str(result_spec.get("outcome") or "").strip(),
        "summary": _bounded_text(result_spec.get("summary"), limit=1000),
        "runs": bounded_collection([_bounded_text(item, limit=200) for item in run_ids]),
        "findings": bounded_collection(
            [_bounded_text(item, limit=200) for item in finding_ids]
        ),
        "artifact_records": bounded_collection(
            [_bounded_text(item, limit=200) for item in artifact_record_ids]
        ),
        "delivery": _delivery(result_spec.get("delivery")),
        "proposals": bounded_collection(normalized_proposals),
        "verification": {
            "status": "internally_verified",
            "packet_revision": packet_revision,
            "additional_verification_required": False,
            "commands": bounded_collection([]),
        },
        "review": deepcopy(review),
    }
    parse_public_contract(bundle, mode="mutation")
    revision = stable_payload_revision(bundle, prefix="result-v1")
    encoded_size = len(
        json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    if encoded_size > EVIDENCE_BUNDLE_MAX_BYTES:
        raise ValueError("Evidence Bundle exceeds the 16 KiB contract budget")
    return bundle, revision


def persisted_result(bundle: dict[str, Any], revision: str) -> dict[str, Any]:
    return {**deepcopy(bundle), "revision": revision}


def _transition_error(
    *,
    assignment_id: str,
    operation_id: str,
    code: str,
    message: str,
    lease_id: str | None,
) -> AssignmentLeaseError:
    return AssignmentLeaseError(
        error_receipt(
            operation="work close",
            assignment_id=assignment_id,
            operation_id=operation_id,
            code=code,
            message=message,
            lease_id=lease_id,
        )
    )


def _commit_time() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _read_dependency(path: Path) -> tuple[Path, bytes | None]:
    try:
        return path, path.read_bytes()
    except FileNotFoundError:
        return path, None


def _packet_read_dependencies(
    root: Path,
    assignment: AssignmentRecord,
) -> list[tuple[Path, bytes | None]]:
    paths = {
        root / "current_state.yaml",
        root / "graph" / "edges.yaml",
    }
    pending = [
        str(item.get("assignment_id") or "")
        for item in assignment.dependencies
        if item.get("assignment_id")
    ]
    seen: set[str] = set()
    while pending and len(seen) < 200:
        dependency_id = pending.pop()
        if dependency_id in seen:
            continue
        seen.add(dependency_id)
        path = root / "assignments" / f"{dependency_id}.yaml"
        paths.add(path)
        try:
            dependency = load_assignment(root, dependency_id)
        except FileNotFoundError:
            continue
        pending.extend(
            str(item.get("assignment_id") or "")
            for item in dependency.dependencies
            if item.get("assignment_id")
        )
    return [
        _read_dependency(path)
        for path in sorted(paths, key=lambda item: item.as_posix())
    ]


def _freshness_validator(
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
        current = _commit_time() if refresh_commit_clock else planned_now
        expires_at = _parse_utc_timestamp(
            (assignment_before.get("lease") or {}).get("expires_at")
        )
        if expires_at is None or expires_at <= current:
            raise _transition_error(
                assignment_id=candidate.assignment_id,
                operation_id=operation_id,
                code="lease_expired",
                message="Assignment lease expired before work close could commit.",
                lease_id=lease_id,
            )
        fresh_packet = build_work_packet_for_assignment(root, candidate, now=current)
        expected_input_revision = (
            candidate.input_revision
            or fresh_packet.get("input_revision")
            or fresh_packet.get("revision")
        )
        close_allowed = "close" in fresh_packet.get("allowed_operations", {}).get(
            "items", []
        )
        if (
            str(fresh_packet.get("revision") or "") != packet_revision
            or input_revision != expected_input_revision
            or not close_allowed
        ):
            raise _transition_error(
                assignment_id=candidate.assignment_id,
                operation_id=operation_id,
                code="stale_inputs",
                message="Work Packet truth changed before work close could commit.",
                lease_id=lease_id,
            )

    return validate


def plan_work_result_transition(
    root: Path,
    *,
    assignment_id: str,
    agent_id: str,
    lease_id: str,
    lease_epoch: int,
    operation_id: str,
    input_revision: str,
    result_spec: dict[str, Any],
    run_ids: list[str],
    finding_ids: list[str],
    artifact_record_ids: list[str],
    next_experiment: dict[str, Any] | None,
    next_actions: list[str],
    review_required: bool | None,
    now: datetime,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    refresh_commit_clock: bool = False,
) -> dict[str, Any]:
    lease_changes, candidate, _lease_before, _lease_after = plan_assignment_lease_renewal(
        root,
        assignment_id=assignment_id,
        agent_id=agent_id,
        lease_id=lease_id,
        lease_epoch=lease_epoch,
        operation="work close",
        operation_id=operation_id,
        now=now,
        lease_seconds=lease_seconds,
    )
    if candidate.kind == "review" or candidate.scope.get("write_policy") == "review_read_only":
        raise _transition_error(
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="review_scope_read_only",
            message="Review assignments must use review report and cannot close producer work.",
            lease_id=lease_id,
        )
    packet = build_work_packet_for_assignment(root, candidate, now=now)
    if "close" not in packet.get("allowed_operations", {}).get("items", []):
        raise _transition_error(
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="assignment_not_ready",
            message=f"Assignment readiness {packet.get('readiness')!r} does not allow work close.",
            lease_id=lease_id,
        )
    expected_input_revision = (
        candidate.input_revision
        or packet.get("input_revision")
        or packet.get("revision")
    )
    if input_revision != expected_input_revision:
        raise _transition_error(
            assignment_id=assignment_id,
            operation_id=operation_id,
            code="stale_inputs",
            message=(
                f"work close input_revision {input_revision!r} does not match "
                f"{expected_input_revision!r}."
            ),
            lease_id=lease_id,
        )

    local_proposals: list[dict[str, Any]] = []
    if next_experiment:
        local_proposals.append(
            {
                "kind": "local_followup",
                "title": str(next_experiment["title"]),
                "rationale": "Continue the same assignment scope after this closeout.",
                "parent_candidate": str(next_experiment.get("parent") or candidate.root_node),
                "dependencies": [],
                "success_criteria": list(next_experiment.get("success_criteria", []) or []),
                "expected_deliverables": ["run", "finding"],
            }
        )
    bundle, result_revision = build_evidence_bundle(
        assignment_id=assignment_id,
        operation_id=operation_id,
        input_revision=input_revision,
        result_spec=result_spec,
        run_ids=run_ids,
        finding_ids=finding_ids,
        artifact_record_ids=artifact_record_ids,
        packet_revision=str(packet["revision"]),
        extra_proposals=local_proposals,
    )

    assignment_path, assignment_before, assignment_after = lease_changes[0]
    agent_path, agent_before, agent_after = lease_changes[1]
    assignment_after = deepcopy(assignment_after)
    agent_after = deepcopy(agent_after)
    assignment_after["result"] = persisted_result(bundle, result_revision)
    if next_experiment:
        assignment_after["status"] = "active"
        assignment_after["current_node"] = str(next_experiment["id"])
        assignment_after["next_actions"] = list(next_actions)
        allowed_operations = ["start", "record", "close"]
    else:
        required = bool(candidate.review.get("required", False)) or bool(
            review_required
        )
        assignment_after.update(
            {
                "agent_id": None,
                "status": "completed",
                "next_actions": [],
                "lease": {
                    "lease_id": None,
                    "owner_agent_id": None,
                    "lease_epoch": 0,
                    "heartbeat_at": None,
                    "expires_at": None,
                },
                "review": {
                    "required": required,
                    "status": "pending" if required else "not_required",
                    "result_revision": None,
                },
            }
        )
        active = [
            item
            for item in agent_after.get("active_assignment_ids", [])
            if item != assignment_id
        ]
        agent_after["active_assignment_ids"] = active
        agent_after["status"] = "active" if active else "idle"
        allowed_operations = []
    parsed = AssignmentRecord.from_dict(assignment_after)
    errors = assignment_contract_errors(parsed)
    if errors:
        raise ValidationError(errors)
    assignment_before = lease_changes[0][1]
    packet_revision = str(packet["revision"])
    return {
        "changes": [
            (assignment_path, assignment_before, assignment_after),
            (agent_path, agent_before, agent_after),
        ],
        "bundle": bundle,
        "result_revision": result_revision,
        "packet_revision": packet_revision,
        "allowed_operations": allowed_operations,
        "read_dependencies": _packet_read_dependencies(root, candidate),
        "commit_validators": [
            _freshness_validator(
                root,
                candidate=candidate,
                assignment_before=assignment_before,
                packet_revision=packet_revision,
                input_revision=input_revision,
                operation_id=operation_id,
                lease_id=lease_id,
                planned_now=now,
                refresh_commit_clock=refresh_commit_clock,
            )
        ],
    }
