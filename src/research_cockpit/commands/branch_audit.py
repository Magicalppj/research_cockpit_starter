from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.maintenance import build_branch_audit
from research_cockpit.model import ValidationError


def branch_audit_payload(root: Path, *, repo: Path, base: str = "main") -> dict[str, Any]:
    return build_branch_audit(root, repo=repo, base=base)


def _print_human(payload: dict[str, Any]) -> None:
    for row in payload.get("branches", []):
        safe_print(f"{row['name']} [{row['recommended_action']}]")
        if row.get("blockers"):
            safe_print(f"  blockers: {', '.join(row['blockers'])}")
    if not payload.get("branches"):
        safe_print("No branches found.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit branch-audit")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base", default="main")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        payload = branch_audit_payload(args.root, repo=args.repo, base=args.base)
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(payload)
        return
    _print_human(payload)


if __name__ == "__main__":
    main()
