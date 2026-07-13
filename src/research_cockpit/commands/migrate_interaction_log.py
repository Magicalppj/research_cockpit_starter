from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shutil
from typing import Any
import uuid

from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.interaction_log import (
    SEGMENT_MAX_BYTES,
    _legacy_signature as _interaction_legacy_signature,
    activate_segment_generation,
    event_content_checksum,
    interaction_event_dir,
    load_interaction_log,
    read_segment_snapshot,
    write_segment_snapshot,
)
from research_cockpit.mutation_lock import mutation_lock
from research_cockpit.paths import default_data_root


ROOT = default_data_root()


def _legacy_event_count(path: Path) -> int:
    if not path.exists():
        return 0
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    events = data.get("events", []) if isinstance(data, dict) else []
    return len(events) if isinstance(events, list) else 0

def _segment_plan(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    event_count = 0
    byte_count = 0
    for event in events:
        line_size = len(
            (json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        )
        if event_count and byte_count + line_size > SEGMENT_MAX_BYTES:
            segments.append({"path": f"events-{len(segments) + 1:06d}.jsonl", "event_count": event_count, "bytes": byte_count})
            event_count = 0
            byte_count = 0
        event_count += 1
        byte_count += line_size
    if event_count:
        segments.append({"path": f"events-{len(segments) + 1:06d}.jsonl", "event_count": event_count, "bytes": byte_count})
    return segments


def _migration_analysis(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    log = load_interaction_log(root, strict=True)
    events = list(log.get("events", []))
    ids = [str(event.get("id") or "") for event in events if event.get("id")]
    duplicate_ids = sorted(event_id for event_id, count in Counter(ids).items() if count > 1)
    order_anomalies: list[dict[str, Any]] = []
    previous = ""
    for index, event in enumerate(events):
        created_at = str(event.get("created_at") or "")
        if previous and created_at and created_at < previous:
            order_anomalies.append({"index": index, "event_id": event.get("id"), "created_at": created_at, "previous_created_at": previous})
        if created_at:
            previous = created_at
    payload = {
        "schema_version": "interaction_log_migration_v1",
        "root": str(root),
        "source_backend": log.get("backend", "legacy_yaml"),
        "event_count": len(events),
        "duplicate_ids": duplicate_ids,
        "order_anomalies": order_anomalies,
        "content_checksum": event_content_checksum(events),
        "target_segments": _segment_plan(events),
    }
    return payload, events


def migrate_interaction_log(root: Path = ROOT, *, dry_run: bool = True) -> dict[str, Any]:
    plan, _ = _migration_analysis(root)
    if dry_run:
        return {**plan, "dry_run": True, "executed": False}

    event_dir = interaction_event_dir(root)
    generation_root = event_dir / "generations"
    generation_name = f"generation-{uuid.uuid4().hex}"
    staging = generation_root / f".staging-{uuid.uuid4().hex}"
    generation_dir = generation_root / generation_name
    generation_root.mkdir(parents=True, exist_ok=True)

    with mutation_lock(root):
        plan, events = _migration_analysis(root)
        legacy_path = root / "graph" / "interaction_log.yaml"
        source = {
            "legacy_event_count": _legacy_event_count(legacy_path),
            "legacy_signature": _interaction_legacy_signature(legacy_path),
        }
        activated = False
        try:
            snapshot_manifest, segments = write_segment_snapshot(staging, events, source=source)
            staged_events = read_segment_snapshot(staging)
            if len(staged_events) != len(events):
                raise ValueError("staged interaction event count does not match source")
            if event_content_checksum(staged_events) != plan["content_checksum"]:
                raise ValueError("staged interaction event checksum does not match source")

            os.replace(staging, generation_dir)
            activate_segment_generation(root, generation_dir, snapshot_manifest, segments)
            activated = True
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            if generation_dir.exists() and not activated:
                shutil.rmtree(generation_dir)

    return {
        **plan,
        "dry_run": False,
        "executed": True,
        "target_segments": segments,
        "generation": generation_dir.relative_to(event_dir).as_posix(),
        "legacy_preserved": (root / "graph" / "interaction_log.yaml").exists(),
    }

def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit migrate-interaction-log")
    parser.add_argument("--root", type=Path, default=ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Inspect the migration without writing; this is the default.")
    mode.add_argument("--execute", action="store_true", help="Write and atomically activate JSONL event segments.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = migrate_interaction_log(args.root, dry_run=not args.execute)
    if args.json:
        emit_json(payload)
        return
    action = "Migrated" if payload["executed"] else "Would migrate"
    safe_print(f"{action}: {payload['event_count']} interaction event(s) into {len(payload['target_segments'])} segment(s)")


if __name__ == "__main__":
    main()
