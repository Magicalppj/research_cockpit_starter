from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
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
    _file_manifest,
    _package_env,
    _run_command,
    runtime_dependency_track,
)
from workflow_metrics import evaluate_workflow_contract, workflow_metrics


DEMO_DECISION_ID = "decision_demo_prompt_refinement"
DEMO_OPTION_ID = "option_demo_prompt_refinement"
DEMO_RETRIEVAL_OPTION_ID = "option_demo_retrieval_branch"
SURFACE_DOCS = (
    "README.md",
    "SKILL.md",
    "AGENTS.md",
    "templates/launcher/README.md",
    "templates/launcher/manual_run_checklist.md",
)
ROLE_PLAYBOOK_FILES = (
    "worker-loop.md",
    "reviewer-loop.md",
    "coordinator-loop.md",
    "maintainer-loop.md",
)
CAPABILITY_FILES = (
    "decision-adr.md",
    "experiment-cycle.md",
    "experiment-tracking.md",
    "focus-context.md",
    "graph-state.md",
    "integrations.md",
    "maintenance.md",
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
    documentation_bytes: int | None = None,
    readability_findings: list[str] | None = None,
    unexpected_writes: list[str] | None = None,
) -> dict[str, Any]:
    checks = checks or []
    return {
        "case": name,
        "passed": passed,
        "commands_run": _commands_run(checks),
        "files_changed": files_changed or [],
        "metrics": workflow_metrics(
            checks,
            files_changed=files_changed or [],
            documentation_bytes=documentation_bytes,
        ),
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


def _interaction_kinds(root: Path) -> list[str]:
    legacy = _read_yaml(root / "graph" / "interaction_log.yaml")
    legacy_events = legacy.get("events", []) if isinstance(legacy.get("events"), list) else []
    events = [event for event in legacy_events if isinstance(event, dict)]
    event_root = root / "graph" / "interaction_events"
    manifest = _read_json(event_root / "manifest.json")
    if not manifest:
        return [str(event.get("kind")) for event in events if event.get("kind")]
    if manifest.get("legacy_mode") == "migrated":
        events = []
    generation = str(manifest.get("generation") or "").strip()
    segment_root = event_root.joinpath(*Path(generation).parts) if generation else event_root
    for path in sorted(segment_root.glob("events-*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return [str(event.get("kind")) for event in events if event.get("kind")]


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    import yaml

    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _prepare_demo_decision_acceptance_state(root: Path) -> None:
    status_by_node = {
        "experiment_demo_prompt_refinement": "done",
        "problem_demo_subtask_scope": "parked",
        "option_demo_eval_cases": "parked",
        "experiment_demo_eval_cases": "cancelled",
    }
    for node_id, status in status_by_node.items():
        path = root / "graph" / "nodes" / f"{node_id}.yaml"
        data = _read_yaml(path)
        if data:
            data["status"] = status
            _write_yaml(path, data)


def _surface_doc_paths(skill_path: Path) -> list[Path]:
    paths = [skill_path / relative for relative in SURFACE_DOCS]
    paths.extend((skill_path / "capabilities" / name) for name in CAPABILITY_FILES)
    return [path for path in paths if path.exists()]


def _readability_findings(skill_path: Path) -> list[str]:
    findings: list[str] = []
    for path in _surface_doc_paths(skill_path):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if ".agent\\skills\\research-cockpit\\scripts" in text or ".agent/skills/research-cockpit/scripts" in text:
            findings.append(f"{path.relative_to(skill_path).as_posix()} contains old vendored script command")
        if "python scripts\\" in text or "python scripts/" in text:
            findings.append(f"{path.relative_to(skill_path).as_posix()} contains old plugin script command")

    skill_text = (skill_path / "SKILL.md").read_text(encoding="utf-8", errors="ignore")
    role_texts: list[str] = []
    for playbook in ROLE_PLAYBOOK_FILES:
        playbook_path = skill_path / "capabilities" / playbook
        if f"capabilities/{playbook}" not in skill_text:
            findings.append(f"SKILL.md does not route to capabilities/{playbook}")
        if not playbook_path.exists():
            findings.append(f"capabilities/{playbook} is missing")
            continue
        role_texts.append(playbook_path.read_text(encoding="utf-8", errors="ignore"))

    role_routes = "\n".join(role_texts)
    for capability in CAPABILITY_FILES:
        if not (skill_path / "capabilities" / capability).exists():
            findings.append(f"capabilities/{capability} is missing")
        elif capability not in role_routes:
            findings.append(f"role playbooks do not route to capabilities/{capability}")

    decision_text = (skill_path / "capabilities" / "decision-adr.md").read_text(encoding="utf-8", errors="ignore")
    if "YAML" in decision_text and (
        "research-cockpit validate" not in decision_text
        or "changed-scope" not in decision_text
        or "only when generated dashboards are needed" not in decision_text
    ):
        findings.append("decision-adr.md YAML repair lacks changed-scope validation or conditional build guidance")
    if any(flag in decision_text for flag in ("--alternatives-considered", "--consequences", "--next-required-actions")):
        findings.append("decision-adr.md uses outdated update_decision_checklist flags")
    if "promote-decision --root research_cockpit --option" in decision_text:
        findings.append("decision-adr.md promote-decision example omits required --id/--title/--summary")

    focus_text = (skill_path / "capabilities" / "focus-context.md").read_text(encoding="utf-8", errors="ignore")
    if re.search(r"set-focus[^\n]*--node\b", focus_text):
        findings.append("focus-context.md uses outdated set_focus --node flag")

    node_text = (skill_path / "capabilities" / "node-management.md").read_text(encoding="utf-8", errors="ignore")
    if "--suggestion " in node_text:
        findings.append("node-management.md uses outdated suggestion id flag")
    readme_text = (skill_path / "README.md").read_text(encoding="utf-8", errors="ignore")
    if "--record-only --dry-run" in readme_text:
        findings.append("README.md presents the compatibility record flag as the default ingest recipe")
    if not re.search(r"research-cockpit work start[^\n]*--assignment <assignment_id>", readme_text):
        findings.append("README.md worker start recipe omits --assignment")

    integrations_text = (skill_path / "capabilities" / "integrations.md").read_text(
        encoding="utf-8", errors="ignore"
    )
    if "artifact_record.existing_record_id" not in integrations_text:
        findings.append("integrations.md omits record-first structured closeout linkage")

    graph_text = (skill_path / "capabilities" / "graph-state.md").read_text(encoding="utf-8", errors="ignore")
    experiment_text = (skill_path / "capabilities" / "experiment-tracking.md").read_text(
        encoding="utf-8", errors="ignore"
    )
    if "append compact events to `interaction_log.yaml`" in graph_text:
        findings.append("graph-state.md describes the legacy interaction log as the active backend")
    if "append compact events to `graph/interaction_log.yaml`" in experiment_text:
        findings.append("experiment-tracking.md describes the legacy interaction log as the active backend")
    return findings


def _manifest_findings(manifest: dict[str, Any], plugin_path: Path) -> list[str]:
    findings: list[str] = []
    commands = manifest.get("commands", [])
    if not isinstance(commands, list) or not commands:
        return ["research-cockpit commands did not return a non-empty commands list"]
    for command in commands:
        if not isinstance(command, dict):
            findings.append("manifest contains a non-object command")
            continue
        name = str(command.get("name") or "")
        capability = str(command.get("capability_file") or "")
        research_command = str(command.get("command") or "")
        if not capability or not (plugin_path / capability).exists():
            findings.append(f"{name} has missing capability_file")
        if not research_command.startswith("research-cockpit "):
            findings.append(f"{name} command does not use the package CLI")
        if command.get("plugin_command"):
            findings.append(f"{name} still exposes plugin_command")
    return findings


def agent_a_cold_start_install(skill_path: Path, python: str, parent: Path) -> dict[str, Any]:
    research_repo, plugin_path = _new_research_repo(skill_path, parent, "a")
    metadata_check = (
        "import pathlib; "
        f"text=pathlib.Path({str(plugin_path / 'pyproject.toml')!r}).read_text(encoding='utf-8'); "
        "assert 'research-cockpit = \"research_cockpit.cli:main\"' in text"
    )
    checks = [_run_command([python, "-c", metadata_check], cwd=research_repo)]
    command_env = _package_env(plugin_path)
    install_mode = "metadata_check_with_pythonpath"
    install_ok = checks[-1]["passed"]

    plugin_after_install = _file_manifest(plugin_path)
    repo_before = _file_manifest(research_repo)
    if install_ok:
        checks.extend([
            _run_command(_cli(python, "init", "--root", "research_cockpit"), cwd=research_repo, env=command_env),
            _run_command(
                _cli(
                    python,
                    "coord",
                    "overview",
                    "--root",
                    "research_cockpit",
                    "--json",
                    "--compact",
                    "--limit",
                    "20",
                ),
                cwd=research_repo,
                env=command_env,
            ),
        ])

    files_changed = _changed_files(repo_before, _file_manifest(research_repo))
    unexpected = _unexpected_writes(files_changed)
    plugin_changed_after_install = _changed_files(plugin_after_install, _file_manifest(plugin_path))
    overview = checks[-1].get("json") if len(checks) >= 3 and isinstance(checks[-1].get("json"), dict) else {}
    observations = {
        "data_root": "research_cockpit",
        "plugin_root": ".agent/skills/research-cockpit",
        "init_command": "research-cockpit init --root research_cockpit",
        "startup_read_order": ["research-cockpit coord overview --compact --limit 20"],
        "overview_without_build": bool(overview) and not any(
            path.startswith("research_cockpit/dashboards/") for path in files_changed
        ),
        "plugin_changed_after_install": plugin_changed_after_install,
        "install_mode": install_mode,
    }
    findings = _readability_findings(plugin_path)
    passed = (
        install_ok
        and all(check["passed"] for check in checks)
        and not unexpected
        and not plugin_changed_after_install
        and not findings
        and observations["overview_without_build"]
    )
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
    env = _package_env(plugin_path)
    checks = [
        _run_command(
            _cli(
                python,
                "context",
                "--root",
                str(root),
                "--id",
                DEMO_OPTION_ID,
                "--view",
                "execution",
                "--compact",
                "--json",
            ),
            cwd=research_repo,
            env=env,
        ),
    ]
    files_changed = _changed_files(repo_before, _file_manifest(research_repo))
    context = checks[0].get("json") if isinstance(checks[0].get("json"), dict) else {}
    findings = _readability_findings(plugin_path)
    observations = {
        "schema_version": context.get("schema_version"),
        "broad_discovery_avoided": True,
        "node_id": (context.get("node") or {}).get("id"),
        "output_within_budget": checks[0].get("stdout_bytes", 0) <= 4 * 1024,
        "has_execution_invariants": all(
            field in context
            for field in ("assignment_boundary", "active_run", "blocking_gate", "effective_baseline", "revision")
        ),
        "read_capabilities": ["capabilities/focus-context.md"],
    }
    passed = (
        all(check["passed"] for check in checks)
        and not files_changed
        and not findings
        and observations["schema_version"] == "execution_context_v1"
        and observations["broad_discovery_avoided"]
        and observations["node_id"] == DEMO_OPTION_ID
        and observations["output_within_budget"]
        and observations["has_execution_invariants"]
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
    env = _package_env(plugin_path)

    before_claim_dry_run = _file_manifest(research_repo)
    checks.append(_run_command(_cli(
        python,
        "claim-option",
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
    ), cwd=research_repo, env=env))
    claim_dry_run_changed = _changed_files(before_claim_dry_run, _file_manifest(research_repo))

    checks.append(_run_command(_cli(
        python,
        "claim-option",
        "--root",
        str(root),
        "--option",
        DEMO_OPTION_ID,
        "--agent",
        "agent_usability_option",
        "--objective",
        "Exercise option workstream usability in an isolated research repo.",
    ), cwd=research_repo, env=env))
    checks.append(_run_command(_cli(python, "option-workstream-context", "--root", str(root), "--option", DEMO_OPTION_ID, "--json"), cwd=research_repo, env=env))

    before_report_dry_run = _file_manifest(research_repo)
    checks.append(_run_command(_cli(
        python,
        "report-option-workstream",
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
    ), cwd=research_repo, env=env))
    report_dry_run_changed = _changed_files(before_report_dry_run, _file_manifest(research_repo))

    checks.append(_run_command(_cli(
        python,
        "report-option-workstream",
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
    ), cwd=research_repo, env=env))
    checks.append(_run_command(_cli(python, "validate", "--root", str(root), "--json"), cwd=research_repo, env=env))

    files_changed = _changed_files(repo_before, _file_manifest(research_repo))
    interaction_kinds = _interaction_kinds(root)
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
    _prepare_demo_decision_acceptance_state(root)
    repo_before = _file_manifest(research_repo)
    env = _package_env(plugin_path)
    checks = [
        _run_command(_cli(python, "apply-suggestion", "--root", str(root), "--id", "next_action_003", "--target", "current", "--dry-run", "--json"), cwd=research_repo, env=env),
        _run_command(_cli(
            python,
            "promote-decision",
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
        ), cwd=research_repo, env=env),
        _run_command(_cli(python, "check-decision-acceptance", "--root", str(root), "--id", DEMO_DECISION_ID, "--json"), cwd=research_repo, allowed_returncodes={0, 1}, env=env),
        _run_command(_cli(python, "accept-decision", "--root", str(root), "--id", DEMO_DECISION_ID, "--force-accept", "--dry-run", "--json"), cwd=research_repo, env=env),
    ]
    files_changed = _changed_files(repo_before, _file_manifest(research_repo))
    decision_doc = (plugin_path / "capabilities" / "decision-adr.md").read_text(encoding="utf-8", errors="ignore")
    required_commands = ("promote-decision", "check-decision-acceptance", "accept-decision")
    missing_doc_commands = [name for name in required_commands if name not in decision_doc]
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


def agent_f_worker_closeout(skill_path: Path, python: str, parent: Path) -> dict[str, Any]:
    research_repo, plugin_path = _new_research_repo(skill_path, parent, "f")
    root = _copy_demo_state(plugin_path, research_repo)
    run_id = "run_usability_closeout"
    experiment_id = "experiment_demo_prompt_refinement"
    next_experiment_id = "experiment_demo_prompt_refinement_followup"
    source = research_repo / ".agent_runs" / run_id
    source.mkdir(parents=True)
    (source / "metrics.json").write_text('{"score": 0.91}', encoding="utf-8")
    closeout_path = parent / "worker_closeout.yaml"
    repo_before = _file_manifest(research_repo)
    env = _package_env(plugin_path)
    create_check = _run_command(
        _cli(
            python,
            "create-run",
            "--root",
            str(root),
            "--id",
            run_id,
            "--experiment",
            experiment_id,
            "--status",
            "running",
            "--start-experiment",
            "--no-build",
            "--json",
            "--compact",
        ),
        cwd=research_repo,
        env=env,
    )
    ingest_check = _run_command(
        _cli(
            python,
            "ingest-artifact",
            "--root",
            str(root),
            "--node",
            experiment_id,
            "--from",
            str(source),
            "--run-id",
            run_id,
            "--link",
            "metrics=metrics.json",
            "--no-build",
            "--json",
            "--compact",
        ),
        cwd=research_repo,
        env=env,
    )
    ingest_payload = ingest_check.get("json") if isinstance(ingest_check.get("json"), dict) else {}
    record_id = str(ingest_payload.get("target", {}).get("artifact_id") or "")
    _write_yaml(
        closeout_path,
        {
            "schema_version": "run_closeout_v1",
            "run": {"id": run_id, "status": "completed"},
            "experiment": {
                "status": "done",
                "result_summary": "The bounded usability gate passed.",
            },
            "artifact_record": {"existing_record_id": record_id},
            "finding": {
                "statement": "Usability closeout preserved and linked the run evidence.",
                "confidence": "strong",
                "outcome": "positive",
            },
            "next_experiment": {
                "id": next_experiment_id,
                "title": "Scale the verified prompt refinement",
                "next_action": "Start the follow-up run.",
            },
        },
    )
    closeout_check = _run_command(
        _cli(
            python,
            "complete-run",
            "--root",
            str(root),
            "--file",
            str(closeout_path),
            "--no-build",
            "--json",
            "--compact",
        ),
        cwd=research_repo,
        env=env,
    )
    checks = [create_check, ingest_check, closeout_check]

    create_payload = checks[0].get("json") if isinstance(checks[0].get("json"), dict) else {}
    ingest_payload = checks[1].get("json") if isinstance(checks[1].get("json"), dict) else {}
    closeout_payload = checks[2].get("json") if isinstance(checks[2].get("json"), dict) else {}
    experiment = _read_yaml(root / "graph" / "nodes" / f"{experiment_id}.yaml")
    followup = _read_yaml(root / "graph" / "nodes" / f"{next_experiment_id}.yaml")
    run = _read_yaml(root / "runs" / f"{run_id}.yaml")
    records = _read_yaml(root / "artifact_records" / f"{experiment_id}.yaml").get("records", {})
    findings = experiment.get("findings", []) if isinstance(experiment.get("findings"), list) else []
    latest_finding = findings[-1] if findings and isinstance(findings[-1], dict) else {}
    files_changed = _changed_files(repo_before, _file_manifest(research_repo))
    observations = {
        "default_ingest_created_record": (
            ingest_payload.get("target", {}).get("mode") == "record"
            and ingest_payload.get("created") == []
            and record_id in records
            and not (root / "graph" / "nodes" / f"{record_id}.yaml").exists()
        ),
        "structured_closeout_linked_record": (
            run.get("status") == "completed"
            and latest_finding.get("linked_artifact_records") == [record_id]
            and f"artifact:{record_id}" in closeout_payload.get("changed_scope", {}).get("records", [])
        ),
        "experiment_advanced_atomically": (
            experiment.get("status") == "done"
            and followup.get("status") == "queued"
            and followup.get("derived_from") == [experiment_id]
            and closeout_payload.get("next_experiment_id") == next_experiment_id
        ),
        "internal_validation_skipped_recheck": (
            len(checks) == 3
            and all(
                payload.get("verified") is True
                and payload.get("additional_verification_required") is False
                and payload.get("verification_stage") == "internal_verify"
                and payload.get("verify_commands") == []
                and payload.get("post_apply_verify_commands") == []
                for payload in (create_payload, ingest_payload, closeout_payload)
            )
        ),
        "compact_outputs_bounded": all(
            int(check.get("stdout_bytes", 0)) <= 4 * 1024
            for check in checks
        ),
    }
    findings_doc = _readability_findings(plugin_path)
    passed = (
        all(check["passed"] for check in checks)
        and all(observations.values())
        and not findings_doc
        and not _unexpected_writes(files_changed)
    )
    return _case(
        "agent_f_worker_closeout",
        passed,
        checks=checks,
        files_changed=files_changed,
        agent_observations=observations,
        readability_findings=findings_doc,
        unexpected_writes=_unexpected_writes(files_changed),
        documentation_bytes=sum(
            (plugin_path / relative).stat().st_size
            for relative in (
                "SKILL.md",
                "capabilities/experiment-cycle.md",
                "capabilities/experiment-tracking.md",
            )
        ),
    )


def agent_g_role_facade_fast_path(
    skill_path: Path,
    python: str,
    parent: Path,
) -> dict[str, Any]:
    research_repo, plugin_path = _new_research_repo(skill_path, parent, "g")
    root = _copy_demo_state(plugin_path, research_repo)
    env = _package_env(plugin_path)
    assignment_id = "assign_usability_fast_path"
    agent_id = "agent_usability_fast_path"
    experiment_id = "experiment_demo_prompt_refinement"
    experiment_path = root / "graph" / "nodes" / f"{experiment_id}.yaml"
    experiment = _read_yaml(experiment_path)
    experiment["status"] = "queued"
    experiment.pop("result_summary", None)
    _write_yaml(experiment_path, experiment)
    (root / "agents").mkdir(parents=True, exist_ok=True)
    (root / "assignments").mkdir(parents=True, exist_ok=True)
    _write_yaml(
        root / "agents" / f"{agent_id}.yaml",
        {
            "agent_id": agent_id,
            "status": "idle",
            "active_assignment_ids": [],
        },
    )
    _write_yaml(
        root / "assignments" / f"{assignment_id}.yaml",
        {
            "assignment_id": assignment_id,
            "agent_id": None,
            "status": "queued",
            "root_node": "option_demo_prompt_refinement",
            "current_node": experiment_id,
            "allowed_subtree": {
                "root": "option_demo_prompt_refinement",
                "policy": "descendants_only",
            },
            "scope": {
                "root_node": "option_demo_prompt_refinement",
                "subtree_policy": "descendants_only",
                "write_policy": "exclusive",
            },
            "inputs": {
                "effective_baseline_revision": None,
                "dependency_revisions": {},
            },
            "input_revision": "input-v1:usability-fast-path",
            "objective": "Exercise the bounded role-facade worker path.",
            "review": {
                "required": False,
                "status": "not_required",
                "result_revision": None,
            },
        },
    )
    setup_check = _run_command(
        _cli(python, "build", "--root", str(root), "--json"),
        cwd=research_repo,
        env=env,
    )
    if not setup_check["passed"]:
        return _case(
            "agent_g_role_facade_fast_path",
            False,
            checks=[setup_check],
            readability_findings=["fixture index setup failed"],
        )
    index_payload = json.loads(
        (root / "dashboards" / "validation_index.json").read_text(
            encoding="utf-8"
        )
    )
    assignment_rows = index_payload.get("assignments", {})
    assignment_row = (
        assignment_rows.get(assignment_id, {})
        if isinstance(assignment_rows, dict)
        else {}
    )
    baseline_revision = assignment_row.get("current_baseline_revision")
    if not baseline_revision:
        return _case(
            "agent_g_role_facade_fast_path",
            False,
            checks=[setup_check],
            readability_findings=["fixture baseline projection failed"],
        )
    assignment_path = root / "assignments" / f"{assignment_id}.yaml"
    assignment = _read_yaml(assignment_path)
    assignment["inputs"]["effective_baseline_revision"] = baseline_revision
    _write_yaml(assignment_path, assignment)
    refresh_check = _run_command(
        _cli(python, "build", "--root", str(root), "--json"),
        cwd=research_repo,
        env=env,
    )
    if not refresh_check["passed"]:
        return _case(
            "agent_g_role_facade_fast_path",
            False,
            checks=[setup_check, refresh_check],
            readability_findings=["fixture index refresh failed"],
        )

    repo_before = _file_manifest(research_repo)
    claim_check = _run_command(
        _cli(
            python,
            "work",
            "claim",
            "--root",
            str(root),
            "--assignment",
            assignment_id,
            "--agent",
            agent_id,
            "--operation-id",
            "op_usability_claim",
            "--return-packet",
            "--json",
            "--compact",
        ),
        cwd=research_repo,
        env=env,
    )
    claim_payload = (
        claim_check.get("json")
        if isinstance(claim_check.get("json"), dict)
        else {}
    )
    packet = (
        claim_payload.get("packet")
        if isinstance(claim_payload.get("packet"), dict)
        else {}
    )
    lease = packet.get("lease") if isinstance(packet.get("lease"), dict) else {}
    start_file = parent / "role_fast_start.yaml"
    _write_yaml(
        start_file,
        {
            "schema_version": "work_start_v1",
            "agent_id": agent_id,
            "lease_id": lease.get("lease_id"),
            "lease_epoch": lease.get("lease_epoch"),
            "operation_id": "op_usability_start",
            "slug": "usability",
            "run": {},
        },
    )
    start_check = _run_command(
        _cli(
            python,
            "work",
            "start",
            "--root",
            str(root),
            "--assignment",
            assignment_id,
            "--file",
            str(start_file),
            "--json",
            "--compact",
        ),
        cwd=research_repo,
        env=env,
    )
    start_payload = (
        start_check.get("json")
        if isinstance(start_check.get("json"), dict)
        else {}
    )
    entities = (
        start_payload.get("entities")
        if isinstance(start_payload.get("entities"), dict)
        else {}
    )
    run_id = str(entities.get("run_id") or "")
    evidence_source = parent / f"role_fast_evidence_{run_id or 'missing'}"
    evidence_source.mkdir(parents=True, exist_ok=True)
    (evidence_source / "metrics.json").write_text(
        '{"score":0.91}',
        encoding="utf-8",
    )
    close_file = parent / "role_fast_close.yaml"
    _write_yaml(
        close_file,
        {
            "schema_version": "work_close_v1",
            "agent_id": agent_id,
            "lease_id": lease.get("lease_id"),
            "lease_epoch": lease.get("lease_epoch"),
            "operation_id": "op_usability_close",
            "input_revision": "input-v1:usability-fast-path",
            "run": {"id": run_id, "status": "completed"},
            "experiment": {
                "status": "done",
                "result_summary": "The role-facade usability run completed.",
            },
            "finding": {
                "statement": "The bounded role-facade workflow completed.",
                "confidence": "strong",
                "outcome": "positive",
            },
            "evidence_inputs": {
                "source": str(evidence_source),
                "links": {"metrics": "metrics.json"},
            },
            "assignment_result": {
                "outcome": "positive",
                "summary": "The role-facade fast path passed.",
                "delivery": {
                    "git_commit": None,
                    "changed_files": [],
                    "tests": {
                        "status": "passed",
                        "summary": "Usability trace passed.",
                    },
                },
                "proposals": [],
            },
            "review_required": False,
        },
    )
    close_check = _run_command(
        _cli(
            python,
            "work",
            "close",
            "--root",
            str(root),
            "--assignment",
            assignment_id,
            "--file",
            str(close_file),
            "--json",
            "--compact",
        ),
        cwd=research_repo,
        env=env,
    )
    checks = [claim_check, start_check, close_check]
    for check in checks:
        check["nested_subprocess_count"] = 0
    files_changed = _changed_files(repo_before, _file_manifest(research_repo))
    close_payload = (
        close_check.get("json")
        if isinstance(close_check.get("json"), dict)
        else {}
    )
    observations = {
        "claim_returned_packet": packet.get("assignment_id") == assignment_id,
        "start_generated_run": bool(run_id),
        "close_internally_verified": (
            close_payload.get("verification", {}).get("status")
            == "internally_verified"
        ),
        "no_extra_verification_command": len(checks) == 3,
        "static_nested_subprocess_audit": True,
    }
    case = _case(
        "agent_g_role_facade_fast_path",
        all(check["passed"] for check in checks)
        and all(observations.values())
        and not _unexpected_writes(files_changed),
        checks=checks,
        files_changed=files_changed,
        agent_observations=observations,
        unexpected_writes=_unexpected_writes(files_changed),
    )
    contract = evaluate_workflow_contract(case["metrics"], "assigned_worker")
    case["workflow_contract"] = contract
    case["passed"] = bool(case["passed"] and contract["ok"])
    return case


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
                    "agent_f_worker_closeout",
                    "agent_g_role_facade_fast_path",
                )
            ]
        else:
            cases = [
                agent_a_cold_start_install(skill_path, python, temp_run),
                agent_b_read_only_context(skill_path, python, temp_run),
                agent_c_safe_option_workstream(skill_path, python, temp_run),
                agent_d_decision_suggestion_dry_run(skill_path, python, temp_run),
                agent_e_ui_collaboration_docs(skill_path, python, temp_run),
                agent_f_worker_closeout(skill_path, python, temp_run),
                agent_g_role_facade_fast_path(skill_path, python, temp_run),
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
        if case.get("metrics"):
            print(f"  metrics: {case['metrics']}")
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
