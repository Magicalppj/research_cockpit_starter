from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILL_PATH = REPO_ROOT / "skills" / "research-cockpit"
DEFAULT_TEMP_PARENT = REPO_ROOT / ".test_tmp"
TEXT_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".txt"}
REQUIRED_MODULES = {
    "networkx": "networkx",
    "yaml": "PyYAML",
}
REQUIRED_PACKAGE_PATHS = (
    "SKILL.md",
    "AGENTS.md",
    "README.md",
    "requirements.txt",
    "cockpit/model.py",
    "scripts/agent_bootstrap.py",
    "scripts/skill_smoke_test.py",
    "scripts/list_agent_commands.py",
    "scripts/claim_option.py",
    "scripts/option_workstream_context.py",
    "scripts/report_option_workstream.py",
    "ui/app.py",
    "research_cockpit/current_state.yaml",
    "agents/openai.yaml",
)
FORBIDDEN_PACKAGE_PARTS = {"dev", "tests", ".test_tmp"}
FORBIDDEN_STRINGS = (
    "D:" + "\\Tools",
    "C:" + "\\Users" + "\\" + "22" + "339",
    "22" + "339",
    "miniconda3" + "\\envs" + "\\aigc",
)


def _track(
    name: str,
    passed: bool,
    *,
    summary: dict[str, Any] | None = None,
    checks: list[dict[str, Any]] | None = None,
    stdout: str = "",
    stderr: str = "",
    skipped: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "skipped": skipped,
        "summary": summary or {},
        "checks": checks or [],
        "stdout": stdout,
        "stderr": stderr,
    }


def _short_text(value: str, limit: int = 1200) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "...<truncated>"


def _script(skill_path: Path, script_name: str) -> str:
    return str(skill_path / "scripts" / script_name)


def _data_root(skill_path: Path) -> str:
    return str(skill_path / "research_cockpit")


def _current_option_id(skill_path: Path) -> str | None:
    import yaml

    path = skill_path / "research_cockpit" / "current_state.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    option_id = data.get("current_option")
    return str(option_id) if option_id else None


def _run_command(args: list[str], *, cwd: Path | None = None, allowed_returncodes: set[int] | None = None) -> dict[str, Any]:
    allowed = allowed_returncodes or {0}
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    except OSError as exc:
        return {
            "command": args,
            "passed": False,
            "returncode": 1,
            "stdout": "",
            "stderr": str(exc),
            "json": None,
        }

    stdout = _short_text(result.stdout)
    stderr = _short_text(result.stderr)
    return {
        "command": args,
        "passed": result.returncode in allowed and "Traceback" not in stdout and "Traceback" not in stderr,
        "returncode": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "json": _try_json(result.stdout),
    }


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _missing_modules_for_python(python: str, required: dict[str, str] = REQUIRED_MODULES) -> list[str]:
    missing: list[str] = []
    for module in required:
        try:
            result = subprocess.run([python, "-c", f"import {module}"], capture_output=True, text=True, check=False)
        except OSError:
            return list(required)
        if result.returncode != 0:
            missing.append(module)
    return missing


def runtime_dependency_track(python: str, required: dict[str, str] = REQUIRED_MODULES) -> dict[str, Any]:
    missing = _missing_modules_for_python(python, required)
    if not missing:
        return _track(
            "runtime_dependencies",
            True,
            summary={"missing_modules": []},
            checks=[{"command": [python, "-c", "import " + ", ".join(required)], "passed": True, "returncode": 0}],
        )

    packages = ", ".join(required.get(module, module) for module in missing)
    modules = ", ".join(missing)
    message = (
        f"Missing Python modules for {python}: {modules}. "
        f"Install requirements with `python -m pip install -r requirements.txt` "
        f"or rerun with an interpreter that already has: {packages}."
    )
    return _track(
        "runtime_dependencies",
        False,
        summary={"missing_modules": missing},
        checks=[{"command": [python, "-c", "import " + ", ".join(required)], "passed": False, "returncode": 1}],
        stdout=message,
    )


def package_shape_track(skill_path: Path) -> dict[str, Any]:
    missing = [relative for relative in REQUIRED_PACKAGE_PATHS if not (skill_path / relative).exists()]
    forbidden_present = [
        path.name
        for path in skill_path.iterdir()
        if path.name in FORBIDDEN_PACKAGE_PARTS or path.name in {"docs_development_status.md", "research_cockpit_v2_specs"}
    ] if skill_path.exists() else []
    passed = not missing and not forbidden_present
    return _track(
        "package_shape",
        passed,
        summary={
            "required_count": len(REQUIRED_PACKAGE_PATHS),
            "missing": missing,
            "forbidden_present": forbidden_present,
        },
    )


def _scan_files(skill_path: Path) -> list[Path]:
    files: list[Path] = []
    if not skill_path.exists():
        return files
    for path in skill_path.rglob("*"):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        if any(part in FORBIDDEN_PACKAGE_PARTS for part in path.relative_to(skill_path).parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            files.append(path)
    return files


def public_scan_track(skill_path: Path) -> dict[str, Any]:
    repo_path = str(REPO_ROOT)
    forbidden = [*FORBIDDEN_STRINGS, repo_path, repo_path.replace("/", "\\")]
    offenders: list[str] = []
    for path in _scan_files(skill_path):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for item in forbidden:
            if item and item in text:
                offenders.append(f"{path.relative_to(skill_path).as_posix()} contains forbidden text")
                break
    return _track(
        "public_scan",
        not offenders,
        summary={"scanned_files": len(_scan_files(skill_path)), "offenders": offenders},
    )


def _copy_skill_package(source: Path, destination: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", "venv", ".git", ".test_tmp")
    shutil.copytree(source, destination, ignore=ignore)


def _file_manifest(root: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    if not root.exists():
        return manifest
    for path in root.rglob("*"):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest[relative] = digest
    return manifest


def _changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    keys = set(before) | set(after)
    return sorted(key for key in keys if before.get(key) != after.get(key))


def read_only_startup_track(skill_path: Path, python: str) -> dict[str, Any]:
    dependency = runtime_dependency_track(python)
    if not dependency["passed"]:
        return _track("read_only_startup", False, checks=[dependency], summary=dependency["summary"], stdout=dependency["stdout"])

    root = _data_root(skill_path)
    option_id = _current_option_id(skill_path)
    commands = [
        [python, _script(skill_path, "agent_bootstrap.py"), "--root", root, "--json"],
        [python, _script(skill_path, "skill_smoke_test.py"), "--root", root, "--json"],
        [python, _script(skill_path, "list_agent_commands.py"), "--json"],
        [python, _script(skill_path, "search_knowledge.py"), "--root", root, "--query", "t5", "--json", "--limit", "5"],
        [python, _script(skill_path, "suggest_next_actions.py"), "--root", root, "--json"],
    ]
    if option_id:
        commands.append([
            python,
            _script(skill_path, "option_workstream_context.py"),
            "--root",
            root,
            "--option",
            option_id,
            "--json",
        ])
    checks = [_run_command(command, cwd=skill_path) for command in commands]
    return _track(
        "read_only_startup",
        all(check["passed"] for check in checks),
        checks=checks,
        summary={"command_count": len(checks)},
    )


def portable_copy_track(skill_path: Path, python: str, destination: Path) -> dict[str, Any]:
    dependency = runtime_dependency_track(python)
    if not dependency["passed"]:
        return _track("portable_copy", False, checks=[dependency], summary=dependency["summary"], stdout=dependency["stdout"])

    copy_path = destination / "research-cockpit"
    _copy_skill_package(skill_path, copy_path)
    check = _run_command([python, str(copy_path / "scripts" / "skill_smoke_test.py"), "--json"], cwd=destination)
    payload = check.get("json") if isinstance(check.get("json"), dict) else {}
    summary = {
        "copy_path": str(copy_path),
        "derived_skill_root": payload.get("skill_root"),
        "derived_root": payload.get("root"),
    }
    return _track("portable_copy", check["passed"], checks=[check], summary=summary)


def isolated_mutation_track(skill_path: Path, python: str, destination: Path) -> dict[str, Any]:
    dependency = runtime_dependency_track(python)
    if not dependency["passed"]:
        return _track("isolated_mutation", False, checks=[dependency], summary=dependency["summary"], stdout=dependency["stdout"])

    source_before = _file_manifest(skill_path)
    copy_path = destination / "research-cockpit"
    _copy_skill_package(skill_path, copy_path)
    copy_before = _file_manifest(copy_path)
    root = _data_root(copy_path)
    commands = [
        [
            python,
            _script(copy_path, "record_finding.py"),
            "--root",
            root,
            "--experiment",
            "exp_042_flan_t5_clap",
            "--statement",
            "Release check synthetic finding.",
            "--confidence",
            "medium",
            "--outcome",
            "mixed",
            "--summary",
            "Release check synthetic finding recorded in isolated copy.",
        ],
        [python, _script(copy_path, "update_decision_evidence.py"), "--root", root, "--id", "decision_flan_t5_clap"],
        [python, _script(copy_path, "validate_cockpit.py"), "--root", root],
        [python, _script(copy_path, "build_dashboard.py"), "--root", root],
    ]
    checks = [_run_command(command, cwd=copy_path) for command in commands]
    source_after = _file_manifest(skill_path)
    copy_after = _file_manifest(copy_path)
    copy_changed = _changed_files(copy_before, copy_after)
    source_changed = source_before != source_after
    return _track(
        "isolated_mutation",
        all(check["passed"] for check in checks) and not source_changed and bool(copy_changed),
        checks=checks,
        summary={
            "copy_path": str(copy_path),
            "source_changed": source_changed,
            "copy_changed_files": copy_changed[:40],
            "copy_changed_count": len(copy_changed),
        },
    )


def decision_gate_track(skill_path: Path, python: str) -> dict[str, Any]:
    dependency = runtime_dependency_track(python)
    if not dependency["passed"]:
        return _track("decision_gate", False, checks=[dependency], summary=dependency["summary"], stdout=dependency["stdout"])

    command = [
        python,
        _script(skill_path, "check_decision_acceptance.py"),
        "--root",
        _data_root(skill_path),
        "--id",
        "decision_flan_t5_clap",
        "--json",
    ]
    check = _run_command(command, cwd=skill_path, allowed_returncodes={0, 1})
    payload = check.get("json") if isinstance(check.get("json"), dict) else {}
    valid_payload = isinstance(payload.get("ready"), bool)
    expected_returncode = 0 if payload.get("ready") else 1
    passed = check["passed"] and valid_payload and check["returncode"] == expected_returncode
    return _track(
        "decision_gate",
        passed,
        checks=[check],
        summary={
            "ready": payload.get("ready"),
            "blocking_failures": payload.get("blocking_failures", []),
            "warnings": payload.get("warnings", []),
        },
    )


def _skipped_track(name: str, reason: str) -> dict[str, Any]:
    return _track(name, True, skipped=True, summary={"reason": reason})


def release_check_payload(
    skill_path: Path = DEFAULT_SKILL_PATH,
    *,
    python: str = sys.executable,
    temp_parent: Path = DEFAULT_TEMP_PARENT,
    keep_temp: bool = False,
    skip_mutating: bool = False,
) -> dict[str, Any]:
    skill_path = skill_path.resolve()
    temp_run = temp_parent / f"release_check_{uuid.uuid4().hex}"
    temp_run.mkdir(parents=True, exist_ok=False)
    tracks: list[dict[str, Any]] = []
    try:
        shape = package_shape_track(skill_path)
        tracks.append(shape)
        tracks.append(public_scan_track(skill_path))
        if not shape["passed"]:
            reason = "package_shape failed"
            tracks.append(_skipped_track("read_only_startup", reason))
            tracks.append(_skipped_track("portable_copy", reason))
            tracks.append(_skipped_track("isolated_mutation", reason))
            tracks.append(_skipped_track("decision_gate", reason))
            return {
                "ok": False,
                "skill_path": str(skill_path),
                "python": python,
                "temp_root": str(temp_run),
                "keep_temp": keep_temp,
                "tracks": tracks,
            }
        tracks.append(read_only_startup_track(skill_path, python))
        tracks.append(portable_copy_track(skill_path, python, temp_run / "portable"))
        if skip_mutating:
            tracks.append(_skipped_track("isolated_mutation", "--skip-mutating was provided"))
        else:
            tracks.append(isolated_mutation_track(skill_path, python, temp_run / "isolated_mutation"))
        tracks.append(decision_gate_track(skill_path, python))
        return {
            "ok": all(track["passed"] for track in tracks),
            "skill_path": str(skill_path),
            "python": python,
            "temp_root": str(temp_run),
            "keep_temp": keep_temp,
            "tracks": tracks,
        }
    finally:
        if not keep_temp:
            shutil.rmtree(temp_run, ignore_errors=True)


def _print_text(payload: dict[str, Any]) -> None:
    state = "OK" if payload["ok"] else "FAILED"
    print(f"Skill release check: {state}")
    print(f"Skill path: {payload['skill_path']}")
    print(f"Python: {payload['python']}")
    for track in payload["tracks"]:
        if track["skipped"]:
            print(f"- {track['name']}: SKIPPED ({track['summary'].get('reason')})")
            continue
        marker = "OK" if track["passed"] else "FAILED"
        print(f"- {track['name']}: {marker}")
        if track["summary"]:
            print(f"  summary: {track['summary']}")
        if not track["passed"]:
            detail = track.get("stdout") or track.get("stderr")
            if detail:
                print(f"  detail: {detail}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-path", type=Path, default=DEFAULT_SKILL_PATH)
    parser.add_argument("--python", default=os.environ.get("RESEARCH_COCKPIT_PYTHON", "").strip() or sys.executable)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--skip-mutating", action="store_true")
    args = parser.parse_args()

    payload = release_check_payload(
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
