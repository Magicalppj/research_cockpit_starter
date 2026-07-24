from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from time import perf_counter
from typing import Any, Iterable


ROOT_DIR = Path(__file__).resolve().parents[2]
RELEASE_CHECK = Path("dev") / "scripts" / "run_skill_release_check.py"
SCHEMA_VERSION = "test_profile_v1"
PROFILE_ORDER = ("fast", "precommit", "full")
PROFILE_TARGET_SECONDS = {
    "fast": 15,
    "precommit": 60,
    "full": 360,
}

FAST_TESTS = (
    "tests.test_verification_profiles",
    "tests.test_operation_receipts",
    "tests.test_work_packets",
    "tests.test_assignment_dependencies",
)

PRECOMMIT_TESTS = (
    *FAST_TESTS,
    (
        "tests.test_coordinator_operations.CoordinatorAssignmentTests."
        "test_session_action_creates_explicit_assignment_once"
    ),
    (
        "tests.test_work_start.WorkStartTests."
        "test_start_generates_run_and_piggybacks_lease_renewal"
    ),
    (
        "tests.test_work_close.WorkCloseTests."
        "test_close_writes_bounded_result_and_completes_assignment_atomically"
    ),
    (
        "tests.test_work_record.WorkRecordTests."
        "test_record_is_idempotent_and_defaults_to_reference"
    ),
    (
        "tests.test_blind_acceptance_regressions.BlindAcceptanceRegressionTests."
        "test_session_targets_experiment_and_start_binds_packet_revision"
    ),
    "tests.test_cli_cutover",
    "tests.test_role_release_surface",
    (
        "tests.test_scripts.ScriptBehaviorTests."
        "test_context_execution_view_is_bounded_and_keeps_execution_invariants"
    ),
    (
        "tests.test_scripts.ScriptBehaviorTests."
        "test_metadata_write_manifest_flags"
    ),
    (
        "tests.test_scripts.ScriptBehaviorTests."
        "test_command_manifest_supported_flags_match_real_help"
    ),
    (
        "tests.test_scripts.ScriptBehaviorTests."
        "test_skill_smoke_test_payload_runs_read_only_workflow"
    ),
)

_UNITTEST_COUNT = re.compile(r"Ran\s+(\d+)\s+tests?\s+in\s+([0-9.]+)s")
_UNITTEST_SKIPPED = re.compile(r"skipped=(\d+)")
_FAILURE_TAIL_BYTES = 8 * 1024


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def profile_test_targets(profile: str) -> tuple[str, ...]:
    if profile == "fast":
        return FAST_TESTS
    if profile == "precommit":
        return PRECOMMIT_TESTS
    if profile == "full":
        return ()
    raise ValueError(f"Unknown test profile: {profile}")


def build_profile_plan(
    profile: str,
    *,
    python: str,
    extra_tests: Iterable[str] = (),
) -> list[dict[str, Any]]:
    targets = profile_test_targets(profile)
    extras = _dedupe(str(value).strip() for value in extra_tests if str(value).strip())
    if profile == "full" and extras:
        raise ValueError("--extra-test is supported only by fast and precommit profiles")

    test_command = [python, "-m", "unittest"]
    if profile != "full":
        test_command.extend(_dedupe((*targets, *extras)))
    test_env = {}
    if profile == "full":
        test_env["RESEARCH_COCKPIT_EXTERNAL_RELEASE_CHECK"] = "1"
    stages: list[dict[str, Any]] = [
        {
            "name": "tests",
            "kind": "unittest",
            "command": test_command,
            "env": test_env,
        }
    ]

    if profile in {"precommit", "full"}:
        release_command = [
            python,
            str(RELEASE_CHECK),
            "--json",
            "--python",
            python,
        ]
        release_name = "release_check_full"
        if profile == "precommit":
            release_command.append("--skip-mutating")
            release_name = "release_check_read_only"
        stages.append(
            {
                "name": release_name,
                "kind": "release_check",
                "command": release_command,
                "env": {},
            }
        )
    return stages


def parse_unittest_summary(output: str) -> dict[str, int | None]:
    count = _UNITTEST_COUNT.search(output)
    skipped = _UNITTEST_SKIPPED.search(output)
    return {
        "tests_run": int(count.group(1)) if count else None,
        "skipped": int(skipped.group(1)) if skipped else 0,
        "reported_duration_ms": round(float(count.group(2)) * 1000) if count else None,
    }


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _tail(data: bytes) -> str:
    if len(data) <= _FAILURE_TAIL_BYTES:
        return _decode(data)
    return "...[truncated]\n" + _decode(data[-_FAILURE_TAIL_BYTES:])


def _release_summary(stdout: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(_decode(stdout))
    except json.JSONDecodeError:
        return {"ok": None, "tracks": []}
    tracks = payload.get("tracks", []) if isinstance(payload, dict) else []
    return {
        "ok": payload.get("ok") if isinstance(payload, dict) else None,
        "tracks": [
            {
                "name": str(track.get("name") or ""),
                "passed": bool(track.get("passed", False)),
                "skipped": bool(track.get("skipped", False)),
            }
            for track in tracks
            if isinstance(track, dict)
        ],
    }


def run_stage(
    stage: dict[str, Any],
    *,
    progress: bool = False,
) -> dict[str, Any]:
    if progress:
        print(f"[test-profile] start {stage['name']}", file=sys.stderr, flush=True)
    started = perf_counter()
    completed = subprocess.run(
        stage["command"],
        cwd=ROOT_DIR,
        env={**os.environ, **stage.get("env", {})},
        capture_output=True,
        check=False,
    )
    duration_ms = round((perf_counter() - started) * 1000)
    result: dict[str, Any] = {
        "name": stage["name"],
        "kind": stage["kind"],
        "passed": completed.returncode == 0,
        "returncode": completed.returncode,
        "duration_ms": duration_ms,
        "stdout_bytes": len(completed.stdout),
        "stderr_bytes": len(completed.stderr),
    }
    if stage["kind"] == "unittest":
        result["summary"] = parse_unittest_summary(
            _decode(completed.stdout + b"\n" + completed.stderr)
        )
    else:
        result["summary"] = _release_summary(completed.stdout)
    if completed.returncode != 0:
        result["stdout_tail"] = _tail(completed.stdout)
        result["stderr_tail"] = _tail(completed.stderr)
    if progress:
        status = "passed" if result["passed"] else "failed"
        print(
            f"[test-profile] {status} {stage['name']} {duration_ms}ms",
            file=sys.stderr,
            flush=True,
        )
    return result


def run_profile(
    profile: str,
    *,
    python: str,
    extra_tests: Iterable[str] = (),
    progress: bool = False,
) -> dict[str, Any]:
    plan = build_profile_plan(profile, python=python, extra_tests=extra_tests)
    started = perf_counter()
    results: list[dict[str, Any]] = []
    for stage in plan:
        result = run_stage(stage, progress=progress)
        results.append(result)
        if not result["passed"]:
            break
    duration_ms = round((perf_counter() - started) * 1000)
    target_ms = PROFILE_TARGET_SECONDS[profile] * 1000
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
        "ok": len(results) == len(plan) and all(item["passed"] for item in results),
        "duration_ms": duration_ms,
        "target_ms": target_ms,
        "within_target": duration_ms <= target_ms,
        "stages_planned": len(plan),
        "stages_run": len(results),
        "stopped_early": len(results) < len(plan),
        "results": results,
    }


def profiles_payload() -> dict[str, Any]:
    purpose = {
        "fast": "Small edit-loop sentinels; add changed scope explicitly.",
        "precommit": "Facade integration plus read-only release checks.",
        "full": "All tests plus the complete release harness before merge or release.",
    }
    return {
        "schema_version": "test_profiles_v1",
        "profiles": [
            {
                "name": profile,
                "target_seconds": PROFILE_TARGET_SECONDS[profile],
                "test_target_count": len(profile_test_targets(profile)) or None,
                "release_mode": (
                    "none"
                    if profile == "fast"
                    else "read_only"
                    if profile == "precommit"
                    else "full"
                ),
                "purpose": purpose[profile],
            }
            for profile in PROFILE_ORDER
        ],
    }


def _emit(payload: dict[str, Any], *, compact: bool) -> None:
    if compact:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a bounded Research Cockpit test profile.")
    parser.add_argument("profile", nargs="?", choices=PROFILE_ORDER, default="fast")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--extra-test", action="append", default=[])
    parser.add_argument("--list", action="store_true", dest="list_profiles")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()

    if args.list_profiles:
        payload = profiles_payload()
    else:
        try:
            payload = run_profile(
                args.profile,
                python=args.python,
                extra_tests=args.extra_test,
                progress=args.progress,
            )
        except ValueError as exc:
            parser.error(str(exc))
    if args.json or args.list_profiles:
        _emit(payload, compact=args.compact)
    else:
        status = "passed" if payload["ok"] else "failed"
        print(
            f"{payload['profile']}: {status} in {payload['duration_ms']}ms "
            f"({payload['stages_run']}/{payload['stages_planned']} stages)"
        )
    if not args.list_profiles and not payload["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
