from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.commands.artifact_retention_audit import _bytes_from_gb
from research_cockpit.maintenance import build_maintenance_audit
from research_cockpit.model import ValidationError


def maintenance_audit_payload(
    root: Path,
    *,
    repo: Path,
    base: str = "main",
    min_size_bytes: int | None = None,
    min_size_gb: float = 10.0,
    max_files: int = 1000,
) -> dict[str, Any]:
    threshold = min_size_bytes if min_size_bytes is not None else _bytes_from_gb(min_size_gb)
    return build_maintenance_audit(root, repo=repo, base=base, min_size_bytes=threshold, max_files=max_files)


def _print_human(payload: dict[str, Any]) -> None:
    safe_print(f"Active assignments: {len(payload.get('active_assignments', []))}")
    safe_print(f"Running runs: {len(payload.get('running_runs', []))}")
    safe_print(f"Worktree candidates: {len(payload.get('worktree_candidates', []))}")
    safe_print(f"Branch candidates: {len(payload.get('branch_candidates', []))}")
    safe_print(f"Large artifact candidates: {len(payload.get('large_artifact_candidates', []))}")
    for action in payload.get("recommended_next_actions", []):
        safe_print(f"- {action}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit maintenance-audit")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base", default="main")
    parser.add_argument("--min-size-gb", type=float, default=10.0)
    parser.add_argument("--max-files", type=int, default=1000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        payload = maintenance_audit_payload(
            args.root,
            repo=args.repo,
            base=args.base,
            min_size_gb=args.min_size_gb,
            max_files=args.max_files,
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
