from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any
import yaml

from research_cockpit.commands._runtime import emit_json, safe_print, yaml_change_diff
from research_cockpit.interaction_log import utc_timestamp
from research_cockpit.mutation_lock import MutationError, mutation_lock
from research_cockpit.paths import default_data_root
from research_cockpit.storage import save_yaml


ROOT = default_data_root()


def _backup_path(path: Path) -> Path:
    stamp = re.sub(r"[^A-Za-z0-9_.-]+", "_", utc_timestamp()).strip("_")
    return path.with_name(f"{path.name}.bak.{stamp}")


def _load_interaction_log_document(path: Path) -> Any:
    if not path.exists():
        return {"events": []}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(
            f"graph/interaction_log.yaml has a YAML parse error and cannot be repaired automatically: {exc}"
        ) from exc


def _candidate_document(data: Any) -> tuple[dict[str, Any], int, list[str]]:
    warnings: list[str] = []
    if not isinstance(data, dict):
        warnings.append("top-level document was not a mapping; replaced with an empty events list")
        return {"events": []}, 0, warnings

    next_data = dict(data)
    events = data.get("events", [])
    if events is None:
        events = []
    if not isinstance(events, list):
        warnings.append("events was not a list; replaced with an empty events list")
        next_data["events"] = []
        return next_data, 0, warnings

    kept = [event for event in events if isinstance(event, dict)]
    dropped = len(events) - len(kept)
    if dropped:
        warnings.append(f"dropped {dropped} non-mapping event item(s)")
    next_data["events"] = kept
    return next_data, dropped, warnings


def repair_interaction_log(
    root: Path = ROOT,
    *,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    path = root / "graph" / "interaction_log.yaml"
    data = _load_interaction_log_document(path)
    candidate, dropped_count, warnings = _candidate_document(data)
    before_for_diff = data if isinstance(data, dict) else {"invalid_document": data}
    changed = before_for_diff != candidate

    if changed and not dry_run:
        with mutation_lock(root):
            data = _load_interaction_log_document(path)
            candidate, dropped_count, warnings = _candidate_document(data)
            before_for_diff = data if isinstance(data, dict) else {"invalid_document": data}
            changed = before_for_diff != candidate
            backup_path = None
            if changed:
                if path.exists():
                    backup_path = _backup_path(path)
                    backup_path.write_bytes(path.read_bytes())
                save_yaml(path, candidate)
    else:
        backup_path = None
    diff = yaml_change_diff([(path, before_for_diff, candidate)]) if show_diff and changed else ""

    payload: dict[str, Any] = {
        "ok": True,
        "root": str(root),
        "path": str(path),
        "dry_run": dry_run,
        "changed": changed and not dry_run,
        "would_change": changed,
        "kept_event_count": len(candidate.get("events", [])),
        "dropped_event_count": dropped_count,
        "backup_path": str(backup_path) if backup_path is not None else None,
        "warnings": warnings,
    }
    if show_diff:
        payload["diff"] = diff
        payload["diff_line_count"] = len(diff.splitlines())
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit repair-interaction-log")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--backup", action="store_true", help="Accepted for explicit repair intent; execution always writes a backup when changing the log.")
    args = parser.parse_args()

    try:
        payload = repair_interaction_log(args.root, dry_run=args.dry_run, show_diff=args.show_diff)
    except (ValueError, MutationError) as exc:
        error_payload = getattr(exc, "payload", None) or {
            "ok": False,
            "partial_success": False,
            "rolled_back": False,
            "written_files": [],
            "error": str(exc),
            "recovery_commands": [f"research-cockpit validate --root {args.root} --json"],
        }
        if args.json:
            emit_json(error_payload)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from None

    if args.json:
        emit_json(payload)
        return
    action = "Would repair" if args.dry_run else "Repaired"
    if not payload["would_change"]:
        action = "No repair needed"
    safe_print(f"{action}: {payload['path']}")
    if payload.get("backup_path"):
        safe_print(f"Backup: {payload['backup_path']}")
    if args.show_diff and payload.get("diff"):
        safe_print(payload["diff"], end="")


if __name__ == "__main__":
    main()
