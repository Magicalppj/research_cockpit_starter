from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from research_cockpit.commands.build_dashboard import build_dashboard_once


BENCHMARK_SCHEMA_VERSION = "benchmark_build_v1"


def _duration_ms(started_at: float) -> float:
    return round(max(0.0, time.perf_counter() - started_at) * 1000, 3)


def _cli_args(command: str, *args: str) -> list[str]:
    return [sys.executable, "-m", "research_cockpit.cli", command, *[str(arg) for arg in args]]


def _run_worker_step(name: str, command: list[str], *, require_changed: bool = False) -> dict[str, Any]:
    started_at = time.perf_counter()
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    summary: dict[str, Any] = {}
    passed = result.returncode == 0
    if stdout.strip().startswith("{"):
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            for key in ("ok", "changed", "would_change", "node_count", "mode"):
                if key in parsed:
                    summary[key] = parsed[key]
            if require_changed and parsed.get("changed") is not True:
                passed = False
                summary["error"] = "expected_changed_true"
    payload: dict[str, Any] = {
        "name": name,
        "passed": passed,
        "returncode": result.returncode,
        "duration_ms": _duration_ms(started_at),
        "stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
        "command": command,
        "summary": summary,
    }
    if not passed:
        payload["stdout_preview"] = stdout[:500]
        payload["stderr_preview"] = stderr[:500]
    return payload


def benchmark_worker_edit_flow(root: Path, *, node_id: str, summary: str) -> dict[str, Any]:
    unique_summary = f"{summary} {time.time_ns()}"
    steps = [
        _run_worker_step(
            "mutate_no_build",
            _cli_args(
                "update-node-fields",
                "--root",
                str(root),
                "--id",
                node_id,
                "--summary",
                unique_summary,
                "--no-build",
                "--json",
                "--compact",
            ),
            require_changed=True,
        ),
        _run_worker_step(
            "compact_context",
            _cli_args(
                "context",
                "--root",
                str(root),
                "--id",
                node_id,
                "--with-bootstrap",
                "--with-artifacts",
                "--compact",
                "--json",
            ),
        ),
        _run_worker_step("full_validate", _cli_args("validate", "--root", str(root), "--json")),
        _run_worker_step("build", _cli_args("build", "--root", str(root), "--json")),
        _run_worker_step("compact_smoke", _cli_args("smoke", "--root", str(root), "--json", "--progress")),
    ]
    return {
        "ok": all(step["passed"] for step in steps),
        "node_id": node_id,
        "summary": {
            "total_duration_ms": round(sum(float(step["duration_ms"]) for step in steps), 3),
            "total_stdout_bytes": sum(int(step["stdout_bytes"]) for step in steps),
            "total_stderr_bytes": sum(int(step["stderr_bytes"]) for step in steps),
        },
        "steps": steps,
    }


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "median": 0.0, "max": 0.0}
    return {
        "min": round(min(values), 3),
        "median": round(float(statistics.median(values)), 3),
        "max": round(max(values), 3),
    }


def _stage_summary(runs: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    values_by_stage: dict[str, list[float]] = {}
    for run in runs:
        for stage in run["profile"].get("stages", []):
            values_by_stage.setdefault(str(stage["name"]), []).append(float(stage.get("duration_ms") or 0))
    return {stage: _stats(values) for stage, values in sorted(values_by_stage.items())}


def benchmark_build(
    root: Path,
    *,
    runs: int,
    profile_output: Path,
    include_resource_search: bool = True,
    include_worker_flow: bool = True,
    changed_node: str | None = None,
    worker_summary: str = "Synthetic worker edit benchmark marker.",
) -> dict[str, Any]:
    if runs < 1:
        raise ValueError("--runs must be at least 1")
    if include_worker_flow and not changed_node:
        changed_node = "experiment_perf_0000"
        if not (root / "graph" / "nodes" / f"{changed_node}.yaml").exists():
            include_worker_flow = False

    run_payloads: list[dict[str, Any]] = []
    for run_index in range(runs):
        payload = build_dashboard_once(
            root,
            json_output=True,
            profile=True,
            profile_output=profile_output,
            include_resource_search=include_resource_search,
        )
        profile = payload["profile"]
        run_payloads.append({
            "index": run_index + 1,
            "total_duration_ms": profile["total_duration_ms"],
            "profile": profile,
        })

    totals = [float(run["total_duration_ms"]) for run in run_payloads]
    last_profile = run_payloads[-1]["profile"]
    payload: dict[str, Any] = {
        "ok": True,
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "root": str(root),
        "run_count": runs,
        "include_resource_search": include_resource_search,
        "profile_output": str((root / profile_output).resolve(strict=False) if not profile_output.is_absolute() else profile_output),
        "summary": {
            "total_duration_ms": _stats(totals),
            "stages": _stage_summary(run_payloads),
            "counts": last_profile.get("counts", {}),
            "output_files": last_profile.get("output_files", []),
        },
        "runs": [
            {
                "index": run["index"],
                "total_duration_ms": run["total_duration_ms"],
                "stages": run["profile"].get("stages", []),
            }
            for run in run_payloads
        ],
    }
    if include_worker_flow:
        worker_flow = benchmark_worker_edit_flow(root, node_id=str(changed_node), summary=worker_summary)
        payload["worker_edit_flow"] = worker_flow
        payload["ok"] = bool(payload["ok"] and worker_flow["ok"])
    return payload


def _print_text(payload: dict[str, Any]) -> None:
    total = payload["summary"]["total_duration_ms"]
    print(f"Benchmarked {payload['run_count']} dashboard builds for {payload['root']}")
    print(f"Total duration ms: min={total['min']} median={total['median']} max={total['max']}")
    for stage, stats in payload["summary"]["stages"].items():
        print(f"- {stage}: min={stats['min']} median={stats['median']} max={stats['max']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Research Cockpit dashboard builds.")
    parser.add_argument("--root", type=Path, required=True, help="Research Cockpit data root to build.")
    parser.add_argument("--runs", type=int, default=3, help="Number of build runs to measure.")
    parser.add_argument(
        "--profile-output",
        type=Path,
        default=Path("dashboards/build_profile.json"),
        help="Profile JSON path for the latest run. Relative paths are resolved under --root.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable benchmark results.")
    parser.add_argument(
        "--skip-resource-search",
        action="store_true",
        help="Benchmark build with local linked resource text indexing disabled.",
    )
    parser.add_argument(
        "--include-worker-flow",
        dest="include_worker_flow",
        action="store_true",
        default=True,
        help="Also measure the current worker edit verification flow. Enabled by default when a target node can be inferred.",
    )
    parser.add_argument(
        "--skip-worker-flow",
        dest="include_worker_flow",
        action="store_false",
        help="Only benchmark dashboard build runs.",
    )
    parser.add_argument("--changed-node", help="Node id to edit when --include-worker-flow is set.")
    parser.add_argument(
        "--worker-summary",
        default="Synthetic worker edit benchmark marker.",
        help="Summary text to write during the worker edit benchmark.",
    )
    args = parser.parse_args()

    try:
        payload = benchmark_build(
            args.root,
            runs=args.runs,
            profile_output=args.profile_output,
            include_resource_search=not args.skip_resource_search,
            include_worker_flow=args.include_worker_flow,
            changed_node=args.changed_node,
            worker_summary=args.worker_summary,
        )
    except ValueError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
        else:
            print(str(exc))
        raise SystemExit(1) from None

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    _print_text(payload)


if __name__ == "__main__":
    main()
