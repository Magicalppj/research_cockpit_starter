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
    _copy_skill_package,
    _file_manifest,
    _run_command,
    _script,
    runtime_dependency_track,
)


DEMO_DECISION_ID = "decision_demo_prompt_refinement"
DEMO_OPTION_ID = "option_demo_prompt_refinement"
DEMO_RETRIEVAL_OPTION_ID = "option_demo_retrieval_branch"
SURFACE_DOCS = ("README.md", "SKILL.md")
CAPABILITY_FILES = (
    "decision-adr.md",
    "experiment-tracking.md",
    "focus-context.md",
    "graph-state.md",
    "integrations.md",
    "node-management.md",
    "troubleshooting.md",
    "ui-dashboard.md",
)


def _commands_run(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "command": check.get("command", []),
            "returncode": check.get("returncode"),
            "passed": check.get("passed", False),
        }
        for check in checks
    ]


def _case(
    name: str,
    passed: bool,
    *,
    checks: list[dict[str, Any]] | None = None,
    files_changed: list[str] | None = None,
    agent_observations: dict[str, Any] | None = None,
    readability_findings: list[str] | None = None,
    unexpected_writes: list[str] | None = None,
) -> dict[str, Any]:
    checks = checks or []
    return {
        "case": name,
        "passed": passed,
        "commands_run": _commands_run(checks),
        "files_changed": files_changed or [],
        "agent_observations": agent_observations or {},
        "readability_findings": readability_findings or [],
        "unexpected_writes": unexpected_writes or [],
        "checks": checks,
    }


def _copy_plugin_to_repo(skill_path: Path, research_repo: Path) -> Path:
    plugin_path = research_repo / ".agent" / "skills" / "research-cockpit"
    plugin_path.parent.mkdir(parents=True, exist_ok=True)
    _copy_skill_package(skill_path, plugin_path)
    return plugin_path


def _copy_demo_state(plugin_path: Path, research_repo: Path) -> Path:
    root = research_repo / "research_cockpit"
    shutil.copytree(plugin_path / "examples" / "demo_research_cockpit", root)
    return root


def _new_research_repo(skill_path: Path, parent: Path, name: str) -> tuple[Path, Path]:
    research_repo = parent / name
    research_repo.mkdir(parents=True, exist_ok=False)
    plugin_path = _copy_plugin_to_repo(skill_path, research_repo)
    return research_repo, plugin_path


def _venv_python(research_repo: Path) -> Path:
    if os.name == "nt":
        return research_repo / ".venv" / "Scripts" / "python.exe"
    return research_repo / ".venv" / "bin" / "python"


def _unexpected_writes(changed: list[str]) -> list[str]:
    return [path for path in changed if not path.startswith("research_cockpit/")]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_yaml(path: Path) -> dict[str, Any]:
    import yaml

    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _surface_doc_paths(skill_path: Path) -> list[Path]:
    paths = [skill_path / relative for relative in SURFACE_DOCS]
    paths.extend((skill_path / "capabilities" / name) for name in CAPABILITY_FILES)
    return [path for path in paths if path.exists()]


def _readability_findings(skill_path: Path) -> list[str]:
    findings: list[str] = []
    for path in _surface_doc_paths(skill_path):
        text = path.read_text(encoding="utf-8", errors="ignore")
        normalized = (
            text.replace(".agent/skills/research-cockpit", "")
            .replace(".agent\\skills\\research-cockpit", "")
            .replace(".codex/skills/research-cockpit", "")
            .replace(".codex\\skills\\research-cockpit", "")
        )
        if "skills/research-cockpit" in normalized or "skills\\research-cockpit" in normalized:
            findings.append(f"{path.relative_to(skill_path).as_posix()} contains a bare old skill path")

    skill_text = (skill_path / "SKILL.md").read_text(encoding="utf-8", errors="ignore")
    for capability in CAPABILITY_FILES:
        if f"capabilities/{capability}" not in skill_text:
            findings.append(f"SKILL.md does not route to capabilities/{capability}")
        if not (skill_path / "capabilities" / capability).exists():
            findings.append(f"capabilities/{capability} is missing")

    decision_text = (skill_path / "capabilities" / "decision-adr.md").read_text(encoding="utf-8", errors="ignore")
    if "YAML" in decision_text and ("validate_cockpit.py" not in decision_text or "build_dashboard.py" not in decision_text):
        findings.append("decision-adr.md mentions YAML repair without validate/build follow-up")
    if any(flag in decision_text for flag in ("--alternatives-considered", "--consequences", "--next-required-actions")):
        findings.append("decision-adr.md uses outdated update_decision_checklist flags")
    if "promote_decision.py --root research_cockpit --option" in decision_text:
        findings.append("decision-adr.md promote_decision example omits required --id/--title/--summary")

    focus_text = (skill_path / "capabilities" / "focus-context.md").read_text(encoding="utf-8", errors="ignore")
    if "set_focus.py" in focus_text and "--node " in focus_text:
        findings.append("focus-context.md uses outdated set_focus --node flag")

    node_text = (skill_path / "capabilities" / "node-management.md").read_text(encoding="utf-8", errors="ignore")
    if "--suggestion " in node_text:
        findings.append("node-management.md uses outdated suggestion id flag")
    return findings


def _manifest_findings(manifest: dict[str, Any], plugin_path: Path) -> list[str]:
    findings: list[str] = []
    commands = manifest.get("commands", [])
    if not isinstance(commands, list) or not commands:
        return ["list_agent_commands.py did not return a non-empty commands list"]
    for command in commands:
        if not isinstance(command, dict):
            findings.append("manifest contains a non-object command")
            continue
        name = str(command.get("name") or "")
        capability = str(command.get("capability_file") or "")
        research_command = str(command.get("command") or "")
        if not capability or not (plugin_path / capability).exists():
            findings.append(f"{name} has missing capability_file")
        if "scripts\\" in research_command and ".agent\\skills\\research-cockpit" not in research_command:
            findings.append(f"{name} command is not runnable from research repo root")
    return findings


def agent_a_cold_start_install(skill_path: Path, python: str, parent: Path) -> dict[str, Any]:
    research_repo, plugin_path = _new_research_repo(skill_path, parent, "a")
    venv_python = _venv_python(research_repo)
    checks = [
        _run_command([python, "-m", "venv", "--system-site-packages", str(research_repo / ".venv")], cwd=research_repo),
    ]
    command_python = str(venv_python)
    command_env: dict[str, str] | None = None
    install_mode = "venv_editable"
    if checks[-1]["passed"]:
        checks.append(_run_command([str(venv_python), "-m", "pip", "install", "--no-deps", "-e", str(plugin_path)], cwd=research_repo))
        install_ok = checks[-1]["passed"]
    else:
        metadata_check = (
            "import pathlib; "
            f"text=pathlib.Path({str(plugin_path / 'pyproject.toml')!r}).read_text(encoding='utf-8'); "
            "assert 'research-cockpit = \"research_cockpit.cli:main\"' in text"
        )
        checks.append(_run_command([python, "-c", metadata_check], cwd=research_repo))
        command_python = python
        command_env = os.environ.copy()
        command_env["PYTHONPATH"] = str(plugin_path / "src")
        install_mode = "metadata_check_with_pythonpath"
        install_ok = checks[-1]["passed"]

    plugin_after_install = _file_manifest(plugin_path)
    repo_before = _file_manifest(research_repo)
    if install_ok:
        checks.extend([
            _run_command([command_python, "-m", "research_cockpit.cli", "init", "--root", "research_cockpit"], cwd=research_repo, env=command_env),
            _run_command([
                command_python,
                _script(plugin_path, "agent_bootstrap.py"),
                "--root",
                "research_cockpit",
                "--build",
                "--json",
            ], cwd=research_repo, env=command_env),
            _run_command([command_python, _script(plugin_path, "validate_cockpit.py"), "--root", "research_cockpit", "--json"], cwd=research_repo, env=command_env),
        ])

    repo_after = _file_manifest(research_repo)
    files_changed = _changed_files(repo_before, repo_after)
    unexpected = _unexpected_writes(files_changed)
    plugin_changed_after_install = _changed_files(plugin_after_install, _file_manifest(plugin_path))
    bootstrap = checks[-2].get("json") if len(checks) >= 4 and isinstance(checks[-2].get("json"), dict) else {}
    context_paths = bootstrap.get("context_paths", {}) if isinstance(bootstrap, dict) else {}
    observations = {
        "data_root": "research_cockpit",
        "plugin_root": ".agent/skills/research-cockpit",
        "init_command": "research-cockpit init --root research_cockpit",
        "bootstrap_read_order": ["agent_bootstrap", "agent_context_pack", "focus_context_pack"],
        "context_paths_exist": all(item.get("exists") for item in context_paths.values()) if context_paths else False,
        "plugin_changed_after_install": plugin_changed_after_install,
        "install_mode": install_mode,
    }
    findings = _readability_findings(plugin_path)
    command_checks = checks[2:] if install_mode == "metadata_check_with_pythonpath" else checks
    passed = install_ok and all(check["passed"] for check in command_checks) and not unexpected and not plugin_changed_after_install and not findings
    return _case(
        "agent_a_cold_start_install",
        passed,
        checks=checks,
        files_changed=files_changed,
        agent_observations=observations,
        readability_findings=findings,
        unexpected_writes=unexpected,
    )


def agent_b_read_only_context(skill_path: Path, python: str, parent: Path) -> dict[str, Any]:
    research_repo, plugin_path = _new_research_repo(skill_path, parent, "b")
    root = _copy_demo_state(plugin_path, research_repo)
    repo_before = _file_manifest(research_repo)
    checks = [
        _run_command([python, _script(plugin_path, "list_agent_commands.py"), "--json"], cwd=research_repo),
        _run_command([python, _script(plugin_path, "agent_bootstrap.py"), "--root", str(root), "--json"], cwd=research_repo),
        _run_command([python, _script(plugin_path, "search_knowledge.py"), "--root", str(root), "--query", "demo", "--json"], cwd=research_repo),
        _run_command([python, _script(plugin_path, "suggest_next_actions.py"), "--root", str(root), "--json"], cwd=research_repo),
    ]
    files_changed = _changed_files(repo_before, _file_manifest(research_repo))
    agent_context = _read_json(root / "dashboards" / "agent_context_pack.json")
    focus_context = _read_json(root / "dashboards" / "focus_context_pack.json")
    suggestions = checks[-1].get("json") if isinstance(checks[-1].get("json"), list) else []
    manifest = checks[0].get("json") if isinstance(checks[0].get("json"), dict) else {}
    findings = [*_readability_findings(plugin_path), *_manifest_findings(manifest, plugin_path)]
    observations = {
        "focus_node": (focus_context.get("focus_node") or {}).get("id"),
        "suggestion_count": len(suggestions),
        "saved_graph_views_present": "saved_graph_views" in agent_context and "saved_graph_views" in focus_context,
        "recent_interactions_present": "recent_interactions" in agent_context and "recent_interactions" in focus_context,
        "read_capabilities": ["capabilities/focus-context.md", "capabilities/graph-state.md"],
    }
    passed = (
        all(check["passed"] for check in checks)
        and not files_changed
        and not findings
        and observations["focus_node"]
        and observations["suggestion_count"] > 0
        and observations["saved_graph_views_present"]
        and observations["recent_interactions_present"]
    )
    return _case(
        "agent_b_read_only_context",
        passed,
        checks=checks,
        files_changed=files_changed,
        agent_observations=observations,
        readability_findings=findings,
        unexpected_writes=_unexpected_writes(files_changed),
    )


def agent_c_safe_option_workstream(skill_path: Path, python: str, parent: Path) -> dict[str, Any]:
    research_repo, plugin_path = _new_research_repo(skill_path, parent, "c")
    root = _copy_demo_state(plugin_path, research_repo)
    repo_before = _file_manifest(research_repo)
    checks: list[dict[str, Any]] = []

    before_claim_dry_run = _file_manifest(research_repo)
    checks.append(_run_command([
        python,
        _script(plugin_path, "claim_option.py"),
        "--root",
        str(root),
        "--option",
        DEMO_OPTION_ID,
        "--agent",
        "agent_usability_option",
        "--objective",
        "Exercise option workstream usability in an isolated research repo.",
        "--dry-run",
        "--json",
    ], cwd=research_repo))
    claim_dry_run_changed = _changed_files(before_claim_dry_run, _file_manifest(research_repo))

    checks.append(_run_command([
        python,
        _script(plugin_path, "claim_option.py"),
        "--root",
        str(root),
        "--option",
        DEMO_OPTION_ID,
        "--agent",
        "agent_usability_option",
        "--objective",
        "Exercise option workstream usability in an isolated research repo.",
    ], cwd=research_repo))
    checks.append(_run_command([python, _script(plugin_path, "option_workstream_context.py"), "--root", str(root), "--option", DEMO_OPTION_ID, "--json"], cwd=research_repo))

    before_report_dry_run = _file_manifest(research_repo)
    checks.append(_run_command([
        python,
        _script(plugin_path, "report_option_workstream.py"),
        "--root",
        str(root),
        "--option",
        DEMO_OPTION_ID,
        "--agent",
        "agent_usability_option",
        "--recommend",
        "continue",
        "--summary",
        "Usability check completed the isolated option workstream preview.",
        "--dry-run",
        "--json",
    ], cwd=research_repo))
    report_dry_run_changed = _changed_files(before_report_dry_run, _file_manifest(research_repo))

    checks.append(_run_command([
        python,
        _script(plugin_path, "report_option_workstream.py"),
        "--root",
        str(root),
        "--option",
        DEMO_OPTION_ID,
        "--agent",
        "agent_usability_option",
        "--recommend",
        "continue",
        "--summary",
        "Usability check completed the isolated option workstream.",
    ], cwd=research_repo))
    checks.append(_run_command([python, _script(plugin_path, "validate_cockpit.py"), "--root", str(root), "--json"], cwd=research_repo))

    files_changed = _changed_files(repo_before, _file_manifest(research_repo))
    log = _read_yaml(root / "graph" / "interaction_log.yaml")
    interaction_kinds = [event.get("kind") for event in log.get("events", []) if isinstance(event, dict)]
    observations = {
        "dry_run_preserved_files": not claim_dry_run_changed and not report_dry_run_changed,
        "interaction_kinds": interaction_kinds,
        "dashboard_updated": any(path.startswith("research_cockpit/dashboards/") for path in files_changed),
    }
    passed = (
        all(check["passed"] for check in checks)
        and not _unexpected_writes(files_changed)
        and observations["dry_run_preserved_files"]
        and {"claim_option", "report_option"}.issubset(set(interaction_kinds))
        and observations["dashboard_updated"]
    )
    return _case(
        "agent_c_safe_option_workstream",
        passed,
        checks=checks,
        files_changed=files_changed,
        agent_observations=observations,
        unexpected_writes=_unexpected_writes(files_changed),
    )


def agent_d_decision_suggestion_dry_run(skill_path: Path, python: str, parent: Path) -> dict[str, Any]:
    research_repo, plugin_path = _new_research_repo(skill_path, parent, "d")
    root = _copy_demo_state(plugin_path, research_repo)
    repo_before = _file_manifest(research_repo)
    checks = [
        _run_command([python, _script(plugin_path, "apply_suggestion.py"), "--root", str(root), "--id", "next_action_003", "--target", "current", "--dry-run", "--json"], cwd=research_repo),
        _run_command([
            python,
            _script(plugin_path, "promote_decision.py"),
            "--root",
            str(root),
            "--id",
            "decision_agent_usability_preview",
            "--option",
            DEMO_RETRIEVAL_OPTION_ID,
            "--title",
            "Preview retrieval branch decision",
            "--summary",
            "Dry-run decision promotion for usability testing.",
            "--dry-run",
            "--json",
        ], cwd=research_repo),
        _run_command([python, _script(plugin_path, "check_decision_acceptance.py"), "--root", str(root), "--id", DEMO_DECISION_ID, "--json"], cwd=research_repo, allowed_returncodes={0, 1}),
        _run_command([python, _script(plugin_path, "accept_decision.py"), "--root", str(root), "--id", DEMO_DECISION_ID, "--force-accept", "--dry-run", "--json"], cwd=research_repo),
    ]
    files_changed = _changed_files(repo_before, _file_manifest(research_repo))
    decision_doc = (plugin_path / "capabilities" / "decision-adr.md").read_text(encoding="utf-8", errors="ignore")
    required_commands = ("apply_suggestion.py", "promote_decision.py", "check_decision_acceptance.py", "accept_decision.py")
    missing_doc_commands = [name for name in required_commands if name not in decision_doc and name != "apply_suggestion.py"]
    observations = {
        "dry_run_preserved_files": not files_changed,
        "acceptance_check_returncode": checks[2]["returncode"],
        "decision_doc_mentions_flow": not missing_doc_commands,
    }
    findings = [f"capabilities/decision-adr.md does not mention {name}" for name in missing_doc_commands]
    passed = all(check["passed"] for check in checks) and not files_changed and not findings
    return _case(
        "agent_d_decision_suggestion_dry_run",
        passed,
        checks=checks,
        files_changed=files_changed,
        agent_observations=observations,
        readability_findings=findings,
        unexpected_writes=_unexpected_writes(files_changed),
    )


def agent_e_ui_collaboration_docs(skill_path: Path, python: str, parent: Path) -> dict[str, Any]:
    research_repo, plugin_path = _new_research_repo(skill_path, parent, "e")
    repo_before = _file_manifest(research_repo)
    readme = (plugin_path / "README.md").read_text(encoding="utf-8", errors="ignore")
    ui_doc = (plugin_path / "capabilities" / "ui-dashboard.md").read_text(encoding="utf-8", errors="ignore")
    build_index = plugin_path / "src" / "research_cockpit" / "ui" / "graph_component" / "frontend" / "build" / "index.html"
    findings: list[str] = []
    required_text = {
        "research-cockpit ui --root research_cockpit": readme + "\n" + ui_doc,
        "React Flow": readme + "\n" + ui_doc,
        "Dagre": readme + "\n" + ui_doc,
        "Refresh": readme,
        "刷新图谱": readme + "\n" + ui_doc,
        "PyVis": readme,
        "Temporary dragging is visual only": ui_doc,
        "without rebuilding the React bundle": ui_doc,
    }
    for expected, text in required_text.items():
        if expected not in text:
            findings.append(f"UI docs missing: {expected}")
    if not build_index.exists():
        findings.append("React Flow production build index.html is missing")

    files_changed = _changed_files(repo_before, _file_manifest(research_repo))
    observations = {
        "frontend_build_required_for_data_changes": False,
        "refresh_reloads_research_cockpit": "without rebuilding the React bundle" in ui_doc,
        "pyvis_fallback_documented": "PyVis" in readme,
        "saved_views_are_dynamic": "not frozen snapshots" in (plugin_path / "capabilities" / "graph-state.md").read_text(encoding="utf-8", errors="ignore"),
        "build_index_exists": build_index.exists(),
    }
    passed = not findings and not files_changed
    return _case(
        "agent_e_ui_collaboration_docs",
        passed,
        checks=[],
        files_changed=files_changed,
        agent_observations=observations,
        readability_findings=findings,
        unexpected_writes=_unexpected_writes(files_changed),
    )


def agent_usability_check_payload(
    skill_path: Path = DEFAULT_SKILL_PATH,
    *,
    python: str = sys.executable,
    temp_parent: Path = DEFAULT_TEMP_PARENT,
    keep_temp: bool = False,
) -> dict[str, Any]:
    skill_path = skill_path.resolve()
    temp_run = temp_parent / f"au_{uuid.uuid4().hex[:10]}"
    temp_run.mkdir(parents=True, exist_ok=False)
    source_before = _file_manifest(skill_path)
    cases: list[dict[str, Any]] = []
    try:
        dependency = runtime_dependency_track(python)
        if not dependency["passed"]:
            cases = [
                _case(
                    name,
                    False,
                    checks=[dependency],
                    readability_findings=[dependency.get("stdout", "runtime dependency check failed")],
                )
                for name in (
                    "agent_a_cold_start_install",
                    "agent_b_read_only_context",
                    "agent_c_safe_option_workstream",
                    "agent_d_decision_suggestion_dry_run",
                    "agent_e_ui_collaboration_docs",
                )
            ]
        else:
            cases = [
                agent_a_cold_start_install(skill_path, python, temp_run),
                agent_b_read_only_context(skill_path, python, temp_run),
                agent_c_safe_option_workstream(skill_path, python, temp_run),
                agent_d_decision_suggestion_dry_run(skill_path, python, temp_run),
                agent_e_ui_collaboration_docs(skill_path, python, temp_run),
            ]
        original_changed = source_before != _file_manifest(skill_path)
        return {
            "ok": all(case["passed"] for case in cases) and not original_changed,
            "skill_path": str(skill_path),
            "python": python,
            "temp_root": str(temp_run),
            "keep_temp": keep_temp,
            "original_package_changed": original_changed,
            "cases": cases,
        }
    finally:
        if not keep_temp:
            shutil.rmtree(temp_run, ignore_errors=True)


def _print_text(payload: dict[str, Any]) -> None:
    state = "OK" if payload["ok"] else "FAILED"
    print(f"Agent usability check: {state}")
    print(f"Plugin path: {payload['skill_path']}")
    print(f"Python: {payload['python']}")
    for case in payload["cases"]:
        marker = "OK" if case["passed"] else "FAILED"
        print(f"- {case['case']}: {marker}")
        if case["readability_findings"]:
            print(f"  findings: {case['readability_findings']}")
        if case["unexpected_writes"]:
            print(f"  unexpected writes: {case['unexpected_writes']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-path", type=Path, default=DEFAULT_SKILL_PATH)
    parser.add_argument("--python", default=os.environ.get("RESEARCH_COCKPIT_PYTHON", "").strip() or sys.executable)
    parser.add_argument("--temp-parent", type=Path, default=DEFAULT_TEMP_PARENT)
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = agent_usability_check_payload(
        args.skill_path,
        python=args.python,
        temp_parent=args.temp_parent,
        keep_temp=args.keep_temp,
    )
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_text(payload)
    raise SystemExit(0 if payload["ok"] else 1)


if __name__ == "__main__":
    main()
