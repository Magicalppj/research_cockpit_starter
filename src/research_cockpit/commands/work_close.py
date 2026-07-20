from __future__ import annotations

import argparse
from pathlib import Path

from research_cockpit.assignment_leases import AssignmentLeaseError
from research_cockpit.assignment_results import close_assignment_work
from research_cockpit.commands._work_lease_cli import emit_work_result, handle_work_error
from research_cockpit.paths import default_data_root
from research_cockpit.storage import load_yaml
from research_cockpit.types import ValidationError


def _resolve_evidence_source(plan: dict, input_path: Path) -> None:
    evidence_inputs = plan.get("evidence_inputs")
    if not isinstance(evidence_inputs, dict) or "source" not in evidence_inputs:
        return
    source = Path(str(evidence_inputs["source"])).expanduser()
    if not source.is_absolute():
        source = input_path.resolve().parent / source
    evidence_inputs["source"] = str(source.absolute())


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit work close", allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--assignment", required=True, dest="assignment_id")
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    try:
        if not args.file.is_file():
            raise FileNotFoundError(args.file)
        plan = load_yaml(args.file)
        if not isinstance(plan, dict):
            raise ValueError("work close input must be a mapping")
        _resolve_evidence_source(plan, args.file)
        payload = close_assignment_work(
            args.root,
            assignment_id=args.assignment_id,
            plan=plan,
        )
    except AssignmentLeaseError as exc:
        handle_work_error(args, exc)
        raise SystemExit(1) from None
    except (ValidationError, ValueError, FileNotFoundError, FileExistsError) as exc:
        parser.error(str(exc))
    emit_work_result(args, payload)


if __name__ == "__main__":
    main()
