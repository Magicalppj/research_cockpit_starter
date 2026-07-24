from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from research_cockpit.storage import load_yaml


MIGRATION_SCHEMA_VERSION = "artifact_storage_migration_v1"
GC_SCHEMA_VERSION = "artifact_gc_transition_v1"


def _identifiers(values: Iterable[str] | None) -> set[str]:
    return {str(value).strip() for value in values or [] if str(value).strip()}


def artifact_lifecycle_reservation_blockers(
    root: Path,
    *,
    experiment_ids: Iterable[str],
    ignore_migration_ids: Iterable[str] | None = None,
    ignore_gc_ids: Iterable[str] | None = None,
) -> list[str]:
    selected = _identifiers(experiment_ids)
    if not selected:
        return []
    ignored_migrations = _identifiers(ignore_migration_ids)
    ignored_gc = _identifiers(ignore_gc_ids)
    blockers: list[str] = []

    migration_root = root / "artifact_migrations"
    if migration_root.is_dir():
        for path in sorted(migration_root.glob("*.yaml")):
            journal = load_yaml(path)
            if not isinstance(journal, dict) or journal.get("schema_version") != MIGRATION_SCHEMA_VERSION:
                blockers.append(f"artifact_lifecycle_state_invalid:{path.name}")
                continue
            migration_id = str(journal.get("migration_id") or "").strip()
            if (
                str(journal.get("experiment_id") or "").strip() in selected
                and migration_id not in ignored_migrations
                and journal.get("phase") != "published"
            ):
                blockers.append(f"artifact_migration:{migration_id or path.stem}")

    gc_root = root / "artifact_gc_manifests"
    if gc_root.is_dir():
        for path in sorted(gc_root.glob("*-prepared.yaml")):
            manifest = load_yaml(path)
            if not isinstance(manifest, dict) or manifest.get("schema_version") != GC_SCHEMA_VERSION:
                blockers.append(f"artifact_lifecycle_state_invalid:{path.name}")
                continue
            gc_id = str(manifest.get("gc_id") or "").strip()
            if (
                str(manifest.get("experiment_id") or "").strip() not in selected
                or gc_id in ignored_gc
            ):
                continue
            completed = any(
                (gc_root / f"{gc_id}-{suffix}.yaml").is_file()
                for suffix in ("quarantined", "purged")
            )
            if not completed:
                phase = str(manifest.get("phase") or "unknown")
                blockers.append(f"artifact_gc:{gc_id or path.stem}:{phase}")

    return sorted(set(blockers))


def assert_no_artifact_lifecycle_reservation(
    root: Path,
    *,
    experiment_ids: Iterable[str],
) -> None:
    blockers = artifact_lifecycle_reservation_blockers(
        root,
        experiment_ids=experiment_ids,
    )
    if blockers:
        raise ValueError(
            "artifact lifecycle reservation blocks active work: "
            + ", ".join(blockers)
        )
