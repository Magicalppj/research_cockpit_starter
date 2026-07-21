from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
from threading import Barrier
import time
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from research_cockpit.cli_progress import PROGRESS_PREFIX, progress_session
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.commands.list_agent_commands import agent_command_manifest
from research_cockpit.interaction_log import (
    InteractionLogError,
    iter_interaction_events,
)
from research_cockpit.model import ResearchNode, load_yaml
from research_cockpit.mutation_lock import MutationError
from research_cockpit.mutation_runtime import (
    execute_mutation_transaction,
    load_targeted_state,
    validate_mutation_candidate,
)
from research_cockpit.storage import find_node_file
from research_cockpit.validation_index import (
    is_index_schema_compatible,
    load_validation_index,
    signature_matches,
)


SCHEMA_VERSION = "multi_agent_baseline_v2"
DEFAULT_AGENT_COUNTS = (1, 4, 8, 16)
STAGE_FIELDS = (
    "prepare_ms",
    "lock_wait_ms",
    "lock_hold_ms",
    "commit_ms",
    "index_patch_ms",
    "transaction_ms",
    "wall_time_ms",
)

WORKFLOW_BASELINES: dict[str, dict[str, Any]] = {
    "assigned_worker_no_payload": {
        "commands": (
            "work open",
            "work start",
            "work close",
        ),
        "cli_invocations": 3,
        "state_load_lower_bound": 3,
        "nested_subprocesses": 0,
        "measurement_fields": (
            "model_visible_bytes",
            "estimated_tokens",
            "control_plane_wall_time_ms",
        ),
    },
    "assigned_worker_final_payload": {
        "commands": (
            "work open",
            "work start",
            "work close",
        ),
        "cli_invocations": 3,
        "state_load_lower_bound": 3,
        "nested_subprocesses": 0,
        "measurement_fields": (
            "model_visible_bytes",
            "estimated_tokens",
            "control_plane_wall_time_ms",
        ),
    },
    "assigned_worker_incremental_evidence": {
        "commands": (
            "work open",
            "work start",
            "work record",
            "work close",
        ),
        "cli_invocations": 4,
        "state_load_lower_bound": 4,
        "nested_subprocesses": 0,
        "measurement_fields": (
            "model_visible_bytes",
            "estimated_tokens",
            "control_plane_wall_time_ms",
        ),
    },
    "unclaimed_worker": {
        "commands": (
            "work claim",
            "work start",
            "work close",
        ),
        "cli_invocations": 3,
        "state_load_lower_bound": 3,
        "nested_subprocesses": 0,
        "measurement_fields": (
            "model_visible_bytes",
            "estimated_tokens",
            "control_plane_wall_time_ms",
        ),
    },
    "reviewer": {
        "commands": (
            "review open",
            "review report",
        ),
        "cli_invocations": 2,
        "state_load_lower_bound": 2,
        "nested_subprocesses": 0,
        "measurement_fields": (
            "model_visible_bytes",
            "estimated_tokens",
            "control_plane_wall_time_ms",
        ),
    },
    "milestone_handoff": {
        "commands": ("coord handoff",),
        "cli_invocations": 1,
        "state_load_lower_bound": 1,
        "nested_subprocesses": 0,
        "measurement_fields": (
            "model_visible_bytes",
            "estimated_tokens",
            "control_plane_wall_time_ms",
        ),
    },
}

WORKFLOW_BASELINE_EVIDENCE = {
    "measurement_status": "current_role_facade_contract",
    "state_load_method": "declared_lower_bound",
    "nested_subprocess_method": "static_domain_call_audit",
    "actual_trace_sources": (
        "run_agent_usability_check",
        "run_skill_release_check",
    ),
}


def canonical_command_inventory(
    manifest: list[dict[str, object]] | None = None,
) -> list[dict[str, Any]]:
    rows = manifest if manifest is not None else agent_command_manifest()
    return [
        {
            "name": str(row["name"]),
            "audiences": tuple(str(item) for item in row.get("audiences", [])),
            "surface": str(row["surface"]),
            "intent": str(row["intent"]),
            "group": str(row["group"]),
            "lifecycle": str(row["lifecycle"]),
            "route_kind": str(row["route_kind"]),
            "status": str(row["status"]),
            "mutating": bool(row.get("mutating")),
        }
        for row in rows
    ]

def parse_progress_events(stderr: str) -> dict[str, float | None]:
    phases: dict[str, float] = {}
    aliases = {
        "targeted_preflight": "prepare_ms",
        "lock_wait": "lock_wait_ms",
        "lock_hold": "lock_hold_ms",
        "commit": "commit_ms",
        "index_update": "index_patch_ms",
        "apply_transaction": "transaction_ms",
    }
    prefixes = (PROGRESS_PREFIX,)
    for line in stderr.splitlines():
        prefix = next((item for item in prefixes if line.startswith(item)), None)
        if prefix is None:
            continue
        try:
            event = json.loads(line[len(prefix) :])
        except json.JSONDecodeError:
            continue
        if event.get("event") != "phase_end":
            continue
        field = aliases.get(str(event.get("phase") or ""))
        duration = event.get("duration_ms")
        if (
            field
            and not isinstance(duration, bool)
            and isinstance(duration, (int, float))
            and math.isfinite(float(duration))
        ):
            phases[field] = round(float(duration), 3)
    return {field: phases.get(field) for field in STAGE_FIELDS if field != "wall_time_ms"}


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _stats(values: list[float | None]) -> dict[str, float | int | None]:
    executed = [float(value) for value in values if value is not None]
    return {
        "executed_count": len(executed),
        "missing_count": len(values) - len(executed),
        "min": round(min(executed), 3) if executed else None,
        "median": (
            round(float(statistics.median(executed)), 3) if executed else None
        ),
        "p95": round(_percentile(executed, 0.95), 3) if executed else None,
        "max": round(max(executed), 3) if executed else None,
    }


def summarize_concurrency_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    conflicts = sum(1 for sample in samples if sample.get("conflict") is True)
    successes = sum(1 for sample in samples if sample.get("success") is True)
    valid_outcomes = sum(
        1 for sample in samples if sample.get("valid_outcome") is True
    )
    return {
        "sample_count": len(samples),
        "success_count": successes,
        "failure_count": len(samples) - successes,
        "conflict_count": conflicts,
        "valid_outcome_count": valid_outcomes,
        "invalid_outcome_count": len(samples) - valid_outcomes,
        "conflict_rate": round(conflicts / len(samples), 6) if samples else 0.0,
        "stages": {
            field: _stats(
                [
                    float(sample[field]) if sample.get(field) is not None else None
                    for sample in samples
                ]
            )
            for field in STAGE_FIELDS
        },
    }


def concurrency_round_passed(
    scenario: str,
    summary: dict[str, Any],
) -> bool:
    sample_count = int(summary.get("sample_count") or 0)
    failures = int(summary.get("failure_count") or 0)
    conflicts = int(summary.get("conflict_count") or 0)
    successes = int(summary.get("success_count") or 0)
    valid_outcomes = int(summary.get("valid_outcome_count") or 0)
    if scenario == "disjoint":
        return (
            sample_count > 0
            and valid_outcomes == sample_count
            and successes == sample_count
            and failures == 0
            and conflicts == 0
        )
    if scenario == "same_target":
        expected_conflicts = max(0, sample_count - 1)
        return (
            sample_count > 0
            and valid_outcomes == sample_count
            and successes == 1
            and failures == expected_conflicts
            and conflicts == expected_conflicts
        )
    raise ValueError(f"unsupported scenario: {scenario}")


def _node_ids(root: Path) -> list[str]:
    node_dir = root / "graph" / "nodes"
    return sorted(path.stem for path in node_dir.glob("*.yaml") if path.is_file())


def _truth_fingerprint(root: Path) -> dict[str, tuple[int, int]]:
    root_path = root.resolve(strict=True)
    candidates = [
        root_path / "coordinator_state.yaml",
        root_path / "current_state.yaml",
        root_path / "graph" / "interaction_log.yaml",
    ]
    for relative in (
        "agents",
        "assignments",
        "artifact_migrations",
        "artifacts",
        "artifact_records",
        "gate_results",
        "graph/interaction_events",
        "graph/nodes",
        "runs",
    ):
        directory = root_path / relative
        if directory.exists():
            candidates.extend(
                path for path in directory.rglob("*") if path.is_file()
            )
    fingerprint: dict[str, tuple[int, int]] = {}
    for path in candidates:
        if path.exists() and path.is_file():
            stat = path.stat()
            fingerprint[path.relative_to(root_path).as_posix()] = (
                stat.st_mtime_ns,
                stat.st_size,
            )
    return fingerprint


def _wait_for_commit_barrier(
    barrier_dir: Path,
    *,
    participant_count: int,
    sample_id: str,
    timeout_seconds: float = 30.0,
) -> None:
    if Path(sample_id).name != sample_id:
        raise ValueError("sample id must be a file name")
    barrier_dir.mkdir(parents=True, exist_ok=True)
    marker = barrier_dir / f"{sample_id}.ready"
    fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    deadline = time.monotonic() + timeout_seconds
    while len(list(barrier_dir.glob("*.ready"))) < participant_count:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"timed out waiting for {participant_count} prepared workers"
            )
        time.sleep(0.01)


def _run_prepared_mutation(
    root: Path,
    node_id: str,
    sample_id: str,
    *,
    commit_barrier_dir: Path,
    participant_count: int,
) -> dict[str, Any]:
    state = load_targeted_state(root, node_ids=[node_id])
    if node_id not in state.nodes:
        raise ValueError(f"Node does not exist: {node_id}")
    path = find_node_file(root, node_id)
    before_data = load_yaml(path)
    after_data = dict(before_data)
    after_data["summary"] = (
        f"Concurrent baseline sample {sample_id} at {time.time_ns()}"
    )
    candidate = dict(state.nodes)
    candidate[node_id] = ResearchNode.from_dict(after_data)
    validate_mutation_candidate(root, state, nodes=candidate)

    _wait_for_commit_barrier(
        commit_barrier_dir,
        participant_count=participant_count,
        sample_id=sample_id,
    )
    return execute_mutation_transaction(
        root,
        [(path, before_data, after_data)],
        interactions=[
            {
                "kind": "update_node_fields",
                "actor": "researcher",
                "node_id": node_id,
                "command": "multi_agent_baseline --mutation-worker",
                "before": {"summary": before_data.get("summary")},
                "after": {"summary": after_data["summary"]},
                "extra": {"benchmark_sample_id": sample_id},
            }
        ],
        rebuild_dashboard=False,
    )


def _mutation_worker_cli(
    root: Path,
    node_id: str,
    sample_id: str,
    *,
    commit_barrier_dir: Path,
    participant_count: int,
) -> int:
    try:
        with progress_session("multi-agent-baseline-worker", explicit=True):
            payload = _run_prepared_mutation(
                root,
                node_id,
                sample_id,
                commit_barrier_dir=commit_barrier_dir,
                participant_count=participant_count,
            )
    except MutationError as exc:
        payload = dict(exc.payload)
        payload.setdefault("ok", False)
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "status": "error", "error": str(exc)},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 1
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


def _run_mutation_sample(
    root: Path,
    node_id: str,
    sample_id: str,
    *,
    start_barrier: Barrier,
    commit_barrier_dir: Path,
    participant_count: int,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--mutation-worker",
        "--root",
        str(root),
        "--node-id",
        node_id,
        "--sample-id",
        sample_id,
        "--commit-barrier",
        str(commit_barrier_dir),
        "--barrier-count",
        str(participant_count),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_DIR), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    start_barrier.wait()
    started_at = time.perf_counter()
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )
    wall_time_ms = round((time.perf_counter() - started_at) * 1000, 3)
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    status = str(payload.get("status") or "")
    success = result.returncode == 0 and payload.get("ok") is True
    conflict = (
        result.returncode != 0
        and payload.get("ok") is False
        and status == "conflict"
        and isinstance(payload.get("conflict_files"), list)
        and bool(payload["conflict_files"])
    )
    return {
        "node_id": node_id,
        "returncode": result.returncode,
        "sample_id": sample_id,
        "success": success,
        "conflict": conflict,
        "valid_outcome": success or conflict,
        "wall_time_ms": wall_time_ms,
        "stdout_bytes": len((result.stdout or "").encode("utf-8")),
        "stderr_bytes": len((result.stderr or "").encode("utf-8")),
        **parse_progress_events(result.stderr or ""),
        **(
            {
                "stdout_preview": (result.stdout or "")[:500],
                "stderr_preview": (result.stderr or "")[:500],
            }
            if not (success or conflict)
            else {}
        ),
    }



def _round_consistency(
    root: Path,
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    all_sample_ids = {str(sample["sample_id"]) for sample in samples}
    successful = {
        str(sample["sample_id"]): str(sample["node_id"])
        for sample in samples
        if sample.get("success") is True
    }
    event_error: str | None = None
    observed_events: list[dict[str, Any]] = []
    try:
        observed_events = [
            event
            for event in iter_interaction_events(root, strict=True)
            if str(event.get("benchmark_sample_id") or "") in all_sample_ids
        ]
    except (InteractionLogError, OSError) as exc:
        event_error = str(exc)
    event_counts: dict[str, int] = {}
    event_node_mismatches: list[str] = []
    for event in observed_events:
        sample_id = str(event.get("benchmark_sample_id") or "")
        event_counts[sample_id] = event_counts.get(sample_id, 0) + 1
        if sample_id in successful and str(event.get("node_id") or "") != successful[sample_id]:
            event_node_mismatches.append(sample_id)
    missing_sample_ids = sorted(set(successful) - set(event_counts))
    unexpected_sample_ids = sorted(set(event_counts) - set(successful))
    duplicate_sample_ids = sorted(
        sample_id for sample_id, count in event_counts.items() if count != 1
    )

    index = load_validation_index(root)
    index_available = is_index_schema_compatible(index)
    mismatched_node_ids: list[str] = []
    indexed_node_ids: list[str] = []
    if index_available and isinstance(index, dict):
        rows = index.get("nodes", {}) or {}
        for node_id in sorted(set(successful.values())):
            row = rows.get(node_id)
            if not isinstance(row, dict):
                mismatched_node_ids.append(node_id)
                continue
            indexed_node_ids.append(node_id)
            rel_path = str(row.get("file") or "")
            if not rel_path or not signature_matches(
                root,
                rel_path,
                row.get("file_signature"),
            ):
                mismatched_node_ids.append(node_id)

    state_mismatches: list[str] = []
    successful_by_node: dict[str, set[str]] = {}
    for sample_id, node_id in successful.items():
        successful_by_node.setdefault(node_id, set()).add(sample_id)
    for node_id, sample_ids in successful_by_node.items():
        data = load_yaml(find_node_file(root, node_id))
        summary = str(data.get("summary") or "") if isinstance(data, dict) else ""
        if not any(sample_id in summary for sample_id in sample_ids):
            state_mismatches.append(node_id)

    events_ok = not (
        event_error
        or missing_sample_ids
        or unexpected_sample_ids
        or duplicate_sample_ids
        or event_node_mismatches
    )
    index_ok = bool(index_available) and not mismatched_node_ids
    state_ok = not state_mismatches
    return {
        "ok": events_ok and index_ok and state_ok,
        "events": {
            "expected_count": len(successful),
            "observed_count": len(observed_events),
            "missing_sample_ids": missing_sample_ids,
            "unexpected_sample_ids": unexpected_sample_ids,
            "duplicate_sample_ids": duplicate_sample_ids,
            "node_mismatch_sample_ids": sorted(event_node_mismatches),
            "error": event_error,
        },
        "index": {
            "available": bool(index_available),
            "indexed_node_ids": indexed_node_ids,
            "mismatched_node_ids": sorted(set(mismatched_node_ids)),
        },
        "state": {"mismatched_node_ids": sorted(state_mismatches)},
    }


def _copy_fixture(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("artifacts", ".mutation.lock", ".validation-index.lock"),
    )


def benchmark_concurrency(
    root: Path,
    *,
    agent_counts: tuple[int, ...] = DEFAULT_AGENT_COUNTS,
    scenarios: tuple[str, ...] = ("disjoint", "same_target"),
    temp_parent: Path | None = None,
) -> dict[str, Any]:
    source = root.resolve(strict=True)
    if not scenarios:
        raise ValueError("scenarios must not be empty")
    unsupported = sorted(set(scenarios) - {"disjoint", "same_target"})
    if unsupported:
        raise ValueError(f"unsupported scenario: {unsupported[0]}")

    resolved_temp_parent: Path | None = None
    if temp_parent is not None:
        resolved_temp_parent = temp_parent.resolve(strict=False)
        if resolved_temp_parent == source or source in resolved_temp_parent.parents:
            raise ValueError("temporary directory must be outside the source root")
        resolved_temp_parent.mkdir(parents=True, exist_ok=True)

    source_before = _truth_fingerprint(source)
    available_nodes = _node_ids(source)
    if not available_nodes:
        raise ValueError("concurrency benchmark needs at least one node")
    required_nodes = max(agent_counts, default=0)
    if required_nodes < 1:
        raise ValueError("agent counts must contain positive integers")
    if any(count < 1 for count in agent_counts):
        raise ValueError("agent counts must contain positive integers")
    if "disjoint" in scenarios and len(available_nodes) < required_nodes:
        raise ValueError(
            f"disjoint benchmark needs {required_nodes} nodes; found {len(available_nodes)}"
        )

    rounds: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(
        prefix="research-cockpit-multi-agent-",
        dir=resolved_temp_parent,
    ) as temporary:
        temporary_root = Path(temporary)
        for scenario in scenarios:
            for count in agent_counts:
                round_root = temporary_root / f"{scenario}-{count}"
                _copy_fixture(source, round_root)
                setup_started = time.perf_counter()
                build_dashboard(round_root)
                setup_ms = round((time.perf_counter() - setup_started) * 1000, 3)
                commit_barrier_dir = (
                    temporary_root / "commit-barriers" / f"{scenario}-{count}"
                )
                commit_barrier_dir.mkdir(parents=True, exist_ok=True)
                targets = (
                    available_nodes[:count]
                    if scenario == "disjoint"
                    else [available_nodes[0]] * count
                )
                start_barrier = Barrier(count)
                with ThreadPoolExecutor(max_workers=count) as executor:
                    futures = [
                        executor.submit(
                            _run_mutation_sample,
                            round_root,
                            node_id,
                            f"{scenario}-{count}-{index}",
                            start_barrier=start_barrier,
                            commit_barrier_dir=commit_barrier_dir,
                            participant_count=count,
                        )
                        for index, node_id in enumerate(targets)
                    ]
                    samples = [future.result() for future in futures]
                rounds.append(
                    {
                        "scenario": scenario,
                        "agent_count": count,
                        "setup_ms": setup_ms,
                        "summary": summarize_concurrency_samples(samples),
                        "consistency": _round_consistency(round_root, samples),
                        "samples": samples,
                    }
                )
    source_root_mutated = source_before != _truth_fingerprint(source)
    return {
        "ok": not source_root_mutated
        and all(row["consistency"]["ok"] for row in rounds)
        and all(
            concurrency_round_passed(row["scenario"], row["summary"]) for row in rounds
        ),
        "schema_version": SCHEMA_VERSION,
        "source_root": str(source),
        "source_root_mutated": source_root_mutated,
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
        },
        "agent_counts": list(agent_counts),
        "scenarios": list(scenarios),
        "synchronization": "cross_process_post_preflight_file_barrier",
        "rounds": rounds,
    }


def artifact_record_layout_profile(root: Path) -> dict[str, Any]:
    started_at = time.perf_counter()
    root_path = root.resolve(strict=True)
    record_rows: list[dict[str, Any]] = []
    record_count = 0
    invalid_record_files: list[str] = []
    record_dir = root_path / "artifact_records"
    if record_dir.exists():
        for path in sorted(record_dir.glob("*.yaml")):
            relative = path.relative_to(root_path).as_posix()
            data = load_yaml(path)
            records = data.get("records") if isinstance(data, dict) else None
            if not isinstance(records, dict):
                invalid_record_files.append(relative)
                records = {}
            experiment_id = (
                str(data.get("experiment_id") or path.stem)
                if isinstance(data, dict)
                else path.stem
            )
            count = len(records)
            record_count += count
            record_rows.append(
                {
                    "path": relative,
                    "experiment_id": experiment_id,
                    "record_count": count,
                    "bytes": path.stat().st_size,
                }
            )

    append_only_by_experiment: dict[str, list[str]] = {}
    invalid_assignment_files: list[str] = []
    assignment_dir = root_path / "assignments"
    if assignment_dir.exists():
        for path in sorted(assignment_dir.glob("*.yaml")):
            relative = path.relative_to(root_path).as_posix()
            data = load_yaml(path)
            if not isinstance(data, dict):
                invalid_assignment_files.append(relative)
                continue
            scope = data.get("scope")
            if not isinstance(scope, dict):
                continue
            if scope.get("write_policy") != "append_only":
                continue
            if data.get("status") not in {"active", "blocked"} or not data.get("agent_id"):
                continue
            experiment_id = str(
                data.get("current_node")
                or scope.get("root_node")
                or data.get("root_node")
                or ""
            )
            assignment_id = str(data.get("assignment_id") or path.stem)
            if experiment_id:
                append_only_by_experiment.setdefault(experiment_id, []).append(
                    assignment_id
                )

    files_by_experiment = {
        str(row["experiment_id"]): str(row["path"]) for row in record_rows
    }
    shared_writer_candidates = [
        {
            "experiment_id": experiment_id,
            "assignment_ids": sorted(assignment_ids),
            "artifact_record_file": (
                files_by_experiment.get(experiment_id)
                or f"artifact_records/{experiment_id}.yaml"
            ),
            "shared_file_exists": experiment_id in files_by_experiment,
        }
        for experiment_id, assignment_ids in sorted(
            append_only_by_experiment.items()
        )
        if len(assignment_ids) > 1
    ]
    if shared_writer_candidates:
        storage_decision = {
            "decision": "defer_layout_migration",
            "migration_required": False,
            "reason": (
                "Artifact YAML is a shared write candidate, but sharding it alone "
                "does not isolate the experiment lifecycle write set used by work "
                "start and work close."
            ),
            "next_evidence": (
                "Profile append-only start/close after their shared experiment "
                "lifecycle writes are isolated; require before/after conflict and "
                "file-count measurements before changing the truth layout."
            ),
        }
    else:
        storage_decision = {
            "decision": "no_layout_change",
            "migration_required": False,
            "reason": "No active append-only assignments share an experiment record file.",
            "next_evidence": "Re-profile when multiple active writers share one experiment.",
        }
    return {
        "schema_version": "artifact_record_layout_profile_v1",
        "root": str(root_path),
        "layout": "per_experiment_mutable_yaml",
        "file_count": len(record_rows),
        "record_count": record_count,
        "total_bytes": sum(int(row["bytes"]) for row in record_rows),
        "max_file_bytes": max(
            (int(row["bytes"]) for row in record_rows),
            default=0,
        ),
        "max_records_per_file": max(
            (int(row["record_count"]) for row in record_rows),
            default=0,
        ),
        "append_only_active_assignment_count": sum(
            len(values) for values in append_only_by_experiment.values()
        ),
        "shared_writer_candidates": shared_writer_candidates,
        "invalid_record_files": invalid_record_files,
        "invalid_assignment_files": invalid_assignment_files,
        "storage_decision": storage_decision,
        "profile_duration_ms": round(
            (time.perf_counter() - started_at) * 1000,
            3,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record command, workflow, and multi-agent concurrency baselines."
    )
    parser.add_argument("--root", type=Path)
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--workflow-baselines", action="store_true")
    parser.add_argument("--storage-profile", action="store_true")
    parser.add_argument("--concurrency", action="store_true")
    parser.add_argument("--agent-count", type=int, action="append")
    parser.add_argument(
        "--scenario",
        choices=("disjoint", "same_target"),
        action="append",
    )
    parser.add_argument("--temp-parent", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--mutation-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--node-id", help=argparse.SUPPRESS)
    parser.add_argument("--sample-id", help=argparse.SUPPRESS)
    parser.add_argument("--commit-barrier", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--barrier-count", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.mutation_worker:
        required = {
            "--root": args.root,
            "--node-id": args.node_id,
            "--sample-id": args.sample_id,
            "--commit-barrier": args.commit_barrier,
            "--barrier-count": args.barrier_count,
        }
        missing = [flag for flag, value in required.items() if value is None]
        if missing:
            parser.error(
                "--mutation-worker requires " + ", ".join(missing)
            )
        assert args.root is not None
        assert args.node_id is not None
        assert args.sample_id is not None
        assert args.commit_barrier is not None
        assert args.barrier_count is not None
        raise SystemExit(
            _mutation_worker_cli(
                args.root,
                args.node_id,
                args.sample_id,
                commit_barrier_dir=args.commit_barrier,
                participant_count=args.barrier_count,
            )
        )

    selected = args.inventory or args.workflow_baselines or args.concurrency
    selected = selected or args.storage_profile
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
    }
    if args.inventory or not selected:
        inventory = canonical_command_inventory()
        payload["command_count"] = len(inventory)
        payload["command_inventory"] = inventory
    if args.workflow_baselines or not selected:
        payload["workflow_baseline_evidence"] = WORKFLOW_BASELINE_EVIDENCE
        payload["workflow_baselines"] = WORKFLOW_BASELINES
    if args.storage_profile:
        if args.root is None:
            parser.error("--storage-profile requires --root")
        payload["artifact_record_layout_profile"] = artifact_record_layout_profile(
            args.root
        )
    if args.concurrency:
        if args.root is None:
            parser.error("--concurrency requires --root")
        try:
            concurrency = benchmark_concurrency(
                args.root,
                agent_counts=tuple(args.agent_count or DEFAULT_AGENT_COUNTS),
                scenarios=tuple(args.scenario or ("disjoint", "same_target")),
                temp_parent=args.temp_parent,
            )
        except (OSError, ValueError) as exc:
            payload = {
                "schema_version": SCHEMA_VERSION,
                "ok": False,
                "error": str(exc),
            }
        else:
            payload["concurrency"] = concurrency
            payload["ok"] = concurrency["ok"]

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"commands: {payload.get('command_count', 'not requested')}")
        if "workflow_baselines" in payload:
            print(f"workflow baselines: {len(payload['workflow_baselines'])}")
        if "artifact_record_layout_profile" in payload:
            print("artifact record layout: profiled")
        if "concurrency" in payload:
            print(f"concurrency rounds: {len(payload['concurrency']['rounds'])}")
    if not payload["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
