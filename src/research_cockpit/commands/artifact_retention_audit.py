from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.maintenance import build_artifact_retention_audit
from research_cockpit.model import ValidationError


def _bytes_from_gb(value: float) -> int:
    return int(value * 1024 * 1024 * 1024)


def artifact_retention_audit_payload(
    root: Path,
    *,
    repo: Path,
    min_size_bytes: int | None = None,
    min_size_gb: float = 10.0,
    max_files: int = 1000,
) -> dict[str, Any]:
    threshold = min_size_bytes if min_size_bytes is not None else _bytes_from_gb(min_size_gb)
    return build_artifact_retention_audit(root, repo=repo, min_size_bytes=threshold, max_files=max_files)


def _print_human(payload: dict[str, Any]) -> None:
    for artifact in payload.get("artifacts", []):
        marker = "cleanup" if artifact.get("cleanup_candidate") else "review"
        safe_print(f"{artifact['artifact_id']} [{marker}] size={artifact['total_size_bytes']} retention={artifact.get('retention_class')}")
    if not payload.get("artifacts"):
        safe_print("No artifact paths found.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit artifact-retention-audit")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--min-size-gb", type=float, default=10.0)
    parser.add_argument("--max-files", type=int, default=1000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        payload = artifact_retention_audit_payload(
            args.root,
            repo=args.repo,
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
