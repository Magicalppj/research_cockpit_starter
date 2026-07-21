from __future__ import annotations

import argparse
from pathlib import Path

from research_cockpit.assignment_leases import (
    AssignmentLeaseError,
    release_assignment,
)
from research_cockpit.commands._work_lease_cli import (
    emit_work_result,
    handle_role_cli_input_error,
    handle_work_error,
)
from research_cockpit.paths import default_data_root
from research_cockpit.types import ValidationError


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit work release", allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--assignment", required=True, dest="assignment_id")
    parser.add_argument("--agent", required=True, dest="agent_id")
    parser.add_argument("--lease-id", required=True)
    parser.add_argument("--lease-epoch", required=True, type=int)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    try:
        payload = release_assignment(
            args.root,
            assignment_id=args.assignment_id,
            agent_id=args.agent_id,
            lease_id=args.lease_id,
            lease_epoch=args.lease_epoch,
            operation_id=args.operation_id,
        )
    except AssignmentLeaseError as exc:
        handle_work_error(args, exc)
        raise SystemExit(1) from None
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        handle_role_cli_input_error(
            args,
            parser,
            exc,
            operation="work release",
            assignment_id=args.assignment_id,
            operation_id=args.operation_id,
        )
    emit_work_result(args, payload)


if __name__ == "__main__":
    main()
