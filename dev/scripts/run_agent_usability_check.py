from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from research_cockpit.baselines import compact_effective_baseline, resolve_effective_baseline
from research_cockpit.commands._runtime import stable_payload_revision
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.evidence_bundles import build_evidence_bundle, persisted_result
from research_cockpit.graph_core import load_nodes
from research_cockpit.storage import load_yaml, save_yaml
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


DEMO_OPTION_ID = "option_demo_prompt_refinement"
DEMO_EXPERIMENT_ID = "experiment_demo_prompt_refinement"


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}


def _new_research_repo(
    skill_path: Path,
    parent: Path,
    name: str,
) -> tuple[Path, Path, Path]:
    research_repo = parent / f"research_repo_{name}"
    plugin_path = research_repo / ".agent" / "skills" / "research-cockpit"
    _copy_skill_package(skill_path, plugin_path)
    root = research_repo / "research_cockpit"
    shutil.copytree(
        plugin_path / "examples" / "demo_research_cockpit",
        root,
    )
    return research_repo, plugin_path, root


def _unexpected_writes(
    changed: list[str],
    *,
    allowed_files: tuple[str, ...] = (),
) -> list[str]:
    return [
        path
        for path in changed
        if not (
            path in allowed_files
            or path == "research_cockpit"
            or path.startswith("research_cockpit/")
            or path == "fresh_research_cockpit"
            or path.startswith("fresh_research_cockpit/")
        )
    ]


def _case(
    name: str,
    passed: bool,
    *,
    checks: list[dict[str, Any]],
    agent_checks: list[dict[str, Any]] | None = None,
    files_changed: list[str] | None = None,
    observations: dict[str, Any] | None = None,
    findings: list[str] | None = None,
    workflow: str | None = None,
    allowed_files: tuple[str, ...] = (),
) -> dict[str, Any]:
    visible_checks = agent_checks if agent_checks is not None else checks
    for check in visible_checks:
        check.setdefault("nested_subprocess_count", 0)
    changed = files_changed or []
    metrics = workflow_metrics(visible_checks, files_changed=changed)
    contract = evaluate_workflow_contract(metrics, workflow) if workflow else None
    unexpected = _unexpected_writes(changed, allowed_files=allowed_files)
    final_passed = passed and not unexpected and (contract is None or contract["ok"])
    return {
        "case": name,
        "passed": final_passed,
        "checks": checks,
        "commands_run": [
            {
                "command": check.get("command", []),
                "returncode": check.get("returncode"),
                "stdout_bytes": check.get("stdout_bytes"),
                "stderr_bytes": check.get("stderr_bytes"),
                "duration_ms": check.get("duration_ms"),
            }
            for check in visible_checks
        ],
        "files_changed": changed,
        "agent_observations": observations or {},
        "readability_findings": findings or [],
        "unexpected_writes": unexpected,
        "metrics": metrics,
        **({"workflow_contract": contract} if contract is not None else {}),
    }


def _readability_findings(skill_path: Path) -> list[str]:
    paths = (
        skill_path / "SKILL.md",
        skill_path / "capabilities" / "worker-loop.md",
        skill_path / "capabilities" / "reviewer-loop.md",
        skill_path / "capabilities" / "coordinator-loop.md",
        skill_path / "capabilities" / "maintainer-loop.md",
    )
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in paths
        if path.is_file()
    )
    findings: list[str] = []
    for required in (
        "work open",
        "work start",
        "work close",
        "review open",
        "review report",
        "coord overview",
        "coord assign",
        "coord handoff",
        "maintenance audit",
    ):
        if required not in text:
            findings.append(f"missing canonical route: {required}")
    for removed in (
        "research-cockpit bootstrap",
        "research-cockpit create-run",
        "research-cockpit complete-run",
        "research-cockpit ingest-artifact",
        "research-cockpit start-agent-session",
        "research-cockpit compact-artifacts",
    ):
        if removed in text:
            findings.append(f"active playbook exposes removed route: {removed}")
    return findings


def agent_a_cold_start_install(
    skill_path: Path,
    python: str,
    parent: Path,
) -> dict[str, Any]:
    research_repo, plugin_path, legacy_root = _new_research_repo(
        skill_path,
        parent,
        "a",
    )
    env = _package_env(plugin_path)
    state_home = parent / "external_state_a"
    project_id = "agent-usability-a"
    git_init = _run_command(["git", "init"], cwd=research_repo, env=env)
    before = _file_manifest(research_repo)
    checks = [
        git_init,
        _run_command(
            _cli(
                python,
                "init",
                "--project-id",
                project_id,
                "--state-home",
                str(state_home),
                "--json",
            ),
            cwd=research_repo,
            env=env,
        ),
        _run_command(
            _cli(
                python,
                "coord",
                "overview",
                "--root",
                str(state_home / project_id),
                "--json",
                "--compact",
                "--limit",
                "20",
            ),
            cwd=research_repo,
            env=env,
        ),
    ]
    changed = _changed_files(before, _file_manifest(research_repo))
    findings = _readability_findings(plugin_path)
    overview = checks[-1].get("json") if isinstance(checks[-1].get("json"), dict) else {}
    init_payload = checks[1].get("json") if isinstance(checks[1].get("json"), dict) else {}
    locator_path = research_repo / ".research-cockpit.yaml"
    locator = _read_yaml(locator_path)
    locator_text = locator_path.read_text(encoding="utf-8") if locator_path.is_file() else ""
    state_root = state_home / project_id
    state_is_external = not state_root.is_relative_to(research_repo.resolve())
    legacy_root_unchanged = not any(
        path == "research_cockpit" or path.startswith("research_cockpit/")
        for path in changed
    )
    external_state_default = (
        init_payload.get("external") is True
        and init_payload.get("root") == str(state_root)
        and (state_root / "current_state.yaml").is_file()
        and state_is_external
        and state_root != legacy_root
        and legacy_root_unchanged
    )
    portable_locator = (
        locator.get("schema_version") == "research_cockpit_locator_v1"
        and locator.get("project_id") == project_id
        and str(state_home.resolve()) not in locator_text
    )
    return _case(
        "agent_a_cold_start_install",
        all(check["passed"] for check in checks)
        and not findings
        and overview.get("schema_version") == "coordination_snapshot_v1",
        checks=checks,
        files_changed=changed,
        observations={
            "startup_path": "coord overview",
            "plugin_unchanged": not any(
                path.startswith(".agent/skills/research-cockpit/") for path in changed
            ),
            "git_repo": git_init["passed"],
            "external_state_default": external_state_default,
            "portable_locator": portable_locator,
        },
        findings=findings,
        allowed_files=(".research-cockpit.yaml",),
    )


def agent_b_known_node_context(
    skill_path: Path,
    python: str,
    parent: Path,
) -> dict[str, Any]:
    research_repo, plugin_path, root = _new_research_repo(skill_path, parent, "b")
    before = _file_manifest(research_repo)
    check = _run_command(
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
        env=_package_env(plugin_path),
    )
    changed = _changed_files(before, _file_manifest(research_repo))
    payload = check.get("json") if isinstance(check.get("json"), dict) else {}
    return _case(
        "agent_b_known_node_context",
        check["passed"]
        and payload.get("schema_version") == "execution_context_v1"
        and not changed,
        checks=[check],
        files_changed=changed,
        observations={
            "single_bounded_read": True,
            "stdout_bytes": check.get("stdout_bytes"),
        },
    )


def _prepare_worker_session(
    research_repo: Path,
    plugin_path: Path,
    root: Path,
    parent: Path,
    python: str,
) -> tuple[dict[str, Any], str, str]:
    experiment_path = root / "graph" / "nodes" / f"{DEMO_EXPERIMENT_ID}.yaml"
    experiment = _read_yaml(experiment_path)
    experiment["status"] = "queued"
    experiment.pop("result_summary", None)
    _write_yaml(experiment_path, experiment)

    agent_id = "agent_usability_worker"
    assignment_id = "assign_usability_worker"
    plan_path = parent / "worker_assignment.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": "coord_assign_v1",
                "operation_id": "op_usability_assign_worker",
                "action": "session",
                "session": {
                    "kind": "experiment",
                    "option_id": DEMO_OPTION_ID,
                    "experiment_id": DEMO_EXPERIMENT_ID,
                    "objective": "Exercise the canonical assigned-worker workflow.",
                    "branch": "codex/usability-worker",
                    "worktree": "../worktrees/usability-worker",
                    "agent_id": agent_id,
                    "assignment_id": assignment_id,
                    "create_worktree": False,
                    "force": False,
                },
            }
        ),
        encoding="utf-8",
    )
    setup = _run_command(
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
        cwd=research_repo,
        env=_package_env(plugin_path),
    )
    return setup, agent_id, assignment_id


def agent_c_assigned_worker_round_trip(
    skill_path: Path,
    python: str,
    parent: Path,
) -> dict[str, Any]:
    research_repo, plugin_path, root = _new_research_repo(skill_path, parent, "c")
    setup, agent_id, assignment_id = _prepare_worker_session(
        research_repo,
        plugin_path,
        root,
        parent,
        python,
    )
    env = _package_env(plugin_path)
    before = _file_manifest(research_repo)

    open_check = _run_command(
        _cli(
            python,
            "work",
            "open",
            "--root",
            str(root),
            "--assignment",
            assignment_id,
            "--json",
            "--compact",
        ),
        cwd=research_repo,
        env=env,
    )
    packet = open_check.get("json") if isinstance(open_check.get("json"), dict) else {}
    lease = packet.get("lease") if isinstance(packet.get("lease"), dict) else {}

    start_path = parent / "worker_start.yaml"
    _write_yaml(
        start_path,
        {
            "schema_version": "work_start_v1",
            "agent_id": agent_id,
            "lease_id": lease.get("lease_id"),
            "lease_epoch": lease.get("lease_epoch"),
            "operation_id": "op_usability_worker_start",
            "input_revision": packet.get("input_revision"),
            "experiment_id": DEMO_EXPERIMENT_ID,
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
            str(start_path),
            "--json",
            "--compact",
        ),
        cwd=research_repo,
        env=env,
    )
    start_payload = (
        start_check.get("json") if isinstance(start_check.get("json"), dict) else {}
    )
    entities = (
        start_payload.get("entities")
        if isinstance(start_payload.get("entities"), dict)
        else {}
    )
    run_id = str(entities.get("run_id") or "")

    evidence_source = parent / "worker_evidence"
    evidence_source.mkdir(parents=True, exist_ok=True)
    evidence_bytes = b'{"score":0.91,"source":"canonical-worker"}'
    (evidence_source / "metrics.json").write_bytes(evidence_bytes)
    close_path = parent / "worker_close.yaml"
    _write_yaml(
        close_path,
        {
            "schema_version": "work_close_v1",
            "agent_id": agent_id,
            "lease_id": lease.get("lease_id"),
            "lease_epoch": lease.get("lease_epoch"),
            "operation_id": "op_usability_worker_close",
            "input_revision": packet.get("input_revision"),
            "run": {"id": run_id, "status": "completed"},
            "experiment": {
                "status": "done",
                "result_summary": "Canonical worker round-trip completed.",
            },
            "finding": {
                "statement": "The three-command worker path preserved final evidence.",
                "confidence": "strong",
                "outcome": "positive",
            },
            "evidence_inputs": {
                "source": str(evidence_source),
                "links": {"metrics": "metrics.json"},
            },
            "assignment_result": {
                "outcome": "positive",
                "summary": "Canonical worker path passed.",
                "delivery": {
                    "git_commit": None,
                    "changed_files": [],
                    "tests": {
                        "status": "passed",
                        "summary": "Usability worker trace passed.",
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
            str(close_path),
            "--json",
            "--compact",
        ),
        cwd=research_repo,
        env=env,
    )
    worker_checks = [open_check, start_check, close_check]
    changed = _changed_files(before, _file_manifest(research_repo))
    close_payload = (
        close_check.get("json") if isinstance(close_check.get("json"), dict) else {}
    )
    assignment = _read_yaml(root / "assignments" / f"{assignment_id}.yaml")
    artifact_record_id = str(
        close_payload.get("entities", {}).get("artifact_record_id") or ""
    )
    artifact_records = _read_yaml(
        root / "artifact_records" / f"{DEMO_EXPERIMENT_ID}.yaml"
    ).get("records", {})
    artifact_record = (
        artifact_records.get(artifact_record_id, {})
        if isinstance(artifact_records, dict)
        else {}
    )
    storage = artifact_record.get("storage", {})
    links = artifact_record.get("links", {})
    legacy_copy_paths = list(
        (root / "artifacts" / DEMO_EXPERIMENT_ID).rglob("metrics.json")
    )
    evidence_preserved = (
        (evidence_source / "metrics.json").read_bytes() == evidence_bytes
        and storage.get("mode") == "reference"
        and storage.get("ownership") == "external"
        and storage.get("uri") == evidence_source.as_uri()
        and links.get("metrics") == (evidence_source / "metrics.json").as_uri()
        and not legacy_copy_paths
    )
    observations = {
        "packet_ready": packet.get("readiness") == "ready",
        "start_generated_run": bool(run_id),
        "close_internally_verified": (
            close_payload.get("verification", {}).get("status") == "internally_verified"
            and close_payload.get("verification", {}).get(
                "additional_verification_required"
            )
            is False
        ),
        "assignment_completed": assignment.get("status") == "completed",
        "final_evidence_preserved": evidence_preserved,
        "agent_command_count": len(worker_checks),
    }
    return _case(
        "agent_c_assigned_worker_round_trip",
        setup["passed"]
        and all(check["passed"] for check in worker_checks)
        and all(observations.values()),
        checks=[setup, *worker_checks],
        agent_checks=worker_checks,
        files_changed=changed,
        observations=observations,
        workflow="assigned_worker",
    )


def _prepare_review_fixture(root: Path) -> tuple[str, str, dict[str, Any], str]:
    producer_id = "assign_usability_producer"
    reviewer_id = "assign_usability_review"
    reviewer_agent = "agent_usability_reviewer"
    bundle, producer_revision = build_evidence_bundle(
        assignment_id=producer_id,
        operation_id="op_usability_producer_close",
        input_revision="input-v1:usability-producer",
        result_spec={
            "outcome": "positive",
            "summary": "Producer evidence is ready for review.",
            "delivery": {
                "git_commit": None,
                "changed_files": [],
                "tests": {"status": "passed", "summary": "Producer tests passed."},
            },
            "proposals": [],
        },
        run_ids=[],
        finding_ids=[],
        artifact_record_ids=[],
        packet_revision="packet-v1:usability-producer",
    )
    save_yaml(
        root / "assignments" / f"{producer_id}.yaml",
        {
            "assignment_id": producer_id,
            "agent_id": None,
            "status": "completed",
            "root_node": DEMO_OPTION_ID,
            "current_node": DEMO_EXPERIMENT_ID,
            "allowed_subtree": {
                "root": DEMO_OPTION_ID,
                "policy": "descendants_only",
            },
            "scope": {
                "root_node": DEMO_OPTION_ID,
                "subtree_policy": "descendants_only",
                "write_policy": "exclusive",
            },
            "review": {"required": True, "status": "pending", "result_revision": None},
            "result": persisted_result(bundle, producer_revision),
        },
    )
    now = datetime.now(timezone.utc)
    lease = {
        "lease_id": "lease_usability_review",
        "owner_agent_id": reviewer_agent,
        "lease_epoch": 1,
        "heartbeat_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "expires_at": (now + timedelta(hours=1))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }
    effective_baseline = compact_effective_baseline(
        resolve_effective_baseline(
            load_nodes(root),
            DEMO_EXPERIMENT_ID,
            load_yaml(root / "current_state.yaml"),
        )
    )
    baseline_revision = (
        None
        if effective_baseline["source_kind"] == "none"
        else stable_payload_revision(effective_baseline, prefix="exec-v1")
    )
    captured_inputs = {
        "effective_baseline_revision": baseline_revision,
        "dependency_revisions": {producer_id: producer_revision},
    }
    input_revision = stable_payload_revision(captured_inputs, prefix="input-v1")
    save_yaml(
        root / "assignments" / f"{reviewer_id}.yaml",
        {
            "assignment_id": reviewer_id,
            "agent_id": reviewer_agent,
            "kind": "review",
            "status": "active",
            "root_node": DEMO_OPTION_ID,
            "current_node": DEMO_EXPERIMENT_ID,
            "allowed_subtree": {
                "root": DEMO_OPTION_ID,
                "policy": "descendants_only",
            },
            "scope": {
                "root_node": DEMO_OPTION_ID,
                "subtree_policy": "descendants_only",
                "write_policy": "review_read_only",
            },
            "dependencies": [
                {"assignment_id": producer_id, "required_status": "completed"}
            ],
            "inputs": captured_inputs,
            "input_revision": input_revision,
            "objective": "Review the producer Evidence Bundle.",
            "lease_epoch_counter": 1,
            "lease": lease,
            "review": {
                "required": False,
                "status": "not_required",
                "result_revision": None,
            },
        },
    )
    save_yaml(
        root / "agents" / f"{reviewer_agent}.yaml",
        {
            "agent_id": reviewer_agent,
            "status": "active",
            "active_assignment_ids": [reviewer_id],
        },
    )
    build_dashboard(root)
    return producer_revision, reviewer_id, lease, input_revision


def agent_d_reviewer_round_trip(
    skill_path: Path,
    python: str,
    parent: Path,
) -> dict[str, Any]:
    research_repo, plugin_path, root = _new_research_repo(skill_path, parent, "d")
    producer_revision, reviewer_id, lease, input_revision = _prepare_review_fixture(root)
    producer_path = root / "assignments" / "assign_usability_producer.yaml"
    producer_before = producer_path.read_bytes()
    before = _file_manifest(research_repo)
    env = _package_env(plugin_path)

    open_check = _run_command(
        _cli(
            python,
            "review",
            "open",
            "--root",
            str(root),
            "--assignment",
            reviewer_id,
            "--json",
            "--compact",
        ),
        cwd=research_repo,
        env=env,
    )
    packet = open_check.get("json") if isinstance(open_check.get("json"), dict) else {}
    report_path = parent / "review_report.yaml"
    _write_yaml(
        report_path,
        {
            "schema_version": "review_report_v1",
            "agent_id": "agent_usability_reviewer",
            "lease_id": lease["lease_id"],
            "lease_epoch": lease["lease_epoch"],
            "operation_id": "op_usability_review_report",
            "input_revision": input_revision,
            "producer_result_revision": producer_revision,
            "verdict": "approved",
            "summary": "Producer evidence satisfies the bounded review criteria.",
            "findings": [],
            "evidence_inspected": [],
            "validation_performed": ["Reviewed the producer Evidence Bundle."],
        },
    )
    report_check = _run_command(
        _cli(
            python,
            "review",
            "report",
            "--root",
            str(root),
            "--assignment",
            reviewer_id,
            "--file",
            str(report_path),
            "--json",
            "--compact",
        ),
        cwd=research_repo,
        env=env,
    )
    reviewer_checks = [open_check, report_check]
    changed = _changed_files(before, _file_manifest(research_repo))
    reviewer = _read_yaml(root / "assignments" / f"{reviewer_id}.yaml")
    observations = {
        "producer_revision_bound": (
            packet.get("producer", {}).get("result_revision") == producer_revision
        ),
        "producer_truth_unchanged": producer_path.read_bytes() == producer_before,
        "reviewer_completed": reviewer.get("status") == "completed",
        "review_verdict_recorded": (
            reviewer.get("result", {}).get("review", {}).get("verdict") == "approved"
        ),
        "agent_command_count": len(reviewer_checks),
    }
    return _case(
        "agent_d_reviewer_round_trip",
        all(check["passed"] for check in reviewer_checks)
        and all(observations.values()),
        checks=reviewer_checks,
        agent_checks=reviewer_checks,
        files_changed=changed,
        observations=observations,
        workflow="reviewer",
    )


def agent_e_coordinator_overview(
    skill_path: Path,
    python: str,
    parent: Path,
) -> dict[str, Any]:
    research_repo, plugin_path, root = _new_research_repo(skill_path, parent, "e")
    before = _file_manifest(research_repo)
    check = _run_command(
        _cli(
            python,
            "coord",
            "overview",
            "--root",
            str(root),
            "--json",
            "--compact",
            "--limit",
            "20",
        ),
        cwd=research_repo,
        env=_package_env(plugin_path),
    )
    changed = _changed_files(before, _file_manifest(research_repo))
    payload = check.get("json") if isinstance(check.get("json"), dict) else {}
    return _case(
        "agent_e_coordinator_overview",
        check["passed"]
        and payload.get("schema_version") == "coordination_snapshot_v1"
        and not changed,
        checks=[check],
        agent_checks=[check],
        files_changed=changed,
        observations={
            "bounded_limit": payload.get("limit") in {None, 20},
            "single_command": True,
        },
        workflow="coordinator_overview",
    )


def agent_f_legacy_data_round_trip(
    skill_path: Path,
    python: str,
    parent: Path,
) -> dict[str, Any]:
    research_repo, plugin_path, root = _new_research_repo(skill_path, parent, "f")
    node_path = root / "graph" / "nodes" / f"{DEMO_EXPERIMENT_ID}.yaml"
    node = _read_yaml(node_path)
    node["legacy_0_2_extension"] = {
        "opaque": "preserve-me",
        "provenance_ref": "legacy://artifact/run-7",
    }
    _write_yaml(node_path, node)
    payload_path = root / "artifacts" / DEMO_EXPERIMENT_ID / "legacy-run" / "payload.bin"
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_bytes = b"\x00legacy-artifact\xff\x10"
    payload_path.write_bytes(payload_bytes)
    manifest_path = payload_path.parent / "_research_cockpit_ingest.json"
    manifest = {
        "schema_version": "artifact_ingest_v1",
        "legacy_unknown": {"retain": True},
        "files": ["payload.bin"],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    before = _file_manifest(research_repo)
    plan_path = parent / "legacy_round_trip.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": "coord_assign_v1",
                "operation_id": "op_usability_legacy_round_trip",
                "action": "graph_plan",
                "graph_plan": {
                    "nodes": [],
                    "updates": [
                        {
                            "id": DEMO_EXPERIMENT_ID,
                            "fields": {"summary": "Updated through the 0.3.0 facade."},
                        }
                    ],
                },
            }
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
        cwd=research_repo,
        env=_package_env(plugin_path),
    )
    changed = _changed_files(before, _file_manifest(research_repo))
    after = _read_yaml(node_path)
    manifest_after = json.loads(manifest_path.read_text(encoding="utf-8"))
    observations = {
        "unknown_node_fields_preserved": (
            after.get("legacy_0_2_extension") == node["legacy_0_2_extension"]
        ),
        "artifact_payload_bytes_preserved": payload_path.read_bytes() == payload_bytes,
        "artifact_manifest_unknown_fields_preserved": (
            manifest_after.get("legacy_unknown") == {"retain": True}
        ),
        "canonical_mutation_applied": (
            after.get("summary") == "Updated through the 0.3.0 facade."
        ),
    }
    return _case(
        "agent_f_legacy_data_round_trip",
        check["passed"] and all(observations.values()),
        checks=[check],
        files_changed=changed,
        observations=observations,
    )


CASE_NAMES = (
    "agent_a_cold_start_install",
    "agent_b_known_node_context",
    "agent_c_assigned_worker_round_trip",
    "agent_d_reviewer_round_trip",
    "agent_e_coordinator_overview",
    "agent_f_legacy_data_round_trip",
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
                    findings=[
                        dependency.get("stdout")
                        or dependency.get("stderr")
                        or "runtime dependency check failed"
                    ],
                )
                for name in CASE_NAMES
            ]
        else:
            cases = [
                agent_a_cold_start_install(skill_path, python, temp_run),
                agent_b_known_node_context(skill_path, python, temp_run),
                agent_c_assigned_worker_round_trip(skill_path, python, temp_run),
                agent_d_reviewer_round_trip(skill_path, python, temp_run),
                agent_e_coordinator_overview(skill_path, python, temp_run),
                agent_f_legacy_data_round_trip(skill_path, python, temp_run),
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
        if case.get("workflow_contract"):
            print(f"  workflow contract: {case['workflow_contract']}")
        if case["readability_findings"]:
            print(f"  findings: {case['readability_findings']}")
        if case["unexpected_writes"]:
            print(f"  unexpected writes: {case['unexpected_writes']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-path", type=Path, default=DEFAULT_SKILL_PATH)
    parser.add_argument(
        "--python",
        default=os.environ.get("RESEARCH_COCKPIT_PYTHON", "").strip() or sys.executable,
    )
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
