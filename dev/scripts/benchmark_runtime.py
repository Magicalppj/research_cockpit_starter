from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import Any

import yaml


SCHEMA_VERSION = "benchmark_runtime_v1"
BENCHMARK_SESSION_ID = f"{os.getpid()}_{time.time_ns()}"
SUPPORTED_OPERATIONS = (
    "validate_changed",
    "context_compact",
    "context_execution",
    "context_execution_unchanged",
    "work_packet",
    "work_packet_unchanged",
    "coord_overview",
    "node_context_compact",
    "mutation",
    "interaction_append",
    "run_closeout",
)
DEFAULT_OPERATIONS = (
    "validate_changed",
    "context_execution",
    "context_execution_unchanged",
    "mutation",
    "run_closeout",
)


def _cli(command: str, *parts: str) -> list[str]:
    return [sys.executable, "-m", "research_cockpit.cli", command, *[str(part) for part in parts]]


def _truth_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    files: dict[str, tuple[int, int]] = {}
    candidates = [
        root / "current_state.yaml",
        root / "coordinator_state.yaml",
        root / "graph" / "interaction_log.yaml",
    ]
    for directory in ("graph/nodes", "runs", "gate_results", "artifact_records", "assignments", "agents"):
        base = root / directory
        if base.exists():
            candidates.extend(path for path in base.iterdir() if path.is_file())
    event_dir = root / "graph" / "interaction_events"
    if event_dir.exists():
        candidates.extend(path for path in event_dir.iterdir() if path.is_file())
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        stat = path.stat()
        try:
            relative = path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
        except ValueError:
            relative = path.as_posix()
        files[relative] = (stat.st_mtime_ns, stat.st_size)
    return files


def _benchmark_plan_path(root: Path, kind: str, sample_index: int) -> Path:
    return root / "dashboards" / f".benchmark-{kind}-{sample_index}.json"


def _run_closeout_plan_path(root: Path, sample_index: int) -> Path:
    return _benchmark_plan_path(root, "work-close", sample_index)


def _coord_update_plan_path(root: Path, sample_index: int) -> Path:
    return _benchmark_plan_path(root, "coord-update", sample_index)


def _write_plan(root: Path, kind: str, sample_index: int, payload: dict[str, Any]) -> Path:
    path = _benchmark_plan_path(root, kind, sample_index)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _run_json(command: list[str], *, label: str) -> dict[str, Any]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not prepare {label} benchmark: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Could not prepare {label} benchmark: command returned non-JSON output"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Could not prepare {label} benchmark: expected a JSON object")
    return payload


def _option_ancestor(root: Path, node_id: str) -> str:
    current = node_id
    visited: set[str] = set()
    while current and current not in visited:
        visited.add(current)
        path = root / "graph" / "nodes" / f"{current}.yaml"
        if not path.is_file():
            break
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            break
        if payload.get("type") == "option":
            return current
        current = str(payload.get("parent") or "")
    raise RuntimeError(f"Could not locate an option ancestor for {node_id!r}")


def _run_closeout_assignment_id(sample_index: int) -> str:
    return f"assign_runtime_{BENCHMARK_SESSION_ID}_{sample_index}"


def _prepare_coord_update(root: Path, changed_node: str, sample_index: int) -> Path:
    plan_path = _coord_update_plan_path(root, sample_index)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": "coord_assign_v1",
                "operation_id": f"op_runtime_update_{BENCHMARK_SESSION_ID}_{sample_index}",
                "action": "graph_plan",
                "graph_plan": {
                    "nodes": [],
                    "updates": [
                        {
                            "id": changed_node,
                            "fields": {
                                "summary": (
                                    f"Runtime benchmark sample {sample_index} "
                                    f"at {time.time_ns()}"
                                )
                            },
                        }
                    ],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return plan_path


def _prepare_run_closeout(root: Path, changed_node: str, sample_index: int) -> Path:
    option_id = _option_ancestor(root, changed_node)
    experiment_id = f"experiment_runtime_{BENCHMARK_SESSION_ID}_{sample_index}"
    assignment_id = _run_closeout_assignment_id(sample_index)
    agent_id = f"agent_runtime_{BENCHMARK_SESSION_ID}_{sample_index}"

    graph_path = _write_plan(
        root,
        "coord-graph",
        sample_index,
        {
            "schema_version": "coord_assign_v1",
            "operation_id": f"op_runtime_graph_{BENCHMARK_SESSION_ID}_{sample_index}",
            "action": "graph_plan",
            "graph_plan": {
                "nodes": [
                    {
                        "id": experiment_id,
                        "type": "experiment",
                        "title": f"Runtime closeout sample {sample_index}",
                        "parent": option_id,
                        "status": "queued",
                    }
                ],
                "updates": [],
            },
        },
    )
    try:
        _run_json(
            _cli(
                "coord",
                "assign",
                "--root",
                root,
                "--file",
                graph_path,
                "--json",
                "--compact",
            ),
            label="coord graph",
        )
    finally:
        graph_path.unlink(missing_ok=True)

    session_path = _write_plan(
        root,
        "coord-session",
        sample_index,
        {
            "schema_version": "coord_assign_v1",
            "operation_id": f"op_runtime_session_{BENCHMARK_SESSION_ID}_{sample_index}",
            "action": "session",
            "session": {
                "kind": "experiment",
                "option_id": option_id,
                "experiment_id": experiment_id,
                "objective": f"Benchmark canonical closeout sample {sample_index}.",
                "branch": f"codex/runtime-{sample_index}",
                "worktree": str(root.parent / f".runtime-worktree-{sample_index}"),
                "agent_id": agent_id,
                "assignment_id": assignment_id,
                "create_worktree": False,
                "force": True,
            },
        },
    )
    try:
        _run_json(
            _cli(
                "coord",
                "assign",
                "--root",
                root,
                "--file",
                session_path,
                "--json",
                "--compact",
            ),
            label="coord session",
        )
    finally:
        session_path.unlink(missing_ok=True)

    packet = _run_json(
        _cli(
            "work",
            "open",
            "--root",
            root,
            "--assignment",
            assignment_id,
            "--json",
            "--compact",
        ),
        label="work open",
    )
    lease = packet.get("lease") if isinstance(packet.get("lease"), dict) else {}
    input_revision = str(packet.get("input_revision") or "")
    if not lease.get("lease_id") or not input_revision:
        raise RuntimeError("Could not prepare work close benchmark: packet lacks lease/input revision")

    start_path = _write_plan(
        root,
        "work-start",
        sample_index,
        {
            "schema_version": "work_start_v1",
            "agent_id": agent_id,
            "lease_id": lease["lease_id"],
            "lease_epoch": lease["lease_epoch"],
            "operation_id": f"op_runtime_start_{BENCHMARK_SESSION_ID}_{sample_index}",
            "input_revision": input_revision,
            "experiment_id": experiment_id,
            "slug": f"runtime-{sample_index}",
            "run": {"launcher": "benchmark"},
        },
    )
    try:
        started = _run_json(
            _cli(
                "work",
                "start",
                "--root",
                root,
                "--assignment",
                assignment_id,
                "--file",
                start_path,
                "--json",
                "--compact",
            ),
            label="work start",
        )
    finally:
        start_path.unlink(missing_ok=True)
    entities = started.get("entities") if isinstance(started.get("entities"), dict) else {}
    run_id = str(entities.get("run_id") or "")
    if not run_id:
        raise RuntimeError("Could not prepare work close benchmark: work start returned no run id")

    gate_specs = []
    gate_dir = root / "gate_results"
    gate_dir.mkdir(parents=True, exist_ok=True)
    for gate_index in range(2):
        gate_id = f"runtime_benchmark_gate_{BENCHMARK_SESSION_ID}_{sample_index}_{gate_index}"
        gate_file = f"gate_results/{gate_id}.json"
        (root / gate_file).write_text(
            json.dumps(
                {
                    "gate_type": "smoke" if gate_index == 0 else "schema",
                    "passed": True,
                    "experiment_id": experiment_id,
                    "run_id": run_id,
                }
            ),
            encoding="utf-8",
        )
        gate_specs.append({"id": gate_id, "file": gate_file})

    plan_path = _run_closeout_plan_path(root, sample_index)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": "work_close_v1",
                "agent_id": agent_id,
                "lease_id": lease["lease_id"],
                "lease_epoch": lease["lease_epoch"],
                "operation_id": f"op_runtime_close_{BENCHMARK_SESSION_ID}_{sample_index}",
                "input_revision": input_revision,
                "run": {"id": run_id, "status": "completed"},
                "experiment": {
                    "status": "done",
                    "result_summary": f"Runtime closeout sample {sample_index} completed.",
                },
                "artifact_record": {
                    "record_id": f"runtime_benchmark_record_{BENCHMARK_SESSION_ID}_{sample_index}",
                    "title": f"Runtime closeout record {sample_index}",
                    "links": {},
                },
                "gates": gate_specs,
                "finding": {
                    "statement": f"Runtime closeout finding {sample_index}.",
                    "confidence": "medium",
                    "outcome": "positive",
                },
                "assignment_result": {
                    "outcome": "positive",
                    "summary": f"Runtime closeout sample {sample_index} passed.",
                    "delivery": {
                        "git_commit": None,
                        "changed_files": [],
                        "tests": {
                            "status": "passed",
                            "summary": "Canonical closeout benchmark passed.",
                        },
                    },
                    "proposals": [],
                },
                "review_required": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return plan_path


def _execution_context_command(
    root: Path,
    changed_node: str,
    *,
    since_revision: str | None = None,
) -> list[str]:
    parts = [
        "--root",
        str(root),
        "--id",
        changed_node,
        "--view",
        "execution",
        "--compact",
        "--json",
    ]
    if since_revision:
        parts.extend(["--since", since_revision])
    return _cli("context", *parts)


def _current_execution_revision(root: Path, changed_node: str) -> str:
    result = subprocess.run(
        _execution_context_command(root, changed_node),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not prepare execution revision benchmark: {detail}")
    payload = json.loads(result.stdout)
    revision = str(payload.get("revision") or "")
    if not revision:
        raise RuntimeError("Execution context did not return a revision")
    return revision


def _work_packet_command(
    root: Path,
    assignment_id: str,
    *,
    since_revision: str | None = None,
) -> list[str]:
    if not assignment_id:
        raise ValueError("--assignment is required for work_packet operations")
    parts = [
        "open",
        "--root",
        str(root),
        "--assignment",
        assignment_id,
        "--json",
        "--compact",
    ]
    if since_revision:
        parts.extend(["--since", since_revision])
    return _cli("work", *parts)


def _current_work_packet_revision(root: Path, assignment_id: str) -> str:
    result = subprocess.run(
        _work_packet_command(root, assignment_id),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not prepare Work Packet revision benchmark: {detail}")
    payload = json.loads(result.stdout)
    revision = str(payload.get("revision") or "")
    if not revision:
        raise RuntimeError("Work Packet did not return a revision")
    return revision


def _command_for(
    operation: str,
    *,
    root: Path,
    changed_node: str,
    sample_index: int,
    assignment_id: str = "",
    progress: bool = False,
) -> list[str]:
    if operation == "validate_changed":
        return _cli("validate", "--root", root, "--changed-node", changed_node, "--json")
    if operation == "context_compact":
        return _cli(
            "context",
            "--root",
            root,
            "--id",
            changed_node,
            "--with-bootstrap",
            "--with-artifacts",
            "--compact",
            "--json",
        )
    if operation == "context_execution":
        return _execution_context_command(root, changed_node)
    if operation == "context_execution_unchanged":
        return _execution_context_command(
            root,
            changed_node,
            since_revision=_current_execution_revision(root, changed_node),
        )
    if operation == "work_packet":
        return _work_packet_command(root, assignment_id)
    if operation == "work_packet_unchanged":
        return _work_packet_command(
            root,
            assignment_id,
            since_revision=_current_work_packet_revision(root, assignment_id),
        )
    if operation == "coord_overview":
        return _cli(
            "coord",
            "overview",
            "--root",
            root,
            "--limit",
            "20",
            "--json",
            "--compact",
        )
    if operation == "node_context_compact":
        return _execution_context_command(root, changed_node)
    if operation in {"mutation", "interaction_append"}:
        plan_path = _prepare_coord_update(root, changed_node, sample_index)
        return _cli(
            "coord",
            "assign",
            "--root",
            root,
            "--file",
            plan_path,
            "--json",
            "--compact",
        )
    if operation == "run_closeout":
        plan_path = _prepare_run_closeout(root, changed_node, sample_index)
        return _cli(
            "work",
            "close",
            "--root",
            root,
            "--assignment",
            _run_closeout_assignment_id(sample_index),
            "--file",
            plan_path,
            "--json",
            "--compact",
        )
    raise ValueError(f"Unsupported operation: {operation}")


def _run_sample(
    operation: str,
    *,
    root: Path,
    changed_node: str,
    sample_index: int,
    assignment_id: str = "",
    progress: bool = False,
) -> dict[str, Any]:
    command = _command_for(
        operation,
        root=root,
        changed_node=changed_node,
        sample_index=sample_index,
        assignment_id=assignment_id,
        progress=progress,
    )
    if progress and operation in {
        "validate_changed",
        "context_compact",
        "context_execution",
        "context_execution_unchanged",
        "node_context_compact",
    }:
        command.append("--progress")
    before = _truth_snapshot(root)
    cpu_before = os.times()
    started_at = time.perf_counter()
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    finally:
        if operation == "run_closeout":
            _run_closeout_plan_path(root, sample_index).unlink(missing_ok=True)
        elif operation in {"mutation", "interaction_append"}:
            _coord_update_plan_path(root, sample_index).unlink(missing_ok=True)
    wall_time_ms = max(0.0, (time.perf_counter() - started_at) * 1000)
    cpu_after = os.times()
    after = _truth_snapshot(root)
    changed = [path for path in set(before) | set(after) if before.get(path) != after.get(path)]
    written_bytes = sum(after.get(path, (0, 0))[1] for path in changed)
    cpu_time_ms = max(
        0.0,
        (
            (cpu_after.children_user - cpu_before.children_user)
            + (cpu_after.children_system - cpu_before.children_system)
        )
        * 1000,
    )
    return {
        "returncode": result.returncode,
        "wall_time_ms": round(wall_time_ms, 3),
        "cpu_time_ms": round(cpu_time_ms, 3),
        "stdout_bytes": len((result.stdout or "").encode("utf-8")),
        "stderr_bytes": len((result.stderr or "").encode("utf-8")),
        "changed_file_count": len(changed),
        "written_bytes": written_bytes,
        "command": command,
        **(
            {
                "stdout_preview": (result.stdout or "")[:500],
                "stderr_preview": (result.stderr or "")[:500],
            }
            if result.returncode
            else {}
        ),
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _stats(samples: list[dict[str, Any]], key: str) -> dict[str, float]:
    values = [float(sample[key]) for sample in samples]
    return {
        "min": round(min(values), 3) if values else 0.0,
        "median": round(float(statistics.median(values)), 3) if values else 0.0,
        "p95": round(_percentile(values, 0.95), 3),
        "max": round(max(values), 3) if values else 0.0,
    }


def benchmark_runtime(
    root: Path,
    *,
    operations: list[str],
    cold_runs: int,
    warm_runs: int,
    changed_node: str,
    assignment_id: str = "",
    progress: bool = False,
) -> dict[str, Any]:
    if cold_runs < 1:
        raise ValueError("--cold-runs must be at least 1")
    if warm_runs < 1:
        raise ValueError("--warm-runs must be at least 1")
    unsupported = [operation for operation in operations if operation not in SUPPORTED_OPERATIONS]
    if unsupported:
        raise ValueError(f"Unsupported operation: {unsupported[0]}")

    if "run_closeout" in operations:
        _run_json(
            _cli(
                "build",
                "--root",
                root,
                "--skip-resource-search",
                "--json",
            ),
            label="validation index",
        )

    results: list[dict[str, Any]] = []
    sample_index = 0
    for operation in operations:
        cold_samples = []
        for _ in range(cold_runs):
            cold_samples.append(_run_sample(operation, root=root, changed_node=changed_node, sample_index=sample_index, assignment_id=assignment_id, progress=progress))
            sample_index += 1
        warm_samples = []
        for _ in range(warm_runs):
            warm_samples.append(_run_sample(operation, root=root, changed_node=changed_node, sample_index=sample_index, assignment_id=assignment_id, progress=progress))
            sample_index += 1
        samples = [*cold_samples, *warm_samples]
        results.append({
            "operation": operation,
            "cold_samples": cold_samples,
            "warm_samples": warm_samples,
            "summary": {
                "wall_time_ms": _stats(samples, "wall_time_ms"),
                "cpu_time_ms": _stats(samples, "cpu_time_ms"),
                "stdout_bytes": _stats(samples, "stdout_bytes"),
                "stderr_bytes": _stats(samples, "stderr_bytes"),
            },
            "warm_summary": {
                "wall_time_ms": _stats(warm_samples, "wall_time_ms"),
                "cpu_time_ms": _stats(warm_samples, "cpu_time_ms"),
                "stdout_bytes": _stats(warm_samples, "stdout_bytes"),
                "stderr_bytes": _stats(warm_samples, "stderr_bytes"),
            },
        })
    return {
        "ok": all(sample["returncode"] == 0 for item in results for sample in [*item["cold_samples"], *item["warm_samples"]]),
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "cold_runs": cold_runs,
        "warm_runs": warm_runs,
        "operations": operations,
        "changed_node": changed_node,
        "assignment_id": assignment_id or None,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Research Cockpit runtime workflows.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cold-runs", type=int, default=3)
    parser.add_argument("--warm-runs", type=int, default=10)
    parser.add_argument(
        "--operation",
        action="append",
        dest="operations",
        choices=SUPPORTED_OPERATIONS,
    )
    parser.add_argument("--changed-node", default="experiment_perf_0000")
    parser.add_argument("--assignment", dest="assignment_id")
    parser.add_argument("--progress", action="store_true", help="Pass phase progress through to the measured command stderr.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        payload = benchmark_runtime(
            args.root,
            operations=args.operations or list(DEFAULT_OPERATIONS),
            cold_runs=args.cold_runs,
            warm_runs=args.warm_runs,
            changed_node=args.changed_node,
            assignment_id=args.assignment_id or "",
            progress=args.progress,
        )
    except ValueError as exc:
        payload = {"ok": False, "error": str(exc)}
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(str(exc))
        raise SystemExit(1) from None
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for result in payload["results"]:
            stats = result["warm_summary"]["wall_time_ms"]
            print(f"{result['operation']}: median={stats['median']}ms p95={stats['p95']}ms")
    if not payload["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
