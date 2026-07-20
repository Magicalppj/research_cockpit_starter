from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILL_PATH = REPO_ROOT
DEFAULT_TEMP_PARENT = REPO_ROOT / ".test_tmp"
TEXT_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".txt"}
REQUIRED_MODULES = {
    "networkx": "networkx",
    "yaml": "PyYAML",
}
REQUIRED_PACKAGE_PATHS = (
    "SKILL.md",
    "templates/launcher/README.md",
    "templates/launcher/manual_run_checklist.md",
    "AGENTS.md",
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "src/research_cockpit/model.py",
    "src/research_cockpit/execution_context.py",
    "src/research_cockpit/paths.py",
    "src/research_cockpit/command_registry.py",
    "src/research_cockpit/role_contracts.py",
    "src/research_cockpit/work_packets.py",
    "src/research_cockpit/ui/app.py",
    "src/research_cockpit/commands/agent_bootstrap.py",
    "src/research_cockpit/commands/skill_smoke_test.py",
    "src/research_cockpit/commands/list_agent_commands.py",
    "src/research_cockpit/commands/work_open.py",
    "src/research_cockpit/commands/claim_option.py",
    "src/research_cockpit/commands/option_workstream_context.py",
    "src/research_cockpit/commands/report_option_workstream.py",
    "src/research_cockpit/commands/update_decision_checklist.py",
    "examples/demo_research_cockpit/current_state.yaml",
    "templates/minimal_research_cockpit/current_state.yaml",
    "capabilities/worker-loop.md",
    "capabilities/reviewer-loop.md",
    "capabilities/coordinator-loop.md",
    "capabilities/maintainer-loop.md",
    "capabilities/graph-state.md",
    "capabilities/focus-context.md",
    "capabilities/maintenance.md",
    "capabilities/node-management.md",
    "capabilities/experiment-cycle.md",
    "capabilities/experiment-tracking.md",
    "capabilities/decision-adr.md",
    "capabilities/ui-dashboard.md",
    "capabilities/integrations.md",
    "capabilities/troubleshooting.md",
    "agents/openai.yaml",
)
SCAN_EXCLUDED_PARTS = {"dev", "tests", ".test_tmp", "node_modules", ".streamlit_tmp", ".git", ".venv", "venv"}
FORBIDDEN_LAYOUT_PARTS = {"skills", "scripts"}
FORBIDDEN_STRINGS = (
    "D:" + "\\Tools",
    "C:" + "\\Users" + "\\" + "22" + "339",
    "22" + "339",
    "miniconda3" + "\\envs" + "\\aigc",
    "Audio Edit",
    "FLAN",
    "CLAP",
    "Gemma",
    "UMT5",
    "dataset_v2",
    "stage_text_encoder",
    "problem_event_text_weak",
    "option_flan",
    "decision_flan",
    "exp_041",
    "exp_042",
    ".agent" + "\\skills" + "\\research-cockpit" + "\\scripts",
    ".agent/skills/research-cockpit/scripts",
    "python scripts" + "\\",
    "python scripts/",
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
        "stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
    }


def _short_text(value: str | None, limit: int = 1200) -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "...<truncated>"


def _cli(python: str, command: str, *args: str) -> list[str]:
    return [python, "-m", "research_cockpit.cli", command, *args]


def _package_env(skill_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    src_path = str(skill_path / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_path if not existing else src_path + os.pathsep + existing
    return env


def _data_root(skill_path: Path) -> str:
    return str(skill_path / "examples" / "demo_research_cockpit")


def _current_option_id(skill_path: Path) -> str | None:
    import yaml

    path = skill_path / "examples" / "demo_research_cockpit" / "current_state.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    option_id = data.get("current_option")
    return str(option_id) if option_id else None


def _run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    allowed_returncodes: set[int] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    allowed = allowed_returncodes or {0}
    started_at = time.perf_counter()
    try:
        command_env = (env or os.environ).copy()
        command_env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            args,
            cwd=cwd,
            env=command_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return {
            "command": args,
            "passed": False,
            "returncode": 1,
            "stdout": "",
            "stderr": str(exc),
            "stdout_bytes": 0,
            "stderr_bytes": len(str(exc).encode("utf-8")),
            "json": None,
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
        }

    stdout_raw = result.stdout or ""
    stderr_raw = result.stderr or ""
    stdout = _short_text(stdout_raw)
    stderr = _short_text(stderr_raw)
    return {
        "command": args,
        "passed": (
            result.returncode in allowed
            and "Traceback" not in stdout_raw
            and "Traceback" not in stderr_raw
        ),
        "returncode": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_bytes": len(stdout_raw.encode("utf-8")),
        "stderr_bytes": len(stderr_raw.encode("utf-8")),
        "json": _try_json(stdout_raw),
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
    }

def _try_json(text: str | None) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _missing_modules_for_python(python: str, required: dict[str, str] = REQUIRED_MODULES) -> list[str]:
    missing: list[str] = []
    for module in required:
        try:
            result = subprocess.run(
                [python, "-c", f"import {module}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
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
        if path.name in FORBIDDEN_LAYOUT_PARTS or path.name in {"docs_development_status.md", "research_cockpit_v2_specs"}
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
    for path in _walk_non_excluded_files(skill_path):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        if any(part in SCAN_EXCLUDED_PARTS or part.endswith(".egg-info") for part in path.relative_to(skill_path).parts):
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


def instruction_surface_track(skill_path: Path) -> dict[str, Any]:
    relative_paths = {
        "root": "SKILL.md",
        "worker": "capabilities/worker-loop.md",
        "reviewer": "capabilities/reviewer-loop.md",
        "coordinator": "capabilities/coordinator-loop.md",
        "maintainer": "capabilities/maintainer-loop.md",
    }
    missing_playbooks = [
        relative_path
        for relative_path in relative_paths.values()
        if not (skill_path / relative_path).is_file()
    ]
    texts = {
        role: (
            (skill_path / relative_path).read_text(encoding="utf-8", errors="replace")
            if (skill_path / relative_path).is_file()
            else ""
        )
        for role, relative_path in relative_paths.items()
    }

    required_routes = (
        "capabilities/worker-loop.md",
        "capabilities/reviewer-loop.md",
        "capabilities/coordinator-loop.md",
        "capabilities/maintainer-loop.md",
    )
    required_terms = {
        "root": (
            "research-cockpit work open",
            "commands --role <role> --name <command>",
            "milestone_handoff",
        ),
        "worker": (
            "--since <revision>",
            "additional_verification_required",
            "artifact_record.existing_record_id",
        ),
        "reviewer": ("producer result revision", "read-only"),
        "coordinator": ("milestone_handoff", "canonical-root"),
    }
    missing_routes = [item for item in required_routes if item not in texts["root"]]
    missing_terms = [
        f"{role}:{term}"
        for role, terms in required_terms.items()
        for term in terms
        if term not in texts[role]
    ]

    forbidden_by_role = {
        "root": ("commands --json --compact --summary-only",),
        "worker": (
            "research-cockpit bootstrap",
            "research-cockpit build",
            "research-cockpit maintenance",
            "commands --json --compact --summary-only",
        ),
        "reviewer": (
            "research-cockpit create-run",
            "research-cockpit maintenance",
            "commands --json --compact --summary-only",
        ),
        "coordinator": ("commands --json --compact --summary-only",),
    }
    forbidden_role_routes = [
        f"{role}:{token}"
        for role, tokens in forbidden_by_role.items()
        for token in tokens
        if token in texts[role]
    ]
    incomplete_lines = [
        {"file": relative_paths[role], "line": index, "text": line}
        for role, text in texts.items()
        for index, line in enumerate(text.splitlines(), start=1)
        if line.strip() == "-"
    ]

    role_bytes = {role: len(text.encode("utf-8")) for role, text in texts.items()}
    role_estimated_tokens = {
        role: (byte_count + 3) // 4
        for role, byte_count in role_bytes.items()
    }
    root_role_bytes = {
        role: role_bytes["root"] + role_bytes[role]
        for role in ("worker", "reviewer", "coordinator", "maintainer")
    }
    root_worker_bytes = root_role_bytes["worker"]
    root_lines = len(texts["root"].splitlines())
    root_command_mentions = texts["root"].count("research-cockpit ")
    passed = (
        not missing_playbooks
        and role_bytes["root"] < 6 * 1024
        and role_bytes["worker"] < 6 * 1024
        and role_bytes["reviewer"] < 5 * 1024
        and role_bytes["coordinator"] < 5 * 1024
        and role_bytes["maintainer"] < 5 * 1024
        and all(byte_count < 12 * 1024 for byte_count in root_role_bytes.values())
        and root_lines <= 90
        and root_command_mentions <= 5
        and not missing_routes
        and not missing_terms
        and not forbidden_role_routes
        and not incomplete_lines
    )
    return _track(
        "instruction_surface",
        passed,
        summary={
            "root_bytes": role_bytes["root"],
            "root_byte_budget": 6 * 1024,
            "root_worker_bytes": root_worker_bytes,
            "root_worker_estimated_tokens": (root_worker_bytes + 3) // 4,
            "combined_byte_budget": 12 * 1024,
            "combined_estimated_token_budget": 3 * 1024,
            "root_role_bytes": root_role_bytes,
            "role_bytes": role_bytes,
            "role_estimated_tokens": role_estimated_tokens,
            "missing_playbooks": missing_playbooks,
            "forbidden_role_routes": forbidden_role_routes,
            "line_count": root_lines,
            "line_budget": 90,
            "byte_count": role_bytes["root"],
            "byte_budget": 6 * 1024,
            "command_mentions": root_command_mentions,
            "command_mention_budget": 5,
            "missing_routes": missing_routes,
            "missing_terms": missing_terms,
            "incomplete_lines": incomplete_lines,
        },
    )


def _copy_skill_package(source: Path, destination: Path) -> None:
    ignore = shutil.ignore_patterns(
        "__pycache__",
        "*.pyc",
        ".venv",
        "venv",
        ".git",
        ".test_tmp",
        ".streamlit_tmp",
        "dev",
        "tests",
        "node_modules",
        "*.egg-info",
    )
    shutil.copytree(source, destination, ignore=ignore)


def _walk_non_excluded_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current, dirs, names in os.walk(root):
        dirs[:] = [
            name
            for name in dirs
            if name not in SCAN_EXCLUDED_PARTS
            and name != "__pycache__"
            and not name.endswith(".egg-info")
        ]
        current_path = Path(current)
        files.extend(current_path / name for name in names)
    return files


def _file_manifest(root: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    if not root.exists():
        return manifest
    for path in _walk_non_excluded_files(root):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        if any(part in SCAN_EXCLUDED_PARTS or part.endswith(".egg-info") for part in path.relative_to(root).parts):
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
    node_id = _current_option_id(skill_path) or "option_demo_prompt_refinement"
    commands = [
        _cli(python, "context", "--root", root, "--id", node_id, "--view", "execution", "--compact", "--json"),
    ]
    env = _package_env(skill_path)
    checks = [_run_command(command, cwd=skill_path, env=env) for command in commands]
    return _track(
        "read_only_startup",
        all(check["passed"] for check in checks),
        checks=checks,
        summary={"command_count": len(checks)},
    )


def workflow_contract_track(skill_path: Path, python: str) -> dict[str, Any]:
    dependency = runtime_dependency_track(python)
    if not dependency["passed"]:
        return _track(
            "workflow_contract",
            False,
            checks=[dependency],
            summary=dependency["summary"],
            stdout=dependency["stdout"],
        )

    root = _data_root(skill_path)
    node_id = _current_option_id(skill_path) or "option_demo_prompt_refinement"
    env = _package_env(skill_path)
    checks = [
        _run_command(
            _cli(python, "commands", "--json", "--compact", "--name", "ingest-artifact"),
            cwd=skill_path,
            env=env,
        ),
        _run_command(
            _cli(python, "commands", "--json", "--compact", "--name", "promote-artifact-record"),
            cwd=skill_path,
            env=env,
        ),
        _run_command(
            _cli(
                python,
                "context",
                "--root",
                root,
                "--id",
                node_id,
                "--view",
                "execution",
                "--compact",
                "--json",
            ),
            cwd=skill_path,
            env=env,
        ),
        _run_command(_cli(python, "complete-run", "--print-schema"), cwd=skill_path, env=env),
        _run_command(_cli(python, "ingest-artifact", "--help"), cwd=skill_path, env=env),
        _run_command(_cli(python, "promote-artifact-record", "--help"), cwd=skill_path, env=env),
        _run_command(
            _cli(
                python,
                "commands",
                "--role",
                "worker",
                "--name",
                "work open",
                "--json",
                "--compact",
            ),
            cwd=skill_path,
            env=env,
        ),
        _run_command(
            _cli(python, "commands", "--name", "commands", "--json", "--compact"),
            cwd=skill_path,
            env=env,
        ),
        _run_command(_cli(python, "work", "open", "--help"), cwd=skill_path, env=env),
        _run_command(_cli(python, "commands", "--help"), cwd=skill_path, env=env),
    ]

    ingest_payload = checks[0].get("json") if isinstance(checks[0].get("json"), dict) else {}
    promote_payload = checks[1].get("json") if isinstance(checks[1].get("json"), dict) else {}
    context_payload = checks[2].get("json") if isinstance(checks[2].get("json"), dict) else {}
    ingest_rows = ingest_payload.get("commands", []) if isinstance(ingest_payload, dict) else []
    promote_rows = promote_payload.get("commands", []) if isinstance(promote_payload, dict) else []
    ingest_row = ingest_rows[0] if ingest_rows and isinstance(ingest_rows[0], dict) else {}
    promote_row = promote_rows[0] if promote_rows and isinstance(promote_rows[0], dict) else {}
    work_payload = checks[6].get("json") if isinstance(checks[6].get("json"), dict) else {}
    commands_payload = checks[7].get("json") if isinstance(checks[7].get("json"), dict) else {}
    work_rows = work_payload.get("commands", []) if isinstance(work_payload, dict) else []
    commands_rows = commands_payload.get("commands", []) if isinstance(commands_payload, dict) else []
    work_row = work_rows[0] if work_rows and isinstance(work_rows[0], dict) else {}
    commands_row = commands_rows[0] if commands_rows and isinstance(commands_rows[0], dict) else {}

    ingest_flags = set(ingest_row.get("supported_flags", []) or [])
    promote_flags = set(promote_row.get("supported_flags", []) or [])
    required_ingest_flags = {"--record-only", "--promote", "--promotion-reason"}
    required_promote_flags = {"--promotion-reason"}
    missing_flags = sorted(
        {f"ingest-artifact:{flag}" for flag in required_ingest_flags - ingest_flags}
        | {f"promote-artifact-record:{flag}" for flag in required_promote_flags - promote_flags}
    )
    manifest_help_missing: list[str] = []
    manifest_help_pairs = (
        ("ingest-artifact", ingest_row, checks[4].get("stdout", "")),
        ("promote-artifact-record", promote_row, checks[5].get("stdout", "")),
        ("work open", work_row, checks[8].get("stdout", "")),
        ("commands", commands_row, checks[9].get("stdout", "")),
    )
    for command_name, row, help_text in manifest_help_pairs:
        for flag in row.get("supported_flags", []) or []:
            if flag != "--progress" and flag not in help_text:
                manifest_help_missing.append(f"{command_name}:{flag}")
    manifest_rows_present = all(row for _, row, _ in manifest_help_pairs)

    public_paths = [
        skill_path / "AGENTS.md",
        skill_path / "SKILL.md",
        skill_path / "README.md",
        skill_path / "capabilities" / "experiment-cycle.md",
        skill_path / "capabilities" / "experiment-tracking.md",
    ]
    public_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in public_paths)
    structured_closeout_documented = (
        "work start --root" in public_text
        and "work close --root" in public_text
        and "--file closeout.yaml" in public_text
        and "work_close_v1" in public_text
        and "evidence_inputs" in public_text
        and "next_experiment" in public_text
    )
    promotion_examples_missing_reason: list[str] = []
    for path in public_paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            if line.lstrip().startswith("research-cockpit promote-artifact-record") and "--promotion-reason" not in line:
                promotion_examples_missing_reason.append(f"{path.name}:{line_number}")

    context_stdout_bytes = int(checks[2].get("stdout_bytes", 0))
    command_stdout_bytes = int(checks[0].get("stdout_bytes", 0))
    context_schema_version = context_payload.get("schema_version")
    artifact_default_mode = (
        "record"
        if "by default" in str(ingest_row.get("purpose") or "").lower()
        else None
    )
    ingest_verification_mode = ingest_row.get("verification_mode")
    ingest_worker_verify_commands = (
        ingest_row.get("batch_policy", {}).get("worker_verify_commands", [])
        if isinstance(ingest_row.get("batch_policy"), dict)
        else []
    )
    schema_text = checks[3].get("stdout", "")
    schema_ok = all(
        token in schema_text
        for token in ("run_closeout_v1", "existing_record_id", "experiment:", "next_experiment:")
    )
    budgets_ok = context_stdout_bytes <= 4 * 1024 and command_stdout_bytes <= 20 * 1024
    passed = (
        all(check["passed"] for check in checks)
        and not missing_flags
        and manifest_rows_present
        and not manifest_help_missing
        and not promotion_examples_missing_reason
        and structured_closeout_documented
        and context_schema_version == "execution_context_v1"
        and artifact_default_mode == "record"
        and ingest_verification_mode == "internal_non_dry_run"
        and ingest_worker_verify_commands == []
        and schema_ok
        and budgets_ok
    )
    return _track(
        "workflow_contract",
        passed,
        checks=checks,
        summary={
            "context_schema_version": context_schema_version,
            "context_stdout_bytes": context_stdout_bytes,
            "context_stdout_budget": 4 * 1024,
            "command_summary_stdout_bytes": command_stdout_bytes,
            "command_summary_stdout_budget": 20 * 1024,
            "artifact_default_mode": artifact_default_mode,
            "ingest_verification_mode": ingest_verification_mode,
            "ingest_worker_verify_commands": ingest_worker_verify_commands,
            "missing_flags": missing_flags,
            "manifest_rows_present": manifest_rows_present,
            "manifest_help_missing": manifest_help_missing,
            "structured_closeout_documented": structured_closeout_documented,
            "closeout_schema_ok": schema_ok,
            "promotion_examples_missing_reason": promotion_examples_missing_reason,
        },
    )


def portable_copy_track(skill_path: Path, python: str, destination: Path) -> dict[str, Any]:
    dependency = runtime_dependency_track(python)
    if not dependency["passed"]:
        return _track("portable_copy", False, checks=[dependency], summary=dependency["summary"], stdout=dependency["stdout"])

    copy_path = destination / "rc"
    _copy_skill_package(skill_path, copy_path)
    check = _run_command(_cli(python, "smoke", "--json"), cwd=copy_path, env=_package_env(copy_path))
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
    copy_path = destination / "rc"
    _copy_skill_package(skill_path, copy_path)
    copy_before = _file_manifest(copy_path)
    root = _data_root(copy_path)
    commands = [
        _cli(
            python,
            "record-finding",
            "--root",
            root,
            "--experiment",
            "experiment_demo_prompt_refinement",
            "--statement",
            "Release check synthetic finding.",
            "--confidence",
            "medium",
            "--outcome",
            "mixed",
            "--summary",
            "Release check synthetic finding recorded in isolated copy.",
        ),
        _cli(python, "update-decision-evidence", "--root", root, "--id", "decision_demo_prompt_refinement"),
        _cli(python, "validate", "--root", root),
        _cli(python, "build", "--root", root),
    ]
    env = _package_env(copy_path)
    checks = [_run_command(command, cwd=copy_path, env=env) for command in commands]
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

    command = _cli(
        python,
        "check-decision-acceptance",
        "--root",
        _data_root(skill_path),
        "--id",
        "decision_demo_prompt_refinement",
        "--json",
    )
    check = _run_command(command, cwd=skill_path, allowed_returncodes={0, 1}, env=_package_env(skill_path))
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
        tracks.append(instruction_surface_track(skill_path))
        if not shape["passed"]:
            reason = "package_shape failed"
            tracks.append(_skipped_track("read_only_startup", reason))
            tracks.append(_skipped_track("workflow_contract", reason))
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
        tracks.append(workflow_contract_track(skill_path, python))
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
