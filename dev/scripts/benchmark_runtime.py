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


SCHEMA_VERSION = "benchmark_runtime_v1"
BENCHMARK_SESSION_ID = f"{os.getpid()}_{time.time_ns()}"
DEFAULT_OPERATIONS = ("validate_changed", "context_compact", "mutation", "run_closeout")


def _cli(command: str, *parts: str) -> list[str]:
    return [sys.executable, "-m", "research_cockpit.cli", command, *[str(part) for part in parts]]


def _truth_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    files: dict[str, tuple[int, int]] = {}
    candidates = [
        root / "current_state.yaml",
        root / "coordinator_state.yaml",
        root / "graph" / "interaction_log.yaml",
    ]
    for directory in ("graph/nodes", "runs", "gate_results", "artifact_records", "assignments"):
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


def _run_closeout_plan_path(root: Path, sample_index: int) -> Path:
    return root / "dashboards" / f".benchmark-run-closeout-{sample_index}.json"


def _prepare_run_closeout(root: Path, changed_node: str, sample_index: int) -> Path:
    run_id = f"runtime_benchmark_closeout_{BENCHMARK_SESSION_ID}_{sample_index}"
    created = subprocess.run(
        _cli(
            "create-run",
            "--root",
            root,
            "--id",
            run_id,
            "--experiment",
            changed_node,
            "--status",
            "running",
            "--coordinator",
            "--no-build",
            "--json",
            "--compact",
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode:
        detail = (created.stderr or created.stdout or "").strip()
        raise RuntimeError(f"Could not prepare run_closeout benchmark: {detail}")
    gate_specs = []
    gate_dir = root / "gate_results"
    gate_dir.mkdir(parents=True, exist_ok=True)
    for gate_index in range(2):
        gate_id = f"runtime_benchmark_gate_{BENCHMARK_SESSION_ID}_{sample_index}_{gate_index}"
        gate_file = f"gate_results/{gate_id}.json"
        (root / gate_file).write_text(
            json.dumps({
                "gate_type": "smoke" if gate_index == 0 else "schema",
                "passed": True,
                "experiment_id": changed_node,
                "run_id": run_id,
            }),
            encoding="utf-8",
        )
        gate_specs.append({"id": gate_id, "file": gate_file})

    plan_path = _run_closeout_plan_path(root, sample_index)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps({
            "schema_version": "run_closeout_v1",
            "run": {"id": run_id, "status": "completed"},
            "artifact_record": {
                "record_id": f"runtime_benchmark_record_{BENCHMARK_SESSION_ID}_{sample_index}",
                "title": f"Runtime closeout record {sample_index}",
                "links": {},
            },
            "gates": gate_specs,
            "finding": {
                "statement": f"Runtime closeout finding {sample_index}.",
                "confidence": "medium",
                "outcome": "inconclusive",
            },
            "next_actions": {
                "experiment": [f"Review runtime closeout sample {sample_index}."]
            },
        }),
        encoding="utf-8",
    )
    return plan_path

def _command_for(
    operation: str,
    *,
    root: Path,
    changed_node: str,
    sample_index: int,
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
    if operation == "node_context_compact":
        return _cli("node-context", "--root", root, "--id", changed_node, "--compact", "--json")
    if operation in {"mutation", "interaction_append"}:
        return _cli(
            "update-node-fields",
            "--root",
            root,
            "--id",
            changed_node,
            "--summary",
            f"Runtime benchmark sample {sample_index} at {time.time_ns()}",
            "--no-build",
            "--json",
            "--compact",
        )
    if operation == "run_closeout":
        plan_path = _prepare_run_closeout(root, changed_node, sample_index)
        return _cli(
            "complete-run",
            "--root",
            root,
            "--file",
            plan_path,
            "--coordinator",
            "--no-build",
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
    progress: bool = False,
) -> dict[str, Any]:
    command = _command_for(
        operation,
        root=root,
        changed_node=changed_node,
        sample_index=sample_index,
        progress=progress,
    )
    if progress:
        command.append("--progress")
    before = _truth_snapshot(root)
    cpu_before = os.times()
    started_at = time.perf_counter()
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    finally:
        if operation == "run_closeout":
            _run_closeout_plan_path(root, sample_index).unlink(missing_ok=True)
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
    progress: bool = False,
) -> dict[str, Any]:
    if cold_runs < 1:
        raise ValueError("--cold-runs must be at least 1")
    if warm_runs < 1:
        raise ValueError("--warm-runs must be at least 1")
    unsupported = [operation for operation in operations if operation not in {
        "validate_changed", "context_compact", "node_context_compact", "mutation", "interaction_append", "run_closeout"
    }]
    if unsupported:
        raise ValueError(f"Unsupported operation: {unsupported[0]}")

    results: list[dict[str, Any]] = []
    sample_index = 0
    for operation in operations:
        cold_samples = []
        for _ in range(cold_runs):
            cold_samples.append(_run_sample(operation, root=root, changed_node=changed_node, sample_index=sample_index, progress=progress))
            sample_index += 1
        warm_samples = []
        for _ in range(warm_runs):
            warm_samples.append(_run_sample(operation, root=root, changed_node=changed_node, sample_index=sample_index, progress=progress))
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
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Research Cockpit runtime workflows.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cold-runs", type=int, default=3)
    parser.add_argument("--warm-runs", type=int, default=10)
    parser.add_argument("--operation", action="append", dest="operations", choices=[
        "validate_changed", "context_compact", "node_context_compact", "mutation", "interaction_append", "run_closeout"
    ])
    parser.add_argument("--changed-node", default="experiment_perf_0000")
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
