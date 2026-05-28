from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from research_cockpit.commands.build_dashboard import build_dashboard_once


BENCHMARK_SCHEMA_VERSION = "benchmark_build_v1"


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
) -> dict[str, Any]:
    if runs < 1:
        raise ValueError("--runs must be at least 1")

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
    return {
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
    args = parser.parse_args()

    try:
        payload = benchmark_build(
            args.root,
            runs=args.runs,
            profile_output=args.profile_output,
            include_resource_search=not args.skip_resource_search,
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
