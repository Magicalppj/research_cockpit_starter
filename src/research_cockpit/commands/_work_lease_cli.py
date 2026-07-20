from __future__ import annotations

import argparse
from typing import Any

from research_cockpit.assignment_leases import AssignmentLeaseError
from research_cockpit.commands._runtime import emit_json, safe_print


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
