from __future__ import annotations

from copy import deepcopy
from typing import Any


ROLE_OPERATIONS: dict[str, tuple[str, ...]] = {
    "worker": ("open", "claim", "renew", "start", "record", "close"),
    "reviewer": ("open", "report"),
    "coordinator": ("overview", "assign", "review", "decide", "handoff"),
    "maintainer": ("audit", "repair", "migrate", "compact"),
}

WORKFLOW_BUDGETS: dict[str, int] = {
    "assigned_worker_cli_invocations": 3,
    "reviewer_cli_invocations": 2,
    "handoff_cli_invocations": 1,
    "core_nested_subprocesses": 0,
    "extra_verification_after_internal_success": 0,
    "worker_stdout_bytes": 12 * 1024,
    "mutation_receipt_bytes": 2 * 1024,
}

def _bounded(items: list[Any], *, limit: int = 20) -> dict[str, Any]:
    return {
        "items": items,
        "limit": limit,
        "total": len(items),
        "omitted": 0,
    }


PUBLIC_CONTRACT_EXAMPLES: dict[str, dict[str, Any]] = {
    "work_packet_v1": {
        "schema_version": "work_packet_v1",
        "revision": "packet-v1:abc123",
        "revision_status": "fresh",
        "input_revision": "input-v1:abc123",
        "assignment_id": "assign_x",
        "agent_id": "agent_x",
        "kind": "experiment",
        "status": "active",
        "readiness": "ready",
        "objective": "Test strategy X against the accepted baseline.",
        "scope": {
            "root_node": "option_x",
            "subtree_policy": "descendants_only",
            "write_policy": "exclusive",
        },
        "dependencies": _bounded(
            [
                {
                    "assignment_id": "assign_baseline",
                    "required_review_status": "approved",
                }
            ]
        ),
        "inputs": {
            "effective_baseline_revision": "exec-v1:abc123",
            "dependency_revisions": {"assign_baseline": "result-v1:def456"},
        },
        "stale_inputs": _bounded([]),
        "success_criteria": _bounded(
            ["Accuracy improves without exceeding the latency budget."]
        ),
        "deliverables": _bounded(
            ["run", "artifact_record", "finding", "git_commit"]
        ),
        "lease": {
            "owner_agent_id": "agent_x",
            "lease_id": "lease_x",
            "lease_epoch": 1,
            "heartbeat_at": "2026-07-19T10:00:00Z",
            "expires_at": "2026-07-19T10:15:00Z",
        },
        "review": {
            "required": True,
            "status": "pending",
            "result_revision": None,
        },
        "allowed_operations": _bounded(["start", "record", "close"]),
        "cursor": {
            "current_node": "experiment_x",
            "next_actions": _bounded(["Start the bounded evaluation."]),
        },
    },
    "evidence_bundle_v1": {
        "schema_version": "evidence_bundle_v1",
        "bundle_kind": "work_result",
        "assignment_id": "assign_x",
        "operation_id": "op_close_assign_x",
        "input_revision": "packet-v1:abc123",
        "outcome": "positive",
        "summary": "Strategy X improved accuracy within the latency budget.",
        "runs": _bounded(["run_x"]),
        "findings": _bounded(["finding_x"]),
        "artifact_records": _bounded(["artifact_record_x"]),
        "delivery": {
            "git_commit": "abcdef1",
            "changed_files": _bounded(["src/retrieval.py"]),
            "tests": {"status": "passed", "summary": "Targeted tests passed."},
        },
        "proposals": _bounded(
            [
                {
                    "kind": "new_branch",
                    "title": "Test strategy X under long-context workloads.",
                    "rationale": "The current evidence does not cover long contexts.",
                    "parent_candidate": "option_x",
                    "dependencies": _bounded(["assign_x"]),
                    "success_criteria": _bounded(
                        ["Long-context quality remains within the accepted budget."]
                    ),
                    "expected_deliverables": _bounded(
                        ["run", "artifact_record", "finding"]
                    ),
                }
            ]
        ),
        "verification": {
            "status": "internally_verified",
            "packet_revision": "packet-v1:abc123",
            "additional_verification_required": False,
            "commands": _bounded([]),
        },
        "review": None,
    },
    "synthesis_packet_v1": {
        "schema_version": "synthesis_packet_v1",
        "revision": "synthesis-v1:abc123",
        "research_question": "Which strategy should become the baseline?",
        "candidate_options": _bounded(["option_x"]),
        "evidence_bundles": _bounded(["result-v1:def456"]),
        "outcome_summaries": _bounded(
            [
                {
                    "assignment_id": "assign_x",
                    "result_revision": "result-v1:def456",
                    "outcome": "positive",
                    "confidence": "high",
                    "summary": "Strategy X improved accuracy within budget.",
                }
            ]
        ),
        "metrics": _bounded(
            [
                {
                    "name": "accuracy",
                    "value": 0.84,
                    "unit": "ratio",
                    "source_result_revision": "result-v1:def456",
                }
            ]
        ),
        "gate_summaries": _bounded(
            [
                {
                    "gate_id": "gate_x",
                    "status": "passed",
                    "summary": "Quality and latency gates passed.",
                    "source_result_revision": "result-v1:def456",
                }
            ]
        ),
        "artifact_links": _bounded(["artifact_record_x"]),
        "contradictions": _bounded([]),
        "missing_evidence": _bounded([]),
        "stale_input_warnings": _bounded([]),
        "decision_criteria": _bounded(["accuracy", "latency"]),
        "unresolved_questions": _bounded([]),
    },

    "coordination_snapshot_v1": {
        "schema_version": "coordination_snapshot_v1",
        "revision": "coord-v1:abc123",
        "counts": {
            "waiting": 0,
            "ready": 1,
            "active": 1,
            "blocked": 0,
            "stale_inputs": 0,
            "expired_leases": 0,
            "pending_review": 0,
        },
        "assignments": _bounded([]),
        "overlap_warnings": _bounded([]),
        "next_page": None,
    },
    "work_operation_v1": {
        "schema_version": "work_operation_v1",
        "ok": True,
        "operation": "work close",
        "assignment_id": "assign_x",
        "operation_id": "op_x",
        "changed": True,
        "packet_revision": "packet-v1:abc123",
        "readiness": "ready",
        "required_action": {
            "kind": "none",
            "command": None,
            "reason": None,
        },
        "allowed_operations": _bounded([]),
        "verification": {
            "status": "internally_verified",
            "additional_verification_required": False,
            "commands": _bounded([]),
        },
        "warnings": _bounded([]),
        "error": None,
        "partial_success": False,
        "rolled_back": False,
    },

}

PUBLIC_CONTRACT_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "work_packet_v1": frozenset(
        {
            "revision",
            "revision_status",
            "input_revision",
            "assignment_id",
            "agent_id",
            "kind",
            "status",
            "readiness",
            "objective",
            "scope",
            "dependencies",
            "inputs",
            "stale_inputs",
            "success_criteria",
            "deliverables",
            "lease",
            "review",
            "allowed_operations",
            "cursor",
        }
    ),
    "evidence_bundle_v1": frozenset(
        {
            "bundle_kind",
            "assignment_id",
            "operation_id",
            "input_revision",
            "outcome",
            "summary",
            "runs",
            "findings",
            "artifact_records",
            "delivery",
            "proposals",
            "verification",
            "review",
        }
    ),
    "synthesis_packet_v1": frozenset(
        {
            "revision",
            "research_question",
            "candidate_options",
            "evidence_bundles",
            "outcome_summaries",
            "metrics",
            "gate_summaries",
            "artifact_links",
            "contradictions",
            "missing_evidence",
            "stale_input_warnings",
            "decision_criteria",
            "unresolved_questions",
        }
    ),
    "coordination_snapshot_v1": frozenset(
        {
            "revision",
            "counts",
            "assignments",
            "overlap_warnings",
            "next_page",
        }
    ),
    "work_operation_v1": frozenset(
        {
            "ok",
            "operation",
            "assignment_id",
            "operation_id",
            "changed",
            "packet_revision",
            "readiness",
            "required_action",
            "allowed_operations",
            "verification",
            "warnings",
            "error",
            "partial_success",
            "rolled_back",
        }
    ),
}


def _contract_error(path: str, message: str) -> None:
    raise ValueError(f"{path}: {message}")


def _require_mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _contract_error(path, "must be a mapping")
    return value


def _require_string(
    value: object,
    path: str,
    *,
    nullable: bool = False,
) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        _contract_error(path, "must be a non-empty string")
    return value


def _require_bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        _contract_error(path, "must be a boolean")
    return value


def _require_int(value: object, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _contract_error(path, f"must be an integer >= {minimum}")
    return value


def _require_enum(value: object, path: str, choices: frozenset[str]) -> str:
    resolved = _require_string(value, path)
    if resolved not in choices:
        _contract_error(path, f"must be one of: {', '.join(sorted(choices))}")
    return resolved


def _require_fields(data: dict[str, Any], path: str, fields: set[str]) -> None:
    missing = sorted(field for field in fields if field not in data)
    if missing:
        _contract_error(path, f"missing required fields: {', '.join(missing)}")


def _reject_unknown_fields(
    data: dict[str, Any],
    path: str,
    allowed: set[str],
    *,
    strict: bool,
) -> None:
    if not strict:
        return
    unknown = sorted(set(data) - allowed)
    if unknown:
        _contract_error(path, f"unknown fields: {', '.join(unknown)}")


def _require_metric_value(value: object, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        _contract_error(path, "must be a number or non-empty string")
    if isinstance(value, str) and not value.strip():
        _contract_error(path, "must be a number or non-empty string")


def _validate_bounded_collection(
    value: object,
    path: str,
    *,
    item_kind: str | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    data = _require_mapping(value, path)
    fields = {"items", "limit", "total", "omitted"}
    _require_fields(data, path, fields)
    _reject_unknown_fields(data, path, fields, strict=strict)
    items = data["items"]
    if not isinstance(items, list):
        _contract_error(f"{path}.items", "must be a list")
    limit = _require_int(data["limit"], f"{path}.limit", minimum=1)
    if limit > 100:
        _contract_error(f"{path}.limit", "must be <= 100")
    total = _require_int(data["total"], f"{path}.total")
    omitted = _require_int(data["omitted"], f"{path}.omitted")
    if len(items) > limit:
        _contract_error(path, "items exceed limit")
    if total != len(items) + omitted:
        _contract_error(path, "total must equal len(items) + omitted")
    for index, item in enumerate(items):
        item_path = f"{path}.items[{index}]"
        if item_kind == "string":
            _require_string(item, item_path)
        elif item_kind == "mapping":
            _require_mapping(item, item_path)
    return data


def _validate_verification(
    value: object,
    path: str,
    *,
    strict: bool = False,
) -> None:
    data = _require_mapping(value, path)
    required = {"status", "additional_verification_required", "commands"}
    allowed = {*required, "packet_revision"}
    _require_fields(data, path, required)
    _reject_unknown_fields(data, path, allowed, strict=strict)
    status = _require_enum(
        data["status"],
        f"{path}.status",
        frozenset(
            {
                "internally_verified",
                "additional_verification_required",
                "failed",
            }
        ),
    )
    additional = _require_bool(
        data["additional_verification_required"],
        f"{path}.additional_verification_required",
    )
    commands = _validate_bounded_collection(
        data["commands"],
        f"{path}.commands",
        item_kind="string",
        strict=strict,
    )
    if "packet_revision" in data:
        _require_string(
            data["packet_revision"],
            f"{path}.packet_revision",
            nullable=True,
        )
    if status == "internally_verified":
        if additional or commands["total"]:
            _contract_error(
                path,
                "internally_verified requires no additional verification commands",
            )
    elif not additional or commands["total"] < 1:
        _contract_error(
            path,
            f"{status} requires additional_verification_required=true and commands",
        )


def _validate_required_action(
    value: object,
    path: str,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    data = _require_mapping(value, path)
    fields = {"kind", "command", "reason"}
    _require_fields(data, path, fields)
    _reject_unknown_fields(data, path, fields, strict=strict)
    kind = _require_enum(
        data["kind"],
        f"{path}.kind",
        frozenset(
            {
                "none",
                "reopen_packet",
                "resolve_dependencies",
                "run_verification",
                "manual_recovery",
            }
        ),
    )
    command = _require_string(
        data["command"],
        f"{path}.command",
        nullable=True,
    )
    reason = _require_string(
        data["reason"],
        f"{path}.reason",
        nullable=True,
    )
    if kind == "none":
        if command is not None or reason is not None:
            _contract_error(path, "kind=none requires null command and reason")
    elif command is None:
        _contract_error(
            f"{path}.command",
            f"kind={kind} requires a command",
        )
    elif reason is None:
        _contract_error(
            f"{path}.reason",
            f"kind={kind} requires a reason",
        )
    return data



def _validate_work_packet(
    payload: dict[str, Any],
    *,
    strict: bool = False,
) -> None:
    path = "work_packet_v1"
    _require_string(payload["revision"], f"{path}.revision")
    revision_status = _require_enum(
        payload["revision_status"],
        f"{path}.revision_status",
        frozenset({"fresh", "stale", "unknown"}),
    )
    input_revision = _require_string(
        payload["input_revision"],
        f"{path}.input_revision",
        nullable=True,
    )
    _require_string(payload["assignment_id"], f"{path}.assignment_id")
    _require_string(payload["agent_id"], f"{path}.agent_id", nullable=True)
    _require_string(payload["kind"], f"{path}.kind")
    _require_enum(
        payload["status"],
        f"{path}.status",
        frozenset(
            {"queued", "active", "blocked", "completed", "cancelled", "retired"}
        ),
    )
    readiness = _require_enum(
        payload["readiness"],
        f"{path}.readiness",
        frozenset(
            {
                "waiting_dependencies",
                "ready",
                "stale_inputs",
                "unknown_inputs",
            }
        ),
    )
    _require_string(payload["objective"], f"{path}.objective")

    scope = _require_mapping(payload["scope"], f"{path}.scope")
    scope_fields = {"root_node", "subtree_policy", "write_policy"}
    _require_fields(scope, f"{path}.scope", scope_fields)
    _reject_unknown_fields(
        scope,
        f"{path}.scope",
        scope_fields,
        strict=strict,
    )
    _require_string(scope["root_node"], f"{path}.scope.root_node")
    _require_string(scope["subtree_policy"], f"{path}.scope.subtree_policy")
    _require_enum(
        scope["write_policy"],
        f"{path}.scope.write_policy",
        frozenset(
            {"exclusive", "append_only", "review_read_only", "coordinator"}
        ),
    )

    dependencies = _validate_bounded_collection(
        payload["dependencies"],
        f"{path}.dependencies",
        item_kind="mapping",
        strict=strict,
    )
    dependency_fields = {
        "assignment_id",
        "required_status",
        "required_review_status",
    }
    for index, item in enumerate(dependencies["items"]):
        dependency_path = f"{path}.dependencies.items[{index}]"
        _require_fields(item, dependency_path, {"assignment_id"})
        _reject_unknown_fields(
            item,
            dependency_path,
            dependency_fields,
            strict=strict,
        )
        _require_string(item["assignment_id"], f"{dependency_path}.assignment_id")
        if "required_status" in item:
            _require_string(
                item["required_status"],
                f"{dependency_path}.required_status",
            )
        if "required_review_status" in item:
            _require_enum(
                item["required_review_status"],
                f"{dependency_path}.required_review_status",
                frozenset({"approved", "changes_requested"}),
            )

    inputs = _require_mapping(payload["inputs"], f"{path}.inputs")
    input_fields = {
        "effective_baseline_revision",
        "dependency_revisions",
    }
    _require_fields(inputs, f"{path}.inputs", input_fields)
    _reject_unknown_fields(
        inputs,
        f"{path}.inputs",
        input_fields,
        strict=strict,
    )
    _require_string(
        inputs["effective_baseline_revision"],
        f"{path}.inputs.effective_baseline_revision",
        nullable=True,
    )
    revisions = _require_mapping(
        inputs["dependency_revisions"],
        f"{path}.inputs.dependency_revisions",
    )
    for key, value in revisions.items():
        _require_string(key, f"{path}.inputs.dependency_revisions key")
        _require_string(value, f"{path}.inputs.dependency_revisions.{key}")

    stale_inputs = _validate_bounded_collection(
        payload["stale_inputs"],
        f"{path}.stale_inputs",
        item_kind="string",
        strict=strict,
    )
    for field in ("success_criteria", "deliverables", "allowed_operations"):
        _validate_bounded_collection(
            payload[field],
            f"{path}.{field}",
            item_kind="string",
            strict=strict,
        )

    lease = _require_mapping(payload["lease"], f"{path}.lease")
    lease_fields = {
        "owner_agent_id",
        "lease_id",
        "lease_epoch",
        "heartbeat_at",
        "expires_at",
    }
    _require_fields(lease, f"{path}.lease", lease_fields)
    _reject_unknown_fields(
        lease,
        f"{path}.lease",
        lease_fields,
        strict=strict,
    )
    lease_values = {
        field: _require_string(
            lease[field],
            f"{path}.lease.{field}",
            nullable=True,
        )
        for field in (
            "owner_agent_id",
            "lease_id",
            "heartbeat_at",
            "expires_at",
        )
    }
    lease_epoch = _require_int(
        lease["lease_epoch"],
        f"{path}.lease.lease_epoch",
    )
    if lease_values["lease_id"] is None:
        if lease_epoch != 0 or any(lease_values.values()):
            _contract_error(
                f"{path}.lease",
                "an unclaimed lease requires epoch 0 and null lease fields",
            )
    elif (
        lease_epoch < 1
        or lease_values["owner_agent_id"] is None
        or lease_values["heartbeat_at"] is None
        or lease_values["expires_at"] is None
    ):
        _contract_error(
            f"{path}.lease",
            "an active lease requires owner, timestamps, and epoch >= 1",
        )

    review = _require_mapping(payload["review"], f"{path}.review")
    review_fields = {"required", "status", "result_revision"}
    _require_fields(review, f"{path}.review", review_fields)
    _reject_unknown_fields(
        review,
        f"{path}.review",
        review_fields,
        strict=strict,
    )
    review_required = _require_bool(
        review["required"],
        f"{path}.review.required",
    )
    review_status = _require_enum(
        review["status"],
        f"{path}.review.status",
        frozenset(
            {"not_required", "pending", "approved", "changes_requested"}
        ),
    )
    result_revision = _require_string(
        review["result_revision"],
        f"{path}.review.result_revision",
        nullable=True,
    )
    if not review_required and (
        review_status != "not_required" or result_revision is not None
    ):
        _contract_error(
            f"{path}.review",
            "review.required=false requires not_required and null result_revision",
        )
    if review_required and review_status == "not_required":
        _contract_error(
            f"{path}.review.status",
            "review.required=true cannot use not_required",
        )
    if review_status in {"approved", "changes_requested"} and result_revision is None:
        _contract_error(
            f"{path}.review.result_revision",
            f"{review_status} requires a result revision",
        )
    if review_status == "pending" and result_revision is not None:
        _contract_error(
            f"{path}.review.result_revision",
            "pending review requires a null result revision",
        )

    cursor = _require_mapping(payload["cursor"], f"{path}.cursor")
    cursor_fields = {"current_node", "next_actions"}
    _require_fields(cursor, f"{path}.cursor", cursor_fields)
    _reject_unknown_fields(
        cursor,
        f"{path}.cursor",
        cursor_fields,
        strict=strict,
    )
    _require_string(
        cursor["current_node"],
        f"{path}.cursor.current_node",
        nullable=True,
    )
    _validate_bounded_collection(
        cursor["next_actions"],
        f"{path}.cursor.next_actions",
        item_kind="string",
        strict=strict,
    )

    if revision_status == "unknown":
        if input_revision is not None:
            _contract_error(
                f"{path}.input_revision",
                "revision_status=unknown requires null input_revision",
            )
        if readiness != "unknown_inputs":
            _contract_error(
                f"{path}.readiness",
                "revision_status=unknown requires unknown_inputs",
            )
    elif input_revision is None:
        _contract_error(
            f"{path}.input_revision",
            f"revision_status={revision_status} requires an input revision",
        )
    if revision_status == "stale":
        if readiness != "stale_inputs" or stale_inputs["total"] < 1:
            _contract_error(
                f"{path}.readiness",
                "revision_status=stale requires stale_inputs and at least one warning",
            )
    elif readiness == "stale_inputs":
        _contract_error(
            f"{path}.readiness",
            "stale_inputs requires revision_status=stale",
        )
    if revision_status == "fresh" and stale_inputs["total"]:
        _contract_error(
            f"{path}.stale_inputs",
            "revision_status=fresh requires no stale inputs",
        )



def _validate_evidence_bundle(
    payload: dict[str, Any],
    *,
    strict: bool = False,
) -> None:
    path = "evidence_bundle_v1"
    bundle_kind = _require_enum(
        payload["bundle_kind"],
        f"{path}.bundle_kind",
        frozenset({"work_result", "review_result"}),
    )
    for field in ("assignment_id", "operation_id", "input_revision", "summary"):
        _require_string(payload[field], f"{path}.{field}")
    _require_enum(
        payload["outcome"],
        f"{path}.outcome",
        frozenset({"positive", "negative", "inconclusive", "mixed"}),
    )
    for field in ("runs", "findings", "artifact_records"):
        _validate_bounded_collection(
            payload[field],
            f"{path}.{field}",
            item_kind="string",
            strict=strict,
        )

    delivery = _require_mapping(payload["delivery"], f"{path}.delivery")
    delivery_fields = {"git_commit", "changed_files", "tests"}
    _require_fields(delivery, f"{path}.delivery", delivery_fields)
    _reject_unknown_fields(
        delivery,
        f"{path}.delivery",
        delivery_fields,
        strict=strict,
    )
    _require_string(
        delivery["git_commit"],
        f"{path}.delivery.git_commit",
        nullable=True,
    )
    _validate_bounded_collection(
        delivery["changed_files"],
        f"{path}.delivery.changed_files",
        item_kind="string",
        strict=strict,
    )
    tests = _require_mapping(delivery["tests"], f"{path}.delivery.tests")
    test_fields = {"status", "summary"}
    _require_fields(tests, f"{path}.delivery.tests", test_fields)
    _reject_unknown_fields(
        tests,
        f"{path}.delivery.tests",
        test_fields,
        strict=strict,
    )
    _require_enum(
        tests["status"],
        f"{path}.delivery.tests.status",
        frozenset({"passed", "failed", "not_run"}),
    )
    _require_string(tests["summary"], f"{path}.delivery.tests.summary")

    proposals = _validate_bounded_collection(
        payload["proposals"],
        f"{path}.proposals",
        item_kind="mapping",
        strict=strict,
    )
    proposal_fields = {
        "kind",
        "title",
        "rationale",
        "parent_candidate",
        "dependencies",
        "success_criteria",
        "expected_deliverables",
    }
    for index, proposal in enumerate(proposals["items"]):
        proposal_path = f"{path}.proposals.items[{index}]"
        _require_fields(
            proposal,
            proposal_path,
            proposal_fields,
        )
        _reject_unknown_fields(
            proposal,
            proposal_path,
            proposal_fields,
            strict=strict,
        )
        _require_enum(
            proposal["kind"],
            f"{proposal_path}.kind",
            frozenset({"local_followup", "new_branch"}),
        )
        for field in ("title", "rationale", "parent_candidate"):
            _require_string(proposal[field], f"{proposal_path}.{field}")
        for field in (
            "dependencies",
            "success_criteria",
            "expected_deliverables",
        ):
            _validate_bounded_collection(
                proposal[field],
                f"{proposal_path}.{field}",
                item_kind="string",
                strict=strict,
            )
    _validate_verification(
        payload["verification"],
        f"{path}.verification",
        strict=strict,
    )

    review = payload["review"]
    if bundle_kind == "work_result":
        if review is not None:
            _contract_error(
                f"{path}.review",
                "work_result requires review=null",
            )
        return

    review_data = _require_mapping(review, f"{path}.review")
    review_fields = {
        "producer_assignment_id",
        "producer_result_revision",
        "findings",
        "evidence_inspected",
        "validation_performed",
        "verdict",
    }
    _require_fields(review_data, f"{path}.review", review_fields)
    _reject_unknown_fields(
        review_data,
        f"{path}.review",
        review_fields,
        strict=strict,
    )
    _require_string(
        review_data["producer_assignment_id"],
        f"{path}.review.producer_assignment_id",
    )
    _require_string(
        review_data["producer_result_revision"],
        f"{path}.review.producer_result_revision",
    )
    review_findings = _validate_bounded_collection(
        review_data["findings"],
        f"{path}.review.findings",
        item_kind="mapping",
        strict=strict,
    )
    finding_fields = {"severity", "code", "summary", "evidence_refs"}
    severity_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    observed_ranks: list[int] = []
    for index, finding in enumerate(review_findings["items"]):
        finding_path = f"{path}.review.findings.items[{index}]"
        _require_fields(finding, finding_path, finding_fields)
        _reject_unknown_fields(
            finding,
            finding_path,
            finding_fields,
            strict=strict,
        )
        severity = _require_enum(
            finding["severity"],
            f"{finding_path}.severity",
            frozenset(severity_rank),
        )
        observed_ranks.append(severity_rank[severity])
        _require_string(finding["code"], f"{finding_path}.code")
        _require_string(finding["summary"], f"{finding_path}.summary")
        _validate_bounded_collection(
            finding["evidence_refs"],
            f"{finding_path}.evidence_refs",
            item_kind="string",
            strict=strict,
        )
    if observed_ranks != sorted(observed_ranks):
        _contract_error(
            f"{path}.review.findings",
            "findings must be ordered by severity",
        )
    for field in ("evidence_inspected", "validation_performed"):
        _validate_bounded_collection(
            review_data[field],
            f"{path}.review.{field}",
            item_kind="string",
            strict=strict,
        )
    _require_enum(
        review_data["verdict"],
        f"{path}.review.verdict",
        frozenset({"approved", "changes_requested", "inconclusive"}),
    )



def _validate_synthesis_packet(
    payload: dict[str, Any],
    *,
    strict: bool = False,
) -> None:
    path = "synthesis_packet_v1"
    _require_string(payload["revision"], f"{path}.revision")
    _require_string(payload["research_question"], f"{path}.research_question")
    for field in (
        "candidate_options",
        "evidence_bundles",
        "artifact_links",
        "contradictions",
        "missing_evidence",
        "stale_input_warnings",
        "decision_criteria",
        "unresolved_questions",
    ):
        _validate_bounded_collection(
            payload[field],
            f"{path}.{field}",
            item_kind="string",
            strict=strict,
        )

    outcomes = _validate_bounded_collection(
        payload["outcome_summaries"],
        f"{path}.outcome_summaries",
        item_kind="mapping",
        strict=strict,
    )
    outcome_fields = {
        "assignment_id",
        "result_revision",
        "outcome",
        "confidence",
        "summary",
    }
    for index, outcome in enumerate(outcomes["items"]):
        outcome_path = f"{path}.outcome_summaries.items[{index}]"
        _require_fields(outcome, outcome_path, outcome_fields)
        _reject_unknown_fields(
            outcome,
            outcome_path,
            outcome_fields,
            strict=strict,
        )
        for field in ("assignment_id", "result_revision", "summary"):
            _require_string(outcome[field], f"{outcome_path}.{field}")
        _require_enum(
            outcome["outcome"],
            f"{outcome_path}.outcome",
            frozenset({"positive", "negative", "inconclusive", "mixed"}),
        )
        _require_enum(
            outcome["confidence"],
            f"{outcome_path}.confidence",
            frozenset({"unknown", "low", "medium", "high"}),
        )

    metrics = _validate_bounded_collection(
        payload["metrics"],
        f"{path}.metrics",
        item_kind="mapping",
        strict=strict,
    )
    metric_fields = {
        "name",
        "value",
        "unit",
        "source_result_revision",
    }
    for index, metric in enumerate(metrics["items"]):
        metric_path = f"{path}.metrics.items[{index}]"
        _require_fields(metric, metric_path, metric_fields)
        _reject_unknown_fields(
            metric,
            metric_path,
            metric_fields,
            strict=strict,
        )
        _require_string(metric["name"], f"{metric_path}.name")
        _require_metric_value(metric["value"], f"{metric_path}.value")
        _require_string(
            metric["unit"],
            f"{metric_path}.unit",
            nullable=True,
        )
        _require_string(
            metric["source_result_revision"],
            f"{metric_path}.source_result_revision",
        )

    gates = _validate_bounded_collection(
        payload["gate_summaries"],
        f"{path}.gate_summaries",
        item_kind="mapping",
        strict=strict,
    )
    gate_fields = {
        "gate_id",
        "status",
        "summary",
        "source_result_revision",
    }
    for index, gate in enumerate(gates["items"]):
        gate_path = f"{path}.gate_summaries.items[{index}]"
        _require_fields(gate, gate_path, gate_fields)
        _reject_unknown_fields(
            gate,
            gate_path,
            gate_fields,
            strict=strict,
        )
        for field in ("gate_id", "summary", "source_result_revision"):
            _require_string(gate[field], f"{gate_path}.{field}")
        _require_enum(
            gate["status"],
            f"{gate_path}.status",
            frozenset({"passed", "failed", "blocked", "not_run"}),
        )



def _validate_coordination_snapshot(
    payload: dict[str, Any],
    *,
    strict: bool = False,
) -> None:
    path = "coordination_snapshot_v1"
    _require_string(payload["revision"], f"{path}.revision")
    counts = _require_mapping(payload["counts"], f"{path}.counts")
    required_counts = {
        "waiting",
        "ready",
        "active",
        "blocked",
        "stale_inputs",
        "expired_leases",
        "pending_review",
    }
    _require_fields(counts, f"{path}.counts", required_counts)
    _reject_unknown_fields(
        counts,
        f"{path}.counts",
        required_counts,
        strict=strict,
    )
    for field in required_counts:
        _require_int(counts[field], f"{path}.counts.{field}")

    assignments = _validate_bounded_collection(
        payload["assignments"],
        f"{path}.assignments",
        item_kind="mapping",
        strict=strict,
    )
    assignment_fields = {
        "assignment_id",
        "kind",
        "status",
        "readiness",
        "agent_id",
        "root_node",
        "review_status",
        "lease_state",
        "packet_revision",
    }
    for index, assignment in enumerate(assignments["items"]):
        assignment_path = f"{path}.assignments.items[{index}]"
        _require_fields(
            assignment,
            assignment_path,
            assignment_fields,
        )
        _reject_unknown_fields(
            assignment,
            assignment_path,
            assignment_fields,
            strict=strict,
        )
        _require_string(
            assignment["assignment_id"],
            f"{assignment_path}.assignment_id",
        )
        _require_enum(
            assignment["kind"],
            f"{assignment_path}.kind",
            frozenset(
                {
                    "experiment",
                    "review",
                    "synthesis",
                    "coordination",
                    "maintenance",
                }
            ),
        )
        _require_enum(
            assignment["status"],
            f"{assignment_path}.status",
            frozenset(
                {
                    "queued",
                    "active",
                    "blocked",
                    "completed",
                    "cancelled",
                    "retired",
                }
            ),
        )
        _require_enum(
            assignment["readiness"],
            f"{assignment_path}.readiness",
            frozenset(
                {
                    "waiting_dependencies",
                    "ready",
                    "stale_inputs",
                    "unknown_inputs",
                }
            ),
        )
        _require_string(
            assignment["agent_id"],
            f"{assignment_path}.agent_id",
            nullable=True,
        )
        _require_string(
            assignment["root_node"],
            f"{assignment_path}.root_node",
        )
        _require_enum(
            assignment["review_status"],
            f"{assignment_path}.review_status",
            frozenset(
                {
                    "not_required",
                    "pending",
                    "approved",
                    "changes_requested",
                }
            ),
        )
        _require_enum(
            assignment["lease_state"],
            f"{assignment_path}.lease_state",
            frozenset(
                {"unclaimed", "active", "expired", "legacy_unknown"}
            ),
        )
        _require_string(
            assignment["packet_revision"],
            f"{assignment_path}.packet_revision",
            nullable=True,
        )

    _validate_bounded_collection(
        payload["overlap_warnings"],
        f"{path}.overlap_warnings",
        item_kind="string",
        strict=strict,
    )
    _require_string(payload["next_page"], f"{path}.next_page", nullable=True)



def _validate_work_operation(
    payload: dict[str, Any],
    *,
    strict: bool = False,
) -> None:
    path = "work_operation_v1"
    ok = _require_bool(payload["ok"], f"{path}.ok")
    _require_string(payload["operation"], f"{path}.operation")
    assignment_id = _require_string(
        payload["assignment_id"],
        f"{path}.assignment_id",
        nullable=True,
    )
    _require_string(payload["operation_id"], f"{path}.operation_id")
    _require_bool(payload["changed"], f"{path}.changed")
    _require_string(
        payload["packet_revision"],
        f"{path}.packet_revision",
        nullable=True,
    )
    _require_enum(
        payload["readiness"],
        f"{path}.readiness",
        frozenset(
            {
                "waiting_dependencies",
                "ready",
                "stale_inputs",
                "unknown_inputs",
                "not_applicable",
            }
        ),
    )
    _validate_required_action(
        payload["required_action"],
        f"{path}.required_action",
        strict=strict,
    )
    _validate_bounded_collection(
        payload["allowed_operations"],
        f"{path}.allowed_operations",
        item_kind="string",
        strict=strict,
    )
    _validate_verification(
        payload["verification"],
        f"{path}.verification",
        strict=strict,
    )
    _validate_bounded_collection(
        payload["warnings"],
        f"{path}.warnings",
        item_kind="string",
        strict=strict,
    )
    partial_success = _require_bool(
        payload["partial_success"],
        f"{path}.partial_success",
    )
    rolled_back = _require_bool(
        payload["rolled_back"],
        f"{path}.rolled_back",
    )
    if partial_success and rolled_back:
        _contract_error(
            path,
            "partial_success and rolled_back cannot both be true",
        )

    error = payload["error"]
    if ok:
        if error is not None:
            _contract_error(f"{path}.error", "successful operations require error=null")
        if partial_success or rolled_back:
            _contract_error(
                path,
                "successful operations cannot be partial or rolled back",
            )
        return

    error_data = _require_mapping(error, f"{path}.error")
    error_fields = {
        "code",
        "message",
        "context",
        "conflict_files",
        "dependency_blockers",
        "retry_action",
    }
    _require_fields(error_data, f"{path}.error", error_fields)
    _reject_unknown_fields(
        error_data,
        f"{path}.error",
        error_fields,
        strict=strict,
    )
    _require_string(error_data["code"], f"{path}.error.code")
    _require_string(error_data["message"], f"{path}.error.message")
    context = _require_mapping(
        error_data["context"],
        f"{path}.error.context",
    )
    context_fields = {
        "assignment_id",
        "lease_id",
        "input_revision",
        "latest_packet_revision",
    }
    _require_fields(context, f"{path}.error.context", context_fields)
    _reject_unknown_fields(
        context,
        f"{path}.error.context",
        context_fields,
        strict=strict,
    )
    for field in context_fields:
        _require_string(
            context[field],
            f"{path}.error.context.{field}",
            nullable=True,
        )
    if (
        assignment_id is not None
        and context["assignment_id"] is not None
        and context["assignment_id"] != assignment_id
    ):
        _contract_error(
            f"{path}.error.context.assignment_id",
            "must match the operation assignment_id",
        )
    for field in ("conflict_files", "dependency_blockers"):
        _validate_bounded_collection(
            error_data[field],
            f"{path}.error.{field}",
            item_kind="string",
            strict=strict,
        )
    _validate_required_action(
        error_data["retry_action"],
        f"{path}.error.retry_action",
        strict=strict,
    )



_PUBLIC_CONTRACT_VALIDATORS = {
    "work_packet_v1": _validate_work_packet,
    "evidence_bundle_v1": _validate_evidence_bundle,
    "synthesis_packet_v1": _validate_synthesis_packet,
    "coordination_snapshot_v1": _validate_coordination_snapshot,
    "work_operation_v1": _validate_work_operation,
}


def public_contract_example(schema_version: str) -> dict[str, Any]:
    try:
        return deepcopy(PUBLIC_CONTRACT_EXAMPLES[schema_version])
    except KeyError as exc:
        raise ValueError(f"unknown public schema version: {schema_version}") from exc


def parse_public_contract(
    payload: object,
    *,
    mode: str = "projection",
) -> dict[str, Any]:
    if mode not in {"projection", "mutation"}:
        raise ValueError("public contract mode must be projection or mutation")
    if not isinstance(payload, dict):
        raise ValueError("public contract payload must be a mapping")
    schema_version = str(payload.get("schema_version") or "")
    try:
        required_fields = PUBLIC_CONTRACT_REQUIRED_FIELDS[schema_version]
    except KeyError as exc:
        raise ValueError(
            f"unknown public schema version: {schema_version or '<missing>'}"
        ) from exc
    if mode == "mutation" and schema_version != "evidence_bundle_v1":
        raise ValueError(f"{schema_version} is not a mutation input contract")
    missing = sorted(field for field in required_fields if field not in payload)
    if missing:
        raise ValueError(
            f"{schema_version} is missing required fields: {', '.join(missing)}"
        )
    strict = mode == "mutation"
    _reject_unknown_fields(
        payload,
        schema_version,
        {*required_fields, "schema_version"},
        strict=strict,
    )
    _PUBLIC_CONTRACT_VALIDATORS[schema_version](
        payload,
        strict=strict,
    )
    return deepcopy(payload)
