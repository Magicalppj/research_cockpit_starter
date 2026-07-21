from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
from typing import Any, Callable

from run_agent_usability_check import (
    agent_a_cold_start_install,
    agent_b_known_node_context,
    agent_c_assigned_worker_round_trip,
    agent_d_reviewer_round_trip,
)
from run_skill_release_check import (
    DEFAULT_SKILL_PATH,
    DEFAULT_TEMP_PARENT,
    _file_manifest,
    _skipped_track,
    _track,
    package_shape_track,
    public_scan_track,
    runtime_dependency_track,
)
from workflow_metrics import workflow_metrics


Scenario = Callable[[Path, str, Path], dict[str, Any]]


def _case_track(
    name: str,
    scenario: Scenario,
    skill_path: Path,
    python: str,
    destination: Path,
) -> dict[str, Any]:
    case = scenario(skill_path, python, destination)
    track = _track(
        name,
        bool(case.get("passed")),
        checks=list(case.get("checks", [])),
        summary={
            "source_case": case.get("case"),
            "copy_changed_files": list(case.get("files_changed", [])),
            "agent_observations": dict(case.get("agent_observations", {})),
            "unexpected_writes": list(case.get("unexpected_writes", [])),
            "workflow_contract": case.get("workflow_contract"),
        },
    )
    metrics = case.get("metrics")
    track["metrics"] = (
        metrics
        if isinstance(metrics, dict)
        else workflow_metrics(
            track["checks"],
            files_changed=track["summary"]["copy_changed_files"],
        )
    )
    return track


def _attach_missing_metrics(tracks: list[dict[str, Any]]) -> None:
    for track in tracks:
        if isinstance(track.get("metrics"), dict):
            continue
        summary = track.get("summary")
        changed_files = (
            summary.get("copy_changed_files", [])
            if isinstance(summary, dict)
            else []
        )
        track["metrics"] = workflow_metrics(
            track.get("checks", []),
            files_changed=changed_files if isinstance(changed_files, list) else [],
        )


def subagent_forward_check_payload(
    skill_path: Path = DEFAULT_SKILL_PATH,
    *,
    python: str = sys.executable,
    temp_parent: Path = DEFAULT_TEMP_PARENT,
    keep_temp: bool = False,
    skip_mutating: bool = False,
) -> dict[str, Any]:
    skill_path = skill_path.resolve()
    temp_run = temp_parent / f"sf_{uuid.uuid4().hex[:8]}"
    temp_run.mkdir(parents=True, exist_ok=False)
    source_before = _file_manifest(skill_path)
    shape = package_shape_track(skill_path)
    public = public_scan_track(skill_path)
    dependency = runtime_dependency_track(python)
    tracks: list[dict[str, Any]] = [shape, public, dependency]
    try:
        if not shape["passed"] or not dependency["passed"]:
            reason = "package shape or runtime dependency check failed"
            tracks.extend(
                _skipped_track(name, reason)
                for name in (
                    "track_a_known_node_reader",
                    "track_b_assigned_worker",
                    "track_c_reviewer",
                    "track_d_portable_install",
                )
            )
        else:
            tracks.append(
                _case_track(
                    "track_a_known_node_reader",
                    agent_b_known_node_context,
                    skill_path,
                    python,
                    temp_run,
                )
            )
            if skip_mutating:
                reason = "--skip-mutating was provided"
                tracks.append(_skipped_track("track_b_assigned_worker", reason))
                tracks.append(_skipped_track("track_c_reviewer", reason))
            else:
                tracks.append(
                    _case_track(
                        "track_b_assigned_worker",
                        agent_c_assigned_worker_round_trip,
                        skill_path,
                        python,
                        temp_run,
                    )
                )
                tracks.append(
                    _case_track(
                        "track_c_reviewer",
                        agent_d_reviewer_round_trip,
                        skill_path,
                        python,
                        temp_run,
                    )
                )
            tracks.append(
                _case_track(
                    "track_d_portable_install",
                    agent_a_cold_start_install,
                    skill_path,
                    python,
                    temp_run,
                )
            )

        source_changed = source_before != _file_manifest(skill_path)
        _attach_missing_metrics(tracks)
        return {
            "ok": all(track["passed"] for track in tracks) and not source_changed,
            "skill_path": str(skill_path),
            "python": python,
            "temp_root": str(temp_run),
            "keep_temp": keep_temp,
            "skip_mutating": skip_mutating,
            "original_package_changed": source_changed,
            "tracks": tracks,
        }
    finally:
        if not keep_temp:
            shutil.rmtree(temp_run, ignore_errors=True)


def _print_text(payload: dict[str, Any]) -> None:
    state = "OK" if payload["ok"] else "FAILED"
    print(f"Subagent forward check: {state}")
    print(f"Skill path: {payload['skill_path']}")
    print(f"Python: {payload['python']}")
    print(f"Original package changed: {payload['original_package_changed']}")
    for track in payload["tracks"]:
        if track["skipped"]:
            print(f"- {track['name']}: SKIPPED ({track['summary'].get('reason')})")
            continue
        marker = "OK" if track["passed"] else "FAILED"
        print(f"- {track['name']}: {marker}")
        if track["summary"]:
            print(f"  summary: {track['summary']}")
        if track.get("metrics"):
            print(f"  metrics: {track['metrics']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-path", type=Path, default=DEFAULT_SKILL_PATH)
    parser.add_argument(
        "--python",
        default=os.environ.get("RESEARCH_COCKPIT_PYTHON", "").strip()
        or sys.executable,
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--skip-mutating", action="store_true")
    args = parser.parse_args()

    payload = subagent_forward_check_payload(
        args.skill_path,
        python=args.python,
        temp_parent=DEFAULT_TEMP_PARENT,
        keep_temp=args.keep_temp,
        skip_mutating=args.skip_mutating,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_text(payload)
    raise SystemExit(0 if payload["ok"] else 1)


if __name__ == "__main__":
    main()
