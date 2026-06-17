from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.maintenance import build_worktree_audit
from research_cockpit.model import ValidationError


def worktree_audit_payload(
    root: Path,
    *,
    repo: Path,
    include_nested: list[Path] | None = None,
) -> dict[str, Any]:
    return build_worktree_audit(root, repo=repo, include_nested=include_nested)


def _print_human(payload: dict[str, Any]) -> None:
    for row in payload.get("worktrees", []):
        marker = "blocked" if row.get("blockers") else "clear"
        safe_print(f"{row.get('branch') or '<detached>'} [{marker}] {row['path']}")
        if row.get("blockers"):
            safe_print(f"  blockers: {', '.join(row['blockers'])}")
    if not payload.get("worktrees"):
        safe_print("No worktrees found.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit worktree-audit")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--include-nested", action="append", type=Path, default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        payload = worktree_audit_payload(args.root, repo=args.repo, include_nested=args.include_nested)
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(payload)
        return
    _print_human(payload)


if __name__ == "__main__":
    main()
