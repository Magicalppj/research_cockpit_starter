from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_cockpit.model import RunRecord, load_runs
from research_cockpit.run_summaries import ACTIVE_RUN_STATUSES
from research_cockpit.storage import load_yaml


@dataclass(frozen=True)
class ActiveRunReference:
    run_id: str
    assignment_id: str | None
    experiment_id: str
    status: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "run_id": self.run_id,
            "assignment_id": self.assignment_id,
            "experiment_id": self.experiment_id,
            "status": self.status,
        }


@dataclass(frozen=True)
class ActiveRunOccupancy:
    assignment_runs: tuple[ActiveRunReference, ...]
    experiment_runs: tuple[ActiveRunReference, ...]

    @property
    def assignment_run_ids(self) -> tuple[str, ...]:
        return tuple(run.run_id for run in self.assignment_runs)

    @property
    def experiment_run_ids(self) -> tuple[str, ...]:
        return tuple(run.run_id for run in self.experiment_runs)

    @property
    def run_ids(self) -> tuple[str, ...]:
        return tuple(sorted({*self.assignment_run_ids, *self.experiment_run_ids}))

    def source_payload(self) -> dict[str, list[dict[str, str | None]]]:
        return {
            "assignment": [run.to_dict() for run in self.assignment_runs],
            "experiment": [run.to_dict() for run in self.experiment_runs],
        }


@dataclass(frozen=True)
class ActiveRunSnapshot:
    assignment_id: str
    experiment_id: str
    occupancy: ActiveRunOccupancy
    file_signatures: tuple[tuple[str, tuple[int, int, int, int]], ...]


def _active_run_reference(
    *,
    run_id: object,
    assignment_id: object,
    experiment_id: object,
    status: object,
) -> ActiveRunReference:
    return ActiveRunReference(
        run_id=str(run_id),
        assignment_id=str(assignment_id) if assignment_id not in (None, "") else None,
        experiment_id=str(experiment_id or ""),
        status=str(status or ""),
    )


def _classify_active_runs(
    references: list[ActiveRunReference],
    *,
    assignment_id: str,
    experiment_id: str,
) -> ActiveRunOccupancy:
    assignment_runs = {
        run.run_id: run for run in references if run.assignment_id == assignment_id
    }
    experiment_runs = {
        run.run_id: run for run in references if run.experiment_id == experiment_id
    }
    return ActiveRunOccupancy(
        assignment_runs=tuple(assignment_runs[key] for key in sorted(assignment_runs)),
        experiment_runs=tuple(experiment_runs[key] for key in sorted(experiment_runs)),
    )


def active_run_occupancy_for_target(
    root: Path,
    *,
    assignment_id: str,
    experiment_id: str,
    indexed_runs: dict[str, Any] | None = None,
) -> ActiveRunOccupancy:
    """Classify active runs occupying an assignment or exact experiment."""
    if indexed_runs is not None:
        indexed_references: list[ActiveRunReference] = []
        for run_id, row in indexed_runs.items():
            if (
                not isinstance(row, dict)
                or "assignment_id" not in row
                or "finished_at" not in row
            ):
                break
            if row.get("status") not in ACTIVE_RUN_STATUSES or row.get("finished_at"):
                continue
            indexed_references.append(
                _active_run_reference(
                    run_id=row.get("run_id") or run_id,
                    assignment_id=row.get("assignment_id"),
                    experiment_id=row.get("experiment_id"),
                    status=row.get("status"),
                )
            )
        else:
            return _classify_active_runs(
                indexed_references,
                assignment_id=assignment_id,
                experiment_id=experiment_id,
            )

    references: list[ActiveRunReference] = []
    for run in load_runs(root).values():
        if run.status not in ACTIVE_RUN_STATUSES or run.finished_at:
            continue
        references.append(
            _active_run_reference(
                run_id=run.run_id,
                assignment_id=run.raw.get("assignment_id"),
                experiment_id=run.experiment_id,
                status=run.status,
            )
        )
    return _classify_active_runs(
        references,
        assignment_id=assignment_id,
        experiment_id=experiment_id,
    )


def active_run_ids_for_target(
    root: Path,
    *,
    assignment_id: str,
    experiment_id: str,
    indexed_runs: dict[str, Any] | None = None,
) -> list[str]:
    occupancy = active_run_occupancy_for_target(
        root,
        assignment_id=assignment_id,
        experiment_id=experiment_id,
        indexed_runs=indexed_runs,
    )
    return list(occupancy.run_ids)


def indexed_run_projection_is_current(
    root: Path,
    indexed_runs: dict[str, Any],
) -> bool:
    """Check a run index with filesystem metadata reads, not YAML parsing."""
    expected: dict[str, tuple[int, int]] = {}
    for row in indexed_runs.values():
        if not isinstance(row, dict):
            return False
        rel_path = Path(str(row.get("file") or ""))
        signature = row.get("file_signature")
        if (
            len(rel_path.parts) != 2
            or rel_path.parts[0] != "runs"
            or rel_path.suffix != ".yaml"
            or not isinstance(signature, dict)
        ):
            return False
        size = signature.get("size")
        mtime_ns = signature.get("mtime_ns")
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or isinstance(mtime_ns, bool)
            or not isinstance(mtime_ns, int)
        ):
            return False
        rel_key = rel_path.as_posix()
        if rel_key in expected:
            return False
        expected[rel_key] = (size, mtime_ns)

    current_paths = sorted((root / "runs").glob("*.yaml"))
    current_names = {f"runs/{path.name}" for path in current_paths}
    if current_names != set(expected):
        return False
    for path in current_paths:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return False
        if expected[f"runs/{path.name}"] != (stat.st_size, stat.st_mtime_ns):
            return False
    return True


def _run_file_signatures(root: Path) -> tuple[tuple[str, tuple[int, int, int, int]], ...]:
    signatures: list[tuple[str, tuple[int, int, int, int]]] = []
    for path in sorted((root / "runs").glob("*.yaml")):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        signatures.append(
            (
                path.name,
                (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino),
            )
        )
    return tuple(signatures)


def capture_active_run_snapshot(
    root: Path,
    *,
    assignment_id: str,
    experiment_id: str,
) -> ActiveRunSnapshot:
    """Load active-run truth once outside the mutation lock and fingerprint its files."""
    for _attempt in range(3):
        before = _run_file_signatures(root)
        occupancy = active_run_occupancy_for_target(
            root,
            assignment_id=assignment_id,
            experiment_id=experiment_id,
        )
        after = _run_file_signatures(root)
        if before == after:
            return ActiveRunSnapshot(
                assignment_id=assignment_id,
                experiment_id=experiment_id,
                occupancy=occupancy,
                file_signatures=after,
            )
    raise RuntimeError("Run truth changed while capturing active-run snapshot; retry.")


def active_run_ids_added_since_snapshot(
    root: Path,
    snapshot: ActiveRunSnapshot,
) -> list[str]:
    """Parse only run files added or changed since a stable preflight snapshot."""
    previous = dict(snapshot.file_signatures)
    current = dict(_run_file_signatures(root))
    changed_names = sorted(
        name for name, signature in current.items() if previous.get(name) != signature
    )
    blockers: set[str] = set()
    for name in changed_names:
        data = load_yaml(root / "runs" / name)
        if not data:
            continue
        run = RunRecord.from_dict(data)
        if run.status not in ACTIVE_RUN_STATUSES or run.finished_at:
            continue
        assignment_id = run.raw.get("assignment_id")
        if (
            assignment_id == snapshot.assignment_id
            or run.experiment_id == snapshot.experiment_id
        ):
            blockers.add(run.run_id)
    return sorted(blockers)
