from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.commands.artifact_retention_audit import _bytes_from_gb
from research_cockpit.maintenance import WORKTREE_CLOSEOUT_CLASSIFICATIONS, build_worktree_closeout_plan
from research_cockpit.model import ValidationError
from research_cockpit.paths import default_data_root

ROOT = default_data_root()


def worktree_closeout_payload(
    root: Path,
    *,
    repo: Path,
    worktree: Path,
    classification: str,
    base: str = "main",
    min_size_bytes: int | None = None,
    min_size_gb: float = 10.0,
    max_files: int = 1000,
    include_nested: list[Path] | None = None,
    compact: bool = False,
) -> dict[str, Any]:
    threshold = min_size_bytes if min_size_bytes is not None else _bytes_from_gb(min_size_gb)
    payload = build_worktree_closeout_plan(
        root,
        repo=repo,
        worktree=worktree,
        classification=classification,
        base=base,
        min_size_bytes=threshold,
        max_files=max_files,
        include_nested=include_nested,
    )
    if not compact:
        return payload
    target = payload.get("target_worktree", {})
    return {
        "ok": payload["ok"],
        "schema_version": payload["schema_version"],
        "dry_run": payload["dry_run"],
        "classification": payload["classification"],
        "safe_to_closeout": payload["safe_to_closeout"],
        "target_worktree": {
            "path": target.get("path"),
            "branch": target.get("branch"),
            "label": target.get("label"),
        },
        "blockers": payload["blockers"],
        "rc_state_updates_needed": payload["rc_state_updates_needed"],
        "execution_commands": payload["execution_commands"],
        "evidence_summary": {
            "finding_count": payload["evidence_summary"]["finding_count"],
            "artifact_ids": payload["evidence_summary"]["artifact_ids"],
        },
        "missing_retention": payload["missing_retention"],
    }


def _print_human(payload: dict[str, Any]) -> None:
    status = "ready" if payload.get("safe_to_closeout") else "blocked"
    safe_print(f"Worktree closeout {status}: {payload.get('target_worktree', {}).get('path')}")
    for blocker in payload.get("blockers", []):
        safe_print(f"- blocker: {blocker}")
    if payload.get("rc_state_updates_needed"):
        safe_print("RC updates needed:")
        for update in payload["rc_state_updates_needed"]:
            safe_print(f"- {update['reason']}: {update['command']}")
    if payload.get("execution_commands"):
        safe_print("Command drafts:")
        for command in payload["execution_commands"]:
            safe_print(f"- {command}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit worktree-closeout")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--worktree", required=True, type=Path)
    parser.add_argument("--classification", required=True, choices=sorted(WORKTREE_CLOSEOUT_CLASSIFICATIONS))
    parser.add_argument("--base", default="main")
    parser.add_argument("--include-nested", action="append", type=Path, default=[])
    parser.add_argument("--min-size-gb", type=float, default=10.0)
    parser.add_argument("--max-files", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    try:
        payload = worktree_closeout_payload(
            args.root,
            repo=args.repo,
            worktree=args.worktree,
            classification=args.classification,
            base=args.base,
            min_size_gb=args.min_size_gb,
            max_files=args.max_files,
            include_nested=args.include_nested,
            compact=args.compact,
        )
    except (ValidationError, ValueError, FileNotFoundError, OSError) as exc:
        safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(payload)
        return
    _print_human(payload)


if __name__ == "__main__":
    main()
