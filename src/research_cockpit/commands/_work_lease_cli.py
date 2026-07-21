from __future__ import annotations

import argparse
from typing import Any, NoReturn

from research_cockpit.assignment_leases import AssignmentLeaseError
from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.operation_receipts import error_receipt, validate_operation_id


def emit_work_result(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if args.json:
        emit_json(payload, compact=args.compact)
        return
    if payload.get("ok"):
        safe_print(
            f"{payload['operation']}: {payload.get('assignment_id')} "
            f"({payload.get('packet_revision') or 'no revision'})"
        )
        return
    safe_print(str(payload.get("error", {}).get("message") or "work operation failed"))


def handle_work_error(args: argparse.Namespace, exc: AssignmentLeaseError) -> None:
    emit_work_result(args, exc.receipt)


def handle_role_cli_input_error(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    exc: Exception,
    *,
    operation: str,
    assignment_id: str | None = None,
    operation_id: object = None,
    input_revision: object = None,
    code: str = "invalid_request",
    retry_command: str | None = None,
) -> NoReturn:
    if not args.json:
        parser.error(str(exc))
    try:
        safe_operation_id = validate_operation_id(str(operation_id or "invalid_request"))
    except ValueError:
        safe_operation_id = "invalid_request"
    safe_input_revision = (
        str(input_revision) if isinstance(input_revision, str) and input_revision else None
    )
    emit_work_result(
        args,
        error_receipt(
            operation=operation,
            assignment_id=assignment_id,
            operation_id=safe_operation_id,
            code=code,
            message=str(exc),
            input_revision=safe_input_revision,
            retry_kind="manual_recovery",
            retry_command=retry_command,
            retry_reason="Correct the versioned input and submit it with a new operation_id.",
        ),
    )
    raise SystemExit(2)
