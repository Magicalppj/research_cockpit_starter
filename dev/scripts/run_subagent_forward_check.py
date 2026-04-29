from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
from typing import Any

from run_skill_release_check import (
    DEFAULT_SKILL_PATH,
    DEFAULT_TEMP_PARENT,
    _changed_files,
    _cli,
    _copy_skill_package,
    _data_root,
    _file_manifest,
    _package_env,
    _run_command,
    _skipped_track,
    _track,
    package_shape_track,
    public_scan_track,
    runtime_dependency_track,
)


DECISION_ID = "decision_demo_prompt_refinement"
PROMPT_OPTION_ID = "option_demo_prompt_refinement"
RETRIEVAL_OPTION_ID = "option_demo_retrieval_branch"
PROMPT_EXPERIMENT_ID = "experiment_demo_prompt_refinement"


def _copy_track_source(skill_path: Path, destination: Path) -> Path:
    copy_path = destination / "rc"
    copy_path.parent.mkdir(parents=True, exist_ok=True)
    _copy_skill_package(skill_path, copy_path)
    return copy_path


def _run_all(
    commands: list[list[str]],
    cwd: Path,
    *,
    allowed_returncodes: list[set[int]] | None = None,
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    allowed_returncodes = allowed_returncodes or [{0} for _ in commands]
    return [
        _run_command(command, cwd=cwd, allowed_returncodes=allowed, env=env)
        for command, allowed in zip(commands, allowed_returncodes)
    ]


def read_only_agent_track(skill_path: Path, python: str, cwd: Path) -> dict[str, Any]:
    dependency = runtime_dependency_track(python)
    if not dependency["passed"]:
        return _track("track_a_read_only_agent", False, checks=[dependency], summary=dependency["summary"], stdout=dependency["stdout"])

    root = _data_root(skill_path)
    commands = [
        _cli(python, "bootstrap", "--root", root, "--json"),
        _cli(python, "search", "--root", root, "--query", "demo", "--json"),
        _cli(python, "suggest-next-actions", "--root", root, "--json"),
        _cli(python, "check-decision-acceptance", "--root", root, "--id", DECISION_ID, "--json"),
    ]
    checks = _run_all(commands, cwd, allowed_returncodes=[{0}, {0}, {0}, {0, 1}], env=_package_env(skill_path))
    checklist = checks[-1].get("json") if isinstance(checks[-1].get("json"), dict) else {}
    return _track(
        "track_a_read_only_agent",
        all(check["passed"] for check in checks),
        checks=checks,
        summary={
            "cwd_independent": True,
            "decision_ready": checklist.get("ready"),
            "blocking_failures": checklist.get("blocking_failures", []),
        },
    )


def prompt_refinement_workstream_track(skill_path: Path, python: str, destination: Path) -> dict[str, Any]:
    dependency = runtime_dependency_track(python)
    if not dependency["passed"]:
        return _track("track_b_prompt_refinement_workstream", False, checks=[dependency], summary=dependency["summary"], stdout=dependency["stdout"])

    source_before = _file_manifest(skill_path)
    copy_path = _copy_track_source(skill_path, destination)
    copy_before = _file_manifest(copy_path)
    root = _data_root(copy_path)
    env = _package_env(copy_path)
    commands = [
        _cli(
            python,
            "claim-option",
            "--root",
            root,
            "--option",
            PROMPT_OPTION_ID,
            "--agent",
            "agent_prompt_refinement_test",
            "--objective",
            "Evaluate prompt refinement branch in isolated forward check.",
            "--no-build",
        ),
        _cli(
            python,
            "update-status",
            "--root",
            root,
            "--id",
            PROMPT_EXPERIMENT_ID,
            "--status",
            "done",
            "--result-summary",
            "Prompt refinement isolated run completed.",
        ),
        _cli(
            python,
            "record-finding",
            "--root",
            root,
            "--experiment",
            PROMPT_EXPERIMENT_ID,
            "--statement",
            "Prompt refinement improved consistency in the isolated forward check.",
            "--confidence",
            "medium",
            "--outcome",
            "positive",
            "--metric",
            "consistency_score",
            "--summary",
            "Prompt refinement improved consistency in the isolated forward check.",
            "--no-build",
        ),
        _cli(python, "update-decision-evidence", "--root", root, "--id", DECISION_ID, "--no-build"),
        _cli(
            python,
            "report-option-workstream",
            "--root",
            root,
            "--option",
            PROMPT_OPTION_ID,
            "--agent",
            "agent_prompt_refinement_test",
            "--recommend",
            "continue",
            "--summary",
            "Continue after the isolated prompt refinement evidence update.",
            "--no-build",
        ),
        _cli(python, "validate", "--root", root),
        _cli(python, "build", "--root", root),
    ]
    checks = _run_all(commands, destination, env=env)
    source_changed = source_before != _file_manifest(skill_path)
    copy_changed = _changed_files(copy_before, _file_manifest(copy_path))
    return _track(
        "track_b_prompt_refinement_workstream",
        all(check["passed"] for check in checks) and not source_changed and bool(copy_changed),
        checks=checks,
        summary={
            "copy_path": str(copy_path),
            "source_changed": source_changed,
            "copy_changed_files": copy_changed[:60],
            "copy_changed_count": len(copy_changed),
        },
    )


def retrieval_branch_track(skill_path: Path, python: str, destination: Path) -> dict[str, Any]:
    dependency = runtime_dependency_track(python)
    if not dependency["passed"]:
        return _track("track_c_retrieval_branch_agent", False, checks=[dependency], summary=dependency["summary"], stdout=dependency["stdout"])

    source_before = _file_manifest(skill_path)
    copy_path = _copy_track_source(skill_path, destination)
    copy_before = _file_manifest(copy_path)
    root = _data_root(copy_path)
    env = _package_env(copy_path)
    new_problem = "problem_demo_retrieval_scope"
    new_option = "option_demo_retrieval_minimal_index"
    new_experiment = "experiment_demo_retrieval_index_smoke"
    commands = [
        _cli(
            python,
            "claim-option",
            "--root",
            root,
            "--option",
            RETRIEVAL_OPTION_ID,
            "--agent",
            "agent_retrieval_test",
            "--status",
            "in_progress",
            "--no-build",
        ),
        _cli(
            python,
            "add-node",
            "--root",
            root,
            "--id",
            new_problem,
            "--type",
            "problem",
            "--title",
            "Define retrieval scope",
            "--parent",
            RETRIEVAL_OPTION_ID,
            "--status",
            "active",
            "--summary",
            "Scope a minimal retrieval branch for the demo cockpit.",
        ),
        _cli(
            python,
            "add-node",
            "--root",
            root,
            "--id",
            new_option,
            "--type",
            "option",
            "--title",
            "Build minimal index",
            "--parent",
            new_problem,
            "--status",
            "active",
            "--summary",
            "Try a minimal local index before expanding retrieval.",
        ),
        _cli(
            python,
            "add-node",
            "--root",
            root,
            "--id",
            new_experiment,
            "--type",
            "experiment",
            "--title",
            "Retrieval index smoke test",
            "--parent",
            new_option,
            "--status",
            "planned",
            "--summary",
            "Check whether a minimal index improves demo lookup.",
        ),
        _cli(
            python,
            "report-option-workstream",
            "--root",
            root,
            "--option",
            RETRIEVAL_OPTION_ID,
            "--agent",
            "agent_retrieval_test",
            "--recommend",
            "continue",
            "--summary",
            "Retrieval branch now has a scoped child problem, option, and planned experiment.",
            "--no-build",
        ),
        _cli(python, "option-workstream-context", "--root", root, "--option", RETRIEVAL_OPTION_ID, "--json"),
        _cli(python, "validate", "--root", root),
        _cli(python, "build", "--root", root),
    ]
    checks = _run_all(commands, destination, env=env)
    context = checks[5].get("json") if isinstance(checks[5].get("json"), dict) else {}
    subtree = context.get("subtree", {}) if isinstance(context, dict) else {}
    recursive_ok = new_experiment in set(subtree.get("experiment_ids", []))
    source_changed = source_before != _file_manifest(skill_path)
    copy_changed = _changed_files(copy_before, _file_manifest(copy_path))
    return _track(
        "track_c_retrieval_branch_agent",
        all(check["passed"] for check in checks) and recursive_ok and not source_changed and bool(copy_changed),
        checks=checks,
        summary={
            "copy_path": str(copy_path),
            "new_node_ids": [new_problem, new_option, new_experiment],
            "recursive_context_contains_new_experiment": recursive_ok,
            "source_changed": source_changed,
            "copy_changed_files": copy_changed[:60],
            "copy_changed_count": len(copy_changed),
        },
    )


def decision_gate_workflow_track(skill_path: Path, python: str, destination: Path) -> dict[str, Any]:
    dependency = runtime_dependency_track(python)
    if not dependency["passed"]:
        return _track("track_d_decision_gate_agent", False, checks=[dependency], summary=dependency["summary"], stdout=dependency["stdout"])

    source_before = _file_manifest(skill_path)
    copy_path = _copy_track_source(skill_path, destination)
    copy_before = _file_manifest(copy_path)
    root = _data_root(copy_path)
    env = _package_env(copy_path)
    commands = [
        _cli(python, "check-decision-acceptance", "--root", root, "--id", DECISION_ID, "--json"),
        _cli(
            python,
            "record-finding",
            "--root",
            root,
            "--experiment",
            PROMPT_EXPERIMENT_ID,
            "--statement",
            "Decision gate forward check evidence supports continuing prompt refinement.",
            "--confidence",
            "medium",
            "--outcome",
            "positive",
            "--summary",
            "Decision gate forward check evidence supports continuing prompt refinement.",
            "--no-build",
        ),
        _cli(python, "update-decision-evidence", "--root", root, "--id", DECISION_ID, "--no-build"),
        _cli(
            python,
            "update-decision-checklist",
            "--root",
            root,
            "--id",
            DECISION_ID,
            "--alternative",
            RETRIEVAL_OPTION_ID,
            "--consequence",
            "Keep retrieval branch available as a fallback.",
            "--next-required-action",
            "Run a follow-up smoke test after accepting the decision.",
            "--no-build",
        ),
        _cli(python, "check-decision-acceptance", "--root", root, "--id", DECISION_ID, "--json"),
        _cli(python, "accept-decision", "--root", root, "--id", DECISION_ID, "--no-build"),
        _cli(python, "validate", "--root", root),
        _cli(python, "build", "--root", root),
    ]
    checks = _run_all(commands, destination, allowed_returncodes=[{0, 1}, {0}, {0}, {0}, {0}, {0}, {0}, {0}], env=env)
    before = checks[0].get("json") if isinstance(checks[0].get("json"), dict) else {}
    after = checks[4].get("json") if isinstance(checks[4].get("json"), dict) else {}
    source_changed = source_before != _file_manifest(skill_path)
    copy_changed = _changed_files(copy_before, _file_manifest(copy_path))
    return _track(
        "track_d_decision_gate_agent",
        all(check["passed"] for check in checks) and before.get("ready") is False and after.get("ready") is True and not source_changed,
        checks=checks,
        summary={
            "copy_path": str(copy_path),
            "ready_before": before.get("ready"),
            "ready_after": after.get("ready"),
            "blocking_failures_before": before.get("blocking_failures", []),
            "blocking_failures_after": after.get("blocking_failures", []),
            "source_changed": source_changed,
            "copy_changed_files": copy_changed[:60],
            "copy_changed_count": len(copy_changed),
        },
    )


def portable_skill_track(skill_path: Path, python: str, destination: Path) -> dict[str, Any]:
    dependency = runtime_dependency_track(python)
    if not dependency["passed"]:
        return _track("track_e_portable_skill_agent", False, checks=[dependency], summary=dependency["summary"], stdout=dependency["stdout"])

    copy_path = destination / ".agent" / "skills" / "research-cockpit"
    copy_path.parent.mkdir(parents=True, exist_ok=True)
    _copy_skill_package(skill_path, copy_path)
    root = _data_root(copy_path)
    commands = [
        _cli(python, "smoke", "--root", root, "--json"),
        _cli(python, "bootstrap", "--root", root, "--json"),
        _cli(python, "commands", "--json"),
    ]
    checks = _run_all(commands, destination, env=_package_env(copy_path))
    public_scan = public_scan_track(copy_path)
    return _track(
        "track_e_portable_skill_agent",
        all(check["passed"] for check in checks) and public_scan["passed"],
        checks=[*checks, public_scan],
        summary={
            "copy_path": str(copy_path),
            "cwd_independent": True,
            "private_scan_passed": public_scan["passed"],
            "private_scan_offenders": public_scan["summary"].get("offenders", []),
        },
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
    temp_run = temp_parent / "subagent_runs" / f"sf_{uuid.uuid4().hex[:8]}"
    temp_run.mkdir(parents=True, exist_ok=False)
    source_before = _file_manifest(skill_path)
    tracks: list[dict[str, Any]] = []
    try:
        shape = package_shape_track(skill_path)
        tracks.append(shape)
        tracks.append(public_scan_track(skill_path))
        if not shape["passed"]:
            reason = "package_shape failed"
            tracks.append(_skipped_track("track_a_read_only_agent", reason))
            tracks.append(_skipped_track("track_b_prompt_refinement_workstream", reason))
            tracks.append(_skipped_track("track_c_retrieval_branch_agent", reason))
            tracks.append(_skipped_track("track_d_decision_gate_agent", reason))
            tracks.append(_skipped_track("track_e_portable_skill_agent", reason))
        else:
            tracks.append(read_only_agent_track(skill_path, python, temp_run))
            if skip_mutating:
                tracks.append(_skipped_track("track_b_prompt_refinement_workstream", "--skip-mutating was provided"))
                tracks.append(_skipped_track("track_c_retrieval_branch_agent", "--skip-mutating was provided"))
                tracks.append(_skipped_track("track_d_decision_gate_agent", "--skip-mutating was provided"))
            else:
                tracks.append(prompt_refinement_workstream_track(skill_path, python, temp_run / "b"))
                tracks.append(retrieval_branch_track(skill_path, python, temp_run / "c"))
                tracks.append(decision_gate_workflow_track(skill_path, python, temp_run / "d"))
            tracks.append(portable_skill_track(skill_path, python, temp_run / "p"))

        source_changed = source_before != _file_manifest(skill_path)
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-path", type=Path, default=DEFAULT_SKILL_PATH)
    parser.add_argument("--python", default=os.environ.get("RESEARCH_COCKPIT_PYTHON", "").strip() or sys.executable)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--skip-mutating", action="store_true")
    args = parser.parse_args()

    payload = subagent_forward_check_payload(
        args.skill_path,
        python=args.python,
        keep_temp=args.keep_temp,
        skip_mutating=args.skip_mutating,
    )
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_text(payload)
    raise SystemExit(0 if payload["ok"] else 1)


if __name__ == "__main__":
    main()
