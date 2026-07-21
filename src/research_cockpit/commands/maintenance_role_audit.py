from __future__ import annotations

import argparse
from pathlib import Path

from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.commands.maintenance_audit import maintenance_audit_payload
from research_cockpit.paths import default_data_root
from research_cockpit.types import ValidationError


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="research-cockpit maintenance audit",
        allow_abbrev=False,
    )
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base", default="main")
    parser.add_argument("--min-size-gb", type=float, default=10.0)
    parser.add_argument("--max-files", type=int, default=1000)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    try:
        result = maintenance_audit_payload(
            args.root,
            repo=args.repo,
            base=args.base,
            min_size_gb=args.min_size_gb,
            max_files=args.max_files,
        )
    except (ValidationError, ValueError, FileNotFoundError, OSError) as exc:
        if args.json:
            emit_json({"ok": False, "error": str(exc)}, compact=args.compact)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from None

    payload = {
        "schema_version": "maintenance_result_v1",
        "command": "audit",
        "executed": False,
        "result": result,
    }
    if args.json:
        emit_json(payload, compact=args.compact)
        return
    safe_print(
        "Maintenance candidates: "
        f"{len(result.get('worktree_candidates', []))} worktrees, "
        f"{len(result.get('branch_candidates', []))} branches, "
        f"{len(result.get('large_artifact_candidates', []))} large artifacts."
    )


if __name__ == "__main__":
    main()
