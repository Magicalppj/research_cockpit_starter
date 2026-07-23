from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from research_cockpit.cli_progress import progress_phase
from research_cockpit.commands.build_dashboard import build_dashboard_from_validated_state
from research_cockpit.commands.skill_smoke_test import compact_root_smoke_from_validation
from research_cockpit.commands.validate_cockpit import full_validation_snapshot
from research_cockpit.coordination import build_coordination_state
from research_cockpit.mutation_lock import MutationError
from research_cockpit.mutation_runtime import execute_mutation_transaction
from research_cockpit.operation_receipts import normalized_request_hash, validate_operation_id
from research_cockpit.research_ledger import (
    build_research_ledger,
    git_toplevel,
    ledger_relative_path,
    write_research_ledger,
)
from research_cockpit.storage import load_yaml
from research_cockpit.validation_index import mark_validation_index_stale


HANDOFF_INPUT_SCHEMA_VERSION = "coord_handoff_v1"
HANDOFF_SCHEMA_VERSION = "milestone_handoff_v1"
_HANDOFF_KINDS = {"merge", "release", "research_stage_closeout"}
_ALLOW_KEYS = {
    "pending_reviews",
    "stale_inputs",
    "active_leases",
    "unresolved_blockers",
}
_COLLECTION_LIMIT = 20


class _HandoffStaleError(RuntimeError):
    pass


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _bounded(values: list[str]) -> dict[str, Any]:
    unique = list(dict.fromkeys(str(value) for value in values if str(value)))
    items = unique[:_COLLECTION_LIMIT]
    return {
        "items": items,
        "limit": _COLLECTION_LIMIT,
        "total": len(unique),
        "omitted": len(unique) - len(items),
    }


def _truth_paths(root: Path) -> list[Path]:
    paths: set[Path] = set()
    for name in ("current_state.yaml", "coordinator_state.yaml", "storage.yaml"):
        path = root / name
        if path.is_file():
            paths.add(path)

    yaml_roots = (
        "agents",
        "assignments",
        "runs",
        "artifact_records",
        "artifact_migrations",
        "artifact_gc_manifests",
    )
    for name in yaml_roots:
        directory = root / name
        if directory.is_dir():
            paths.update(path for path in directory.rglob("*.yaml") if path.is_file())
            paths.update(path for path in directory.rglob("*.yml") if path.is_file())

    node_root = root / "graph" / "nodes"
    if node_root.is_dir():
        paths.update(path for path in node_root.rglob("*.yaml") if path.is_file())
        paths.update(path for path in node_root.rglob("*.yml") if path.is_file())
    for name in ("edges.yaml", "interaction_log.yaml"):
        path = root / "graph" / name
        if path.is_file():
            paths.add(path)
    event_root = root / "graph" / "interaction_events"
    if event_root.is_dir():
        paths.update(path for path in event_root.rglob("*") if path.is_file())

    gate_root = root / "gate_results"
    if gate_root.is_dir():
        for suffix in ("*.yaml", "*.yml", "*.json"):
            paths.update(path for path in gate_root.rglob(suffix) if path.is_file())

    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def root_truth_revision(root: Path) -> str:
    rows: list[tuple[str, int | None, int | None]] = []
    for path in _truth_paths(root):
        relative = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
        except OSError:
            rows.append((relative, None, None))
            continue
        rows.append((relative, stat.st_size, stat.st_mtime_ns))
    encoded = json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return f"root-v1:{hashlib.sha256(encoded).hexdigest()}"


def _parse_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("coord handoff input must be a mapping")
    supported = {
        "schema_version",
        "operation_id",
        "kind",
        "summary",
        "strict_lifecycle",
        "allow",
    }
    unknown = sorted(set(plan) - supported)
    if unknown:
        raise ValueError("coord handoff input does not support: " + ", ".join(unknown))
    if plan.get("schema_version") != HANDOFF_INPUT_SCHEMA_VERSION:
        raise ValueError("coord handoff requires schema_version: coord_handoff_v1")
    raw_operation_id = plan.get("operation_id")
    if not isinstance(raw_operation_id, str):
        raise ValueError("coord handoff operation_id must be a string")
    operation_id = validate_operation_id(raw_operation_id)
    raw_kind = plan.get("kind")
    if not isinstance(raw_kind, str):
        raise ValueError("coord handoff kind must be a string")
    kind = raw_kind.strip()
    if kind not in _HANDOFF_KINDS:
        raise ValueError("coord handoff kind must be merge, release, or research_stage_closeout")
    raw_summary = plan.get("summary", "")
    if not isinstance(raw_summary, str):
        raise ValueError("coord handoff summary must be a string")
    summary = raw_summary.strip()
    if len(summary) > 2000:
        raise ValueError("coord handoff summary must not exceed 2000 characters")
    strict_lifecycle = plan.get("strict_lifecycle", True)
    if not isinstance(strict_lifecycle, bool):
        raise ValueError("coord handoff strict_lifecycle must be a boolean")
    raw_allow = plan.get("allow") or {}
    if not isinstance(raw_allow, dict):
        raise ValueError("coord handoff allow must be a mapping")
    unknown_allow = sorted(set(raw_allow) - _ALLOW_KEYS)
    if unknown_allow:
        raise ValueError("coord handoff allow does not support: " + ", ".join(unknown_allow))
    allow: dict[str, bool] = {}
    for key in sorted(_ALLOW_KEYS):
        value = raw_allow.get(key, False)
        if not isinstance(value, bool):
            raise ValueError(f"coord handoff allow.{key} must be a boolean")
        allow[key] = value
    return {
        "schema_version": HANDOFF_INPUT_SCHEMA_VERSION,
        "operation_id": operation_id,
        "kind": kind,
        "summary": summary,
        "strict_lifecycle": strict_lifecycle,
        "allow": allow,
    }


def _handoff_path(root: Path, operation_id: str) -> Path:
    file_id = operation_id
    if ":" in file_id:
        digest = hashlib.sha256(file_id.encode("utf-8")).hexdigest()[:8]
        file_id = f"{file_id.replace(':', '_')}-{digest}"
    return root / "handoffs" / f"{file_id}.yaml"


def _existing_report(path: Path, request_hash: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    report = load_yaml(path)
    if not isinstance(report, dict):
        return {}
    receipt = report.get("receipt")
    if report.get("request_hash") == request_hash and isinstance(receipt, dict):
        return deepcopy(report)
    return {}


def _resolve_ledger_repo(repo: Path | None) -> Path | None:
    if repo is None:
        return None
    resolved = git_toplevel(repo)
    if resolved is None:
        raise ValueError(f"ledger repository must be a Git worktree: {repo}")
    return resolved


def _rebuild_ledger(repo: Path | None, report: dict[str, Any]) -> None:
    if repo is None:
        return
    projection = report.get("ledger_projection")
    if isinstance(projection, dict):
        write_research_ledger(repo, projection)


def _gate_summary(
    validation: dict[str, Any] | None = None,
    build: dict[str, Any] | None = None,
    smoke: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failed_checks = [
        str(check.get("name") or "unknown")
        for check in (smoke or {}).get("checks", [])
        if isinstance(check, dict) and not check.get("passed")
    ]
    return {
        "validation": {
            "ok": bool((validation or {}).get("ok")),
            "node_count": int((validation or {}).get("node_count", 0)),
            "error_count": len((validation or {}).get("errors", []) or []),
        },
        "build": {
            "ok": bool((build or {}).get("ok")),
            "node_count": int((build or {}).get("node_count", 0)),
            "written_file_count": len((build or {}).get("written_files", []) or []),
        },
        "smoke": {
            "ok": bool((smoke or {}).get("ok")),
            "mode": (smoke or {}).get("mode"),
            "check_count": len((smoke or {}).get("checks", []) or []),
            "failed_checks": _bounded(failed_checks),
        },
    }


def _empty_blockers() -> dict[str, Any]:
    return {
        "pending_reviews": _bounded([]),
        "stale_inputs": _bounded([]),
        "active_leases": _bounded([]),
        "unresolved": _bounded([]),
        "blocking_categories": [],
    }


def _collect_blockers(state: dict[str, Any], allow: dict[str, bool]) -> dict[str, Any]:
    rows = [row for row in state.get("rows", []) if isinstance(row, dict)]
    pending_reviews = [
        str(row.get("assignment_id") or "")
        for row in rows
        if row.get("review_status") == "pending"
    ]
    stale_inputs = [
        str(row.get("assignment_id") or "")
        for row in rows
        if row.get("readiness") == "stale_inputs"
    ]
    active_leases = [
        str(row.get("assignment_id") or "")
        for row in rows
        if row.get("lease_state") == "active"
    ]
    unresolved = [
        str(row.get("assignment_id") or "")
        for row in rows
        if row.get("status") in {"queued", "active", "blocked"}
        or row.get("readiness") in {"waiting_dependencies", "unknown_inputs"}
        or row.get("lease_state") == "expired"
    ]
    unresolved.extend(str(value) for value in state.get("overlap_warnings", []) if str(value))
    categories = {
        "pending_reviews": pending_reviews,
        "stale_inputs": stale_inputs,
        "active_leases": active_leases,
        "unresolved_blockers": unresolved,
    }
    blocking_categories = [
        key for key, values in categories.items() if values and not allow.get(key, False)
    ]
    return {
        "pending_reviews": _bounded(pending_reviews),
        "stale_inputs": _bounded(stale_inputs),
        "active_leases": _bounded(active_leases),
        "unresolved": _bounded(unresolved),
        "blocking_categories": blocking_categories,
    }


def _error_receipt(
    parsed: dict[str, Any],
    *,
    status: str,
    code: str,
    message: str,
    target_revision: str | None = None,
    gates: dict[str, Any] | None = None,
    blockers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "ok": False,
        "operation": "coord handoff",
        "operation_id": parsed["operation_id"],
        "kind": parsed["kind"],
        "status": status,
        "changed": False,
        "target_revision": target_revision,
        "report_file": None,
        "gates": gates or _gate_summary(),
        "blockers": blockers or _empty_blockers(),
        "error": {"code": code, "message": message},
    }


def _mark_stale(root: Path, *, target_revision: str, current_revision: str) -> None:
    try:
        mark_validation_index_stale(
            root,
            reason="handoff_stale",
            detail=f"target={target_revision}; current={current_revision}",
        )
    except Exception:
        return


def execute_milestone_handoff(
    root: Path,
    plan: dict[str, Any],
    *,
    repo: Path | None = None,
) -> dict[str, Any]:
    parsed = _parse_plan(plan)
    ledger_repo = _resolve_ledger_repo(repo)
    request_hash = normalized_request_hash(parsed)
    report_path = _handoff_path(root, parsed["operation_id"])
    existing = _existing_report(report_path, request_hash)
    if existing is not None:
        if existing:
            _rebuild_ledger(ledger_repo, existing)
            return deepcopy(existing["receipt"])
        return _error_receipt(
            parsed,
            status="idempotency_conflict",
            code="idempotency_conflict",
            message="operation_id was already used with a different normalized handoff request",
        )

    target_revision = root_truth_revision(root)
    validation: dict[str, Any] | None = None
    build: dict[str, Any] | None = None
    smoke: dict[str, Any] | None = None
    active_phase = "validation"
    try:
        with progress_phase("handoff.validate"):
            validation, validation_state = full_validation_snapshot(
                root,
                strict_lifecycle=parsed["strict_lifecycle"],
            )
        if not validation.get("ok"):
            return _error_receipt(
                parsed,
                status="failed",
                code="validation_failed",
                message="Full validation failed; handoff gates stopped before build.",
                target_revision=target_revision,
                gates=_gate_summary(validation=validation),
            )
        active_phase = "build"
        with progress_phase("handoff.build"):
            build = build_dashboard_from_validated_state(root, validation_state)
        if not build.get("ok"):
            return _error_receipt(
                parsed,
                status="failed",
                code="build_failed",
                message="Dashboard build failed.",
                target_revision=target_revision,
                gates=_gate_summary(validation=validation, build=build),
            )
        active_phase = "smoke"
        with progress_phase("handoff.smoke"):
            smoke = compact_root_smoke_from_validation(root, validation, validation_state)
        if not smoke.get("ok"):
            return _error_receipt(
                parsed,
                status="failed",
                code="smoke_failed",
                message="Compact root smoke failed.",
                target_revision=target_revision,
                gates=_gate_summary(validation=validation, build=build, smoke=smoke),
            )
        active_phase = "coordination"
        with progress_phase("handoff.coordination"):
            coordination = build_coordination_state(root)
    except Exception as exc:
        code = f"{active_phase}_failed"
        return _error_receipt(
            parsed,
            status="failed",
            code=code,
            message=str(exc),
            target_revision=target_revision,
            gates=_gate_summary(validation=validation, build=build, smoke=smoke),
        )

    current_revision = root_truth_revision(root)
    if current_revision != target_revision:
        _mark_stale(
            root,
            target_revision=target_revision,
            current_revision=current_revision,
        )
        return _error_receipt(
            parsed,
            status="handoff_stale",
            code="handoff_stale",
            message="Canonical truth changed while handoff gates were running; retry the handoff.",
            target_revision=target_revision,
            gates=_gate_summary(validation=validation, build=build, smoke=smoke),
        )

    blockers = _collect_blockers(coordination, parsed["allow"])
    blocked = bool(blockers["blocking_categories"])
    relative_report = report_path.relative_to(root).as_posix()
    created_at = _utc_timestamp()
    ledger_projection = build_research_ledger(
        root,
        validation_state,
        operation_id=parsed["operation_id"],
        kind=parsed["kind"],
        target_revision=target_revision,
        timestamp=created_at,
    )
    ledger_file = (
        ledger_relative_path(parsed["operation_id"])
        if ledger_repo is not None
        else None
    )
    receipt = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "ok": not blocked,
        "operation": "coord handoff",
        "operation_id": parsed["operation_id"],
        "kind": parsed["kind"],
        "status": "blocked" if blocked else "completed",
        "changed": True,
        "target_revision": target_revision,
        "report_file": relative_report,
        "ledger_file": ledger_file,
        "gates": _gate_summary(validation=validation, build=build, smoke=smoke),
        "blockers": blockers,
        "error": (
            {
                "code": "lifecycle_blockers",
                "message": "Handoff gates passed, but unresolved lifecycle blockers prevent completion.",
            }
            if blocked
            else None
        ),
    }
    report = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "operation_id": parsed["operation_id"],
        "request_hash": request_hash,
        "request": parsed,
        "target_revision": target_revision,
        "created_at": created_at,
        "ledger_projection": ledger_projection,
        "receipt": receipt,
    }

    def validate_revision() -> None:
        latest = root_truth_revision(root)
        if latest != target_revision:
            raise _HandoffStaleError(f"target={target_revision}; current={latest}")

    try:
        with progress_phase("handoff.commit"):
            execute_mutation_transaction(
                root,
                [(report_path, None, report)],
                interactions=[
                    {
                        "kind": "milestone_handoff_recorded",
                        "actor": "coordinator",
                        "command": "research-cockpit coord handoff",
                        "after": {
                            "operation_id": parsed["operation_id"],
                            "kind": parsed["kind"],
                            "status": receipt["status"],
                            "target_revision": target_revision,
                            "report_file": relative_report,
                        },
                    }
                ],
                rebuild_dashboard=False,
                commit_validators=[validate_revision],
            )
    except _HandoffStaleError:
        latest = root_truth_revision(root)
        _mark_stale(root, target_revision=target_revision, current_revision=latest)
        return _error_receipt(
            parsed,
            status="handoff_stale",
            code="handoff_stale",
            message="Canonical truth changed before the handoff receipt could be committed.",
            target_revision=target_revision,
            gates=receipt["gates"],
            blockers=blockers,
        )
    except MutationError:
        concurrent = _existing_report(report_path, request_hash)
        if concurrent:
            _rebuild_ledger(ledger_repo, concurrent)
            return deepcopy(concurrent["receipt"])
        latest = root_truth_revision(root)
        if latest != target_revision:
            _mark_stale(root, target_revision=target_revision, current_revision=latest)
            return _error_receipt(
                parsed,
                status="handoff_stale",
                code="handoff_stale",
                message="Canonical truth changed before the handoff receipt could be committed.",
                target_revision=target_revision,
                gates=receipt["gates"],
                blockers=blockers,
            )
        return _error_receipt(
            parsed,
            status="failed",
            code="handoff_commit_failed",
            message="Handoff report transaction failed.",
            target_revision=target_revision,
            gates=receipt["gates"],
            blockers=blockers,
        )
    _rebuild_ledger(ledger_repo, report)
    return receipt
