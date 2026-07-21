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
    "AGENTS.md",
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "docs/command-interface.md",
    "docs/migrations/0.3.0-cli-cutover.md",
    "templates/launcher/README.md",
    "templates/launcher/manual_run_checklist.md",
    "src/research_cockpit/model.py",
    "src/research_cockpit/execution_context.py",
    "src/research_cockpit/paths.py",
    "src/research_cockpit/command_registry.py",
    "src/research_cockpit/role_contracts.py",
    "src/research_cockpit/work_packets.py",
    "src/research_cockpit/assignment_records.py",
    "src/research_cockpit/coordinator_operations.py",
    "src/research_cockpit/coordinator_decisions.py",
    "src/research_cockpit/maintenance_actions.py",
    "src/research_cockpit/ui/app.py",
    "src/research_cockpit/commands/skill_smoke_test.py",
    "src/research_cockpit/commands/list_agent_commands.py",
    "src/research_cockpit/commands/work_open.py",
    "src/research_cockpit/commands/work_start.py",
    "src/research_cockpit/commands/work_record.py",
    "src/research_cockpit/commands/work_close.py",
    "src/research_cockpit/commands/review_open.py",
    "src/research_cockpit/commands/review_report.py",
    "src/research_cockpit/commands/coord_overview.py",
    "src/research_cockpit/commands/coord_assign.py",
    "src/research_cockpit/commands/coord_review.py",
    "src/research_cockpit/commands/coord_decide.py",
    "src/research_cockpit/commands/coord_handoff.py",
    "src/research_cockpit/commands/maintenance_role_audit.py",
    "src/research_cockpit/commands/maintenance_role_repair.py",
    "src/research_cockpit/commands/maintenance_role_migrate.py",
    "src/research_cockpit/commands/maintenance_role_compact.py",
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
            "0.3.0 canonical role surface",
        ),
        "worker": (
            "--since <revision>",
            "internally_verified",
            "evidence_inputs",
        ),
        "reviewer": (
            "producer result revision",
            "不重写 producer Evidence Bundle",
        ),
        "coordinator": (
            "coord handoff",
            "captured revision",
        ),
        "maintainer": (
            "execute: false",
            "bounded verification",
        ),
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
            "research-cockpit complete-run",
            "research-cockpit ingest-artifact",
            "research-cockpit set-cursor",
            "commands --json --compact --summary-only",
        ),
        "reviewer": (
            "research-cockpit create-run",
            "research-cockpit complete-run",
            "research-cockpit maintenance",
            "commands --json --compact --summary-only",
        ),
        "coordinator": (
            "research-cockpit start-agent-session",
            "research-cockpit set-focus",
            "research-cockpit promote-decision",
            "commands --json --compact --summary-only",
        ),
        "maintainer": (
            "research-cockpit compact-artifacts",
            "research-cockpit maintenance-audit",
        ),
    }
    forbidden_role_routes = [
        f"{role}:{token}"
        for role, tokens in forbidden_by_role.items()
        for token in tokens
        if token in texts[role]
    ]
    incomplete_lines = [
        {"file": relative_paths[role], "line": index, "text": line}
        for role, text_value in texts.items()
        for index, line in enumerate(text_value.splitlines(), start=1)
        if line.strip() == "-"
    ]

    role_bytes = {role: len(text_value.encode("utf-8")) for role, text_value in texts.items()}
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
    manifest_names = (
        ("worker", "work open"),
        ("worker", "work record"),
        ("worker", "work close"),
        ("coordinator", "coord assign"),
        ("coordinator", "coord decide"),
        ("maintainer", "maintenance compact"),
    )
    manifest_checks = [
        _run_command(
            _cli(
                python,
                "commands",
                "--role",
                role,
                "--name",
                name,
                "--json",
                "--compact",
            ),
            cwd=skill_path,
            env=env,
        )
        for role, name in manifest_names
    ]
    context_check = _run_command(
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
    )
    schema_commands = (
        ("work record", _cli(python, "work", "record", "--print-schema", "--compact")),
        ("coord assign", _cli(python, "coord", "assign", "--print-schema", "--compact")),
        ("coord decide", _cli(python, "coord", "decide", "--print-schema", "--compact")),
        (
            "maintenance compact",
            _cli(python, "maintenance", "compact", "--print-schema", "--compact"),
        ),
        ("coord handoff", _cli(python, "coord", "handoff", "--print-schema", "--compact")),
    )
    schema_checks = [
        _run_command(command, cwd=skill_path, env=env)
        for _name, command in schema_commands
    ]
    removed_check = _run_command(
        _cli(python, "ingest-artifact", "--help"),
        cwd=skill_path,
        env=env,
        allowed_returncodes={2},
    )
    checks = [*manifest_checks, context_check, *schema_checks, removed_check]

    rows: dict[str, dict[str, Any]] = {}
    for (_role, name), check in zip(manifest_names, manifest_checks):
        payload = check.get("json") if isinstance(check.get("json"), dict) else {}
        command_rows = payload.get("commands", []) if isinstance(payload, dict) else []
        rows[name] = (
            command_rows[0]
            if len(command_rows) == 1 and isinstance(command_rows[0], dict)
            else {}
        )
    canonical_rows_present = all(
        rows.get(name, {}).get("name") == name
        and rows[name].get("surface") == "core"
        and str(rows[name].get("command") or "").startswith("research-cockpit ")
        for _role, name in manifest_names
    )
    manifest_help_missing = [
        name
        for _role, name in manifest_names
        if not rows.get(name, {}).get("command")
        or not isinstance(rows[name].get("supported_flags"), list)
        or not rows[name].get("input_schema_version")
        or not rows[name].get("output_schema_version")
    ]

    context_payload = (
        context_check.get("json")
        if isinstance(context_check.get("json"), dict)
        else {}
    )
    context_stdout_bytes = int(context_check.get("stdout_bytes", 0))
    command_stdout_bytes = max(
        (int(check.get("stdout_bytes", 0)) for check in manifest_checks),
        default=0,
    )
    schema_text = "\n".join(check.get("stdout", "") for check in schema_checks)
    schema_ok = all(
        token in schema_text
        for token in (
            "work_record_v1",
            "coord_assign_v1",
            "coord_decide_v1",
            "maintenance_action_v1",
            "coord_handoff_v1",
        )
    )
    removed_routes_rejected = (
        removed_check.get("returncode") == 2
        and "invalid choice" in removed_check.get("stderr", "")
    )

    public_paths = [
        skill_path / "AGENTS.md",
        skill_path / "SKILL.md",
        skill_path / "README.md",
        skill_path / "capabilities" / "experiment-cycle.md",
        skill_path / "capabilities" / "experiment-tracking.md",
        skill_path / "docs" / "command-interface.md",
    ]
    public_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in public_paths
        if path.is_file()
    )
    structured_closeout_documented = all(
        token in public_text
        for token in (
            "work start",
            "work record",
            "work close",
            "work_close_v1",
            "evidence_inputs",
            "next_experiment",
            "additional_verification_required",
        )
    )
    budgets_ok = context_stdout_bytes <= 4 * 1024 and command_stdout_bytes <= 20 * 1024
    passed = (
        all(check["passed"] for check in checks)
        and canonical_rows_present
        and not manifest_help_missing
        and structured_closeout_documented
        and context_payload.get("schema_version") == "execution_context_v1"
        and schema_ok
        and removed_routes_rejected
        and budgets_ok
    )
    return _track(
        "workflow_contract",
        passed,
        checks=checks,
        summary={
            "context_schema_version": context_payload.get("schema_version"),
            "context_stdout_bytes": context_stdout_bytes,
            "context_stdout_budget": 4 * 1024,
            "command_summary_stdout_bytes": command_stdout_bytes,
            "command_summary_stdout_budget": 20 * 1024,
            "canonical_rows_present": canonical_rows_present,
            "manifest_rows_present": canonical_rows_present,
            "manifest_help_missing": manifest_help_missing,
            "structured_closeout_documented": structured_closeout_documented,
            "canonical_schema_contracts": schema_ok,
            "removed_routes_rejected": removed_routes_rejected,
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
        return _track(
            "isolated_mutation",
            False,
            checks=[dependency],
            summary=dependency["summary"],
            stdout=dependency["stdout"],
        )

    source_before = _file_manifest(skill_path)
    copy_path = destination / "rc"
    _copy_skill_package(skill_path, copy_path)
    copy_before = _file_manifest(copy_path)
    root = Path(_data_root(copy_path))
    plan_path = destination / "coord_assign.json"
    summary_text = "Canonical release check mutation."
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": "coord_assign_v1",
                "operation_id": f"op_release_check_{uuid.uuid4().hex}",
                "action": "graph_plan",
                "graph_plan": {
                    "nodes": [],
                    "updates": [
                        {
                            "id": "experiment_demo_prompt_refinement",
                            "fields": {"summary": summary_text},
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    check = _run_command(
        _cli(
            python,
            "coord",
            "assign",
            "--root",
            str(root),
            "--file",
            str(plan_path),
            "--json",
            "--compact",
        ),
        cwd=copy_path,
        env=_package_env(copy_path),
    )
    import yaml

    node_path = root / "graph" / "nodes" / "experiment_demo_prompt_refinement.yaml"
    node = yaml.safe_load(node_path.read_text(encoding="utf-8")) or {}
    source_changed = source_before != _file_manifest(skill_path)
    copy_changed = _changed_files(copy_before, _file_manifest(copy_path))
    payload = check.get("json") if isinstance(check.get("json"), dict) else {}
    internally_verified = (
        payload.get("verification", {}).get("status") == "internally_verified"
        and payload.get("verification", {}).get("additional_verification_required") is False
    )
    return _track(
        "isolated_mutation",
        (
            check["passed"]
            and not source_changed
            and bool(copy_changed)
            and node.get("summary") == summary_text
            and internally_verified
        ),
        checks=[check],
        summary={
            "copy_path": str(copy_path),
            "source_changed": source_changed,
            "copy_changed_files": copy_changed[:40],
            "copy_changed_count": len(copy_changed),
            "canonical_route": "coord assign",
            "internally_verified": internally_verified,
        },
    )


def decision_gate_track(skill_path: Path, python: str) -> dict[str, Any]:
    dependency = runtime_dependency_track(python)
    if not dependency["passed"]:
        return _track(
            "decision_gate",
            False,
            checks=[dependency],
            summary=dependency["summary"],
            stdout=dependency["stdout"],
        )

    expected = (
        "promote",
        "refresh_evidence",
        "update_checklist",
        "accept",
        "set_baseline",
    )
    checks: list[dict[str, Any]] = []
    returned_actions: list[str] = []
    schema_versions: set[str] = set()
    for action in expected:
        check = _run_command(
            _cli(
                python,
                "coord",
                "decide",
                "--print-schema",
                "--action",
                action,
                "--compact",
            ),
            cwd=skill_path,
            env=_package_env(skill_path),
        )
        checks.append(check)
        payload = check.get("json") if isinstance(check.get("json"), dict) else {}
        if check["passed"] and payload.get("action") == action:
            returned_actions.append(action)
        if isinstance(payload.get("schema_version"), str):
            schema_versions.add(payload["schema_version"])
    passed = returned_actions == list(expected) and schema_versions == {"coord_decide_v1"}
    return _track(
        "decision_gate",
        passed,
        checks=checks,
        summary={
            "schema_version": "coord_decide_v1" if schema_versions == {"coord_decide_v1"} else None,
            "allowed_actions": returned_actions,
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
