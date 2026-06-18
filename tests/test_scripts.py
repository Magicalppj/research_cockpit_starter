from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import time
import unittest
import uuid
from datetime import datetime, timezone, date
from pathlib import Path
import sys
from typing import Any
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT_DIR
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))
DEV_SCRIPTS_DIR = ROOT_DIR / "dev" / "scripts"
sys.path.insert(0, str(DEV_SCRIPTS_DIR))
existing_pythonpath = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = str(SRC_DIR) if not existing_pythonpath else str(SRC_DIR) + os.pathsep + existing_pythonpath

from research_cockpit.model import ValidationError, load_nodes, load_yaml, save_yaml, validate_cockpit
from research_cockpit.assignment_scope import AssignmentScopeError
from research_cockpit.baselines import (
    build_accepted_decision_rows,
    build_accepted_option_rows,
    build_baseline_overview_rows,
    build_set_baseline_command,
    resolve_effective_baseline,
)
from research_cockpit.commands.accept_decision import accept_decision
from research_cockpit.commands.add_node import add_node, add_node_result
from research_cockpit.commands.active_resources import active_resources_payload
from research_cockpit.commands.agent_bootstrap import (
    BootstrapIdentityError,
    agent_bootstrap_payload,
    format_dependency_error,
    missing_runtime_dependencies,
)
from research_cockpit.commands.apply_graph_plan import apply_graph_plan
from research_cockpit.commands.apply_suggestion import apply_suggestion
from research_cockpit.commands.agent_session_context import agent_session_context_payload
from research_cockpit.commands.artifact_retention_audit import artifact_retention_audit_payload
from research_cockpit.commands.assignment_view import assignment_view_payload
from research_cockpit.commands.branch_audit import branch_audit_payload
from research_cockpit.commands.build_dashboard import build_dashboard, dashboard_watch_signature, watch_dashboard
from research_cockpit.commands.check_decision_acceptance import decision_acceptance_payload
from research_cockpit.commands.claim_option import claim_option
from research_cockpit.commands.cleanup_suggestion_lifecycle import cleanup_suggestion_lifecycle
from research_cockpit.commands.close_branch import close_branch
from research_cockpit.commands.close_current_experiment import close_current_experiment
from research_cockpit.commands.complete_experiment import complete_experiment
from research_cockpit.commands.complete_experiments import complete_experiments
from research_cockpit.commands.context import context_payload
from research_cockpit.commands.create_artifact import create_artifact
from research_cockpit.commands.create_followup_experiment import create_followup_experiment
from research_cockpit.commands.create_run import create_run
from research_cockpit.commands.create_workstream import create_workstream
from research_cockpit.commands.create_note import create_note
from research_cockpit.commands.complete_run import complete_run
from research_cockpit.commands.finalize_workstream import finalize_workstream
from research_cockpit.commands.ingest_artifact import ingest_artifact
from research_cockpit.commands.import_worktree_findings import import_worktree_findings
from research_cockpit.commands.ingest_gate_result import ingest_gate_result
from research_cockpit.commands.link_artifact import link_artifact
from research_cockpit.commands.list_runs import list_runs_payload
from research_cockpit.commands.lint_semantic import semantic_lint
from research_cockpit.commands.list_agent_commands import agent_command_manifest
from research_cockpit.commands.maintenance_audit import maintenance_audit_payload
from research_cockpit.commands.migrate_terminal_next_actions import migrate_terminal_next_actions
from research_cockpit.commands.node_context import node_context_payload
from research_cockpit.commands.option_workstream_context import compact_option_workstream_context, option_workstream_context_payload
from research_cockpit.commands.promote_decision import promote_decision
from research_cockpit.commands.record_gate_result import record_gate_result
from research_cockpit.commands.record_finding import record_finding
from research_cockpit.commands.repair_interaction_log import repair_interaction_log
from research_cockpit.commands.report_option_workstream import report_option_workstream
from research_cockpit.commands.run_context import run_context_payload
from research_cockpit.commands.set_agent_focus import set_agent_focus
from research_cockpit.commands.set_baseline import set_baseline
from research_cockpit.commands.set_cursor import set_cursor
from research_cockpit.commands.set_focus import set_focus
from research_cockpit.commands.skill_smoke_test import missing_modules_for_python, skill_smoke_test_payload
from research_cockpit.commands.start_agent_session import start_agent_session
from research_cockpit.commands.sync_focus_actions import sync_focus_actions
from research_cockpit.commands.suggest_next_actions import select_suggestions
from research_cockpit.commands.update_decision_evidence import update_decision_evidence
from research_cockpit.commands.update_decision_checklist import update_decision_checklist
from research_cockpit.commands.update_node_fields import update_node_fields
from research_cockpit.commands.update_finding import update_finding
from research_cockpit.commands.update_run import update_run
from research_cockpit.commands.update_workstream_fields import update_workstream_fields
from research_cockpit.commands.update_suggestion_state import update_suggestion_state
from research_cockpit.commands.update_status import update_status
from research_cockpit.commands.worktree_audit import worktree_audit_payload
from research_cockpit.commands.worktree_closeout import worktree_closeout_payload
from research_cockpit.context_packs import build_agent_context
from research_cockpit.gate_results import load_gate_result, normalize_gate_result
from research_cockpit.graph_views import upsert_graph_view
from research_cockpit.mutation_lock import MutationError, mutation_lock
from research_cockpit.mutation_runtime import finish_mutation
from research_cockpit.progress import load_progress_heartbeat
from research_cockpit.resources import build_link_rows
from research_cockpit.run_summaries import build_run_summaries
from workflow_metrics import workflow_metrics


def write_node(root: Path, data: dict) -> None:
    save_yaml(root / "graph" / "nodes" / f"{data['id']}.yaml", data)


def interaction_events(root: Path) -> list[dict]:
    return load_yaml(root / "graph" / "interaction_log.yaml").get("events", [])


def write_malformed_interaction_log(root: Path) -> None:
    (root / "graph" / "interaction_log.yaml").write_text("events:\n- kind: broken\n  command: [\n", encoding="utf-8")


def assert_mutation_json_failed_without_writes(testcase: unittest.TestCase, payload: dict) -> None:
    testcase.assertFalse(payload["partial_success"])
    testcase.assertEqual(payload["written_files"], [])
    testcase.assertIn("interaction_log.yaml", payload["error"])


def cli_command(command: str, *args: str) -> list[str]:
    return [sys.executable, "-m", "research_cockpit.cli", command, *args]


class ScriptBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_parent = ROOT_DIR / ".test_tmp"
        temp_parent.mkdir(exist_ok=True)
        self.tmp_root = temp_parent / f"scripts_{uuid.uuid4().hex}"
        self.root = self.tmp_root / "research_cockpit"
        self.root.mkdir(parents=True)
        write_node(
            self.root,
            {
                "id": "stage_text",
                "type": "stage",
                "title": "Text",
                "status": "active",
                "children": ["problem_text"],
            },
        )
        write_node(
            self.root,
            {
                "id": "problem_text",
                "type": "problem",
                "title": "Weak text",
                "status": "active",
                "parent": "stage_text",
                "children": ["option_t5"],
            },
        )
        write_node(
            self.root,
            {
                "id": "option_t5",
                "type": "option",
                "title": "T5",
                "status": "active",
                "parent": "problem_text",
                "children": ["exp_t5"],
            },
        )
        write_node(
            self.root,
            {
                "id": "exp_t5",
                "type": "experiment",
                "title": "T5 ablation",
                "status": "planned",
                "parent": "option_t5",
            },
        )
        save_yaml(
            self.root / "current_state.yaml",
            {
                "current_stage": "stage_text",
                "current_problem": "problem_text",
                "current_option": "option_t5",
                "current_focus_path": ["stage_text", "problem_text", "option_t5"],
            },
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def start_t5_assignment(self, *, label: str = "t5") -> dict[str, Any]:
        return start_agent_session(
            self.root,
            option_id="option_t5",
            label=label,
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=self.tmp_root / "worktrees" / "agent_t5",
            rebuild_dashboard=False,
        )

    def write_other_scope_branch(
        self,
        *,
        option_id: str,
        experiment_id: str,
        option_title: str = "Other scope option",
        experiment_title: str = "Other scope experiment",
        experiment_status: str = "running",
        experiment_next_actions: list[str] | None = None,
    ) -> None:
        write_node(
            self.root,
            {
                "id": option_id,
                "type": "option",
                "title": option_title,
                "status": "active",
                "parent": "problem_text",
            },
        )
        experiment: dict[str, Any] = {
            "id": experiment_id,
            "type": "experiment",
            "title": experiment_title,
            "status": experiment_status,
            "parent": option_id,
        }
        if experiment_next_actions is not None:
            experiment["next_actions"] = experiment_next_actions
        write_node(self.root, experiment)

    def run_dev_script(self, script_name: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(DEV_SCRIPTS_DIR / script_name), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def generate_large_fixture(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run_dev_script("generate_large_cockpit_fixture.py", "--root", str(root), *args)

    def test_add_node_uses_type_default_status_and_validates_parent(self) -> None:
        add_node(
            self.root,
            node_id="exp_new",
            node_type="experiment",
            title="New experiment",
            parent="option_t5",
            status=None,
            summary="",
        )

        data = load_yaml(self.root / "graph" / "nodes" / "exp_new.yaml")

        self.assertEqual(data["status"], "planned")
        self.assertEqual(data["parent"], "option_t5")

    def test_add_node_rejects_unknown_parent(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            add_node(
                self.root,
                node_id="option_bad",
                node_type="option",
                title="Bad",
                parent="missing_problem",
                status=None,
                summary="",
            )

        self.assertIn("missing_problem", str(ctx.exception))

    def test_add_node_cli_supports_dry_run_json_no_build_and_default_build(self) -> None:
        dry_run = subprocess.run(
            [
                *cli_command("add-node"),
                "--root",
                str(self.root),
                "--id",
                "exp_preview",
                "--type",
                "experiment",
                "--title",
                "Preview experiment",
                "--parent",
                "option_t5",
                "--dry-run",
                "--show-diff",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(dry_run.stdout)

        self.assertEqual(dry_run.returncode, 0, dry_run.stdout + dry_run.stderr)
        self.assertTrue(payload["would_change"])
        self.assertFalse(payload["changed"])
        self.assertIn("diff", payload)
        self.assertFalse((self.root / "graph" / "nodes" / "exp_preview.yaml").exists())

        no_build = subprocess.run(
            [
                *cli_command("add-node"),
                "--root",
                str(self.root),
                "--id",
                "exp_no_build",
                "--type",
                "experiment",
                "--title",
                "No build experiment",
                "--parent",
                "option_t5",
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        no_build_payload = json.loads(no_build.stdout)

        self.assertEqual(no_build.returncode, 0, no_build.stdout + no_build.stderr)
        self.assertTrue(no_build_payload["changed"])
        self.assertTrue((self.root / "graph" / "nodes" / "exp_no_build.yaml").exists())
        self.assertFalse((self.root / "dashboards").exists())

        default_build = subprocess.run(
            [
                *cli_command("add-node"),
                "--root",
                str(self.root),
                "--id",
                "exp_build",
                "--type",
                "experiment",
                "--title",
                "Build experiment",
                "--parent",
                "option_t5",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(default_build.returncode, 0, default_build.stdout + default_build.stderr)
        self.assertTrue((self.root / "dashboards" / "graph_view.json").exists())

    def test_cli_validation_errors_are_clean_without_traceback(self) -> None:
        add_failed = subprocess.run(
            cli_command(
                "add-node",
                "--root",
                str(self.root),
                "--id",
                "option_bad",
                "--type",
                "option",
                "--title",
                "Bad",
                "--parent",
                "missing_problem",
            ),
            capture_output=True,
            text=True,
        )
        add_output = add_failed.stdout + add_failed.stderr

        self.assertEqual(add_failed.returncode, 1)
        self.assertIn("Parent node does not exist: missing_problem", add_output)
        self.assertNotIn("Traceback", add_output)

        status_failed = subprocess.run(
            cli_command(
                "update-status",
                "--root",
                str(self.root),
                "--id",
                "option_t5",
                "--status",
                "not_a_status",
            ),
            capture_output=True,
            text=True,
        )
        status_output = status_failed.stdout + status_failed.stderr

        self.assertEqual(status_failed.returncode, 1)
        self.assertIn("Invalid status 'not_a_status'", status_output)
        self.assertNotIn("Traceback", status_output)

    def test_update_status_updates_experiment_result(self) -> None:
        update_status(
            self.root,
            node_id="exp_t5",
            status="done",
            summary="Completed.",
            result_summary="Improved edit following.",
        )

        data = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")

        self.assertEqual(data["status"], "done")
        self.assertEqual(data["summary"], "Completed.")
        self.assertEqual(data["result_summary"], "Improved edit following.")

    def test_update_status_rebuilds_by_default_and_no_build_skips_dashboard(self) -> None:
        update_status(
            self.root,
            node_id="exp_t5",
            status="queued",
            rebuild_dashboard=False,
        )

        self.assertFalse((self.root / "dashboards" / "graph_view.json").exists())

        update_status(
            self.root,
            node_id="exp_t5",
            status="running",
        )

        self.assertTrue((self.root / "dashboards" / "graph_view.json").exists())

    def test_update_status_cli_dry_run_json_diff_does_not_write(self) -> None:
        before = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")
        out = subprocess.run(
            [
                *cli_command("update-status"),
                "--root",
                str(self.root),
                "--id",
                "exp_t5",
                "--status",
                "queued",
                "--summary",
                "Preview summary.",
                "--dry-run",
                "--show-diff",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(payload["dry_run"])
        self.assertFalse(payload["changed"])
        self.assertTrue(payload["would_change"])
        self.assertIn("diff", payload)
        self.assertEqual(before, after)

    def test_update_status_rejects_terminal_parent_with_active_descendants(self) -> None:
        problem_path = self.root / "graph" / "nodes" / "problem_text.yaml"
        before = problem_path.read_text(encoding="utf-8")

        out = subprocess.run(
            [
                *cli_command("update-status"),
                "--root",
                str(self.root),
                "--id",
                "problem_text",
                "--status",
                "resolved",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertEqual(payload["error"], "terminal_parent_has_active_descendants")
        self.assertEqual(payload["node_id"], "problem_text")
        self.assertEqual(payload["target_status"], "resolved")
        self.assertEqual([item["id"] for item in payload["blocking_descendants"]], ["option_t5", "exp_t5"])
        self.assertIn("close-branch", payload["suggested_commands"][0])
        self.assertEqual(before, problem_path.read_text(encoding="utf-8"))

    def test_close_branch_dry_run_lists_safe_updates_and_experiment_warning(self) -> None:
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["children"] = ["exp_t5", "problem_child"]
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)
        write_node(
            self.root,
            {
                "id": "problem_child",
                "type": "problem",
                "title": "Child problem",
                "status": "open",
                "parent": "option_t5",
            },
        )
        before_problem_child = (self.root / "graph" / "nodes" / "problem_child.yaml").read_text(encoding="utf-8")

        out = subprocess.run(
            [
                *cli_command("close-branch"),
                "--root",
                str(self.root),
                "--id",
                "problem_text",
                "--downstream-status",
                "parked",
                "--dry-run",
                "--show-diff",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(payload["dry_run"])
        self.assertTrue(payload["would_change"])
        self.assertFalse(payload["changed"])
        self.assertEqual([item["id"] for item in payload["updates"]], ["problem_child"])
        self.assertIn("problem_child.yaml", payload["diff"])
        self.assertEqual(before_problem_child, (self.root / "graph" / "nodes" / "problem_child.yaml").read_text(encoding="utf-8"))
        skipped = {item["id"]: item["reason"] for item in payload["skipped"]}
        self.assertEqual(skipped["exp_t5"], "requires_include_experiments")
        self.assertEqual(skipped["option_t5"], "blocked_by_active_descendants")
        self.assertFalse(payload["parent_ready_for_terminal_status"])
        self.assertIn("--include-experiments", payload["recommended_commands"][0])

    def test_close_branch_compact_json_keeps_safety_preview_fields(self) -> None:
        out = subprocess.run(
            [
                *cli_command("close-branch"),
                "--root",
                str(self.root),
                "--id",
                "problem_text",
                "--dry-run",
                "--json",
                "--compact",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertFalse(payload["parent_ready_for_terminal_status"])
        self.assertGreater(payload["skipped_count"], 0)
        self.assertGreater(payload["remaining_active_count"], 0)
        self.assertIn("skipped", payload)
        self.assertIn("remaining_active_descendants", payload)
        self.assertEqual(payload["remaining_active_descendants"][0]["id"], "option_t5")

    def test_close_branch_include_experiments_closes_descendants_before_parent_status(self) -> None:
        result = close_branch(
            self.root,
            node_id="problem_text",
            include_experiments=True,
            rebuild_dashboard=False,
        )

        self.assertTrue(result["changed"])
        self.assertTrue(result["parent_ready_for_terminal_status"])
        self.assertEqual(load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")["status"], "parked")
        self.assertEqual(load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")["status"], "cancelled")
        self.assertIn("--status <terminal_status>", result["recommended_commands"][0])

        out = subprocess.run(
            [
                *cli_command("update-status"),
                "--root",
                str(self.root),
                "--id",
                "problem_text",
                "--status",
                "resolved",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertFalse((self.root / "dashboards" / "graph_view.json").exists())

    def test_close_branch_cli_include_experiments_no_build_writes_only_descendants(self) -> None:
        out = subprocess.run(
            [
                *cli_command("close-branch"),
                "--root",
                str(self.root),
                "--id",
                "problem_text",
                "--include-experiments",
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(payload["changed"])
        self.assertTrue(payload["parent_ready_for_terminal_status"])
        self.assertEqual(
            {item["id"]: item["after_status"] for item in payload["updates"]},
            {"exp_t5": "cancelled", "option_t5": "parked"},
        )
        self.assertEqual(load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")["status"], "active")
        self.assertEqual(load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")["status"], "parked")
        self.assertEqual(load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")["status"], "cancelled")
        self.assertIn("--status <terminal_status>", payload["recommended_commands"][0])
        self.assertFalse((self.root / "dashboards" / "graph_view.json").exists())

    def test_close_branch_json_error_for_invalid_root_type(self) -> None:
        out = subprocess.run(
            [
                *cli_command("close-branch"),
                "--root",
                str(self.root),
                "--id",
                "stage_text",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["changed"])
        self.assertEqual(payload["id"], "stage_text")
        self.assertIn("must be problem or option", payload["error"])

    def test_close_branch_json_error_when_id_missing(self) -> None:
        out = subprocess.run(
            [
                *cli_command("close-branch"),
                "--root",
                str(self.root),
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["changed"])
        self.assertIsNone(payload["id"])
        self.assertIn("--id is required", payload["error"])

    def test_set_focus_updates_current_state(self) -> None:
        set_focus(
            self.root,
            stage="stage_text",
            problem="problem_text",
            option="option_t5",
            path=["stage_text", "problem_text", "option_t5"],
            hypothesis="T5 helps.",
            open_risks=["Need cache parity"],
            next_actions=["Run ablation"],
        )

        current = load_yaml(self.root / "current_state.yaml")

        self.assertEqual(current["current_stage"], "stage_text")
        self.assertEqual(current["current_hypothesis"], "T5 helps.")
        self.assertEqual(current["next_actions"], ["Run ablation"])

    def test_set_focus_updates_focus_node_and_rebuilds_dashboard(self) -> None:
        set_focus(
            self.root,
            stage="stage_text",
            problem="problem_text",
            option="option_t5",
            focus_node="option_t5",
            path=["stage_text", "problem_text", "option_t5"],
        )

        current = load_yaml(self.root / "current_state.yaml")
        graph = json.loads((self.root / "dashboards" / "graph_view.json").read_text(encoding="utf-8"))
        focus_context = json.loads((self.root / "dashboards" / "focus_context_pack.json").read_text(encoding="utf-8"))

        self.assertEqual(current["current_focus_node"], "option_t5")
        coordinator = load_yaml(self.root / "coordinator_state.yaml")
        self.assertEqual(coordinator["selected_node"], "option_t5")
        self.assertEqual(graph["current_focus_node"], "option_t5")
        self.assertEqual(focus_context["focus_node"]["id"], "option_t5")
        interaction_log = load_yaml(self.root / "graph" / "interaction_log.yaml")
        self.assertEqual(interaction_log["events"][0]["kind"], "set_focus")
        self.assertEqual(interaction_log["events"][0]["node_id"], "option_t5")
        self.assertIn("recent_interactions", focus_context)

    def test_set_focus_derives_path_when_focus_node_is_supplied(self) -> None:
        set_focus(
            self.root,
            focus_node="exp_t5",
        )

        current = load_yaml(self.root / "current_state.yaml")
        graph = json.loads((self.root / "dashboards" / "graph_view.json").read_text(encoding="utf-8"))

        self.assertEqual(current["current_stage"], "stage_text")
        self.assertEqual(current["current_problem"], "problem_text")
        self.assertEqual(current["current_option"], "option_t5")
        self.assertEqual(current["current_focus_node"], "exp_t5")
        self.assertEqual(current["current_focus_path"], ["stage_text", "problem_text", "option_t5", "exp_t5"])
        self.assertEqual(graph["current_focus_node"], "exp_t5")

    def test_build_dashboard_writes_expected_files(self) -> None:
        paths = build_dashboard(self.root)

        expected = {
            "graph_view.json",
            "agent_context_pack.json",
            "focus_context_pack.json",
            "current_state.md",
            "current_state.json",
            "experiment_matrix.json",
            "linked_resources.json",
            "next_action_suggestions.json",
            "search_index.json",
            "decision_acceptance_checklists.json",
            "option_workstreams.json",
            "assignment_view.json",
        }

        self.assertEqual({path.name for path in paths}, expected)
        graph = json.loads((self.root / "dashboards" / "graph_view.json").read_text(encoding="utf-8"))
        context = json.loads((self.root / "dashboards" / "agent_context_pack.json").read_text(encoding="utf-8"))
        focus_context = json.loads((self.root / "dashboards" / "focus_context_pack.json").read_text(encoding="utf-8"))
        matrix = json.loads((self.root / "dashboards" / "experiment_matrix.json").read_text(encoding="utf-8"))
        links = json.loads((self.root / "dashboards" / "linked_resources.json").read_text(encoding="utf-8"))
        suggestions = json.loads((self.root / "dashboards" / "next_action_suggestions.json").read_text(encoding="utf-8"))
        search_index = json.loads((self.root / "dashboards" / "search_index.json").read_text(encoding="utf-8"))
        checklists = json.loads((self.root / "dashboards" / "decision_acceptance_checklists.json").read_text(encoding="utf-8"))
        option_workstreams = json.loads((self.root / "dashboards" / "option_workstreams.json").read_text(encoding="utf-8"))
        assignment_view = json.loads((self.root / "dashboards" / "assignment_view.json").read_text(encoding="utf-8"))
        nodes = load_nodes(self.root)

        self.assertTrue(graph["nodes"])
        self.assertNotIn("raw", graph["nodes"][0])
        self.assertIn("label", graph["nodes"][0])
        self.assertEqual(context["linked_nodes"][0]["id"], "stage_text")
        self.assertIn("suggested_next_actions", context)
        self.assertIn("search_index_summary", context)
        self.assertIn("suggested_next_actions", focus_context)
        self.assertIn("search_index_summary", focus_context)
        self.assertIn("resource_count", context["search_index_summary"])
        self.assertIn("resource_skipped_count", focus_context["search_index_summary"])
        self.assertEqual(focus_context["focus_node"]["id"], "problem_text")
        self.assertEqual(matrix[0]["id"], "exp_t5")
        self.assertIsInstance(links, list)
        self.assertIsInstance(suggestions, list)
        self.assertIsInstance(search_index, list)
        self.assertIsInstance(checklists, list)
        self.assertIsInstance(option_workstreams, list)
        self.assertIn("assignments", assignment_view)
        self.assertIn("active_option_workstreams", context)
        self.assertIn("assignment_view", context)
        self.assertIn("option_workstream_context", focus_context)
        self.assertIn("saved_graph_views", context)
        self.assertIn("saved_graph_views", focus_context)
        self.assertIn("stage_text", nodes)
        self.assertIn("metadata", context)
        self.assertIn("metadata", focus_context)
        self.assertIsInstance(context["metadata"]["worktree_dirty"], bool)

    def test_build_dashboard_reuses_read_models_in_context_packs(self) -> None:
        import research_cockpit.commands.build_dashboard as build_dashboard_module

        with (
            patch(
                "research_cockpit.commands.build_dashboard.build_link_rows",
                wraps=build_dashboard_module.build_link_rows,
            ) as dashboard_link_rows,
            patch(
                "research_cockpit.commands.build_dashboard.build_action_suggestions",
                wraps=build_dashboard_module.build_action_suggestions,
            ) as dashboard_action_suggestions,
            patch(
                "research_cockpit.commands.build_dashboard.build_search_index",
                wraps=build_dashboard_module.build_search_index,
            ) as dashboard_search_index,
            patch(
                "research_cockpit.commands.build_dashboard.build_option_workstream_rows",
                wraps=build_dashboard_module.build_option_workstream_rows,
            ) as dashboard_option_workstreams,
            patch(
                "research_cockpit.commands.build_dashboard.build_assignment_view",
                wraps=build_dashboard_module.build_assignment_view,
            ) as dashboard_assignment_view,
            patch(
                "research_cockpit.commands.build_dashboard.GraphTopology.from_nodes",
                wraps=build_dashboard_module.GraphTopology.from_nodes,
            ) as dashboard_topology,
            patch("research_cockpit.context_packs.build_link_rows", side_effect=AssertionError("duplicate link rows")),
            patch("research_cockpit.context_packs.build_search_index", side_effect=AssertionError("duplicate search index")),
            patch(
                "research_cockpit.context_packs.build_action_suggestions",
                side_effect=AssertionError("duplicate suggestions"),
            ),
            patch(
                "research_cockpit.context_packs.build_option_workstream_rows",
                side_effect=AssertionError("duplicate option workstreams"),
            ),
            patch("research_cockpit.context_packs.build_assignment_view", side_effect=AssertionError("duplicate assignments")),
            patch("research_cockpit.search_index.build_link_rows", side_effect=AssertionError("duplicate search links")),
        ):
            build_dashboard(self.root)

        context = json.loads((self.root / "dashboards" / "agent_context_pack.json").read_text(encoding="utf-8"))
        focus_context = json.loads((self.root / "dashboards" / "focus_context_pack.json").read_text(encoding="utf-8"))

        self.assertEqual(dashboard_link_rows.call_count, 1)
        self.assertEqual(dashboard_action_suggestions.call_count, 1)
        self.assertEqual(dashboard_search_index.call_count, 1)
        self.assertEqual(dashboard_option_workstreams.call_count, 1)
        self.assertEqual(dashboard_assignment_view.call_count, 1)
        self.assertEqual(dashboard_topology.call_count, 1)
        self.assertIn("search_index_summary", context)
        self.assertIn("search_index_summary", focus_context)

    def test_build_cli_profile_reports_stage_metrics_and_writes_profile_output(self) -> None:
        profile_path = self.root / "dashboards" / "build_profile.json"
        note_path = self.root / "notes" / "problems" / "problem_text.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("# Problem Note\nSearch profile note.\n", encoding="utf-8")
        resource_path = self.root / "resources" / "problem_context.txt"
        resource_path.parent.mkdir(parents=True, exist_ok=True)
        resource_path.write_text("Search profile resource.\n", encoding="utf-8")
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["links"] = {
            "notes": "notes/problems/problem_text.md",
            "context": "resources/problem_context.txt",
        }
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)

        out = subprocess.run(
            [
                *cli_command("build"),
                "--root",
                str(self.root),
                "--json",
                "--profile",
                "--profile-output",
                "dashboards/build_profile.json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        self.assertTrue(profile_path.exists())
        self.assertIn("profile", payload)
        self.assertEqual(payload["profile_output"], str(profile_path))
        profile = payload["profile"]
        written_profile = json.loads(profile_path.read_text(encoding="utf-8"))

        self.assertEqual(written_profile["schema_version"], "build_profile_v1")
        self.assertEqual(profile["schema_version"], "build_profile_v1")
        self.assertEqual(profile["counts"]["node_count"], payload["node_count"])
        self.assertGreaterEqual(profile["counts"]["edge_count"], 0)
        self.assertEqual(profile["counts"]["graph_view_raw_omitted_node_count"], payload["node_count"])
        self.assertGreater(
            profile["counts"]["graph_view_with_raw_estimated_bytes"],
            profile["counts"]["graph_view_slim_estimated_bytes"],
        )
        self.assertEqual(profile["counts"]["search_note_count"], 1)
        self.assertEqual(profile["counts"]["search_node_count"], payload["node_count"])
        self.assertGreaterEqual(profile["counts"]["search_resource_indexed_count"], 1)
        self.assertGreaterEqual(profile["counts"]["search_resource_unique_indexed_count"], 1)
        self.assertGreaterEqual(
            profile["counts"]["search_resource_total_count"],
            profile["counts"]["search_resource_indexed_count"],
        )
        self.assertGreater(profile["counts"]["search_resource_bytes_read"], 0)
        self.assertGreaterEqual(profile["total_duration_ms"], 0)
        self.assertTrue(profile["stages"])
        self.assertIn("load_nodes", {stage["name"] for stage in profile["stages"]})
        self.assertIn("build_search_index", {stage["name"] for stage in profile["stages"]})
        self.assertIn("write_outputs", {stage["name"] for stage in profile["stages"]})
        self.assertTrue(all(stage["duration_ms"] >= 0 for stage in profile["stages"]))
        output_files = {item["path"]: item["bytes"] for item in profile["output_files"]}
        self.assertGreater(output_files["dashboards/graph_view.json"], 0)
        self.assertGreater(output_files["dashboards/agent_context_pack.json"], 0)

    def test_build_cli_profile_output_rejects_unsafe_paths(self) -> None:
        unsafe_paths = {
            "../profile.json": "inside <root>/dashboards",
            "current_state.yaml": "inside <root>/dashboards",
            "notes/profile.json": "inside <root>/dashboards",
            "dashboards/profile.txt": ".json file",
            "dashboards/graph_view.json": "must not overwrite standard dashboard outputs",
        }

        for profile_output, expected_error in unsafe_paths.items():
            with self.subTest(profile_output=profile_output):
                out = subprocess.run(
                    [
                        *cli_command("build"),
                        "--root",
                        str(self.root),
                        "--json",
                        "--profile-output",
                        profile_output,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
                self.assertIn(expected_error, out.stdout + out.stderr)

        self.assertIsInstance(load_yaml(self.root / "current_state.yaml"), dict)
        self.assertFalse((self.root / "dashboards").exists())

    def test_build_cli_can_skip_resource_search_text_reads(self) -> None:
        note_path = self.root / "notes" / "problems" / "problem_text.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("# Problem Note\nNote search stays enabled.\n", encoding="utf-8")
        resource_path = self.root / "resources" / "problem_context.txt"
        resource_path.parent.mkdir(parents=True, exist_ok=True)
        resource_path.write_text("Resource search should be skipped.\n", encoding="utf-8")
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["links"] = {
            "notes": "notes/problems/problem_text.md",
            "context": "resources/problem_context.txt",
        }
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)

        out = subprocess.run(
            [
                *cli_command("build"),
                "--root",
                str(self.root),
                "--json",
                "--profile",
                "--skip-resource-search",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        profile = payload["profile"]
        search_index = json.loads((self.root / "dashboards" / "search_index.json").read_text(encoding="utf-8"))
        resource_entry = next(item for item in search_index if item.get("path") == "resources/problem_context.txt")
        note_entry = next(item for item in search_index if item.get("entry_id") == "note:notes/problems/problem_text.md")

        self.assertEqual(resource_entry["skip_reason"], "resource_search_disabled")
        self.assertEqual(resource_entry["bytes_read"], 0)
        self.assertIn("Note search stays enabled", note_entry["text"])
        self.assertEqual(profile["counts"]["search_resource_text_enabled"], 0)
        self.assertEqual(profile["counts"]["search_resource_indexed_count"], 0)
        self.assertEqual(profile["counts"]["search_resource_unique_indexed_count"], 0)
        self.assertEqual(profile["counts"]["search_resource_bytes_read"], 0)
        self.assertGreaterEqual(profile["counts"]["search_resource_disabled_count"], 1)

    def test_build_resource_scan_limits_summarize_dirs_and_skip_generated_payloads(self) -> None:
        artifact_dir = self.root / "artifacts" / "heavy_payload"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "summary.md").write_text("# Summary\nSearchable summary text.\n", encoding="utf-8")
        (artifact_dir / "audio.wav").write_bytes(b"Searchable raw audio should not be indexed.")
        (artifact_dir / "checkpoint.pt").write_bytes(b"Searchable checkpoint should not be indexed.")
        cache_dir = self.root / "artifacts" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "summary.md").write_text("Cache summary should not be indexed.\n", encoding="utf-8")
        write_node(
            self.root,
            {
                "id": "artifact_heavy_payload",
                "type": "artifact",
                "title": "Heavy payload",
                "status": "done",
                "path": "artifacts/heavy_payload",
            },
        )
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["linked_artifacts"] = ["artifact_heavy_payload"]
        problem["links"] = {
            "raw_audio": "artifacts/heavy_payload/audio.wav",
            "checkpoint": "artifacts/heavy_payload/checkpoint.pt",
            "cache_dir": "artifacts/cache",
        }
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)

        out = subprocess.run(
            [
                *cli_command("build"),
                "--root",
                str(self.root),
                "--json",
                "--profile",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        profile = payload["profile"]
        search_index = json.loads((self.root / "dashboards" / "search_index.json").read_text(encoding="utf-8"))
        by_path = {item.get("path"): item for item in search_index if item.get("source") == "resource"}

        self.assertIn("resource_scan_settings", profile)
        self.assertIn("max_files_per_artifact", profile["resource_scan_settings"])
        self.assertIn("max_bytes_per_artifact", profile["resource_scan_settings"])
        self.assertIn("skip_patterns", profile["resource_scan_settings"])
        self.assertIn("summary_files", profile["resource_scan_settings"])
        self.assertIn("Searchable summary text", by_path["artifacts/heavy_payload"]["text"])
        self.assertEqual(by_path["artifacts/heavy_payload"]["text"].count("Searchable summary text"), 1)
        self.assertEqual(by_path["artifacts/heavy_payload"]["scan_kind"], "directory_summary")
        self.assertEqual(by_path["artifacts/heavy_payload"]["scan_file_count"], 1)
        self.assertEqual(by_path["artifacts/heavy_payload/audio.wav"]["skip_reason"], "resource_scan_skip_pattern")
        self.assertEqual(by_path["artifacts/heavy_payload/checkpoint.pt"]["skip_reason"], "resource_scan_skip_pattern")
        self.assertEqual(by_path["artifacts/cache"]["skip_reason"], "resource_scan_skip_pattern")
        self.assertNotIn("raw audio should not be indexed", json.dumps(search_index))
        self.assertNotIn("checkpoint should not be indexed", json.dumps(search_index))
        self.assertNotIn("Cache summary should not be indexed", json.dumps(search_index))
        self.assertGreaterEqual(profile["counts"]["search_resource_directory_summary_count"], 1)
        self.assertGreaterEqual(profile["counts"]["search_resource_scan_skip_pattern_count"], 3)
        self.assertTrue(any(warning["code"] == "resource_scan_skipped_payload" for warning in profile["warnings"]))

    def test_generate_large_cockpit_fixture_cli_creates_valid_profile_fixture(self) -> None:
        fixture_root = self.tmp_root / "perf_fixture"

        out = self.generate_large_fixture(
            fixture_root,
            "--nodes",
            "24",
            "--links-per-node",
            "2",
            "--note-count",
            "3",
            "--resource-count",
            "4",
            "--json",
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        self.assertEqual(payload["node_count"], 24)
        self.assertEqual(payload["note_count"], 3)
        self.assertEqual(payload["resource_count"], 4)

        nodes = load_nodes(fixture_root)
        self.assertEqual(len(nodes), 24)
        self.assertIn("stage_perf_0000", nodes)
        self.assertIn("artifact_perf_0000", nodes)
        self.assertEqual(nodes["option_perf_0001"].parent, "problem_perf_0001")
        self.assertEqual(nodes["problem_perf_0001"].parent, "option_perf_0000")
        self.assertEqual(len(list((fixture_root / "notes").glob("*.md"))), 3)
        self.assertEqual(len(list((fixture_root / "artifacts" / "search_resources").glob("*.md"))), 4)
        self.assertTrue((fixture_root / ".synthetic_research_cockpit_fixture.json").exists())

        for node in nodes.values():
            for child_id in node.children:
                self.assertEqual(nodes[child_id].parent, node.id)
            if node.parent:
                self.assertIn(node.id, nodes[node.parent].children)

        note_links = sorted(
            node.raw.get("links", {}).get("notes")
            for node in nodes.values()
            if node.raw.get("links", {}).get("notes")
        )
        self.assertEqual(note_links, [f"notes/note_{index:04d}.md" for index in range(3)])
        linked_resource_targets = {
            row["target"]
            for row in build_link_rows(fixture_root, nodes)
            if str(row["target"]).startswith("artifacts/search_resources/resource_")
        }
        self.assertEqual(
            {_resource for _resource in linked_resource_targets},
            {f"artifacts/search_resources/resource_{index:04d}.md" for index in range(4)},
        )

        validate = subprocess.run(
            [*cli_command("validate"), "--root", str(fixture_root), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(validate.returncode, 0, validate.stdout + validate.stderr)

        build = subprocess.run(
            [*cli_command("build"), "--root", str(fixture_root), "--json", "--profile"],
            capture_output=True,
            text=True,
            check=False,
        )
        build_payload = json.loads(build.stdout)
        self.assertEqual(build.returncode, 0, build.stderr or build.stdout)
        self.assertEqual(build_payload["profile"]["counts"]["node_count"], 24)
        self.assertEqual(build_payload["profile"]["counts"]["linked_resource_count"], payload["linked_resource_count"])
        search_index = json.loads((fixture_root / "dashboards" / "search_index.json").read_text(encoding="utf-8"))
        resource_entries = {
            entry["path"]
            for entry in search_index
            if entry.get("source") == "resource"
            and str(entry.get("path", "")).startswith("artifacts/search_resources/resource_")
        }
        self.assertEqual(
            resource_entries,
            {f"artifacts/search_resources/resource_{index:04d}.md" for index in range(4)},
        )

        smoke = subprocess.run(
            [*cli_command("smoke"), "--root", str(fixture_root), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        smoke_payload = json.loads(smoke.stdout)
        self.assertEqual(smoke.returncode, 0, smoke.stdout + smoke.stderr)
        self.assertTrue(smoke_payload["ok"])

        second_root = self.tmp_root / "perf_fixture_second"
        second = self.generate_large_fixture(
            second_root,
            "--nodes",
            "24",
            "--links-per-node",
            "2",
            "--note-count",
            "3",
            "--resource-count",
            "4",
            "--json",
        )
        self.assertEqual(second.returncode, 0, second.stderr or second.stdout)
        for relative_path in (
            "current_state.yaml",
            "graph/nodes/experiment_perf_0000.yaml",
            "graph/nodes/option_perf_0001.yaml",
            "artifacts/search_resources/resource_0000.md",
            "notes/note_0000.md",
        ):
            self.assertEqual(
                (fixture_root / relative_path).read_text(encoding="utf-8"),
                (second_root / relative_path).read_text(encoding="utf-8"),
                relative_path,
            )

    def test_generate_large_cockpit_fixture_force_only_replaces_marked_fixture(self) -> None:
        unsafe_root = self.tmp_root / "not_a_fixture"
        unsafe_root.mkdir()
        keep_path = unsafe_root / "keep.txt"
        keep_path.write_text("do not delete", encoding="utf-8")

        unsafe = self.generate_large_fixture(
            unsafe_root,
            "--nodes",
            "5",
            "--note-count",
            "1",
            "--resource-count",
            "1",
            "--force",
            "--json",
        )
        unsafe_payload = json.loads(unsafe.stdout)
        self.assertNotEqual(unsafe.returncode, 0, unsafe.stdout + unsafe.stderr)
        self.assertFalse(unsafe_payload["ok"])
        self.assertIn("non-synthetic fixture root", unsafe_payload["error"])
        self.assertTrue(keep_path.exists())

        fixture_root = self.tmp_root / "replaceable_fixture"
        first = self.generate_large_fixture(
            fixture_root,
            "--nodes",
            "5",
            "--note-count",
            "1",
            "--resource-count",
            "1",
            "--json",
        )
        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
        stale_path = fixture_root / "stale.txt"
        stale_path.write_text("replace me", encoding="utf-8")

        second = self.generate_large_fixture(
            fixture_root,
            "--nodes",
            "6",
            "--note-count",
            "1",
            "--resource-count",
            "1",
            "--force",
            "--json",
        )
        second_payload = json.loads(second.stdout)
        self.assertEqual(second.returncode, 0, second.stderr or second.stdout)
        self.assertEqual(second_payload["node_count"], 6)
        self.assertFalse(stale_path.exists())
        self.assertTrue((fixture_root / ".synthetic_research_cockpit_fixture.json").exists())

    def test_benchmark_build_cli_reports_profile_statistics(self) -> None:
        fixture_root = self.tmp_root / "benchmark_fixture"

        generated = self.generate_large_fixture(
            fixture_root,
            "--nodes",
            "12",
            "--links-per-node",
            "1",
            "--note-count",
            "2",
            "--resource-count",
            "2",
            "--json",
        )
        self.assertEqual(generated.returncode, 0, generated.stderr or generated.stdout)

        out = self.run_dev_script(
            "benchmark_build.py",
            "--root",
            str(fixture_root),
            "--runs",
            "2",
            "--json",
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema_version"], "benchmark_build_v1")
        self.assertEqual(payload["run_count"], 2)
        self.assertTrue(payload["include_resource_search"])
        self.assertEqual(len(payload["runs"]), 2)
        self.assertEqual(payload["summary"]["counts"]["node_count"], 12)
        total = payload["summary"]["total_duration_ms"]
        self.assertGreaterEqual(total["min"], 0)
        self.assertGreaterEqual(total["median"], total["min"])
        self.assertGreaterEqual(total["max"], total["median"])
        self.assertIn("load_nodes", payload["summary"]["stages"])
        self.assertIn("write_outputs", payload["summary"]["stages"])
        for stats in payload["summary"]["stages"].values():
            self.assertEqual(set(stats), {"min", "median", "max"})
        for run in payload["runs"]:
            self.assertIn("index", run)
            self.assertIn("total_duration_ms", run)
            self.assertIsInstance(run["stages"], list)
        self.assertTrue((fixture_root / "dashboards" / "build_profile.json").exists())

        light = self.run_dev_script(
            "benchmark_build.py",
            "--root",
            str(fixture_root),
            "--runs",
            "1",
            "--skip-resource-search",
            "--json",
        )
        light_payload = json.loads(light.stdout)

        self.assertEqual(light.returncode, 0, light.stderr or light.stdout)
        self.assertFalse(light_payload["include_resource_search"])
        self.assertEqual(light_payload["summary"]["counts"]["search_resource_text_enabled"], 0)
        self.assertGreaterEqual(light_payload["summary"]["counts"]["search_resource_disabled_count"], 1)

    def test_benchmark_build_cli_rejects_zero_runs(self) -> None:
        out = self.run_dev_script(
            "benchmark_build.py",
            "--root",
            str(self.root),
            "--runs",
            "0",
            "--json",
        )
        payload = json.loads(out.stdout)

        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertFalse(payload["ok"])
        self.assertIn("--runs must be at least 1", payload["error"])

    def test_dashboard_splits_next_actions_by_scope(self) -> None:
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "exp_t5"
        current["current_focus_path"] = ["stage_text", "problem_text", "option_t5", "exp_t5"]
        current["next_actions"] = ["Coordinator: assign GPU window."]
        save_yaml(self.root / "current_state.yaml", current)

        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["next_actions"] = ["Clarify failure mode."]
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["children"] = ["exp_t5", "exp_done"]
        option["next_actions"] = ["Compare T5 branch budget."]
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["next_actions"] = ["Run focused smoke."]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        write_node(
            self.root,
            {
                "id": "exp_done",
                "type": "experiment",
                "title": "Completed branch",
                "status": "done",
                "parent": "option_t5",
                "next_actions": ["Move completed follow-up."],
            },
        )

        build_dashboard(self.root)

        agent_context = json.loads((self.root / "dashboards" / "agent_context_pack.json").read_text(encoding="utf-8"))
        focus_context = json.loads((self.root / "dashboards" / "focus_context_pack.json").read_text(encoding="utf-8"))
        current_payload = json.loads((self.root / "dashboards" / "current_state.json").read_text(encoding="utf-8"))
        node_payload = node_context_payload(self.root, node_id="exp_t5", compact=True)
        combined_payload = context_payload(self.root, node_id="exp_t5", compact=True)

        for payload in (agent_context, focus_context, current_payload, node_payload):
            scopes = payload["next_action_scopes"]
            self.assertEqual([item["action"] for item in scopes["focus_next_actions"]], ["Run focused smoke."])
            self.assertEqual(
                [item["action"] for item in scopes["parent_option_next_actions"]],
                ["Compare T5 branch budget."],
            )
            self.assertEqual(
                [item["action"] for item in scopes["parent_problem_next_actions"]],
                ["Clarify failure mode."],
            )
            self.assertEqual(
                [item["action"] for item in scopes["global_coordinator_next_actions"]],
                ["Coordinator: assign GPU window."],
            )
            self.assertEqual(scopes["stale_terminal_node_next_actions"][0]["node_id"], "exp_done")
            self.assertTrue(scopes["stale_terminal_node_next_actions"][0]["stale"])
            self.assertTrue(scopes["stale_terminal_node_next_actions"][0]["migration_candidate"])

        self.assertEqual(agent_context["next_actions"], ["Coordinator: assign GPU window."])
        self.assertEqual(
            focus_context["next_actions"],
            ["Coordinator: assign GPU window.", "Run focused smoke."],
        )
        self.assertIn("next_action_scopes", combined_payload["current_global_focus"])
        self.assertIn("next_action_scopes", combined_payload["node_context"])

    def test_dashboard_next_action_scopes_keep_global_only_actions_separate(self) -> None:
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "exp_t5"
        current["current_focus_path"] = ["stage_text", "problem_text", "option_t5", "exp_t5"]
        current["next_actions"] = ["Coordinator-only handoff."]
        save_yaml(self.root / "current_state.yaml", current)

        context = build_agent_context(self.root, load_nodes(self.root))

        scopes = context["next_action_scopes"]
        self.assertEqual(scopes["focus_next_actions"], [])
        self.assertEqual(scopes["parent_option_next_actions"], [])
        self.assertEqual(scopes["parent_problem_next_actions"], [])
        self.assertEqual(
            [item["action"] for item in scopes["global_coordinator_next_actions"]],
            ["Coordinator-only handoff."],
        )

    def test_next_action_scopes_mark_terminal_focus_and_parent_actions_stale(self) -> None:
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "exp_t5"
        current["current_focus_path"] = ["stage_text", "problem_text", "option_t5", "exp_t5"]
        save_yaml(self.root / "current_state.yaml", current)

        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["status"] = "resolved"
        problem["next_actions"] = ["Move resolved problem follow-up."]
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["status"] = "accepted"
        option["next_actions"] = ["Move accepted option follow-up."]
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["next_actions"] = ["Move done experiment follow-up."]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)

        scopes = build_agent_context(self.root, load_nodes(self.root))["next_action_scopes"]

        for key in ("focus_next_actions", "parent_option_next_actions", "parent_problem_next_actions"):
            self.assertTrue(scopes[key][0]["is_terminal"])
            self.assertTrue(scopes[key][0]["stale"])
            self.assertTrue(scopes[key][0]["migration_candidate"])
        stale_ids = {item["node_id"] for item in scopes["stale_terminal_node_next_actions"]}
        self.assertEqual(stale_ids, {"exp_t5", "option_t5", "problem_text"})
        for item in scopes["stale_terminal_node_next_actions"]:
            self.assertEqual(
                item["reason"].count("Terminal nodes should keep conclusions"),
                1,
            )

    def test_build_cli_json_reports_generated_files(self) -> None:
        out = subprocess.run(
            [*cli_command("build"), "--root", str(self.root), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["node_count"], 4)
        self.assertEqual(len(payload["written_files"]), 12)
        self.assertTrue((self.root / "dashboards" / "graph_view.json").exists())

    def test_build_watch_json_max_iterations_reports_one_iteration(self) -> None:
        out = subprocess.run(
            [
                *cli_command("build"),
                "--root",
                str(self.root),
                "--watch",
                "--interval",
                "0",
                "--max-iterations",
                "1",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        payload = json.loads(out.stdout.splitlines()[0])
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["watch"])
        self.assertEqual(payload["iteration"], 1)
        self.assertTrue(payload["truth_source_changed"])
        self.assertTrue(payload["build_attempted"])
        self.assertEqual(payload["last_build_status"], "success")
        self.assertTrue(payload["last_build_at"])
        self.assertEqual(payload["last_build_error"], "")
        self.assertEqual(len(payload["written_files"]), 12)

    def test_dashboard_watch_signature_changes_when_run_becomes_stale(self) -> None:
        save_yaml(
            self.root / "runs" / "run_watch.yaml",
            {
                "run_id": "run_watch",
                "status": "running",
                "experiment_id": "exp_t5",
                "started_at": "2026-05-27T00:00:00Z",
            },
        )

        fresh = dashboard_watch_signature(
            self.root,
            now=datetime(2026, 5, 27, 23, 0, tzinfo=timezone.utc),
        )
        stale = dashboard_watch_signature(
            self.root,
            now=datetime(2026, 5, 28, 1, 0, tzinfo=timezone.utc),
        )

        self.assertNotEqual(fresh, stale)
        self.assertEqual(fresh[1], (("run_watch", "running", False),))
        self.assertEqual(stale[1], (("run_watch", "running", True),))

    def test_dashboard_watch_signature_tracks_progress_file_state(self) -> None:
        progress_path = self.root / "artifacts" / "exp_t5" / "run_watch_progress" / "progress.json"
        progress_path.parent.mkdir(parents=True)
        progress_path.write_text(
            json.dumps(
                {
                    "status": "running",
                    "completed_steps": 1,
                    "total_steps": 4,
                    "last_update": "2026-05-27T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        save_yaml(
            self.root / "runs" / "run_watch_progress.yaml",
            {
                "run_id": "run_watch_progress",
                "status": "running",
                "experiment_id": "exp_t5",
                "started_at": "2026-05-27T00:00:00Z",
                "progress_file": "artifacts/exp_t5/run_watch_progress/progress.json",
            },
        )
        save_yaml(
            self.root / "runs" / "run_watch_progress_done.yaml",
            {
                "run_id": "run_watch_progress_done",
                "status": "completed",
                "experiment_id": "exp_t5",
                "started_at": "2026-05-27T00:00:00Z",
                "finished_at": "2026-05-27T00:10:00Z",
                "progress_file": "artifacts/exp_t5/run_watch_progress/progress.json",
            },
        )
        save_yaml(
            self.root / "runs" / "run_watch_progress_missing.yaml",
            {
                "run_id": "run_watch_progress_missing",
                "status": "running",
                "experiment_id": "exp_t5",
                "started_at": "2026-05-27T00:00:00Z",
                "progress_file": "artifacts/exp_t5/run_watch_progress_missing/progress.json",
            },
        )

        fresh = dashboard_watch_signature(
            self.root,
            now=datetime(2026, 5, 27, 0, 30, tzinfo=timezone.utc),
        )
        stale = dashboard_watch_signature(
            self.root,
            now=datetime(2026, 5, 27, 2, 0, tzinfo=timezone.utc),
        )

        self.assertNotEqual(fresh, stale)
        fresh_progress = {run_id: signature for run_id, signature in fresh[2]}
        stale_progress = {run_id: signature for run_id, signature in stale[2]}
        self.assertFalse(fresh_progress["run_watch_progress"][-1])
        self.assertTrue(stale_progress["run_watch_progress"][-1])
        self.assertFalse(fresh_progress["run_watch_progress_done"][-1])
        self.assertFalse(stale_progress["run_watch_progress_done"][-1])
        self.assertEqual(
            fresh_progress["run_watch_progress_missing"],
            ("artifacts/exp_t5/run_watch_progress_missing/progress.json", "missing"),
        )

    def test_dashboard_watch_json_marks_time_sensitive_rebuilds(self) -> None:
        signatures = [
            (("truth",), (("run_watch", "running", False),), ()),
            (("truth",), (("run_watch", "running", False),), ()),
            (("truth",), (("run_watch", "running", True),), ()),
            (("truth",), (("run_watch", "running", True),), ()),
        ]
        with (
            patch("research_cockpit.commands.build_dashboard.dashboard_watch_signature", side_effect=signatures),
            patch(
                "research_cockpit.commands.build_dashboard.build_dashboard_once",
                return_value={
                    "ok": True,
                    "root": str(self.root),
                    "node_count": 0,
                    "written_files": ["dashboards/agent_context_pack.json"],
                    "json": True,
                },
            ),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            watch_dashboard(self.root, interval=0, max_iterations=2, json_output=True)

        events = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertTrue(events[0]["truth_source_changed"])
        self.assertFalse(events[0]["time_sensitive_changed"])
        self.assertFalse(events[1]["truth_source_changed"])
        self.assertTrue(events[1]["time_sensitive_changed"])
        self.assertEqual(events[1]["last_build_status"], "success")

    def test_dashboard_watch_can_write_profile_output(self) -> None:
        profile_path = self.root / "dashboards" / "watch_profile.json"

        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            watch_dashboard(
                self.root,
                interval=0,
                max_iterations=1,
                json_output=True,
                profile=True,
                profile_output=Path("dashboards/watch_profile.json"),
            )

        payload = json.loads(stdout.getvalue().splitlines()[0])
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["build_attempted"])
        self.assertEqual(payload["profile_output"], str(profile_path))
        self.assertEqual(payload["profile"]["schema_version"], "build_profile_v1")
        self.assertEqual(profile["schema_version"], "build_profile_v1")

    def test_dashboard_watch_json_reports_build_failure_without_crashing(self) -> None:
        with (
            patch("research_cockpit.commands.build_dashboard.dashboard_watch_signature", return_value=(("truth",), (), (), ())),
            patch("research_cockpit.commands.build_dashboard.build_dashboard_once", side_effect=ValueError("bad graph")),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            watch_dashboard(self.root, interval=0, max_iterations=1, json_output=True)

        payload = json.loads(stdout.getvalue().splitlines()[0])
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["build_attempted"])
        self.assertEqual(payload["last_build_status"], "failed")
        self.assertTrue(payload["last_build_at"])
        self.assertIn("bad graph", payload["last_build_error"])
        self.assertIn("bad graph", payload["error"])
        self.assertEqual(payload["written_files"], [])

    def test_dashboard_watch_json_reports_signature_failure_without_crashing(self) -> None:
        with (
            patch("research_cockpit.commands.build_dashboard.dashboard_watch_signature", side_effect=FileNotFoundError("race")),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            watch_dashboard(self.root, interval=0, max_iterations=1, json_output=True)

        payload = json.loads(stdout.getvalue().splitlines()[0])
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["build_attempted"])
        self.assertEqual(payload["last_build_status"], "failed")
        self.assertTrue(payload["last_build_at"])
        self.assertIn("race", payload["last_build_error"])
        self.assertIn("race", payload["error"])
        self.assertEqual(payload["written_files"], [])

    def test_build_cli_respects_root_argument_over_environment(self) -> None:
        env_root = self.tmp_root / "env_research_cockpit"
        shutil.copytree(self.root, env_root)
        env = os.environ.copy()
        env["RESEARCH_COCKPIT_ROOT"] = str(env_root)

        out = subprocess.run(
            [*cli_command("build"), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        self.assertTrue((self.root / "dashboards" / "agent_context_pack.json").exists())
        self.assertFalse((env_root / "dashboards").exists())

    def test_upsert_graph_view_rejects_malformed_interaction_log_without_write(self) -> None:
        write_malformed_interaction_log(self.root)

        with self.assertRaises(MutationError):
            upsert_graph_view(self.root, {"title": "Broken log view"})

        self.assertFalse((self.root / "graph" / "graph_views.yaml").exists())

    def test_mutation_lock_timeout_reports_owner_metadata(self) -> None:
        with mutation_lock(self.root):
            with self.assertRaises(MutationError) as ctx:
                with mutation_lock(self.root, timeout_seconds=0.01):
                    pass

        payload = ctx.exception.payload
        self.assertEqual(payload["lock_path"], str(self.root / "graph" / ".mutation.lock"))
        self.assertEqual(payload["owner_pid"], str(os.getpid()))
        self.assertIn("created_at", payload)
        self.assertGreaterEqual(payload["waited_seconds"], 0)
        self.assertIn("error", payload)

    def test_finish_mutation_rejects_stale_yaml_before_without_write(self) -> None:
        current_path = self.root / "current_state.yaml"
        before = load_yaml(current_path)
        stale_after = dict(before)
        stale_after["next_actions"] = ["stale write"]
        newer_current = dict(before)
        newer_current["current_hypothesis"] = "newer concurrent write"
        save_yaml(current_path, newer_current)

        with self.assertRaises(MutationError) as ctx:
            finish_mutation(
                self.root,
                [(current_path, before, stale_after)],
                interaction={"kind": "test_stale_conflict"},
                rebuild_dashboard=False,
            )

        payload = ctx.exception.payload
        self.assertFalse(payload["partial_success"])
        self.assertFalse(payload["rolled_back"])
        self.assertEqual(payload["conflict_files"], [str(current_path)])
        self.assertEqual(load_yaml(current_path), newer_current)

    def test_parallel_complete_experiment_writes_different_nodes_serially(self) -> None:
        option_path = self.root / "graph" / "nodes" / "option_t5.yaml"
        option = load_yaml(option_path)
        option["children"] = [*option.get("children", []), "exp_t6"]
        save_yaml(option_path, option)
        write_node(
            self.root,
            {
                "id": "exp_t6",
                "type": "experiment",
                "title": "T6 ablation",
                "status": "planned",
                "parent": "option_t5",
            },
        )
        commands = [
            [
                *cli_command("complete-experiment"),
                "--root",
                str(self.root),
                "--id",
                "exp_t5",
                "--finding",
                "T5 completed.",
                "--confidence",
                "medium",
                "--no-build",
                "--json",
            ],
            [
                *cli_command("complete-experiment"),
                "--root",
                str(self.root),
                "--id",
                "exp_t6",
                "--finding",
                "T6 completed.",
                "--confidence",
                "medium",
                "--no-build",
                "--json",
            ],
        ]

        procs = [
            subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for command in commands
        ]
        results = [proc.communicate(timeout=20) for proc in procs]

        for proc, (stdout, stderr) in zip(procs, results):
            self.assertEqual(proc.returncode, 0, stderr or stdout)
        self.assertEqual(load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")["status"], "done")
        self.assertEqual(load_yaml(self.root / "graph" / "nodes" / "exp_t6.yaml")["status"], "done")
        events = interaction_events(self.root)
        self.assertEqual(len(events), 2)
        self.assertTrue(all(isinstance(event, dict) for event in events))

    def test_cli_delegates_subcommand_help(self) -> None:
        out = subprocess.run(
            [*cli_command("claim-option"), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        self.assertIn("research-cockpit claim-option", out.stdout)
        self.assertIn("--option", out.stdout)
        self.assertIn("--agent", out.stdout)

    def test_claim_option_writes_workstream_and_rebuilds_dashboard(self) -> None:
        claim_option(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Evaluate T5 path",
        )

        data = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        workstreams = json.loads((self.root / "dashboards" / "option_workstreams.json").read_text(encoding="utf-8"))

        self.assertEqual(data["agent_workstream"]["owner"], "agent_t5")
        self.assertEqual(data["agent_workstream"]["status"], "claimed")
        self.assertEqual(data["agent_workstream"]["objective"], "Evaluate T5 path")
        self.assertEqual(data["agent_workstream"]["report_to_problem"], "problem_text")
        self.assertEqual(workstreams[0]["owner"], "agent_t5")
        event = interaction_events(self.root)[-1]
        self.assertEqual(event["kind"], "claim_option")
        self.assertEqual(event["actor"], "agent_t5")
        self.assertEqual(event["node_id"], "option_t5")
        self.assertEqual(event["option_id"], "option_t5")
        self.assertEqual(event["agent_id"], "agent_t5")
        self.assertEqual(event["after"]["agent_workstream"]["status"], "claimed")
        self.assertIsNone(event["before"]["agent_workstream"])

    def test_claim_option_rejects_other_active_owner_and_force_overrides(self) -> None:
        claim_option(self.root, option_id="option_t5", agent_id="agent_a", rebuild_dashboard=False)

        with self.assertRaises(ValueError) as ctx:
            claim_option(self.root, option_id="option_t5", agent_id="agent_b", rebuild_dashboard=False)

        self.assertIn("already claimed", str(ctx.exception))

        claim_option(self.root, option_id="option_t5", agent_id="agent_b", force=True, rebuild_dashboard=False)
        data = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        self.assertEqual(data["agent_workstream"]["owner"], "agent_b")

    def test_claim_option_cli_dry_run_json_previews_without_writing(self) -> None:
        command = "claim-option"
        option_path = self.root / "graph" / "nodes" / "option_t5.yaml"
        before = option_path.read_text(encoding="utf-8")

        out = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--option",
                "option_t5",
                "--agent",
                "agent_t5",
                "--objective",
                "Evaluate T5 path",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after = option_path.read_text(encoding="utf-8")

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["option_id"], "option_t5")
        self.assertEqual(payload["agent_id"], "agent_t5")
        self.assertIsNone(payload["before"]["agent_workstream"])
        self.assertEqual(payload["after"]["agent_workstream"]["owner"], "agent_t5")
        self.assertEqual(payload["after"]["agent_workstream"]["status"], "claimed")
        self.assertEqual(before, after)
        self.assertFalse((self.root / "graph" / "interaction_log.yaml").exists())
        self.assertFalse((self.root / "dashboards").exists())

    def test_claim_option_cli_dry_run_conflict_does_not_write(self) -> None:
        command = "claim-option"
        option_path = self.root / "graph" / "nodes" / "option_t5.yaml"
        claim_option(self.root, option_id="option_t5", agent_id="agent_a", rebuild_dashboard=False)
        before = option_path.read_text(encoding="utf-8")
        before_event_count = len(interaction_events(self.root))

        out = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--option",
                "option_t5",
                "--agent",
                "agent_b",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after = option_path.read_text(encoding="utf-8")

        self.assertEqual(out.returncode, 1)
        self.assertIn("already claimed", out.stdout)
        self.assertEqual(before, after)
        self.assertEqual(len(interaction_events(self.root)), before_event_count)

    def test_start_agent_session_dry_run_outputs_handoff_without_writing(self) -> None:
        worktree = self.tmp_root / "worktrees" / "agent_t5"
        option_path = self.root / "graph" / "nodes" / "option_t5.yaml"
        before = option_path.read_text(encoding="utf-8")

        payload = start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=worktree,
            base="main",
            dry_run=True,
            show_diff=True,
        )

        self.assertTrue(payload["dry_run"])
        self.assertTrue(payload["preflight_ok"])
        self.assertEqual(payload["session_id"], "session_agent_t5_option_t5")
        self.assertEqual(payload["root_boundary"]["required_root"], str(self.root.resolve()))
        self.assertTrue(payload["root_boundary"]["do_not_mutate_worktree_root"])
        self.assertEqual(payload["handoff"]["launch_env"]["RESEARCH_COCKPIT_ROOT"], str(self.root.resolve()))
        self.assertEqual(payload["handoff"]["stable_artifact_root"], str((self.root / "artifacts").resolve()))
        self.assertIn("ingest-artifact", payload["handoff"]["commands"]["ingest_artifact"])
        self.assertTrue(any("worktree paths" in item for item in payload["handoff"]["guardrails"]))
        self.assertIn("git", payload["git_command"][0])
        self.assertIn("diff", payload)
        self.assertEqual(before, option_path.read_text(encoding="utf-8"))
        self.assertFalse((self.root / "agents").exists())
        self.assertFalse((self.root / "assignments").exists())
        self.assertFalse((self.root / "graph" / "interaction_log.yaml").exists())

    def test_start_agent_session_resolves_relative_worktree_from_repo_root(self) -> None:
        expected_worktree = (self.root.resolve().parent / "worktrees" / "agent_t5").resolve()

        payload = start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=Path("worktrees") / "agent_t5",
            dry_run=True,
        )

        self.assertEqual(payload["root_boundary"]["worktree_path"], str(expected_worktree))
        self.assertEqual(payload["git_command"][7], str(expected_worktree))
        self.assertEqual(payload["handoff"]["worktree"], str(expected_worktree))

    def test_start_agent_session_sparse_dry_run_outputs_command_plan(self) -> None:
        worktree = self.tmp_root / "worktrees" / "agent_t5"

        payload = start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=worktree,
            base="main",
            dry_run=True,
            sparse=True,
            sparse_profile="ml-experiment",
        )

        sparse = payload["sparse_worktree"]
        self.assertTrue(sparse["enabled"])
        self.assertEqual(sparse["profile"], "ml-experiment")
        self.assertEqual(sparse["mode"], "manual_command_plan")
        self.assertIn("--no-checkout", payload["git_command"])
        self.assertIn("/research_cockpit/", sparse["excluded_paths"])
        self.assertIn("/outputs/", sparse["excluded_paths"])
        self.assertIn("/logs/", sparse["excluded_paths"])
        self.assertIn("/data/", sparse["excluded_paths"])
        command_argvs = [item["argv"] for item in sparse["commands"]]
        self.assertIn(["git", "-C", str(worktree.resolve()), "sparse-checkout", "init", "--no-cone"], command_argvs)
        self.assertIn(
            ["git", "-C", str(worktree.resolve()), "sparse-checkout", "set", "--no-cone", "--stdin"],
            command_argvs,
        )
        set_command = next(item for item in sparse["commands"] if "--stdin" in item["argv"])
        self.assertIn("!/research_cockpit/", set_command["stdin"])
        self.assertIn("!/outputs/", set_command["stdin"])
        self.assertTrue(any("canonical" in item for item in sparse["notes"]))

    def test_start_agent_session_sparse_requires_dry_run(self) -> None:
        worktree = self.tmp_root / "worktrees" / "agent_t5"

        with self.assertRaises(ValueError) as ctx:
            start_agent_session(
                self.root,
                option_id="option_t5",
                agent_id="agent_t5",
                objective="Run T5 branch",
                branch="agent/option_t5",
                worktree=worktree,
                sparse=True,
            )

        self.assertIn("--sparse currently provides dry-run", str(ctx.exception))

    def test_start_agent_session_reports_created_worktree_if_yaml_write_fails(self) -> None:
        worktree = self.tmp_root / "worktrees" / "agent_t5"
        failure = MutationError(
            "conflict",
            {
                "ok": False,
                "partial_success": False,
                "rolled_back": False,
                "written_files": [],
                "error": "conflict",
                "recovery_commands": ["research-cockpit validate --root research_cockpit --json"],
            },
        )

        with patch("research_cockpit.commands.start_agent_session._run_git_worktree_add") as git_add:
            with patch("research_cockpit.commands.start_agent_session.finish_mutation", side_effect=failure):
                with self.assertRaises(MutationError) as ctx:
                    start_agent_session(
                        self.root,
                        option_id="option_t5",
                        agent_id="agent_t5",
                        objective="Run T5 branch",
                        branch="agent/option_t5",
                        worktree=worktree,
                        create_worktree=True,
                        rebuild_dashboard=False,
                    )

        git_add.assert_called_once()
        payload = ctx.exception.payload
        self.assertTrue(payload["created_worktree"])
        self.assertEqual(payload["worktree"], str(worktree))
        self.assertIn("--worktree", payload["recovery_commands"][0])
        self.assertNotIn("--create-worktree", payload["recovery_commands"][0])

    def test_start_agent_session_records_session_without_absolute_worktree(self) -> None:
        worktree = self.tmp_root / "worktrees" / "agent_t5"
        worktree.mkdir(parents=True)

        payload = start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=worktree,
            rebuild_dashboard=False,
        )

        data = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        workstream = data["agent_workstream"]
        self.assertEqual(payload["changed"], True)
        self.assertEqual(workstream["owner"], "agent_t5")
        self.assertEqual(workstream["status"], "in_progress")
        self.assertEqual(workstream["session_id"], "session_agent_t5_option_t5")
        self.assertEqual(workstream["git_branch"], "agent/option_t5")
        self.assertEqual(workstream["worktree_label"], "agent_t5")
        self.assertNotIn(str(worktree), json.dumps(workstream))

    def test_start_agent_session_generates_agent_and_assignment_identity(self) -> None:
        worktree = self.tmp_root / "worktrees" / "cache_probe"
        worktree.mkdir(parents=True)

        with (
            patch("research_cockpit.commands.start_agent_session.secrets.token_hex", return_value="8f4c2a"),
            patch("research_cockpit.commands.start_agent_session.today", return_value="2026-06-03"),
        ):
            payload = start_agent_session(
                self.root,
                option_id="option_t5",
                label="Cache Probe",
                objective="Run cache probe",
                branch="agent/option_t5",
                worktree=worktree,
                rebuild_dashboard=False,
            )

        agent_id = "agent_20260603_8f4c2a_cache_probe"
        assignment_id = "assign_20260603_8f4c2a"
        agent = load_yaml(self.root / "agents" / f"{agent_id}.yaml")
        assignment = load_yaml(self.root / "assignments" / f"{assignment_id}.yaml")
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")

        self.assertEqual(payload["agent_id"], agent_id)
        self.assertEqual(payload["assignment_id"], assignment_id)
        self.assertEqual(payload["launch_env"]["RESEARCH_COCKPIT_AGENT_ID"], agent_id)
        self.assertEqual(payload["launch_env"]["RESEARCH_COCKPIT_ASSIGNMENT_ID"], assignment_id)
        self.assertEqual(payload["handoff"]["launch_env"]["RESEARCH_COCKPIT_AGENT_ID"], agent_id)
        self.assertEqual(payload["handoff"]["launch_env"]["RESEARCH_COCKPIT_ASSIGNMENT_ID"], assignment_id)
        self.assertIn("--assignment", payload["startup_command"])
        self.assertEqual(
            payload["startup_command_args"],
            [
                "research-cockpit",
                "bootstrap",
                "--root",
                str(self.root.resolve()),
                "--assignment",
                assignment_id,
                "--json",
            ],
        )
        self.assertEqual(agent["active_assignment_ids"], [assignment_id])
        self.assertEqual(assignment["agent_id"], agent_id)
        self.assertEqual(assignment["root_node"], "option_t5")
        self.assertEqual(assignment["current_node"], "option_t5")
        self.assertEqual(assignment["allowed_subtree"], {"root": "option_t5", "policy": "descendants_only"})
        self.assertEqual(assignment["worktree"]["branch"], "agent/option_t5")
        self.assertEqual(assignment["worktree"]["label"], "cache_probe")
        self.assertEqual(option["agent_workstream"]["owner"], agent_id)
        self.assertEqual(validate_cockpit(self.root, load_nodes(self.root)), [])
        out = subprocess.run(
            [
                *cli_command("bootstrap"),
                "--root",
                str(self.root),
                "--assignment",
                assignment_id,
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        bootstrap_payload = json.loads(out.stdout)
        self.assertEqual(bootstrap_payload["scope"]["mode"], "assignment")
        self.assertEqual(bootstrap_payload["scope"]["primary_context"], "assignment_scope")
        self.assertEqual(bootstrap_payload["scope"]["assignment_id"], assignment_id)
        self.assertEqual(bootstrap_payload["scope"]["agent_id"], agent_id)
        self.assertEqual(bootstrap_payload["assignment_scope"], bootstrap_payload["agent_scope"])
        self.assertEqual(
            bootstrap_payload["agent_scope"]["handoff"]["launch_env"]["RESEARCH_COCKPIT_ASSIGNMENT_ID"],
            assignment_id,
        )
        guidance_text = json.dumps(bootstrap_payload["mutation_guidance"])
        self.assertIn(f"--assignment {assignment_id}", guidance_text)
        self.assertIn(f"create-followup-experiment --root <root> --assignment {assignment_id}", guidance_text)
        self.assertIn(f"migrate-terminal-next-actions --root <root> --assignment {assignment_id}", guidance_text)
        self.assertIn("set-cursor", guidance_text)
        self.assertNotIn("research-cockpit init", guidance_text)
        self.assertNotIn("set-baseline", guidance_text)
        self.assertNotIn("sync-focus-actions", guidance_text)
        self.assertNotIn("update-suggestion-state", guidance_text)
        self.assertNotIn("finalize-workstream", guidance_text)

    def test_start_agent_session_quotes_shell_sensitive_root_in_startup_command(self) -> None:
        repo_root = self.tmp_root / "repo & spaces"
        data_root = repo_root / "research_cockpit"
        shutil.copytree(self.root, data_root)
        worktree = repo_root / "worktrees" / "cache probe"
        worktree.mkdir(parents=True)

        payload = start_agent_session(
            data_root,
            option_id="option_t5",
            label="cache probe",
            objective="Run cache probe",
            branch="agent/option_t5",
            worktree=worktree,
            rebuild_dashboard=False,
        )

        self.assertIn(f"'{data_root.resolve()}'", payload["startup_command"])
        out = subprocess.run(
            [
                *cli_command("bootstrap"),
                *payload["startup_command_args"][2:],
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        bootstrap_payload = json.loads(out.stdout)
        self.assertEqual(bootstrap_payload["scope"]["assignment_id"], payload["assignment_id"])

    def test_start_agent_session_cli_generates_identity_without_agent_arg(self) -> None:
        worktree = self.tmp_root / "worktrees" / "cache_probe"
        worktree.mkdir(parents=True)

        out = subprocess.run(
            [
                *cli_command("start-agent-session"),
                "--root",
                str(self.root),
                "--option",
                "option_t5",
                "--label",
                "cache probe",
                "--objective",
                "Run cache probe",
                "--branch",
                "agent/option_t5",
                "--worktree",
                str(worktree),
                "--json",
                "--no-build",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        self.assertRegex(payload["agent_id"], r"^agent_\d{8}_[0-9a-f]{6}_cache_probe$")
        self.assertRegex(payload["assignment_id"], r"^assign_\d{8}_[0-9a-f]{6}$")
        self.assertTrue((self.root / "agents" / f"{payload['agent_id']}.yaml").exists())
        self.assertTrue((self.root / "assignments" / f"{payload['assignment_id']}.yaml").exists())
        self.assertEqual(payload["launch_env"]["RESEARCH_COCKPIT_ASSIGNMENT_ID"], payload["assignment_id"])

    def test_start_agent_session_cli_sparse_dry_run_outputs_plan(self) -> None:
        worktree = self.tmp_root / "worktrees" / "cache_probe"

        out = subprocess.run(
            [
                *cli_command("start-agent-session"),
                "--root",
                str(self.root),
                "--option",
                "option_t5",
                "--agent",
                "agent_t5",
                "--objective",
                "Run cache probe",
                "--branch",
                "agent/option_t5",
                "--worktree",
                str(worktree),
                "--dry-run",
                "--json",
                "--sparse",
                "--sparse-profile",
                "ml-experiment",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["sparse_worktree"]["profile"], "ml-experiment")
        self.assertIn("--no-checkout", payload["git_command"])
        self.assertTrue(any("--stdin" in item["argv"] for item in payload["sparse_worktree"]["commands"]))
        self.assertFalse((self.root / "agents").exists())
        self.assertFalse(worktree.exists())

    def test_start_agent_session_cli_sparse_text_dry_run_points_to_json_plan(self) -> None:
        worktree = self.tmp_root / "worktrees" / "cache_probe"

        out = subprocess.run(
            [
                *cli_command("start-agent-session"),
                "--root",
                str(self.root),
                "--option",
                "option_t5",
                "--agent",
                "agent_t5",
                "--objective",
                "Run cache probe",
                "--branch",
                "agent/option_t5",
                "--worktree",
                str(worktree),
                "--dry-run",
                "--sparse",
                "--sparse-profile",
                "ml-experiment",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        self.assertIn("sparse_worktree.commands", out.stdout)
        self.assertIn("--json", out.stdout)
        self.assertFalse(worktree.exists())

    def test_start_agent_session_rejects_unsafe_identity_ids(self) -> None:
        worktree = self.tmp_root / "worktrees" / "agent_t5"
        worktree.mkdir(parents=True)
        current_before = (self.root / "current_state.yaml").read_text(encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            start_agent_session(
                self.root,
                option_id="option_t5",
                agent_id="../current_state",
                objective="Run T5 branch",
                branch="agent/option_t5",
                worktree=worktree,
                rebuild_dashboard=False,
            )

        self.assertIn("agent_id must contain only letters", str(ctx.exception))
        self.assertEqual(current_before, (self.root / "current_state.yaml").read_text(encoding="utf-8"))

    def test_start_agent_session_rejects_unsafe_assignment_id(self) -> None:
        worktree = self.tmp_root / "worktrees" / "agent_t5"
        worktree.mkdir(parents=True)
        current_before = (self.root / "current_state.yaml").read_text(encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            start_agent_session(
                self.root,
                option_id="option_t5",
                agent_id="agent_t5",
                assignment_id="../current_state",
                objective="Run T5 branch",
                branch="agent/option_t5",
                worktree=worktree,
                rebuild_dashboard=False,
            )

        self.assertIn("assignment_id must contain only letters", str(ctx.exception))
        self.assertEqual(current_before, (self.root / "current_state.yaml").read_text(encoding="utf-8"))

    def test_start_agent_session_force_reuses_existing_assignment_for_new_agent(self) -> None:
        worktree_a = self.tmp_root / "worktrees" / "agent_a"
        worktree_b = self.tmp_root / "worktrees" / "agent_b"
        worktree_a.mkdir(parents=True)
        worktree_b.mkdir(parents=True)
        first = start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_a",
            objective="Run T5 branch",
            branch="agent/option_t5_a",
            worktree=worktree_a,
            rebuild_dashboard=False,
        )

        second = start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_b",
            objective="Take over T5 branch",
            branch="agent/option_t5_b",
            worktree=worktree_b,
            force=True,
            rebuild_dashboard=False,
        )

        assignment_id = first["assignment_id"]
        self.assertEqual(second["assignment_id"], assignment_id)
        assignment = load_yaml(self.root / "assignments" / f"{assignment_id}.yaml")
        old_agent = load_yaml(self.root / "agents" / "agent_a.yaml")
        new_agent = load_yaml(self.root / "agents" / "agent_b.yaml")
        self.assertEqual(assignment["agent_id"], "agent_b")
        self.assertNotIn(assignment_id, old_agent.get("active_assignment_ids", []))
        self.assertIn(assignment_id, new_agent.get("active_assignment_ids", []))
        self.assertEqual(validate_cockpit(self.root, load_nodes(self.root)), [])

    def test_start_agent_session_preserves_existing_agent_label_and_smoke_uses_coordinator_bootstrap(self) -> None:
        worktree_a = self.tmp_root / "worktrees" / "agent_multi_a"
        worktree_b = self.tmp_root / "worktrees" / "agent_multi_b"
        worktree_a.mkdir(parents=True)
        worktree_b.mkdir(parents=True)
        write_node(
            self.root,
            {
                "id": "option_multi_assignment",
                "type": "option",
                "title": "Multi assignment option",
                "status": "active",
                "parent": "problem_text",
            },
        )
        first = start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_multi",
            label="First Display",
            objective="Run first assignment",
            branch="agent/multi_a",
            worktree=worktree_a,
            rebuild_dashboard=False,
        )
        second = start_agent_session(
            self.root,
            option_id="option_multi_assignment",
            agent_id="agent_multi",
            label="Second Display",
            objective="Run second assignment",
            branch="agent/multi_b",
            worktree=worktree_b,
            rebuild_dashboard=False,
        )

        agent = load_yaml(self.root / "agents" / "agent_multi.yaml")
        smoke = skill_smoke_test_payload(self.root)

        self.assertEqual(agent["label"], "first_display")
        self.assertEqual(agent["display_name"], "First Display")
        self.assertEqual(agent["active_assignment_ids"], [first["assignment_id"], second["assignment_id"]])
        self.assertTrue(smoke["ok"])

    def test_start_agent_session_can_create_git_worktree(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not available")
        repo_root = self.tmp_root / "repo"
        data_root = repo_root / "research_cockpit"
        shutil.copytree(self.root, data_root)
        for command in (
            ["git", "init", str(repo_root)],
            ["git", "-C", str(repo_root), "config", "user.email", "test@example.com"],
            ["git", "-C", str(repo_root), "config", "user.name", "Test"],
            ["git", "-C", str(repo_root), "add", "."],
            ["git", "-C", str(repo_root), "commit", "-m", "initial"],
        ):
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                self.skipTest(f"git worktree setup failed: {completed.stderr or completed.stdout}")
        worktree = self.tmp_root / "created_worktree"

        payload = start_agent_session(
            data_root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=worktree,
            base="HEAD",
            create_worktree=True,
            rebuild_dashboard=False,
        )

        self.assertTrue(payload["created_worktree"])
        self.assertTrue(worktree.exists())
        workstream = load_yaml(data_root / "graph" / "nodes" / "option_t5.yaml")["agent_workstream"]
        self.assertEqual(workstream["git_branch"], "agent/option_t5")
        self.assertEqual(workstream["worktree_label"], "created_worktree")
        self.assertNotIn(str(worktree), json.dumps(workstream))

    def test_start_agent_session_rejects_worktree_local_cockpit(self) -> None:
        worktree = self.tmp_root / "worktrees" / "agent_t5"
        (worktree / "research_cockpit").mkdir(parents=True)

        with self.assertRaises(ValueError) as ctx:
            start_agent_session(
                self.root,
                option_id="option_t5",
                agent_id="agent_t5",
                objective="Run T5 branch",
                branch="agent/option_t5",
                worktree=worktree,
                dry_run=True,
            )

        self.assertIn("worktree contains research_cockpit", str(ctx.exception))

    def test_set_agent_focus_does_not_change_global_focus_and_context_reads_it(self) -> None:
        worktree = self.tmp_root / "worktrees" / "agent_t5"
        worktree.mkdir(parents=True)
        start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=worktree,
            rebuild_dashboard=False,
        )
        before_global = load_yaml(self.root / "current_state.yaml").get("current_focus_path")

        result = set_agent_focus(
            self.root,
            agent_id="agent_t5",
            node_id="exp_t5",
            next_actions=["Run downstream test"],
            rebuild_dashboard=False,
        )
        current = load_yaml(self.root / "current_state.yaml")
        payload = agent_session_context_payload(self.root, agent_id="agent_t5", compact=True)

        self.assertEqual(result["after"]["agent_focus"]["current_focus_node"], "exp_t5")
        self.assertEqual(current["current_focus_path"], before_global)
        self.assertEqual(current["agent_focuses"]["agent_t5"]["current_option"], "option_t5")
        self.assertEqual(payload["required_root"], str(self.root.resolve()))
        self.assertTrue(payload["do_not_mutate_worktree_root"])
        self.assertEqual(payload["stable_artifact_root"], str((self.root / "artifacts").resolve()))
        self.assertIn("ingest-artifact", payload["handoff"]["commands"]["ingest_artifact"])
        self.assertEqual(payload["assignment"]["current_node"], "option_t5")
        self.assertEqual(payload["agent_focus"]["source"], "assignment")
        self.assertEqual(payload["agent_focus"]["current_focus_node"], "option_t5")
        self.assertEqual(payload["option_context"]["hierarchy_policy"]["workstream_file_hint"]["problem.parent"], "option_t5")
        self.assertIn("create_child_workstream", payload["option_context"]["suggested_commands"])

    def test_set_cursor_updates_assignment_record_and_assignment_context_reads_it(self) -> None:
        session = start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=self.tmp_root / "worktrees" / "agent_t5",
            rebuild_dashboard=False,
        )

        result = set_cursor(
            self.root,
            assignment_id=session["assignment_id"],
            node_id="exp_t5",
            next_actions=["Run downstream test"],
            rebuild_dashboard=False,
        )
        assignment = load_yaml(self.root / "assignments" / f"{session['assignment_id']}.yaml")
        current = load_yaml(self.root / "current_state.yaml")
        payload = agent_session_context_payload(
            self.root,
            assignment_id=session["assignment_id"],
            compact=True,
        )
        payload_by_agent = agent_session_context_payload(
            self.root,
            agent_id="agent_t5",
            compact=True,
        )

        self.assertEqual(result["after"]["assignment"]["current_node"], "exp_t5")
        self.assertEqual(assignment["current_node"], "exp_t5")
        self.assertEqual(assignment["next_actions"], ["Run downstream test"])
        self.assertNotIn("agent_focuses", current)
        self.assertEqual(payload["assignment"]["assignment_id"], session["assignment_id"])
        self.assertEqual(payload["assignment"]["current_node"], "exp_t5")
        self.assertEqual(payload["assignment"]["next_actions"], ["Run downstream test"])
        self.assertEqual(payload["assignment_cursor"]["current_node"], "exp_t5")
        self.assertEqual(payload["agent_focus"]["source"], "assignment")
        self.assertEqual(payload["agent_focus"]["current_focus_node"], "exp_t5")
        self.assertIn("--assignment", payload["handoff"]["commands"]["read_context"])
        self.assertIn("set-cursor", payload["handoff"]["commands"]["set_cursor"])
        self.assertEqual(payload_by_agent["assignment"]["assignment_id"], session["assignment_id"])
        self.assertEqual(payload_by_agent["assignment"]["current_node"], "exp_t5")
        self.assertEqual(payload_by_agent["agent_focus"]["source"], "assignment")
        self.assertIn("--assignment", payload_by_agent["handoff"]["commands"]["read_context"])
        self.assertIn("set-cursor", payload_by_agent["handoff"]["commands"]["set_cursor"])
        self.assertNotIn("set_agent_focus", payload_by_agent["handoff"]["commands"])

    def test_set_cursor_cli_compact_json_uses_assignment_target(self) -> None:
        session = start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=self.tmp_root / "worktrees" / "agent_t5",
            rebuild_dashboard=False,
        )

        out = subprocess.run(
            [
                *cli_command("set-cursor"),
                "--root",
                str(self.root),
                "--assignment",
                session["assignment_id"],
                "--node",
                "exp_t5",
                "--next-action",
                "Review run output",
                "--json",
                "--compact",
                "--no-build",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)
        assignment = load_yaml(self.root / "assignments" / f"{session['assignment_id']}.yaml")

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(payload["command"], "research-cockpit set-cursor")
        self.assertEqual(payload["target"]["assignment_id"], session["assignment_id"])
        self.assertEqual(assignment["current_node"], "exp_t5")
        self.assertEqual(assignment["next_actions"], ["Review run output"])

    def test_set_agent_focus_actual_json_omits_would_change_and_compact_is_supported(self) -> None:
        out = subprocess.run(
            [
                *cli_command("set-agent-focus"),
                "--root",
                str(self.root),
                "--agent",
                "agent_t5",
                "--node",
                "exp_t5",
                "--json",
                "--no-build",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        compact = subprocess.run(
            [
                *cli_command("set-agent-focus"),
                "--root",
                str(self.root),
                "--agent",
                "agent_t5",
                "--node",
                "exp_t5",
                "--json",
                "--compact",
                "--no-build",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        payload = json.loads(out.stdout)
        compact_payload = json.loads(compact.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(payload["changed"])
        self.assertNotIn("would_change", payload)
        self.assertEqual(compact.returncode, 0, compact.stdout + compact.stderr)
        self.assertEqual(compact_payload["command"], "research-cockpit set-agent-focus")
        self.assertEqual(compact_payload["target"], {"agent_id": "agent_t5", "node_id": "exp_t5"})

    def test_agent_session_context_json_error_is_structured(self) -> None:
        out = subprocess.run(
            [
                *cli_command("agent-session-context"),
                "--root",
                str(self.root),
                "--agent",
                "missing_agent",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("No active agent session", payload["error"])

    def test_build_dashboard_rows_include_agent_session_fields(self) -> None:
        worktree = self.tmp_root / "worktrees" / "agent_t5"
        worktree.mkdir(parents=True)
        session = start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=worktree,
            rebuild_dashboard=False,
        )
        set_cursor(
            self.root,
            assignment_id=session["assignment_id"],
            node_id="exp_t5",
            next_actions=["Review assignment output"],
            rebuild_dashboard=False,
        )
        set_focus(
            self.root,
            focus_node="problem_text",
            next_actions=["Coordinator review"],
            rebuild_dashboard=False,
        )

        build_dashboard(self.root)
        rows = json.loads((self.root / "dashboards" / "option_workstreams.json").read_text(encoding="utf-8"))
        agent_context = json.loads((self.root / "dashboards" / "agent_context_pack.json").read_text(encoding="utf-8"))

        self.assertEqual(rows[0]["session_id"], "session_agent_t5_option_t5")
        self.assertEqual(rows[0]["git_branch"], "agent/option_t5")
        self.assertEqual(rows[0]["worktree_label"], "agent_t5")
        self.assertEqual(rows[0]["agent_focus_node"], "exp_t5")
        self.assertEqual(rows[0]["agent_focus_source"], "assignment")
        self.assertEqual(rows[0]["assignment_id"], session["assignment_id"])
        self.assertEqual(rows[0]["assignment_current_node"], "exp_t5")
        self.assertEqual(rows[0]["assignment_next_actions"], ["Review assignment output"])
        self.assertEqual(agent_context["primary_context"], "assignment_overview")
        self.assertTrue(agent_context["legacy_global_fields_are_coordinator_only"])
        self.assertTrue(agent_context["current_focus_is_coordinator_only"])
        self.assertTrue(agent_context["current_global_focus"]["coordinator_only"])
        self.assertEqual(
            agent_context["current_global_focus"]["next_action_scopes"]["global_coordinator_next_actions"][0]["source"],
            "coordinator_state",
        )
        self.assertEqual(agent_context["assignment_overview"]["assignments"][0]["assignment_id"], session["assignment_id"])
        self.assertEqual(agent_context["assignment_overview"]["assignments"][0]["current_node"], "exp_t5")
        self.assertEqual(
            agent_context["assignment_overview"]["assignments"][0]["next_actions"],
            ["Review assignment output"],
        )

    def test_option_workstream_context_cli_outputs_json(self) -> None:
        command = "option-workstream-context"

        result = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--option", "option_t5", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["option"]["id"], "option_t5")
        self.assertEqual(payload["upstream_problem"]["id"], "problem_text")

    def test_option_workstream_context_cli_accepts_id_alias(self) -> None:
        by_option = subprocess.run(
            [*cli_command("option-workstream-context"), "--root", str(self.root), "--option", "option_t5", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        by_id = subprocess.run(
            [*cli_command("option-workstream-context"), "--root", str(self.root), "--id", "option_t5", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(by_option.returncode, 0, by_option.stderr or by_option.stdout)
        self.assertEqual(by_id.returncode, 0, by_id.stderr or by_id.stdout)
        self.assertEqual(json.loads(by_id.stdout)["option"]["id"], "option_t5")
        self.assertEqual(json.loads(by_id.stdout), json.loads(by_option.stdout))

    def test_option_workstream_context_cli_compact_json_is_terse(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_context",
                "type": "artifact",
                "title": "Context bundle",
                "status": "done",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["linked_artifacts"] = ["artifact_context"]
        experiment["success_criteria"] = [
            "The compact context shows whether planned experiments have enough validation detail.",
            "The full node context remains available for exact field text.",
        ]
        experiment["metrics"] = ["command_count", "extra_context_reads"]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        record_finding(
            self.root,
            experiment_id="exp_t5",
            statement="Compact context can summarize findings.",
            confidence="medium",
            outcome="positive",
            rebuild_dashboard=False,
        )
        progress_path = self.root / "artifacts" / "exp_t5" / "run_compact_context" / "progress.json"
        progress_path.parent.mkdir(parents=True)
        progress_path.write_text(
            json.dumps(
                {
                    "status": "running",
                    "completed_steps": 1,
                    "total_steps": 4,
                    "last_update": "2999-01-01T00:00:00Z",
                    "current_stage": "smoke",
                }
            ),
            encoding="utf-8",
        )
        save_yaml(
            self.root / "runs" / "run_compact_context.yaml",
            {
                "run_id": "run_compact_context",
                "status": "running",
                "experiment_id": "exp_t5",
                "started_at": "2026-05-27T01:00:00Z",
                "command": "python train.py --long",
                "progress_file": "artifacts/exp_t5/run_compact_context/progress.json",
            },
        )

        out = subprocess.run(
            [
                *cli_command("option-workstream-context"),
                "--root",
                str(self.root),
                "--id",
                "option_t5",
                "--compact",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        self.assertEqual(payload["option"]["id"], "option_t5")
        self.assertEqual(payload["upstream_problem"]["id"], "problem_text")
        self.assertEqual(payload["subtree"]["experiment_ids"], ["exp_t5"])
        self.assertEqual(payload["evidence_summary"]["experiment_count"], 1)
        self.assertEqual(payload["evidence_summary"]["finding_count"], 1)
        self.assertEqual(payload["evidence_summary"]["artifact_count"], 1)
        self.assertEqual(len(payload["experiment_summaries"]), 1)
        experiment_summary = payload["experiment_summaries"][0]
        self.assertEqual(experiment_summary["id"], "exp_t5")
        self.assertEqual(experiment_summary["success_criteria_count"], 2)
        self.assertEqual(experiment_summary["first_success_criterion"], "The compact context shows whether planned experiments have enough validation detail.")
        self.assertEqual(experiment_summary["metric_count"], 2)
        self.assertEqual(experiment_summary["finding_count"], 1)
        self.assertEqual(experiment_summary["linked_artifact_count"], 1)
        self.assertEqual(experiment_summary["run_summary"]["active_run_ids"], ["run_compact_context"])
        self.assertEqual(experiment_summary["run_summary"]["active_progress"][0]["percent_complete"], 25.0)
        self.assertEqual(experiment_summary["run_summary"]["active_progress"][0]["current_stage"], "smoke")
        self.assertNotIn("schema_version", experiment_summary["run_summary"]["active_progress"][0])
        self.assertNotIn("path", experiment_summary["run_summary"]["active_progress"][0])
        self.assertNotIn("python train.py", str(experiment_summary["run_summary"]))
        self.assertEqual(payload["hierarchy_policy"]["workstream_file_hint"]["problem.parent"], "option_t5")
        self.assertNotIn("active_option.parent", payload["hierarchy_policy"]["workstream_file_hint"])
        self.assertIn("active_option.parent", payload["hierarchy_policy"]["command_created_shape"])
        self.assertIn("create_child_workstream", payload["suggested_commands"])
        self.assertIn("--id option_t5", payload["suggested_commands"]["context"])
        self.assertNotIn("subtree_nodes", payload)
        self.assertNotIn("experiments", payload)

    def test_finding_linked_artifacts_are_visible_in_context_resources(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_finding_only",
                "type": "artifact",
                "title": "Finding-only bundle",
                "status": "done",
                "path": "outputs/finding_only",
                "links": {"metrics": "outputs/finding_only/metrics.json"},
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["findings"] = [
            {
                "id": "exp_t5_finding_001",
                "statement": "Old data linked the artifact only from the finding.",
                "confidence": "medium",
                "outcome": "positive",
                "linked_artifacts": ["artifact_finding_only"],
            }
        ]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)

        payload = context_payload(
            self.root,
            node_id="exp_t5",
            with_artifacts=True,
            compact=True,
        )
        rows = build_link_rows(self.root, load_nodes(self.root))
        experiment_rows = [row for row in rows if row.get("node_id") == "exp_t5"]

        self.assertEqual(payload["artifacts"]["artifact_ids"], ["artifact_finding_only"])
        self.assertIn("outputs/finding_only", {row["target"] for row in experiment_rows})
        self.assertIn("outputs/finding_only/metrics.json", {row["target"] for row in experiment_rows})

        compact = compact_option_workstream_context(
            option_workstream_context_payload(self.root, option_id="option_t5"),
            load_nodes(self.root),
        )
        self.assertEqual(compact["evidence_summary"]["artifact_count"], 1)
        self.assertEqual(compact["experiment_summaries"][0]["linked_artifact_count"], 1)

    def test_node_context_cli_outputs_json_and_manifest_lists_command(self) -> None:
        result = subprocess.run(
            [*cli_command("node-context"), "--root", str(self.root), "--id", "option_t5", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        manifest = agent_command_manifest()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["node"]["id"], "option_t5")
        self.assertEqual(payload["type_context"]["kind"], "option")
        self.assertIn("--root", payload["command_drafts"]["claim_option"])
        self.assertNotIn("python scripts", payload["command_drafts"]["claim_option"])
        self.assertNotIn(".py", payload["command_drafts"]["claim_option"])
        command = [item for item in manifest if item["name"] == "node-context"][0]
        self.assertFalse(command["mutating"])
        self.assertTrue(command["supports_json"])

    def test_context_and_node_context_skip_bad_interaction_events_with_warnings(self) -> None:
        save_yaml(
            self.root / "graph" / "interaction_log.yaml",
            {
                "events": [
                    {"kind": "set_focus", "node_id": "option_t5"},
                    "malformed event",
                ]
            },
        )

        node_payload = node_context_payload(self.root, node_id="option_t5")
        context = context_payload(self.root, node_id="option_t5", compact=True)

        self.assertEqual(node_payload["recent_interactions"][0]["kind"], "set_focus")
        self.assertTrue(any("events[2]" in warning for warning in node_payload["warnings"]))
        self.assertTrue(any("events[2]" in warning for warning in context["warnings"]))

    def test_node_context_payload_for_decision_includes_acceptance_repairs(self) -> None:
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
                "supporting_experiments": ["exp_t5"],
            },
        )

        payload = node_context_payload(self.root, node_id="decision_t5")

        self.assertEqual(payload["node"]["id"], "decision_t5")
        self.assertFalse(payload["type_context"]["acceptance"]["ready"])
        self.assertTrue(payload["type_context"]["repair_hints"])
        repair_commands = " ".join(item.get("command", "") for item in payload["type_context"]["repair_hints"])
        self.assertIn("record-finding", repair_commands)
        self.assertIn("--root", repair_commands)

    def test_report_option_workstream_writes_report_and_marks_reported(self) -> None:
        claim_option(self.root, option_id="option_t5", agent_id="agent_t5", rebuild_dashboard=False)
        record_finding(
            self.root,
            experiment_id="exp_t5",
            statement="T5 improves text alignment.",
            confidence="medium",
            outcome="positive",
            rebuild_dashboard=False,
        )

        report_option_workstream(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            recommendation="continue",
            summary="Evidence is promising but incomplete.",
        )

        data = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        self.assertEqual(data["agent_workstream"]["status"], "reported")
        self.assertEqual(data["workstream_report"]["reporting_agent"], "agent_t5")
        self.assertEqual(data["workstream_report"]["recommendation"], "continue")
        self.assertEqual(data["workstream_report"]["finding_count"], 1)
        event = interaction_events(self.root)[-1]
        self.assertEqual(event["kind"], "report_option")
        self.assertEqual(event["actor"], "agent_t5")
        self.assertEqual(event["option_id"], "option_t5")
        self.assertEqual(event["recommendation"], "continue")
        self.assertEqual(event["after"]["workstream_report"]["summary"], "Evidence is promising but incomplete.")

    def test_report_option_workstream_cli_dry_run_json_previews_without_writing(self) -> None:
        command = "report-option-workstream"
        option_path = self.root / "graph" / "nodes" / "option_t5.yaml"
        claim_option(self.root, option_id="option_t5", agent_id="agent_t5", rebuild_dashboard=False)
        record_finding(
            self.root,
            experiment_id="exp_t5",
            statement="T5 improves text alignment.",
            confidence="medium",
            outcome="positive",
            rebuild_dashboard=False,
        )
        before = option_path.read_text(encoding="utf-8")
        before_event_count = len(interaction_events(self.root))

        out = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--option",
                "option_t5",
                "--agent",
                "agent_t5",
                "--recommend",
                "continue",
                "--summary",
                "Evidence is promising but incomplete.",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after = option_path.read_text(encoding="utf-8")

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["option_id"], "option_t5")
        self.assertEqual(payload["agent_id"], "agent_t5")
        self.assertEqual(payload["recommendation"], "continue")
        self.assertEqual(payload["before"]["agent_workstream"]["status"], "claimed")
        self.assertEqual(payload["after"]["agent_workstream"]["status"], "reported")
        self.assertEqual(payload["after"]["workstream_report"]["finding_count"], 1)
        self.assertEqual(payload["evidence_summary"]["findings_count"], 1)
        self.assertEqual(before, after)
        self.assertEqual(len(interaction_events(self.root)), before_event_count)
        self.assertFalse((self.root / "dashboards").exists())

    def test_report_option_workstream_cli_dry_run_owner_mismatch_does_not_write(self) -> None:
        command = "report-option-workstream"
        option_path = self.root / "graph" / "nodes" / "option_t5.yaml"
        claim_option(self.root, option_id="option_t5", agent_id="agent_a", rebuild_dashboard=False)
        before = option_path.read_text(encoding="utf-8")
        before_event_count = len(interaction_events(self.root))

        out = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--option",
                "option_t5",
                "--agent",
                "agent_b",
                "--recommend",
                "continue",
                "--summary",
                "Evidence is promising but incomplete.",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after = option_path.read_text(encoding="utf-8")

        self.assertEqual(out.returncode, 1)
        self.assertIn("owned by agent_a", out.stdout)
        self.assertEqual(before, after)
        self.assertEqual(len(interaction_events(self.root)), before_event_count)

    def test_finalize_workstream_updates_explicit_status_report_artifact_and_focus(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_bundle",
                "type": "artifact",
                "title": "Bundle",
                "status": "done",
            },
        )
        record_finding(
            self.root,
            experiment_id="exp_t5",
            statement="T5 result is usable.",
            confidence="medium",
            outcome="positive",
            rebuild_dashboard=False,
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["next_actions"] = ["Open follow-up branch."]
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        summary_file = self.tmp_root / "summary.md"
        summary_file.write_text("Final option summary.", encoding="utf-8")

        result = finalize_workstream(
            self.root,
            option_id="option_t5",
            status="accepted",
            problem_status="resolved",
            stage_status="done",
            summary_file=summary_file,
            summary_target="all",
            artifact_ids=["artifact_bundle"],
            sync_focus=True,
            report=True,
            agent_id="agent_t5",
            rebuild_dashboard=False,
        )
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        stage = load_yaml(self.root / "graph" / "nodes" / "stage_text.yaml")
        current = load_yaml(self.root / "current_state.yaml")

        self.assertTrue(result["changed"])
        self.assertEqual(option["status"], "accepted")
        self.assertEqual(option["summary"], "Final option summary.")
        self.assertEqual(option["workstream_report"]["recommendation"], "accept")
        self.assertEqual(option["workstream_report"]["linked_artifacts"], ["artifact_bundle"])
        self.assertEqual(problem["status"], "resolved")
        self.assertEqual(problem["current_best_option"], "option_t5")
        self.assertEqual(problem["linked_artifacts"], ["artifact_bundle"])
        self.assertEqual(stage["status"], "done")
        self.assertEqual(current["next_actions"], ["Open follow-up branch."])
        self.assertEqual(interaction_events(self.root)[-1]["kind"], "finalize_workstream")

    def test_finalize_workstream_cli_dry_run_defaults_summary_to_report_only(self) -> None:
        summary_file = self.tmp_root / "summary.md"
        summary_file.write_text("Report-only summary.", encoding="utf-8")
        before_option = (self.root / "graph" / "nodes" / "option_t5.yaml").read_text(encoding="utf-8")
        out = subprocess.run(
            [
                *cli_command("finalize-workstream"),
                "--root",
                str(self.root),
                "--option",
                "option_t5",
                "--status",
                "promising",
                "--problem-status",
                "active",
                "--summary-file",
                str(summary_file),
                "--report",
                "--dry-run",
                "--show-diff",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after_option = (self.root / "graph" / "nodes" / "option_t5.yaml").read_text(encoding="utf-8")
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["after"]["option"]["status"], "promising")
        self.assertIsNone(payload["after"]["option"]["summary"])
        self.assertEqual(payload["after"]["option"]["workstream_report"]["summary"], "Report-only summary.")
        self.assertIn("diff", payload)
        self.assertEqual(before_option, after_option)

    def test_finalize_workstream_cli_accepts_file_input_and_flag_overrides(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_bundle",
                "type": "artifact",
                "title": "Bundle",
                "status": "done",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        summary_file = self.tmp_root / "report.md"
        summary_file.write_text("Report summary from file.", encoding="utf-8")
        finalize_file = self.tmp_root / "finalize.yaml"
        save_yaml(
            finalize_file,
            {
                "option": "option_t5",
                "status": "promising",
                "problem_status": "active",
                "summary_file": str(summary_file),
                "summary_target": "report",
                "artifacts": ["artifact_bundle"],
                "sync_focus": False,
                "report": True,
                "agent": "agent_file",
            },
        )

        dry_run = subprocess.run(
            [
                *cli_command("finalize-workstream"),
                "--root",
                str(self.root),
                "--file",
                str(finalize_file),
                "--status",
                "accepted",
                "--problem-status",
                "resolved",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        dry_payload = json.loads(dry_run.stdout)

        self.assertEqual(dry_run.returncode, 0, dry_run.stdout + dry_run.stderr)
        self.assertEqual(dry_payload["after"]["option"]["status"], "accepted")
        self.assertEqual(dry_payload["after"]["problem"]["status"], "resolved")
        self.assertFalse((self.root / "dashboards").exists())

        out = subprocess.run(
            [
                *cli_command("finalize-workstream"),
                "--root",
                str(self.root),
                "--file",
                str(finalize_file),
                "--status",
                "accepted",
                "--problem-status",
                "resolved",
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(option["status"], "accepted")
        self.assertEqual(problem["status"], "resolved")
        self.assertNotIn("summary", option)
        self.assertEqual(option["workstream_report"]["summary"], "Report summary from file.")
        self.assertEqual(option["workstream_report"]["linked_artifacts"], ["artifact_bundle"])
        self.assertFalse((self.root / "dashboards").exists())

    def test_finalize_workstream_rejects_terminal_option_with_active_descendants(self) -> None:
        option_path = self.root / "graph" / "nodes" / "option_t5.yaml"
        before = option_path.read_text(encoding="utf-8")

        out = subprocess.run(
            [
                *cli_command("finalize-workstream"),
                "--root",
                str(self.root),
                "--option",
                "option_t5",
                "--status",
                "accepted",
                "--problem-status",
                "resolved",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertEqual(payload["error"], "terminal_parent_has_active_descendants")
        errors_by_node = {error["node_id"]: error for error in payload["lifecycle_errors"]}
        self.assertIn("option_t5", errors_by_node)
        self.assertIn("problem_text", errors_by_node)
        self.assertEqual(errors_by_node["option_t5"]["target_status"], "accepted")
        self.assertEqual(
            [item["id"] for item in errors_by_node["option_t5"]["blocking_descendants"]],
            ["exp_t5"],
        )
        self.assertIn("close-branch", payload["suggested_commands"][0])
        self.assertEqual(before, option_path.read_text(encoding="utf-8"))

    def test_finalize_workstream_file_summary_relative_to_finalize_file(self) -> None:
        plan_dir = self.tmp_root / "plans"
        summary_file = plan_dir / "notes" / "report.md"
        summary_file.parent.mkdir(parents=True)
        summary_file.write_text("Relative summary from finalize directory.", encoding="utf-8")
        finalize_file = plan_dir / "finalize.yaml"
        save_yaml(
            finalize_file,
            {
                "option": "option_t5",
                "status": "promising",
                "summary_file": "notes/report.md",
                "report": True,
            },
        )

        out = subprocess.run(
            [
                *cli_command("finalize-workstream"),
                "--root",
                str(self.root),
                "--file",
                str(finalize_file),
                "--dry-run",
                "--show-diff",
                "--json",
                "--compact",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(payload["resolved_inputs"]["summary_file"], str(summary_file))
        self.assertTrue(payload["diff_included"])
        self.assertGreater(payload["diff_line_count"], 0)
        self.assertIn("diff", payload)

    def test_finalize_workstream_file_summary_relative_to_root(self) -> None:
        summary_file = self.root / "notes" / "report.md"
        summary_file.parent.mkdir(parents=True)
        summary_file.write_text("Relative summary from data root.", encoding="utf-8")
        finalize_file = self.tmp_root / "finalize_root_relative.yaml"
        save_yaml(
            finalize_file,
            {
                "option": "option_t5",
                "status": "promising",
                "summary_file": "notes/report.md",
                "report": True,
            },
        )

        out = subprocess.run(
            [
                *cli_command("finalize-workstream"),
                "--root",
                str(self.root),
                "--file",
                str(finalize_file),
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(payload["resolved_inputs"]["summary_file"], str(summary_file))
        self.assertEqual(payload["after"]["option"]["workstream_report"]["summary"], "Relative summary from data root.")

    def test_finalize_workstream_file_summary_relative_to_cwd_fallback(self) -> None:
        cwd_dir = self.tmp_root / "cwd_summary"
        cwd_dir.mkdir()
        summary_file = cwd_dir / "cwd_report.md"
        summary_file.write_text("Relative summary from cwd fallback.", encoding="utf-8")
        finalize_file = self.tmp_root / "finalize_cwd_relative.yaml"
        save_yaml(
            finalize_file,
            {
                "option": "option_t5",
                "status": "promising",
                "summary_file": "cwd_report.md",
                "report": True,
            },
        )

        out = subprocess.run(
            [
                *cli_command("finalize-workstream"),
                "--root",
                str(self.root),
                "--file",
                str(finalize_file),
                "--dry-run",
                "--json",
            ],
            cwd=cwd_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(payload["resolved_inputs"]["summary_file"], str(summary_file))
        self.assertEqual(payload["after"]["option"]["workstream_report"]["summary"], "Relative summary from cwd fallback.")

    def test_finalize_workstream_file_summary_missing_lists_attempts(self) -> None:
        finalize_file = self.tmp_root / "missing_summary_finalize.yaml"
        save_yaml(
            finalize_file,
            {
                "option": "option_t5",
                "status": "promising",
                "summary_file": "missing/report.md",
                "report": True,
            },
        )

        out = subprocess.run(
            [
                *cli_command("finalize-workstream"),
                "--root",
                str(self.root),
                "--file",
                str(finalize_file),
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 1)
        self.assertIn("Summary file does not exist", out.stdout)
        self.assertIn("Tried:", out.stdout)
        self.assertIn(str(finalize_file.parent / "missing" / "report.md"), out.stdout)
        self.assertIn(str(self.root / "missing" / "report.md"), out.stdout)

    def test_finalize_workstream_rejects_invalid_file_input(self) -> None:
        finalize_file = self.tmp_root / "bad_finalize.yaml"
        save_yaml(finalize_file, {"status": "accepted"})

        out = subprocess.run(
            [
                *cli_command("finalize-workstream"),
                "--root",
                str(self.root),
                "--file",
                str(finalize_file),
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 1)
        self.assertIn("option", out.stdout.lower())

    def test_finalize_workstream_strips_utf8_bom_from_summary_file(self) -> None:
        summary_file = self.tmp_root / "bom_summary.md"
        summary_file.write_text("\ufeffReport summary with BOM.", encoding="utf-8")

        out = subprocess.run(
            [
                *cli_command("finalize-workstream"),
                "--root",
                str(self.root),
                "--option",
                "option_t5",
                "--status",
                "promising",
                "--summary-file",
                str(summary_file),
                "--report",
                "--dry-run",
                "--show-diff",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(payload["after"]["option"]["workstream_report"]["summary"], "Report summary with BOM.")
        self.assertNotIn("\ufeff", payload["diff"])

    def test_option_workstream_context_payload_summarizes_option(self) -> None:
        payload = option_workstream_context_payload(self.root, option_id="option_t5")

        self.assertEqual(payload["option"]["id"], "option_t5")
        self.assertEqual(payload["subtree"]["experiment_ids"], ["exp_t5"])
        self.assertIn("context", payload["suggested_commands"])
        self.assertIn("--id option_t5", payload["suggested_commands"]["context"])

    def test_context_payload_combines_node_bootstrap_artifacts_and_related_experiments(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_cache",
                "type": "artifact",
                "title": "Feature cache",
                "status": "done",
                "path": "outputs/cache",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["linked_artifacts"] = ["artifact_cache"]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)

        payload = context_payload(
            self.root,
            node_id="option_t5",
            with_bootstrap=True,
            with_artifacts=True,
            compact=True,
        )

        self.assertEqual(payload["node"]["id"], "option_t5")
        self.assertTrue(payload["validation"]["ok"])
        self.assertEqual(payload["related"]["problem"]["id"], "problem_text")
        self.assertEqual(payload["related"]["experiments"][0]["id"], "exp_t5")
        self.assertEqual(payload["artifacts"]["artifact_ids"], ["artifact_cache"])
        self.assertIn("mutation_guidance", payload["bootstrap"])
        self.assertIn("complete_experiments", payload["recommended_commands"])
        self.assertEqual(payload["target_context"]["node_id"], "option_t5")
        self.assertEqual(payload["current_global_focus"]["current_focus_node"], "problem_text")
        self.assertTrue(payload["context_boundary"]["target_differs_from_global_focus"])
        self.assertIn("target node", payload["context_boundary"]["warning"])

    def test_context_payload_filters_claim_recommendation_for_closed_option(self) -> None:
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["status"] = "accepted"
        option["workstream_report"] = {"status": "reported", "summary": "Closed."}
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)

        payload = context_payload(
            self.root,
            node_id="option_t5",
            with_bootstrap=True,
            compact=True,
        )
        steps = payload["node_context"].get("recommended_next_steps", [])

        self.assertEqual(payload["recommended_commands"].get("claim_option"), None)
        self.assertFalse(any("Claim the option workstream" in item.get("action", "") for item in steps))
        self.assertEqual(payload["target_context"]["node_status"], "accepted")

    def test_context_cli_json_for_experiment(self) -> None:
        out = subprocess.run(
            [
                *cli_command("context"),
                "--root",
                str(self.root),
                "--node",
                "exp_t5",
                "--with-artifacts",
                "--compact",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(payload["node"]["id"], "exp_t5")
        self.assertEqual(payload["related"]["option"]["id"], "option_t5")
        self.assertEqual(payload["related"]["experiments"][0]["id"], "exp_t5")

    def test_agent_bootstrap_payload_reports_context_without_building_by_default(self) -> None:
        progress_path = self.root / "artifacts" / "exp_t5" / "run_bootstrap_running" / "progress.json"
        progress_path.parent.mkdir(parents=True)
        progress_path.write_text(
            json.dumps(
                {
                    "status": "running",
                    "completed_steps": 2,
                    "total_steps": 4,
                    "last_update": "2999-01-01T00:00:00Z",
                    "current_stage": "train",
                    "latest_artifact": "artifacts/exp_t5/run_bootstrap_running/partial.json",
                    "warnings": ["warmup slow"],
                }
            ),
            encoding="utf-8",
        )
        save_yaml(
            self.root / "runs" / "run_bootstrap_running.yaml",
            {
                "run_id": "run_bootstrap_running",
                "status": "running",
                "experiment_id": "exp_t5",
                "started_at": "2000-01-01T00:00:00Z",
                "monitor_command": "tail -f run.log",
                "stop_command": "tmux kill-session -t run_bootstrap",
                "progress_file": "artifacts/exp_t5/run_bootstrap_running/progress.json",
            },
        )
        save_yaml(
            self.root / "runs" / "run_bootstrap_failed.yaml",
            {
                "run_id": "run_bootstrap_failed",
                "status": "failed",
                "experiment_id": "exp_t5",
                "started_at": "2026-05-27T01:00:00Z",
                "finished_at": "2026-05-27T01:10:00Z",
            },
        )
        save_yaml(
            self.root / "runs" / "run_bootstrap_completed.yaml",
            {
                "run_id": "run_bootstrap_completed",
                "status": "completed",
                "experiment_id": "exp_t5",
                "started_at": "2026-05-27T02:00:00Z",
                "finished_at": "2026-05-27T02:10:00Z",
            },
        )

        payload = agent_bootstrap_payload(self.root, build=False)

        self.assertTrue(payload["validation"]["ok"])
        self.assertEqual(payload["focus"]["current_focus_node"], "problem_text")
        self.assertFalse(payload["context_paths"]["agent_context_pack"]["exists"])
        self.assertTrue(payload["skill"]["path"])
        self.assertTrue(payload["skill"]["exists"])
        self.assertIn("top_suggestions", payload)
        self.assertIn("search_summary", payload)
        self.assertIn("mutation_guidance", payload)
        self.assertIn("apply-graph-plan", " ".join(payload["mutation_guidance"]["command_skeletons"]))
        batch_mode = payload["mutation_guidance"]["multi_agent_batch_mode"]
        self.assertIn("coordinator", batch_mode["default"])
        self.assertIn("does not replace validate/smoke", batch_mode["default"])
        self.assertIn("commands --json --compact", " ".join(batch_mode["rules"]))
        self.assertIn("smoke --root <root> --json", " ".join(batch_mode["finish_commands"]))
        self.assertIn("record-finding", " ".join(batch_mode["examples"]["findings"]))
        self.assertIn("ingest-artifact", " ".join(batch_mode["examples"]["artifacts"]))
        self.assertIn("update-run", " ".join(batch_mode["examples"]["runs"]))
        self.assertIn("sync-focus-actions", " ".join(batch_mode["examples"]["next_actions"]))
        hierarchy = payload["mutation_guidance"]["hierarchy_policy"]
        self.assertEqual(hierarchy["default_branch_shape"], "option -> problem -> option -> experiment/decision")
        self.assertIn("create-workstream", hierarchy["recommended_command"])
        self.assertIsInstance(payload["git"]["worktree_dirty"], bool)
        self.assertEqual(payload["run_overview"]["running_count"], 1)
        self.assertEqual(payload["run_overview"]["failed_count"], 1)
        self.assertEqual(payload["run_overview"]["completed_count"], 1)
        self.assertEqual(payload["run_overview"]["possibly_stale_count"], 1)
        self.assertEqual(payload["run_overview"]["running"][0]["run_id"], "run_bootstrap_running")
        self.assertEqual(payload["run_overview"]["failed"][0]["run_id"], "run_bootstrap_failed")
        self.assertEqual(payload["run_overview"]["recently_completed"][0]["run_id"], "run_bootstrap_completed")
        self.assertNotIn("monitor_command", payload["run_overview"]["running"][0])
        self.assertNotIn("stop_command", payload["run_overview"]["running"][0])
        self.assertEqual(payload["run_overview"]["running"][0]["progress"]["percent_complete"], 50.0)
        self.assertEqual(payload["run_overview"]["running"][0]["progress"]["current_stage"], "train")
        self.assertEqual(
            payload["run_overview"]["running"][0]["progress"]["latest_artifact"],
            "artifacts/exp_t5/run_bootstrap_running/partial.json",
        )
        self.assertEqual(payload["run_overview"]["running"][0]["progress"]["warnings"], ["warmup slow"])
        self.assertNotIn("schema_version", payload["run_overview"]["running"][0]["progress"])
        self.assertNotIn("path", payload["run_overview"]["running"][0]["progress"])

    def test_agent_bootstrap_agent_scope_does_not_treat_global_focus_as_agent_focus(self) -> None:
        stage_path = self.root / "graph" / "nodes" / "stage_text.yaml"
        stage_data = load_yaml(stage_path)
        stage_data["children"] = ["problem_text", "problem_other"]
        save_yaml(stage_path, stage_data)
        write_node(
            self.root,
            {
                "id": "problem_other",
                "type": "problem",
                "title": "Other problem",
                "status": "active",
                "parent": "stage_text",
                "children": ["option_other"],
            },
        )
        write_node(
            self.root,
            {
                "id": "option_other",
                "type": "option",
                "title": "Other option",
                "status": "active",
                "parent": "problem_other",
                "children": ["exp_other"],
            },
        )
        write_node(
            self.root,
            {
                "id": "exp_other",
                "type": "experiment",
                "title": "Other experiment",
                "status": "running",
                "parent": "option_other",
            },
        )
        current_path = self.root / "current_state.yaml"
        current = load_yaml(current_path)
        current["current_problem"] = "problem_other"
        current["current_option"] = "option_other"
        current["current_focus_node"] = "exp_other"
        current["current_focus_path"] = ["stage_text", "problem_other", "option_other", "exp_other"]
        save_yaml(current_path, current)
        start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=self.tmp_root / "worktrees" / "agent_t5",
            rebuild_dashboard=False,
        )
        session = load_yaml(self.root / "agents" / "agent_t5.yaml")["active_assignment_ids"][0]
        set_cursor(
            self.root,
            assignment_id=session,
            node_id="exp_t5",
            next_actions=["Review T5 output"],
            rebuild_dashboard=False,
        )

        payload = agent_bootstrap_payload(self.root, build=False, agent_id="agent_t5")

        self.assertEqual(payload["scope"]["mode"], "assignment")
        self.assertEqual(payload["scope"]["primary_context"], "assignment_scope")
        self.assertEqual(payload["scope"]["identity_source"], "explicit_agent")
        self.assertTrue(payload["scope"]["assignment_id"].startswith("assign_"))
        self.assertEqual(payload["assignment_scope"], payload["agent_scope"])
        self.assertEqual(payload["agent_scope"]["agent_id"], "agent_t5")
        self.assertEqual(payload["agent_scope"]["option_id"], "option_t5")
        self.assertEqual(payload["agent_scope"]["assignment_id"], payload["scope"]["assignment_id"])
        self.assertEqual(payload["agent_scope"]["current_node"], "exp_t5")
        self.assertEqual(payload["agent_scope"]["next_actions"], ["Review T5 output"])
        self.assertEqual(payload["agent_scope"]["agent_focus"]["current_focus_node"], "exp_t5")
        self.assertEqual(payload["agent_scope"]["agent_focus"]["source"], "assignment")
        self.assertFalse(payload["agent_scope"]["uses_global_current_state"])
        self.assertEqual(payload["agent_scope"]["option_context"]["option"]["id"], "option_t5")
        self.assertEqual(payload["focus"]["current_focus_node"], "exp_other")
        self.assertTrue(payload["focus"]["coordinator_only"])
        self.assertIn("agent-session-context", payload["agent_scope"]["handoff"]["commands"]["read_context"])
        self.assertEqual(
            payload["mutation_guidance"]["hierarchy_policy"]["workstream_file_hint"]["problem.parent"],
            "option_t5",
        )

    def test_agent_bootstrap_cli_accepts_agent_scope(self) -> None:
        start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=self.tmp_root / "worktrees" / "agent_t5",
            rebuild_dashboard=False,
        )
        out = subprocess.run(
            [
                *cli_command("bootstrap"),
                "--root",
                str(self.root),
                "--agent",
                "agent_t5",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(payload["scope"]["mode"], "assignment")
        self.assertEqual(payload["scope"]["primary_context"], "assignment_scope")
        self.assertEqual(payload["scope"]["identity_source"], "explicit_agent")
        self.assertTrue(payload["scope"]["assignment_id"].startswith("assign_"))
        self.assertEqual(payload["assignment_scope"], payload["agent_scope"])
        self.assertEqual(payload["agent_scope"]["agent_id"], "agent_t5")
        self.assertTrue(payload["focus"]["coordinator_only"])

    def test_agent_bootstrap_resolves_assignment_from_environment(self) -> None:
        session = start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=self.tmp_root / "worktrees" / "agent_t5",
            rebuild_dashboard=False,
        )

        with patch.dict(
            os.environ,
            {
                "RESEARCH_COCKPIT_ASSIGNMENT_ID": session["assignment_id"],
                "RESEARCH_COCKPIT_AGENT_ID": "agent_t5",
            },
        ):
            payload = agent_bootstrap_payload(self.root, build=False)

        self.assertEqual(payload["scope"]["mode"], "assignment")
        self.assertEqual(payload["scope"]["primary_context"], "assignment_scope")
        self.assertEqual(payload["scope"]["identity_source"], "env_assignment")
        self.assertEqual(payload["scope"]["assignment_id"], session["assignment_id"])
        self.assertEqual(payload["assignment_scope"], payload["agent_scope"])
        self.assertEqual(payload["assignment_scope"]["current_node"], "option_t5")
        self.assertEqual(payload["agent_scope"]["handoff"]["launch_env"]["RESEARCH_COCKPIT_ASSIGNMENT_ID"], session["assignment_id"])

    def test_agent_bootstrap_rejects_conflicting_env_assignment_and_agent(self) -> None:
        session = start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=self.tmp_root / "worktrees" / "agent_t5",
            rebuild_dashboard=False,
        )

        with patch.dict(
            os.environ,
            {
                "RESEARCH_COCKPIT_ASSIGNMENT_ID": session["assignment_id"],
                "RESEARCH_COCKPIT_AGENT_ID": "agent_other",
            },
        ):
            with self.assertRaises(BootstrapIdentityError) as ctx:
                agent_bootstrap_payload(self.root, build=False)

        self.assertEqual(ctx.exception.payload["error"], "assignment_identity_mismatch")
        self.assertEqual(ctx.exception.payload["assignment_id"], session["assignment_id"])
        self.assertEqual(ctx.exception.payload["assignment_agent_id"], "agent_t5")
        self.assertEqual(ctx.exception.payload["agent_id"], "agent_other")

    def test_agent_bootstrap_cli_rejects_ambiguous_active_assignments_without_identity(self) -> None:
        problem_path = self.root / "graph" / "nodes" / "problem_text.yaml"
        problem = load_yaml(problem_path)
        problem["children"] = [*problem.get("children", []), "option_other"]
        save_yaml(problem_path, problem)
        write_node(
            self.root,
            {
                "id": "option_other",
                "type": "option",
                "title": "Other option",
                "status": "active",
                "parent": "problem_text",
            },
        )
        start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_a",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=self.tmp_root / "worktrees" / "agent_a",
            rebuild_dashboard=False,
        )
        start_agent_session(
            self.root,
            option_id="option_other",
            agent_id="agent_b",
            objective="Run other branch",
            branch="agent/option_other",
            worktree=self.tmp_root / "worktrees" / "agent_b",
            rebuild_dashboard=False,
        )
        env = os.environ.copy()
        env.pop("RESEARCH_COCKPIT_ASSIGNMENT_ID", None)
        env.pop("RESEARCH_COCKPIT_AGENT_ID", None)

        out = subprocess.run(
            [*cli_command("bootstrap"), "--root", str(self.root), "--json"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        self.assertEqual(out.returncode, 1)
        payload = json.loads(out.stdout)
        self.assertEqual(payload["error"], "assignment_identity_required")
        self.assertIn("assignment_ids", payload)

    def test_agent_bootstrap_surfaces_missing_progress_warning_without_crashing(self) -> None:
        save_yaml(
            self.root / "runs" / "run_missing_progress.yaml",
            {
                "run_id": "run_missing_progress",
                "status": "running",
                "experiment_id": "exp_t5",
                "started_at": "2026-05-27T01:00:00Z",
                "progress_file": "artifacts/exp_t5/run_missing_progress/progress.json",
            },
        )

        payload = agent_bootstrap_payload(self.root, build=False)

        self.assertTrue(payload["validation"]["ok"])
        self.assertEqual(payload["run_overview"]["running"][0]["run_id"], "run_missing_progress")
        self.assertIn("does not exist", payload["run_overview"]["running"][0]["progress"]["schema_warnings"][0])

    def test_agent_bootstrap_reports_malformed_run_without_crashing(self) -> None:
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        (self.root / "runs" / "broken.yaml").write_text("[\n", encoding="utf-8")

        payload = agent_bootstrap_payload(self.root, build=False)

        self.assertFalse(payload["validation"]["ok"])
        self.assertTrue(any("runs/broken.yaml: YAML parse error" in error for error in payload["validation"]["errors"]))
        self.assertTrue(
            any("runs/broken.yaml: YAML parse error" in warning for warning in payload["run_overview"]["warnings"])
        )

    def test_agent_bootstrap_cli_json_builds_when_requested(self) -> None:
        command = "bootstrap"

        default_out = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        build_out = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--json", "--build"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(default_out.returncode, 0)
        self.assertEqual(build_out.returncode, 0)
        default_payload = json.loads(default_out.stdout)
        build_payload = json.loads(build_out.stdout)
        self.assertTrue(default_payload["validation"]["ok"])
        self.assertTrue(build_payload["context_paths"]["agent_context_pack"]["exists"])
        self.assertTrue((self.root / "dashboards" / "agent_context_pack.json").exists())

    def test_init_cli_json_can_build_dashboards(self) -> None:
        default_root = self.tmp_root / "init_default"
        build_root = self.tmp_root / "init_build"

        default_out = subprocess.run(
            [*cli_command("init"), "--root", str(default_root), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        build_out = subprocess.run(
            [*cli_command("init"), "--root", str(build_root), "--build", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(default_out.returncode, 0, default_out.stdout + default_out.stderr)
        self.assertEqual(build_out.returncode, 0, build_out.stdout + build_out.stderr)
        default_payload = json.loads(default_out.stdout)
        build_payload = json.loads(build_out.stdout)
        self.assertEqual(default_payload["root"], str(default_root))
        self.assertFalse(default_payload["built"])
        self.assertTrue(build_payload["built"])
        self.assertTrue((build_root / "dashboards" / "agent_context_pack.json").exists())
        self.assertFalse((default_root / "dashboards" / "agent_context_pack.json").exists())

    def test_agent_bootstrap_cli_reports_plugin_path_from_cwd(self) -> None:
        research_repo = self.tmp_root / "research_repo"
        research_repo.mkdir()
        out = subprocess.run(
            [*cli_command("bootstrap"), "--root", str(self.root), "--json"],
            cwd=research_repo,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        self.assertTrue(payload["plugin"]["exists"])
        self.assertNotEqual(payload["plugin"]["path"], ".")

    def test_agent_bootstrap_dependency_error_is_clear(self) -> None:
        missing = missing_runtime_dependencies({"definitely_missing_module_for_test": "example-package"})

        self.assertEqual(missing, ["definitely_missing_module_for_test"])
        message = format_dependency_error(missing)
        self.assertIn("definitely_missing_module_for_test", message)
        self.assertIn("pip install -e .", message)

    def test_run_lifecycle_commands_create_update_complete_and_context(self) -> None:
        result = create_run(
            self.root,
            run_id="run_t5_smoke",
            experiment_id="exp_t5",
            status="running",
            started_at="2026-05-27T01:00:00Z",
            launcher="tmux",
            command="python train.py --smoke",
            tmux_session="t5-smoke",
            pid=1234,
            log_root="artifacts/exp_t5/run_t5_smoke/logs",
            output_root="artifacts/exp_t5/run_t5_smoke",
            monitor_command="tail -f artifacts/exp_t5/run_t5_smoke/logs/run.log",
            stop_command="tmux kill-session -t t5-smoke",
            progress_file="artifacts/exp_t5/run_t5_smoke/progress.json",
            config_file="configs/exp_t5_smoke.yaml",
            rebuild_dashboard=False,
        )

        run_path = self.root / "runs" / "run_t5_smoke.yaml"
        saved = load_yaml(run_path)
        self.assertTrue(result["changed"])
        self.assertEqual(saved["run_id"], "run_t5_smoke")
        self.assertEqual(saved["status"], "running")
        self.assertEqual(saved["experiment_id"], "exp_t5")
        self.assertEqual(saved["pid"], 1234)
        self.assertFalse((self.root / "dashboards").exists())

        update_result = update_run(
            self.root,
            run_id="run_t5_smoke",
            status="failed",
            progress_file="artifacts/exp_t5/run_t5_smoke/progress_failed.json",
            rebuild_dashboard=False,
        )
        updated = load_yaml(run_path)
        self.assertEqual(update_result["before"]["status"], "running")
        self.assertEqual(update_result["after"]["status"], "failed")
        self.assertEqual(updated["status"], "failed")
        self.assertEqual(updated["progress_file"], "artifacts/exp_t5/run_t5_smoke/progress_failed.json")

        complete_result = complete_run(
            self.root,
            run_id="run_t5_smoke",
            status="completed",
            finished_at="2026-05-27T02:00:00Z",
            rebuild_dashboard=False,
        )
        completed = load_yaml(run_path)
        self.assertEqual(complete_result["after"]["status"], "completed")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["finished_at"], "2026-05-27T02:00:00Z")

        list_payload = list_runs_payload(self.root, experiment_id="exp_t5")
        self.assertEqual(list_payload["count"], 1)
        self.assertEqual(list_payload["runs"][0]["run_id"], "run_t5_smoke")
        self.assertEqual(list_payload["runs"][0]["experiment"]["title"], "T5 ablation")

        context = run_context_payload(self.root, run_id="run_t5_smoke")
        self.assertEqual(context["run"]["status"], "completed")
        self.assertEqual(context["experiment"]["id"], "exp_t5")
        self.assertEqual(context["monitor"]["progress_file"], "artifacts/exp_t5/run_t5_smoke/progress_failed.json")
        self.assertEqual(context["control"]["stop_command"], "tmux kill-session -t t5-smoke")

    def test_progress_heartbeat_schema_supports_unknown_total_and_stale(self) -> None:
        progress_path = self.root / "artifacts" / "exp_t5" / "run_t5" / "progress.json"
        progress_path.parent.mkdir(parents=True)
        progress_path.write_text(
            json.dumps(
                {
                    "status": "running",
                    "completed_steps": 12,
                    "total_steps": None,
                    "last_update": "2026-05-27T00:00:00Z",
                    "current_stage": "synthesis",
                    "latest_artifact": "artifacts/exp_t5/run_t5/partial.json",
                    "warnings": ["cache warmup slower than expected"],
                }
            ),
            encoding="utf-8",
        )

        progress = load_progress_heartbeat(
            self.root,
            "artifacts/exp_t5/run_t5/progress.json",
            now=datetime(2026, 5, 27, 2, 0, tzinfo=timezone.utc),
        )

        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertEqual(progress["schema_version"], "progress_heartbeat_v1")
        self.assertEqual(progress["status"], "running")
        self.assertEqual(progress["completed_steps"], 12)
        self.assertNotIn("percent_complete", progress)
        self.assertTrue(progress["possibly_stale"])
        self.assertEqual(progress["current_stage"], "synthesis")
        self.assertEqual(progress["latest_artifact"], "artifacts/exp_t5/run_t5/partial.json")
        self.assertEqual(progress["warnings"], ["cache warmup slower than expected"])

    def test_progress_heartbeat_reports_missing_and_malformed_files(self) -> None:
        malformed_path = self.root / "artifacts" / "exp_t5" / "run_bad" / "progress.json"
        malformed_path.parent.mkdir(parents=True)
        malformed_path.write_text("{", encoding="utf-8")

        malformed = load_progress_heartbeat(self.root, "artifacts/exp_t5/run_bad/progress.json")
        missing = load_progress_heartbeat(self.root, "artifacts/exp_t5/missing/progress.json")
        unsafe = load_progress_heartbeat(self.root, "../outside/progress.json")

        self.assertIsNotNone(malformed)
        self.assertIsNotNone(missing)
        self.assertIsNotNone(unsafe)
        assert malformed is not None and missing is not None and unsafe is not None
        self.assertTrue(malformed["exists"])
        self.assertIn("JSON parse error", malformed["schema_warnings"][0])
        self.assertFalse(missing["exists"])
        self.assertIn("does not exist", missing["schema_warnings"][0])
        self.assertFalse(unsafe["exists"])
        self.assertIn("relative path inside the data root", unsafe["schema_warnings"][0])

    def test_progress_heartbeat_rejects_paths_resolving_outside_root(self) -> None:
        external_dir = self.tmp_root / "external_progress"
        external_dir.mkdir()
        link_dir = self.root / "artifacts" / "linked_progress"
        link_dir.parent.mkdir(parents=True)
        try:
            os.symlink(external_dir, link_dir, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        progress = load_progress_heartbeat(self.root, "artifacts/linked_progress/progress.json")

        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertFalse(progress["exists"])
        self.assertIn("resolve inside the data root", progress["schema_warnings"][0])

    def test_progress_heartbeat_reports_path_resolution_errors(self) -> None:
        with patch("research_cockpit.progress.Path.resolve", side_effect=RuntimeError("symlink loop")):
            progress = load_progress_heartbeat(self.root, "artifacts/exp_t5/progress.json")

        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertFalse(progress["exists"])
        self.assertIn("could not be resolved inside the data root", progress["schema_warnings"][0])

    def test_progress_heartbeat_rejects_fractional_steps_and_non_string_fields(self) -> None:
        progress_path = self.root / "artifacts" / "exp_t5" / "run_invalid" / "progress.json"
        progress_path.parent.mkdir(parents=True)
        progress_path.write_text(
            json.dumps(
                {
                    "status": "running",
                    "completed_steps": 1.9,
                    "total_steps": 4,
                    "last_update": "2026-05-27T00:00:00Z",
                    "current_stage": {"name": "train"},
                    "latest_artifact": ["artifact.json"],
                }
            ),
            encoding="utf-8",
        )

        progress = load_progress_heartbeat(self.root, "artifacts/exp_t5/run_invalid/progress.json")

        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertNotIn("completed_steps", progress)
        self.assertNotIn("percent_complete", progress)
        self.assertNotIn("current_stage", progress)
        self.assertNotIn("latest_artifact", progress)
        self.assertIn("completed_steps must be an integer", progress["schema_warnings"])
        self.assertIn("current_stage must be a string", progress["schema_warnings"])
        self.assertIn("latest_artifact must be a string", progress["schema_warnings"])

    def test_run_context_includes_progress_heartbeat_summary(self) -> None:
        progress_path = self.root / "artifacts" / "exp_t5" / "run_t5_progress" / "progress.json"
        progress_path.parent.mkdir(parents=True)
        progress_path.write_text(
            json.dumps(
                {
                    "status": "running",
                    "completed_steps": 2,
                    "total_steps": 4,
                    "last_update": "2999-01-01T00:00:00Z",
                    "current_stage": "train",
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        create_run(
            self.root,
            run_id="run_t5_progress",
            experiment_id="exp_t5",
            status="running",
            progress_file="artifacts/exp_t5/run_t5_progress/progress.json",
            rebuild_dashboard=False,
        )

        context = run_context_payload(self.root, run_id="run_t5_progress")
        compact = run_context_payload(self.root, run_id="run_t5_progress", compact=True)

        self.assertEqual(context["monitor"]["progress"]["percent_complete"], 50.0)
        self.assertEqual(context["monitor"]["progress"]["current_stage"], "train")
        self.assertFalse(context["monitor"]["progress"]["possibly_stale"])
        self.assertEqual(compact["progress"]["percent_complete"], 50.0)

    def test_run_context_surfaces_progress_schema_warnings(self) -> None:
        progress_path = self.root / "artifacts" / "exp_t5" / "run_bad_progress" / "progress.json"
        progress_path.parent.mkdir(parents=True)
        progress_path.write_text("{", encoding="utf-8")
        create_run(
            self.root,
            run_id="run_bad_progress",
            experiment_id="exp_t5",
            status="running",
            progress_file="artifacts/exp_t5/run_bad_progress/progress.json",
            rebuild_dashboard=False,
        )

        context = run_context_payload(self.root, run_id="run_bad_progress")
        compact = run_context_payload(self.root, run_id="run_bad_progress", compact=True)
        human = subprocess.run(
            [*cli_command("run-context"), "--root", str(self.root), "--id", "run_bad_progress"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertIn("JSON parse error", context["monitor"]["progress"]["schema_warnings"][0])
        self.assertIn("JSON parse error", compact["progress"]["schema_warnings"][0])
        self.assertEqual(human.returncode, 0, human.stdout + human.stderr)
        self.assertIn("Progress: unavailable", human.stdout)

    def test_gate_result_schema_normalizes_passed_warning_only_gate(self) -> None:
        gate = normalize_gate_result(
            {
                "gate_type": "dataset_check",
                "passed": True,
                "expected": {"rows": 100},
                "observed": {"rows": 100},
                "warnings": ["class imbalance is high"],
                "next_allowed_action": "precompute",
            },
            path="artifacts/exp_t5/run_gate/gate_result.json",
            experiment_id="exp_t5",
            run_id="run_gate",
        )

        self.assertTrue(gate["valid"])
        self.assertFalse(gate["blocks_next_action"])
        self.assertEqual(gate["schema_version"], "gate_result_v1")
        self.assertEqual(gate["gate_type"], "dataset_check")
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["expected"], {"rows": 100})
        self.assertEqual(gate["observed"], {"rows": 100})
        self.assertEqual(gate["warnings"], ["class imbalance is high"])
        self.assertEqual(gate["next_allowed_action"], "precompute")
        self.assertEqual(gate["experiment_id"], "exp_t5")
        self.assertEqual(gate["run_id"], "run_gate")

    def test_gate_result_schema_blocks_failed_or_fatal_gate(self) -> None:
        failed = normalize_gate_result(
            {
                "gate_type": "cache_check",
                "passed": False,
                "fatal_failures": {"missing_cache": "embeddings_v4"},
                "warnings": [],
            }
        )
        fatal = normalize_gate_result(
            {
                "gate_type": "smoke_check",
                "passed": True,
                "fatal_failures": {"nan_loss": True},
            }
        )

        self.assertTrue(failed["valid"])
        self.assertTrue(failed["blocks_next_action"])
        self.assertTrue(fatal["valid"])
        self.assertTrue(fatal["blocks_next_action"])

    def test_gate_result_schema_normalizes_preflight_resource_fields(self) -> None:
        passed = normalize_gate_result(
            {
                "gate_type": "preflight",
                "passed": True,
                "disk_available_gb": 1200,
                "estimated_required_gb": 800,
                "gpu_ids": [0, 1],
                "port": 7860,
                "port_available": True,
                "cache_dir": "cache/precompute",
                "cache_dir_exists": True,
                "cache_available_gb": 500,
                "conflicting_processes": [],
                "next_allowed_action": "full_run",
            }
        )
        failed = normalize_gate_result(
            {
                "gate_type": "preflight",
                "passed": False,
                "preflight": {
                    "disk_available_gb": 100,
                    "estimated_required_gb": 800,
                    "gpu_ids": [],
                    "port_available": False,
                    "cache_dir": "cache/precompute",
                    "cache_dir_exists": False,
                    "conflicting_processes": ["pid 1234"],
                },
                "fatal_failures": {"disk": "insufficient"},
                "next_allowed_action": "full_run",
            }
        )

        self.assertTrue(passed["valid"])
        self.assertFalse(passed["blocks_next_action"])
        self.assertEqual(passed["preflight"]["disk_available_gb"], 1200)
        self.assertEqual(passed["preflight"]["estimated_required_gb"], 800)
        self.assertEqual(passed["preflight"]["gpu_ids"], [0, 1])
        self.assertEqual(passed["preflight"]["port"], 7860)
        self.assertTrue(passed["preflight"]["port_available"])
        self.assertEqual(passed["preflight"]["cache_dir"], "cache/precompute")
        self.assertTrue(passed["preflight"]["cache_dir_exists"])
        self.assertEqual(passed["preflight"]["cache_available_gb"], 500)
        self.assertEqual(passed["preflight"]["conflicting_processes"], [])
        self.assertEqual(passed["next_allowed_action"], "full_run")
        self.assertTrue(failed["valid"])
        self.assertTrue(failed["blocks_next_action"])
        self.assertEqual(failed["blocked_actions"], ["full_run"])
        self.assertEqual(failed["preflight"]["conflicting_processes"], ["pid 1234"])

    def test_gate_result_schema_reports_bad_preflight_field_shapes(self) -> None:
        gate = normalize_gate_result(
            {
                "gate_type": "preflight",
                "passed": True,
                "preflight": [],
                "disk_available_gb": "lots",
                "gpu_ids": "0,1",
                "port": "7860",
                "port_available": "yes",
                "cache_dir": 42,
                "cache_dir_exists": "true",
                "conflicting_processes": "none",
            }
        )

        self.assertFalse(gate["valid"])
        self.assertTrue(gate["blocks_next_action"])
        self.assertIn("preflight must be a JSON object", gate["schema_warnings"])
        self.assertIn("disk_available_gb must be a number", gate["schema_warnings"])
        self.assertIn("gpu_ids must be a list", gate["schema_warnings"])
        self.assertIn("port must be an integer", gate["schema_warnings"])
        self.assertIn("port_available must be a boolean", gate["schema_warnings"])
        self.assertIn("cache_dir must be a string", gate["schema_warnings"])
        self.assertIn("cache_dir_exists must be a boolean", gate["schema_warnings"])
        self.assertIn("conflicting_processes must be a list", gate["schema_warnings"])

    def test_gate_result_schema_reports_malformed_and_unsafe_files(self) -> None:
        malformed_path = self.root / "artifacts" / "exp_t5" / "run_gate_bad" / "gate_result.json"
        malformed_path.parent.mkdir(parents=True)
        malformed_path.write_text("{", encoding="utf-8")
        valid_path = self.root / "artifacts" / "exp_t5" / "run_gate_ok" / "gate_result.json"
        valid_path.parent.mkdir(parents=True)
        valid_path.write_text(
            json.dumps(
                {
                    "gate_type": "preflight",
                    "passed": True,
                    "expected": {},
                    "observed": {"disk_available_gb": 1200},
                    "fatal_failures": {},
                    "warnings": [],
                    "next_allowed_action": "smoke",
                    "experiment_id": "exp_t5",
                    "run_id": "run_gate_ok",
                }
            ),
            encoding="utf-8",
        )

        valid = load_gate_result(self.root, "artifacts/exp_t5/run_gate_ok/gate_result.json")
        conflicting = load_gate_result(
            self.root,
            "artifacts/exp_t5/run_gate_ok/gate_result.json",
            experiment_id="exp_other",
            run_id="run_other",
        )
        malformed = load_gate_result(self.root, "artifacts/exp_t5/run_gate_bad/gate_result.json")
        missing = load_gate_result(self.root, "artifacts/exp_t5/missing/gate_result.json")
        unsafe = load_gate_result(self.root, "../outside/gate_result.json")

        self.assertIsNotNone(valid)
        self.assertIsNotNone(conflicting)
        self.assertIsNotNone(malformed)
        self.assertIsNotNone(missing)
        self.assertIsNotNone(unsafe)
        assert (
            valid is not None
            and conflicting is not None
            and malformed is not None
            and missing is not None
            and unsafe is not None
        )
        self.assertTrue(valid["valid"])
        self.assertFalse(valid["blocks_next_action"])
        self.assertEqual(valid["experiment_id"], "exp_t5")
        self.assertEqual(valid["run_id"], "run_gate_ok")
        self.assertFalse(conflicting["valid"])
        self.assertTrue(conflicting["blocks_next_action"])
        self.assertEqual(conflicting["experiment_id"], "exp_other")
        self.assertEqual(conflicting["run_id"], "run_other")
        self.assertIn("experiment_id does not match gate result file", conflicting["schema_warnings"])
        self.assertIn("run_id does not match gate result file", conflicting["schema_warnings"])
        self.assertFalse(malformed["valid"])
        self.assertTrue(malformed["blocks_next_action"])
        self.assertIn("JSON parse error", malformed["schema_warnings"][0])
        self.assertFalse(missing["exists"])
        self.assertIn("does not exist", missing["schema_warnings"][0])
        self.assertFalse(unsafe["exists"])
        self.assertIn("relative path inside the data root", unsafe["schema_warnings"][0])

    def test_gate_result_schema_reports_bad_field_shapes(self) -> None:
        gate = normalize_gate_result(
            {
                "gate_type": ["dataset_check"],
                "passed": "yes",
                "expected": [],
                "observed": [],
                "fatal_failures": [],
                "warnings": "warn",
                "next_allowed_action": {"action": "precompute"},
            }
        )

        self.assertFalse(gate["valid"])
        self.assertTrue(gate["blocks_next_action"])
        self.assertIn("gate_type must be a string", gate["schema_warnings"])
        self.assertIn("passed must be a boolean", gate["schema_warnings"])
        self.assertIn("expected must be a JSON object", gate["schema_warnings"])
        self.assertIn("observed must be a JSON object", gate["schema_warnings"])
        self.assertIn("fatal_failures must be a JSON object", gate["schema_warnings"])
        self.assertIn("warnings must be a list", gate["schema_warnings"])
        self.assertIn("next_allowed_action must be a string", gate["schema_warnings"])

        empty_string_shapes = normalize_gate_result(
            {
                "gate_type": "dataset_check",
                "passed": True,
                "expected": "",
                "observed": "",
                "fatal_failures": "",
                "warnings": "",
            }
        )

        self.assertFalse(empty_string_shapes["valid"])
        self.assertTrue(empty_string_shapes["blocks_next_action"])
        self.assertIn("expected must be a JSON object", empty_string_shapes["schema_warnings"])
        self.assertIn("observed must be a JSON object", empty_string_shapes["schema_warnings"])
        self.assertIn("fatal_failures must be a JSON object", empty_string_shapes["schema_warnings"])
        self.assertIn("warnings must be a list", empty_string_shapes["schema_warnings"])

    def test_record_gate_result_writes_standard_file_and_context_summary(self) -> None:
        create_run(
            self.root,
            run_id="run_t5_gate",
            experiment_id="exp_t5",
            status="running",
            rebuild_dashboard=False,
        )

        result = record_gate_result(
            self.root,
            gate_id="gate_t5_dataset",
            experiment_id="exp_t5",
            run_id="run_t5_gate",
            gate_type="dataset_check",
            passed=True,
            expected={"rows": 100},
            observed={"rows": 100},
            warnings=["class imbalance is high"],
            next_allowed_action="precompute",
            rebuild_dashboard=False,
            show_diff=True,
        )

        gate_file = self.root / "gate_results" / "gate_t5_dataset.json"
        record_file = self.root / "gate_results" / "gate_t5_dataset.yaml"
        saved_gate = json.loads(gate_file.read_text(encoding="utf-8"))
        saved_record = load_yaml(record_file)
        run_context = run_context_payload(self.root, run_id="run_t5_gate", compact=True)
        node_context = node_context_payload(self.root, node_id="exp_t5", compact=True)
        agent_context = build_agent_context(self.root, load_nodes(self.root))

        self.assertTrue(result["changed"])
        self.assertEqual(result["gate_id"], "gate_t5_dataset")
        self.assertEqual(saved_gate["gate_type"], "dataset_check")
        self.assertTrue(saved_gate["passed"])
        self.assertEqual(saved_record["gate_result_file"], "gate_results/gate_t5_dataset.json")
        self.assertEqual(saved_record["experiment_id"], "exp_t5")
        self.assertEqual(saved_record["run_id"], "run_t5_gate")
        self.assertIn("gate_t5_dataset.yaml", result["diff"])
        self.assertIn("gate_t5_dataset.json", result["diff"])
        self.assertEqual(run_context["gate_results"]["summary"]["total_count"], 1)
        self.assertFalse(run_context["gate_results"]["latest"]["blocks_next_action"])
        self.assertEqual(node_context["gate_summary"]["latest_gate_id"], "gate_t5_dataset")
        self.assertEqual(agent_context["gate_overview"]["total_count"], 1)

    def test_record_gate_result_writes_preflight_fields_and_blocks_full_run(self) -> None:
        create_run(
            self.root,
            run_id="run_t5_preflight",
            experiment_id="exp_t5",
            status="queued",
            rebuild_dashboard=False,
        )

        result = record_gate_result(
            self.root,
            gate_id="gate_t5_preflight",
            experiment_id="exp_t5",
            run_id="run_t5_preflight",
            gate_type="preflight",
            passed=False,
            preflight={
                "disk_available_gb": 100,
                "estimated_required_gb": 800,
                "gpu_ids": [0],
                "port_available": True,
                "cache_dir": "cache/precompute",
                "cache_dir_exists": True,
                "conflicting_processes": ["python train.py"],
            },
            fatal_failures={"disk": "insufficient"},
            next_allowed_action="full_run",
            rebuild_dashboard=False,
        )

        saved_gate = json.loads((self.root / "gate_results" / "gate_t5_preflight.json").read_text(encoding="utf-8"))
        run_context = run_context_payload(self.root, run_id="run_t5_preflight", compact=True)
        experiment_context = node_context_payload(self.root, node_id="exp_t5")

        self.assertTrue(result["gate_result"]["blocks_next_action"])
        self.assertEqual(result["gate_result"]["blocked_actions"], ["full_run"])
        self.assertEqual(saved_gate["preflight"]["disk_available_gb"], 100)
        self.assertEqual(saved_gate["preflight"]["cache_dir"], "cache/precompute")
        self.assertEqual(run_context["gate_results"]["latest"]["preflight"]["estimated_required_gb"], 800)
        self.assertEqual(run_context["gate_results"]["latest"]["blocked_actions"], ["full_run"])
        self.assertEqual(
            experiment_context["type_context"]["gate_results"]["blocking"][0]["blocked_actions"],
            ["full_run"],
        )

    def test_ingest_gate_result_links_artifact_file_and_surfaces_blocking_gate(self) -> None:
        gate_path = self.root / "artifacts" / "exp_t5" / "run_t5_gate_failed" / "gate_result.json"
        gate_path.parent.mkdir(parents=True)
        gate_path.write_text(
            json.dumps(
                {
                    "gate_type": "smoke_check",
                    "passed": False,
                    "expected": {"exit_code": 0},
                    "observed": {"exit_code": 1},
                    "fatal_failures": {"exit_code": 1},
                    "warnings": [],
                    "next_allowed_action": "inspect_logs",
                }
            ),
            encoding="utf-8",
        )
        create_run(
            self.root,
            run_id="run_t5_gate_failed",
            experiment_id="exp_t5",
            status="failed",
            rebuild_dashboard=False,
        )
        create_artifact(
            self.root,
            artifact_id="artifact_t5_gate_failed",
            title="T5 gate failed bundle",
            status="done",
            path="artifacts/exp_t5/run_t5_gate_failed",
            links={"gate_result": "artifacts/exp_t5/run_t5_gate_failed/gate_result.json"},
            link_to=["exp_t5"],
            rebuild_dashboard=False,
        )

        result = ingest_gate_result(
            self.root,
            gate_id="gate_t5_smoke_failed",
            gate_result_file="artifacts/exp_t5/run_t5_gate_failed/gate_result.json",
            run_id="run_t5_gate_failed",
            artifact_id="artifact_t5_gate_failed",
            rebuild_dashboard=False,
        )

        saved_record = load_yaml(self.root / "gate_results" / "gate_t5_smoke_failed.yaml")
        run_context = run_context_payload(self.root, run_id="run_t5_gate_failed", compact=True)
        experiment_context = node_context_payload(self.root, node_id="exp_t5")
        agent_context = build_agent_context(self.root, load_nodes(self.root))

        self.assertTrue(result["changed"])
        self.assertEqual(result["experiment_id"], "exp_t5")
        self.assertEqual(saved_record["artifact_id"], "artifact_t5_gate_failed")
        self.assertEqual(run_context["gate_results"]["summary"]["blocking_count"], 1)
        self.assertTrue(run_context["gate_results"]["latest"]["blocks_next_action"])
        self.assertEqual(run_context["gate_results"]["latest"]["artifact_id"], "artifact_t5_gate_failed")
        self.assertEqual(
            experiment_context["type_context"]["gate_results"]["blocking"][0]["gate_id"],
            "gate_t5_smoke_failed",
        )
        self.assertEqual(agent_context["gate_overview"]["blocking_count"], 1)

    def test_ingest_gate_result_allows_malformed_existing_file_as_blocking_context(self) -> None:
        gate_path = self.root / "artifacts" / "exp_t5" / "run_t5_gate_malformed" / "gate_result.json"
        gate_path.parent.mkdir(parents=True)
        gate_path.write_text("{", encoding="utf-8")

        result = ingest_gate_result(
            self.root,
            gate_id="gate_t5_malformed",
            gate_result_file="artifacts/exp_t5/run_t5_gate_malformed/gate_result.json",
            experiment_id="exp_t5",
            rebuild_dashboard=False,
        )
        experiment_context = node_context_payload(self.root, node_id="exp_t5")

        self.assertTrue(result["changed"])
        self.assertTrue(result["gate_result"]["blocks_next_action"])
        self.assertIn("JSON parse error", result["gate_result"]["schema_warnings"][0])
        self.assertEqual(
            experiment_context["type_context"]["gate_results"]["blocking"][0]["gate_id"],
            "gate_t5_malformed",
        )

    def test_gate_result_commands_reject_missing_unsafe_or_mismatched_artifacts(self) -> None:
        create_run(
            self.root,
            run_id="run_t5_gate_reject",
            experiment_id="exp_t5",
            status="running",
            rebuild_dashboard=False,
        )
        write_node(
            self.root,
            {
                "id": "exp_other",
                "type": "experiment",
                "title": "Other experiment",
                "status": "planned",
                "parent": "option_t5",
                "linked_artifacts": ["artifact_other_gate"],
            },
        )
        write_node(
            self.root,
            {
                "id": "artifact_other_gate",
                "type": "artifact",
                "title": "Other gate bundle",
                "status": "done",
                "path": "artifacts/exp_other/run_gate",
                "links": {
                    "gate_result": "artifacts/exp_other/run_gate/gate_result.json",
                },
            },
        )

        with self.assertRaises((ValueError, FileNotFoundError)):
            ingest_gate_result(
                self.root,
                gate_id="gate_missing",
                gate_result_file="artifacts/exp_t5/missing_gate_result.json",
                experiment_id="exp_t5",
                rebuild_dashboard=False,
            )
        with self.assertRaises(ValueError):
            ingest_gate_result(
                self.root,
                gate_id="gate_unsafe",
                gate_result_file="../outside/gate_result.json",
                experiment_id="exp_t5",
                rebuild_dashboard=False,
            )
        with self.assertRaises(ValueError):
            record_gate_result(
                self.root,
                gate_id="gate_bad_path",
                experiment_id="exp_t5",
                gate_type="smoke_check",
                passed=True,
                gate_result_file="graph/nodes/gate_bad_path.yaml",
                rebuild_dashboard=False,
            )
        with self.assertRaises(ValueError):
            record_gate_result(
                self.root,
                gate_id="gate_bad_artifact",
                experiment_id="exp_t5",
                gate_type="smoke_check",
                passed=True,
                artifact_id="artifact_other_gate",
                rebuild_dashboard=False,
            )

        gate_path = self.root / "artifacts" / "exp_t5" / "run_t5_gate_reject" / "gate_result.json"
        gate_path.parent.mkdir(parents=True)
        gate_path.write_text(
            json.dumps({"gate_type": "smoke_check", "passed": True}),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            ingest_gate_result(
                self.root,
                gate_id="gate_ingest_bad_artifact",
                gate_result_file="artifacts/exp_t5/run_t5_gate_reject/gate_result.json",
                run_id="run_t5_gate_reject",
                artifact_id="artifact_other_gate",
                rebuild_dashboard=False,
            )

    def test_gate_context_blocks_manual_records_with_disallowed_paths(self) -> None:
        save_yaml(
            self.root / "gate_results" / "gate_t5_manual_bad.yaml",
            {
                "schema_version": "gate_result_record_v1",
                "gate_id": "gate_t5_manual_bad",
                "experiment_id": "exp_t5",
                "gate_result_file": "current_state.json",
                "recorded_at": "2026-05-27T00:00:00Z",
            },
        )

        experiment_context = node_context_payload(self.root, node_id="exp_t5")
        gate_context = experiment_context["type_context"]["gate_results"]
        signature_text = str(dashboard_watch_signature(self.root))

        self.assertEqual(gate_context["summary"]["blocking_count"], 1)
        self.assertEqual(gate_context["blocking"][0]["gate_id"], "gate_t5_manual_bad")
        self.assertIn("gate_result_file must live under gate_results/ or artifacts/", gate_context["warnings"][0])
        self.assertIn("gate_result_file must live under gate_results/ or artifacts/", gate_context["blocking"][0]["schema_warnings"][0])
        self.assertIn("current_state.json", signature_text)
        self.assertIn("invalid", signature_text)

    def test_gate_context_filters_warnings_to_requested_scope(self) -> None:
        write_node(
            self.root,
            {
                "id": "exp_other_gate_scope",
                "type": "experiment",
                "title": "Other scoped gate experiment",
                "status": "planned",
                "parent": "option_t5",
            },
        )
        save_yaml(
            self.root / "gate_results" / "gate_other_manual_bad.yaml",
            {
                "schema_version": "gate_result_record_v1",
                "gate_id": "gate_other_manual_bad",
                "experiment_id": "exp_other_gate_scope",
                "gate_result_file": "current_state.json",
                "recorded_at": "2026-05-27T00:00:00Z",
            },
        )

        experiment_context = node_context_payload(self.root, node_id="exp_t5")
        other_context = node_context_payload(self.root, node_id="exp_other_gate_scope")

        self.assertEqual(experiment_context["type_context"]["gate_results"]["warnings"], [])
        self.assertEqual(other_context["type_context"]["gate_results"]["summary"]["blocking_count"], 1)
        self.assertIn(
            "gate_result_file must live under gate_results/ or artifacts/",
            other_context["type_context"]["gate_results"]["warnings"][0],
        )

    def test_gate_context_blocks_manual_records_missing_gate_result_file(self) -> None:
        save_yaml(
            self.root / "gate_results" / "gate_t5_missing_file.yaml",
            {
                "schema_version": "gate_result_record_v1",
                "gate_id": "gate_t5_missing_file",
                "experiment_id": "exp_t5",
                "recorded_at": "2026-05-27T00:00:00Z",
            },
        )

        experiment_context = node_context_payload(self.root, node_id="exp_t5")
        gate_context = experiment_context["type_context"]["gate_results"]

        self.assertEqual(gate_context["summary"]["blocking_count"], 1)
        self.assertEqual(gate_context["blocking"][0]["gate_id"], "gate_t5_missing_file")
        self.assertIn("missing gate_result_file", gate_context["warnings"][0])
        self.assertIn("missing gate_result_file", gate_context["blocking"][0]["schema_warnings"][0])

    def test_run_summaries_use_progress_heartbeat_for_stale_and_warnings(self) -> None:
        stale_path = self.root / "artifacts" / "exp_t5" / "run_progress_stale" / "progress.json"
        stale_path.parent.mkdir(parents=True)
        stale_path.write_text(
            json.dumps(
                {
                    "status": "running",
                    "completed_steps": 3,
                    "total_steps": 6,
                    "last_update": "2026-05-27T00:00:00Z",
                    "current_stage": "eval",
                }
            ),
            encoding="utf-8",
        )
        malformed_path = self.root / "artifacts" / "exp_t5" / "run_progress_bad" / "progress.json"
        malformed_path.parent.mkdir(parents=True)
        malformed_path.write_text("{", encoding="utf-8")
        terminal_path = self.root / "artifacts" / "exp_t5" / "run_progress_done" / "progress.json"
        terminal_path.parent.mkdir(parents=True)
        terminal_path.write_text(
            json.dumps(
                {
                    "status": "running",
                    "completed_steps": 6,
                    "total_steps": 6,
                    "last_update": "2026-05-27T00:00:00Z",
                    "current_stage": "done",
                }
            ),
            encoding="utf-8",
        )
        save_yaml(
            self.root / "runs" / "run_progress_stale.yaml",
            {
                "run_id": "run_progress_stale",
                "status": "running",
                "experiment_id": "exp_t5",
                "started_at": "2026-05-27T00:30:00Z",
                "progress_file": "artifacts/exp_t5/run_progress_stale/progress.json",
            },
        )
        save_yaml(
            self.root / "runs" / "run_progress_bad.yaml",
            {
                "run_id": "run_progress_bad",
                "status": "running",
                "experiment_id": "exp_t5",
                "started_at": "2026-05-27T00:30:00Z",
                "progress_file": "artifacts/exp_t5/run_progress_bad/progress.json",
            },
        )
        save_yaml(
            self.root / "runs" / "run_progress_done.yaml",
            {
                "run_id": "run_progress_done",
                "status": "completed",
                "experiment_id": "exp_t5",
                "started_at": "2026-05-27T00:30:00Z",
                "finished_at": "2026-05-27T01:00:00Z",
                "progress_file": "artifacts/exp_t5/run_progress_done/progress.json",
            },
        )

        summaries, warnings = build_run_summaries(
            self.root,
            load_nodes(self.root),
            now=datetime(2026, 5, 27, 2, 0, tzinfo=timezone.utc),
        )
        by_id = {summary["run_id"]: summary for summary in summaries}

        self.assertEqual(warnings, [])
        self.assertTrue(by_id["run_progress_stale"]["possibly_stale"])
        self.assertEqual(by_id["run_progress_stale"]["stale_reasons"], ["progress_heartbeat"])
        self.assertEqual(by_id["run_progress_stale"]["progress"]["percent_complete"], 50.0)
        self.assertEqual(by_id["run_progress_stale"]["progress"]["current_stage"], "eval")
        self.assertIn("JSON parse error", by_id["run_progress_bad"]["progress"]["schema_warnings"][0])
        self.assertFalse(by_id["run_progress_done"]["possibly_stale"])
        self.assertNotIn("stale_reasons", by_id["run_progress_done"])
        self.assertFalse(by_id["run_progress_done"]["progress"]["possibly_stale"])

    def test_create_run_rejects_invalid_experiment_id(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            create_run(
                self.root,
                run_id="run_missing",
                experiment_id="missing_experiment",
                status="queued",
                rebuild_dashboard=False,
            )

        self.assertIn("missing_experiment", str(ctx.exception))
        self.assertFalse((self.root / "runs" / "run_missing.yaml").exists())

    def test_complete_run_supports_cancelled_status(self) -> None:
        create_run(
            self.root,
            run_id="run_cancel",
            experiment_id="exp_t5",
            status="running",
            rebuild_dashboard=False,
        )

        result = complete_run(
            self.root,
            run_id="run_cancel",
            status="cancelled",
            finished_at="2026-05-27T03:00:00Z",
            rebuild_dashboard=False,
        )

        saved = load_yaml(self.root / "runs" / "run_cancel.yaml")
        self.assertEqual(result["after"]["status"], "cancelled")
        self.assertEqual(saved["status"], "cancelled")
        self.assertEqual(saved["finished_at"], "2026-05-27T03:00:00Z")

    def test_complete_run_records_complete_audit_event(self) -> None:
        create_run(
            self.root,
            run_id="run_audit",
            experiment_id="exp_t5",
            status="running",
            rebuild_dashboard=False,
        )

        complete_run(
            self.root,
            run_id="run_audit",
            status="failed",
            finished_at="2026-05-27T04:00:00Z",
            rebuild_dashboard=False,
        )

        event = interaction_events(self.root)[-1]
        self.assertEqual(event["kind"], "complete_run")
        self.assertIn("complete-run", event["command"])
        self.assertEqual(event["after"]["status"], "failed")

    def test_run_lifecycle_cli_supports_json_compact_and_no_build(self) -> None:
        create_out = subprocess.run(
            [
                *cli_command("create-run"),
                "--root",
                str(self.root),
                "--id",
                "run_cli",
                "--experiment",
                "exp_t5",
                "--status",
                "running",
                "--launcher",
                "shell",
                "--command",
                "python run.py",
                "--monitor-command",
                "tail -f run.log",
                "--stop-command",
                "pkill -f run.py",
                "--no-build",
                "--json",
                "--compact",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        create_payload = json.loads(create_out.stdout)

        self.assertEqual(create_out.returncode, 0, create_out.stdout + create_out.stderr)
        self.assertTrue(create_payload["ok"])
        self.assertEqual(create_payload["target"], "run_cli")
        self.assertTrue(create_payload["changed"])
        self.assertEqual(create_payload["created"], ["run_cli"])
        self.assertFalse((self.root / "dashboards").exists())

        context_out = subprocess.run(
            [
                *cli_command("run-context"),
                "--root",
                str(self.root),
                "--id",
                "run_cli",
                "--json",
                "--compact",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        context_payload = json.loads(context_out.stdout)

        self.assertEqual(context_out.returncode, 0, context_out.stdout + context_out.stderr)
        self.assertEqual(context_payload["run_id"], "run_cli")
        self.assertEqual(context_payload["status"], "running")
        self.assertEqual(context_payload["experiment_id"], "exp_t5")
        self.assertEqual(context_payload["monitor_command"], "tail -f run.log")
        self.assertEqual(context_payload["stop_command"], "pkill -f run.py")

        list_out = subprocess.run(
            [
                *cli_command("list-runs"),
                "--root",
                str(self.root),
                "--json",
                "--compact",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        list_payload = json.loads(list_out.stdout)

        self.assertEqual(list_out.returncode, 0, list_out.stdout + list_out.stderr)
        self.assertEqual(list_payload["count"], 1)
        self.assertEqual(list_payload["runs"], ["run_cli"])

    def test_run_read_commands_accept_compact_without_json(self) -> None:
        create_run(
            self.root,
            run_id="run_human_compact",
            experiment_id="exp_t5",
            status="running",
            rebuild_dashboard=False,
        )

        for command in (
            [*cli_command("list-runs"), "--root", str(self.root), "--compact"],
            [*cli_command("run-context"), "--root", str(self.root), "--id", "run_human_compact", "--compact"],
        ):
            with self.subTest(command=command[3]):
                out = subprocess.run(command, capture_output=True, text=True, check=False)

                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                self.assertIn("run_human_compact", out.stdout)

    def test_active_resources_payload_lists_only_active_runs_by_default(self) -> None:
        save_yaml(
            self.root / "runs" / "run_gpu_active.yaml",
            {
                "run_id": "run_gpu_active",
                "status": "running",
                "experiment_id": "exp_t5",
                "tmux_session": "rc_gpu_active",
                "pid": 4242,
                "output_root": "artifacts/exp_t5/run_gpu_active",
                "log_root": "artifacts/exp_t5/run_gpu_active/logs",
                "progress_file": "artifacts/exp_t5/run_gpu_active/progress.json",
                "resources": {
                    "gpu_ids": ["0"],
                    "ports": [7860],
                    "worktree": "../agent-a",
                },
            },
        )
        save_yaml(
            self.root / "runs" / "run_done.yaml",
            {
                "run_id": "run_done",
                "status": "completed",
                "experiment_id": "exp_t5",
                "finished_at": "2026-05-27T03:00:00Z",
                "resources": {"gpu_ids": ["1"]},
            },
        )
        save_yaml(
            self.root / "runs" / "run_running_finished_at.yaml",
            {
                "run_id": "run_running_finished_at",
                "status": "running",
                "experiment_id": "exp_t5",
                "finished_at": "2026-05-27T03:30:00Z",
                "resources": {"ports": [7861]},
            },
        )

        payload = active_resources_payload(self.root)
        by_id = {item["run_id"]: item for item in payload["runs"]}

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema_version"], "active_resources_v1")
        self.assertEqual(payload["active_count"], 2)
        self.assertEqual(payload["selected_count"], 2)
        self.assertNotIn("active", payload)
        self.assertEqual(set(by_id), {"run_gpu_active", "run_running_finished_at"})
        self.assertEqual(by_id["run_gpu_active"]["resources"]["gpu_ids"], ["0"])
        self.assertEqual(by_id["run_gpu_active"]["tmux_session"], "rc_gpu_active")
        self.assertNotIn("run_done", by_id)

        with_terminal = active_resources_payload(self.root, include_terminal=True)
        self.assertEqual(with_terminal["active_count"], 2)
        self.assertEqual(with_terminal["selected_count"], 3)

    def test_active_resources_cli_json_and_manifest_metadata(self) -> None:
        save_yaml(
            self.root / "runs" / "run_cli_active_resources.yaml",
            {
                "run_id": "run_cli_active_resources",
                "status": "queued",
                "experiment_id": "exp_t5",
                "config_file": "artifacts/exp_t5/run_cli_active_resources/config.yaml",
                "resources": {"ports": [9001]},
            },
        )
        save_yaml(
            self.root / "runs" / "run_cli_active_resources_list.yaml",
            {
                "run_id": "run_cli_active_resources_list",
                "status": "running",
                "experiment_id": "exp_t5",
                "resources": ["gpu:1"],
            },
        )

        out = subprocess.run(
            [
                *cli_command("active-resources"),
                "--root",
                str(self.root),
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)
        by_id = {item["run_id"]: item for item in payload["runs"]}
        manifest = {item["name"]: item for item in agent_command_manifest()}
        human_out = subprocess.run(
            [
                *cli_command("active-resources"),
                "--root",
                str(self.root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(by_id["run_cli_active_resources"]["resources"]["ports"], [9001])
        self.assertEqual(by_id["run_cli_active_resources_list"]["resources"], ["gpu:1"])
        self.assertEqual(human_out.returncode, 0, human_out.stdout + human_out.stderr)
        self.assertIn("resources=list", human_out.stdout)
        self.assertIn("active-resources", manifest)
        self.assertFalse(manifest["active-resources"]["mutating"])
        self.assertIn("maintenance", manifest["active-resources"]["workflow_tags"])
        self.assertIn("--include-terminal", manifest["active-resources"]["supported_flags"])
        self.assertIn("resources", manifest["active-resources"]["fields_supported"])

    def test_worktree_audit_joins_git_worktrees_to_assignments_and_runs(self) -> None:
        session = self.start_t5_assignment()
        create_run(
            self.root,
            run_id="run_worktree_audit",
            experiment_id="exp_t5",
            status="running",
            rebuild_dashboard=False,
        )
        repo = self.tmp_root / "repo"
        main_worktree = repo
        agent_worktree = self.tmp_root / "worktrees" / "agent_t5"

        def fake_git(git_repo: Path, *args: str) -> str:
            if args == ("worktree", "list", "--porcelain"):
                return (
                    f"worktree {main_worktree}\n"
                    "HEAD abc123\n"
                    "branch refs/heads/main\n"
                    "\n"
                    f"worktree {agent_worktree}\n"
                    "HEAD def456\n"
                    "branch refs/heads/agent/option_t5\n"
                )
            if args == ("-c", "status.refreshIndex=false", "status", "--porcelain"):
                return ""
            raise AssertionError(f"unexpected git call: {git_repo} {args}")

        with patch("research_cockpit.maintenance._git_output", side_effect=fake_git):
            payload = worktree_audit_payload(self.root, repo=repo)

        by_branch = {item["branch"]: item for item in payload["worktrees"]}
        agent_row = by_branch["agent/option_t5"]

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema_version"], "worktree_audit_v1")
        self.assertEqual(agent_row["label"], "agent_t5")
        self.assertEqual(agent_row["active_assignment_ids"], [session["assignment_id"]])
        self.assertEqual(agent_row["option_workstream_node_ids"], ["option_t5"])
        self.assertIn("option_t5", agent_row["active_node_ids"])
        self.assertEqual(agent_row["run_statuses"], [{"run_id": "run_worktree_audit", "status": "running"}])
        self.assertIn("active_assignment", agent_row["blockers"])
        self.assertIn("active_run", agent_row["blockers"])
        self.assertFalse(agent_row["safe_to_remove"])

    def test_worktree_audit_blocks_active_workstream_and_locked_or_bare_rows(self) -> None:
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["agent_workstream"] = {
            "status": "in_progress",
            "git_branch": "agent/option_t5",
            "worktree_label": "agent_t5",
            "session_id": "session_t5",
        }
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)
        repo = self.tmp_root / "repo"
        agent_worktree = self.tmp_root / "worktrees" / "agent_t5"
        bare_worktree = self.tmp_root / "bare_repo"

        def fake_git(git_repo: Path, *args: str) -> str:
            if args == ("worktree", "list", "--porcelain"):
                return (
                    f"worktree {agent_worktree}\n"
                    "HEAD def456\n"
                    "branch refs/heads/agent/option_t5\n"
                    "locked cleanup pending\n"
                    "\n"
                    f"worktree {bare_worktree}\n"
                    "bare\n"
                )
            if args == ("-c", "status.refreshIndex=false", "status", "--porcelain"):
                return ""
            raise AssertionError(f"unexpected git call: {git_repo} {args}")

        with patch("research_cockpit.maintenance._git_output", side_effect=fake_git):
            payload = worktree_audit_payload(self.root, repo=repo)

        by_path = {item["path"]: item for item in payload["worktrees"]}
        agent_row = by_path[str(agent_worktree)]
        bare_row = by_path[str(bare_worktree)]

        self.assertIn("active_workstream", agent_row["blockers"])
        self.assertIn("locked_worktree", agent_row["blockers"])
        self.assertFalse(agent_row["safe_to_remove"])
        self.assertIn("bare_worktree", bare_row["blockers"])
        self.assertFalse(bare_row["safe_to_remove"])

    def test_worktree_audit_blocks_run_from_retired_assignment_and_prunable_rows(self) -> None:
        session = self.start_t5_assignment()
        assignment = load_yaml(self.root / "assignments" / f"{session['assignment_id']}.yaml")
        assignment["status"] = "completed"
        save_yaml(self.root / "assignments" / f"{session['assignment_id']}.yaml", assignment)
        create_run(
            self.root,
            run_id="run_retired_assignment",
            experiment_id="exp_t5",
            status="running",
            rebuild_dashboard=False,
        )
        repo = self.tmp_root / "repo"
        agent_worktree = self.tmp_root / "worktrees" / "agent_t5"
        prunable_worktree = self.tmp_root / "worktrees" / "missing"

        def fake_git(git_repo: Path, *args: str) -> str:
            if args == ("worktree", "list", "--porcelain"):
                return (
                    f"worktree {agent_worktree}\n"
                    "HEAD def456\n"
                    "branch refs/heads/agent/option_t5\n"
                    "\n"
                    f"worktree {prunable_worktree}\n"
                    "HEAD 000000\n"
                    "branch refs/heads/agent/missing\n"
                    "prunable gitdir file points to non-existent location\n"
                )
            if args == ("-c", "status.refreshIndex=false", "status", "--porcelain"):
                if git_repo == prunable_worktree:
                    raise AssertionError("prunable worktree should not run git status")
                return ""
            raise AssertionError(f"unexpected git call: {git_repo} {args}")

        with patch("research_cockpit.maintenance._git_output", side_effect=fake_git):
            payload = worktree_audit_payload(self.root, repo=repo)

        by_path = {item["path"]: item for item in payload["worktrees"]}

        self.assertIn("active_run", by_path[str(agent_worktree)]["blockers"])
        self.assertEqual(
            by_path[str(agent_worktree)]["run_statuses"],
            [{"run_id": "run_retired_assignment", "status": "running"}],
        )
        self.assertIn("prunable_worktree", by_path[str(prunable_worktree)]["blockers"])
        self.assertFalse(by_path[str(prunable_worktree)]["safe_to_remove"])

    def test_worktree_closeout_dry_run_generates_cleanup_plan(self) -> None:
        session = self.start_t5_assignment()
        assignment = load_yaml(self.root / "assignments" / f"{session['assignment_id']}.yaml")
        assignment["status"] = "completed"
        save_yaml(self.root / "assignments" / f"{session['assignment_id']}.yaml", assignment)
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["agent_workstream"]["status"] = "reported"
        option["linked_artifacts"] = ["artifact_closeout"]
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["findings"] = [
            {
                "id": "finding_closeout",
                "statement": "Closeout evidence is preserved.",
                "confidence": "medium",
                "evidence": ["exp_t5"],
                "linked_artifacts": ["artifact_closeout"],
            }
        ]
        experiment["linked_artifacts"] = ["artifact_closeout"]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        artifact_dir = self.tmp_root / "artifacts" / "closeout"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "summary.txt").write_text("preserved", encoding="utf-8")
        write_node(
            self.root,
            {
                "id": "artifact_closeout",
                "type": "artifact",
                "title": "Closeout evidence",
                "status": "done",
                "path": "artifacts/closeout",
                "retention": {"class": "portable_review_bundle"},
            },
        )
        create_run(
            self.root,
            run_id="run_closeout",
            experiment_id="exp_t5",
            status="completed",
            output_root="artifacts/closeout",
            output_retention={"class": "portable_review_bundle"},
            rebuild_dashboard=False,
        )
        repo = self.tmp_root
        agent_worktree = self.tmp_root / "worktrees" / "agent_t5"

        def fake_git(git_repo: Path, *args: str) -> str:
            if args == ("worktree", "list", "--porcelain"):
                return (
                    f"worktree {repo}\n"
                    "HEAD abc123\n"
                    "branch refs/heads/main\n"
                    "\n"
                    f"worktree {agent_worktree}\n"
                    "HEAD def456\n"
                    "branch refs/heads/agent/option_t5\n"
                )
            if args == ("-c", "status.refreshIndex=false", "status", "--porcelain"):
                return ""
            if args == ("branch", "--format=%(refname:short)"):
                return "main\nagent/option_t5\n"
            if args == ("branch", "--merged", "main", "--format=%(refname:short)"):
                return "main\nagent/option_t5\n"
            raise AssertionError(f"unexpected git call: {git_repo} {args}")

        with patch("research_cockpit.maintenance._git_output", side_effect=fake_git):
            payload = worktree_closeout_payload(
                self.root,
                repo=repo,
                worktree=agent_worktree,
                classification="discard_after_recording",
                min_size_bytes=1,
            )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["schema_version"], "worktree_closeout_v1")
        self.assertEqual(payload["classification"], "discard_after_recording")
        self.assertEqual(payload["blockers"], [])
        self.assertEqual(payload["target_worktree"]["branch"], "agent/option_t5")
        self.assertEqual(payload["evidence_summary"]["finding_count"], 1)
        self.assertEqual(payload["evidence_summary"]["artifact_ids"], ["artifact_closeout"])
        self.assertEqual(payload["rc_state_updates_needed"], [])
        self.assertIn(f"git -C {repo} worktree remove {agent_worktree}", payload["execution_commands"])
        self.assertIn("git -C", payload["execution_commands"][1])
        self.assertIn("branch -d agent/option_t5", payload["execution_commands"][1])

    def test_worktree_closeout_blocks_active_dirty_and_unrecorded_work(self) -> None:
        session = self.start_t5_assignment()
        repo = self.tmp_root
        agent_worktree = self.tmp_root / "worktrees" / "agent_t5"
        create_run(
            self.root,
            run_id="run_closeout_active",
            experiment_id="exp_t5",
            status="running",
            output_root=str(agent_worktree / "outputs" / "run_closeout_active"),
            rebuild_dashboard=False,
        )

        def fake_git(git_repo: Path, *args: str) -> str:
            if args == ("worktree", "list", "--porcelain"):
                return (
                    f"worktree {repo}\n"
                    "HEAD abc123\n"
                    "branch refs/heads/main\n"
                    "\n"
                    f"worktree {agent_worktree}\n"
                    "HEAD def456\n"
                    "branch refs/heads/agent/option_t5\n"
                )
            if args == ("-c", "status.refreshIndex=false", "status", "--porcelain"):
                return " M file.py\n" if git_repo == repo else " M train.py\n"
            if args == ("branch", "--format=%(refname:short)"):
                return "main\nagent/option_t5\n"
            if args == ("branch", "--merged", "main", "--format=%(refname:short)"):
                return "main\n"
            raise AssertionError(f"unexpected git call: {git_repo} {args}")

        with patch("research_cockpit.maintenance._git_output", side_effect=fake_git):
            payload = worktree_closeout_payload(
                self.root,
                repo=repo,
                worktree=agent_worktree,
                classification="discard_after_recording",
                min_size_bytes=1,
            )

        self.assertFalse(payload["ok"])
        self.assertFalse(payload["safe_to_closeout"])
        self.assertEqual(payload["assignment_ids"], [session["assignment_id"]])
        for blocker in (
            "active_assignment",
            "active_workstream",
            "active_run",
            "dirty_worktree",
            "dirty_outer_repo",
            "active_resource",
            "missing_finding_or_evidence",
        ):
            self.assertIn(blocker, payload["blockers"])
        self.assertTrue(payload["rc_state_updates_needed"])
        self.assertTrue(payload["execution_commands"])

    def test_worktree_closeout_blocks_base_branch_deletion_draft(self) -> None:
        repo = self.tmp_root
        secondary_main = self.tmp_root / "worktrees" / "main_copy"

        def fake_git(git_repo: Path, *args: str) -> str:
            if args == ("worktree", "list", "--porcelain"):
                return (
                    f"worktree {repo}\n"
                    "HEAD abc123\n"
                    "branch refs/heads/main\n"
                    "\n"
                    f"worktree {secondary_main}\n"
                    "HEAD abc123\n"
                    "branch refs/heads/main\n"
                )
            if args == ("-c", "status.refreshIndex=false", "status", "--porcelain"):
                return ""
            if args == ("branch", "--format=%(refname:short)"):
                return "main\n"
            if args == ("branch", "--merged", "main", "--format=%(refname:short)"):
                return "main\n"
            raise AssertionError(f"unexpected git call: {git_repo} {args}")

        with patch("research_cockpit.maintenance._git_output", side_effect=fake_git):
            payload = worktree_closeout_payload(
                self.root,
                repo=repo,
                worktree=secondary_main,
                classification="discard_after_recording",
                min_size_bytes=1,
            )

        self.assertFalse(payload["safe_to_closeout"])
        self.assertIn("base_branch", payload["blockers"])
        self.assertEqual(payload["execution_commands"], [f"git -C {repo} worktree remove {secondary_main}"])
        self.assertFalse(any("branch -d main" in command for command in payload["execution_commands"]))

    def test_branch_audit_classifies_checked_out_merged_and_research_candidates(self) -> None:
        session = self.start_t5_assignment()
        write_node(
            self.root,
            {
                "id": "artifact_candidate_branch",
                "type": "artifact",
                "title": "Candidate branch evidence",
                "status": "done",
                "path": "artifacts/candidate_branch",
            },
        )
        write_node(
            self.root,
            {
                "id": "option_candidate_branch",
                "type": "option",
                "title": "Candidate branch",
                "status": "active",
                "parent": "problem_text",
                "linked_artifacts": ["artifact_candidate_branch"],
                "agent_workstream": {
                    "status": "reported",
                    "git_branch": "codex/candidate",
                    "worktree_label": "candidate",
                    "session_id": "session_candidate",
                },
            },
        )
        repo = self.tmp_root / "repo"
        agent_worktree = self.tmp_root / "worktrees" / "agent_t5"

        def fake_git(git_repo: Path, *args: str) -> str:
            if args == ("worktree", "list", "--porcelain"):
                return (
                    f"worktree {repo}\n"
                    "HEAD abc123\n"
                    "branch refs/heads/main\n"
                    "\n"
                    f"worktree {agent_worktree}\n"
                    "HEAD def456\n"
                    "branch refs/heads/agent/option_t5\n"
                )
            if args == ("branch", "--format=%(refname:short)"):
                return "main\nagent/option_t5\ncodex/done\ncodex/candidate\nresearch/keep\n"
            if args == ("branch", "--merged", "main", "--format=%(refname:short)"):
                return "main\ncodex/done\n"
            raise AssertionError(f"unexpected git call: {git_repo} {args}")

        with patch("research_cockpit.maintenance._git_output", side_effect=fake_git):
            payload = branch_audit_payload(self.root, repo=repo, base="main")

        by_name = {item["name"]: item for item in payload["branches"]}

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema_version"], "branch_audit_v1")
        self.assertTrue(by_name["agent/option_t5"]["checked_out"])
        self.assertEqual(by_name["agent/option_t5"]["active_assignment_ids"], [session["assignment_id"]])
        self.assertIn(str(agent_worktree), by_name["agent/option_t5"]["checked_out_worktrees"])
        self.assertTrue(by_name["codex/done"]["merged"])
        self.assertEqual(by_name["codex/done"]["recommended_action"], "delete_candidate")
        self.assertFalse(by_name["codex/candidate"]["merged"])
        self.assertEqual(by_name["codex/candidate"]["recommended_action"], "preserve_as_research_candidate")
        self.assertEqual(by_name["codex/candidate"]["evidence_count"], 1)
        self.assertEqual(by_name["research/keep"]["recommended_action"], "keep_research")

    def test_branch_audit_keeps_active_workstream_and_descendant_evidence(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_descendant_branch",
                "type": "artifact",
                "title": "Descendant branch evidence",
                "status": "done",
                "path": "artifacts/descendant_branch",
            },
        )
        write_node(
            self.root,
            {
                "id": "exp_descendant_branch",
                "type": "experiment",
                "title": "Descendant evidence experiment",
                "status": "done",
                "parent": "option_t5",
                "linked_artifacts": ["artifact_descendant_branch"],
            },
        )
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["agent_workstream"] = {
            "status": "in_progress",
            "git_branch": "codex/active-workstream",
            "worktree_label": "active_workstream",
            "session_id": "session_active",
        }
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)
        repo = self.tmp_root / "repo"

        def fake_git(git_repo: Path, *args: str) -> str:
            if args == ("worktree", "list", "--porcelain"):
                return f"worktree {repo}\nHEAD abc123\nbranch refs/heads/main\n"
            if args == ("branch", "--format=%(refname:short)"):
                return "main\ncodex/active-workstream\n"
            if args == ("branch", "--merged", "main", "--format=%(refname:short)"):
                return "main\ncodex/active-workstream\n"
            raise AssertionError(f"unexpected git call: {git_repo} {args}")

        with patch("research_cockpit.maintenance._git_output", side_effect=fake_git):
            payload = branch_audit_payload(self.root, repo=repo, base="main")

        row = {item["name"]: item for item in payload["branches"]}["codex/active-workstream"]

        self.assertEqual(row["active_workstream_node_ids"], ["option_t5"])
        self.assertIn("active_workstream", row["blockers"])
        self.assertEqual(row["recommended_action"], "keep_active")
        self.assertEqual(row["evidence_node_ids"], ["artifact_descendant_branch"])

    def test_worktree_and_branch_audit_manifest_metadata(self) -> None:
        manifest = {item["name"]: item for item in agent_command_manifest()}

        self.assertFalse(manifest["worktree-audit"]["mutating"])
        self.assertFalse(manifest["branch-audit"]["mutating"])
        self.assertFalse(manifest["worktree-closeout"]["mutating"])
        self.assertIn("maintenance", manifest["worktree-audit"]["workflow_tags"])
        self.assertIn("maintenance", manifest["branch-audit"]["workflow_tags"])
        self.assertIn("maintenance", manifest["worktree-closeout"]["workflow_tags"])
        self.assertIn("--repo", manifest["worktree-audit"]["supported_flags"])
        self.assertIn("--include-nested", manifest["worktree-audit"]["supported_flags"])
        self.assertIn("label", manifest["worktree-audit"]["fields_supported"])
        self.assertNotIn("worktree_label", manifest["worktree-audit"]["fields_supported"])
        self.assertIn("--repo", manifest["branch-audit"]["supported_flags"])
        self.assertIn("--base", manifest["branch-audit"]["supported_flags"])
        self.assertIn("name", manifest["branch-audit"]["fields_supported"])
        self.assertNotIn("branch", manifest["branch-audit"]["fields_supported"])
        self.assertIn("--worktree", manifest["worktree-closeout"]["supported_flags"])
        self.assertIn("--classification", manifest["worktree-closeout"]["supported_flags"])
        self.assertIn("execution_commands", manifest["worktree-closeout"]["fields_supported"])

        maintenance_manifest = {
            item["name"]: item
            for item in agent_command_manifest(compact=True, workflow="maintenance")
        }
        self.assertIn("worktree-closeout", maintenance_manifest)

    def test_artifact_retention_audit_flags_large_missing_and_active_blockers(self) -> None:
        repo = self.tmp_root
        for rel_path in (
            "artifacts/large_missing/payload.bin",
            "artifacts/active_output/cache.bin",
            "artifacts/clear_cache/cache.bin",
        ):
            path = repo / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * 8)
        write_node(
            self.root,
            {
                "id": "artifact_large_missing",
                "type": "artifact",
                "title": "Large missing retention",
                "status": "done",
                "path": "artifacts/large_missing",
            },
        )
        write_node(
            self.root,
            {
                "id": "artifact_active_output",
                "type": "artifact",
                "title": "Active output cache",
                "status": "done",
                "path": "artifacts/active_output",
                "retention": {"class": "disposable_cache"},
            },
        )
        write_node(
            self.root,
            {
                "id": "artifact_clear_cache",
                "type": "artifact",
                "title": "Clear cache",
                "status": "done",
                "path": "artifacts/clear_cache",
                "retention": {"class": "disposable_cache"},
            },
        )
        write_node(
            self.root,
            {
                "id": "artifact_linked_cache",
                "type": "artifact",
                "title": "Linked cache",
                "status": "done",
                "path": "artifacts/clear_cache",
                "retention": {"class": "disposable_cache"},
            },
        )
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["linked_artifacts"] = ["artifact_linked_cache"]
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)
        external_dir = self.tmp_root.parent / f"external_artifact_{uuid.uuid4().hex}"
        external_dir.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(external_dir, ignore_errors=True))
        (external_dir / "payload.bin").write_bytes(b"x" * 8)
        write_node(
            self.root,
            {
                "id": "artifact_external_cache",
                "type": "artifact",
                "title": "External cache",
                "status": "done",
                "path": str(external_dir),
                "retention": {"class": "disposable_cache"},
            },
        )
        create_run(
            self.root,
            run_id="run_active_output",
            experiment_id="exp_t5",
            status="running",
            output_root="artifacts/active_output",
            rebuild_dashboard=False,
        )

        payload = artifact_retention_audit_payload(self.root, repo=repo, min_size_bytes=1)
        by_id = {item["artifact_id"]: item for item in payload["artifacts"]}

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema_version"], "artifact_retention_audit_v1")
        self.assertTrue(by_id["artifact_large_missing"]["large"])
        self.assertTrue(by_id["artifact_large_missing"]["missing_retention"])
        self.assertIn("missing_retention", by_id["artifact_large_missing"]["warnings"])
        self.assertFalse(by_id["artifact_large_missing"]["cleanup_candidate"])
        self.assertTrue(by_id["artifact_active_output"]["large"])
        self.assertEqual(by_id["artifact_active_output"]["retention_class"], "disposable_cache")
        self.assertEqual(by_id["artifact_active_output"]["active_resource_references"][0]["run_id"], "run_active_output")
        self.assertIn("active_resource", by_id["artifact_active_output"]["blockers"])
        self.assertFalse(by_id["artifact_active_output"]["cleanup_candidate"])
        self.assertTrue(by_id["artifact_clear_cache"]["cleanup_candidate"])
        self.assertIn("artifact_large_missing", payload["large_artifact_candidates"])
        self.assertIn("artifact_clear_cache", payload["cleanup_candidates"])
        self.assertIn("linked_reference", by_id["artifact_linked_cache"]["blockers"])
        self.assertFalse(by_id["artifact_linked_cache"]["cleanup_candidate"])
        self.assertIn("external_path", by_id["artifact_external_cache"]["blockers"])
        self.assertIn("external_path", by_id["artifact_external_cache"]["warnings"])
        self.assertFalse(by_id["artifact_external_cache"]["cleanup_candidate"])

    def test_artifact_retention_audit_blocks_active_subtree_and_baseline_artifacts(self) -> None:
        self.start_t5_assignment()
        repo = self.tmp_root
        for rel_path in (
            "artifacts/active_subtree/cache.bin",
            "artifacts/baseline_cache/cache.bin",
        ):
            path = repo / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * 8)
        write_node(
            self.root,
            {
                "id": "artifact_active_subtree",
                "type": "artifact",
                "title": "Active subtree artifact",
                "status": "done",
                "parent": "option_t5",
                "path": "artifacts/active_subtree",
                "retention": {"class": "disposable_cache"},
            },
        )
        write_node(
            self.root,
            {
                "id": "artifact_baseline_cache",
                "type": "artifact",
                "title": "Baseline cache",
                "status": "done",
                "path": "artifacts/baseline_cache",
                "retention": {"class": "disposable_cache"},
            },
        )
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["baseline"] = {
            "option": "option_t5",
            "artifacts": ["artifact_baseline_cache"],
            "reason": "Current baseline artifact.",
        }
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)

        payload = artifact_retention_audit_payload(self.root, repo=repo, min_size_bytes=1)
        by_id = {item["artifact_id"]: item for item in payload["artifacts"]}

        self.assertIn("active_assignment", by_id["artifact_active_subtree"]["blockers"])
        self.assertFalse(by_id["artifact_active_subtree"]["cleanup_candidate"])
        self.assertIn("linked_reference", by_id["artifact_baseline_cache"]["blockers"])
        self.assertEqual(by_id["artifact_baseline_cache"]["linked_references"][0]["source"], "baseline.artifacts")
        self.assertFalse(by_id["artifact_baseline_cache"]["cleanup_candidate"])

    def test_maintenance_audit_aggregates_sections_and_next_actions(self) -> None:
        session = self.start_t5_assignment()
        repo = self.tmp_root
        output_file = repo / "artifacts" / "maintenance_large" / "payload.bin"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(b"x" * 8)
        write_node(
            self.root,
            {
                "id": "artifact_maintenance_large",
                "type": "artifact",
                "title": "Maintenance large artifact",
                "status": "done",
                "path": "artifacts/maintenance_large",
            },
        )
        create_run(
            self.root,
            run_id="run_maintenance_active",
            experiment_id="exp_t5",
            status="running",
            output_root="artifacts/maintenance_large",
            progress_file="artifacts/maintenance_large/progress.json",
            config_file="artifacts/maintenance_large/config.yaml",
            rebuild_dashboard=False,
        )
        run_data = load_yaml(self.root / "runs" / "run_maintenance_active.yaml")
        run_data["resources"] = {"gpu_ids": ["0"], "cache": "artifacts/maintenance_large/cache"}
        save_yaml(self.root / "runs" / "run_maintenance_active.yaml", run_data)
        agent_worktree = self.tmp_root / "worktrees" / "agent_t5"

        def fake_git(git_repo: Path, *args: str) -> str:
            if args == ("worktree", "list", "--porcelain"):
                return (
                    f"worktree {repo}\n"
                    "HEAD abc123\n"
                    "branch refs/heads/main\n"
                    "\n"
                    f"worktree {agent_worktree}\n"
                    "HEAD def456\n"
                    "branch refs/heads/agent/option_t5\n"
                )
            if args == ("-c", "status.refreshIndex=false", "status", "--porcelain"):
                return ""
            if args == ("branch", "--format=%(refname:short)"):
                return "main\nagent/option_t5\n"
            if args == ("branch", "--merged", "main", "--format=%(refname:short)"):
                return "main\n"
            raise AssertionError(f"unexpected git call: {git_repo} {args}")

        with patch("research_cockpit.maintenance._git_output", side_effect=fake_git):
            payload = maintenance_audit_payload(self.root, repo=repo, min_size_bytes=1)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema_version"], "maintenance_audit_v1")
        self.assertEqual(payload["active_assignments"][0]["assignment_id"], session["assignment_id"])
        self.assertEqual(payload["running_runs"][0]["run_id"], "run_maintenance_active")
        self.assertEqual(payload["active_resources"][0]["resources"]["cache"], "artifacts/maintenance_large/cache")
        self.assertEqual(payload["active_resources"][0]["progress_file"], "artifacts/maintenance_large/progress.json")
        self.assertFalse(payload["worktree_candidates"])
        self.assertFalse(payload["branch_candidates"])
        self.assertTrue(payload["blocked_worktrees"])
        self.assertTrue(payload["blocked_branches"])
        self.assertIn("artifact_maintenance_large", payload["large_artifact_candidates"])
        self.assertIn("artifacts/maintenance_large", payload["large_output_candidates"])
        self.assertIn("active_assignment", payload["unsafe_cleanup_blockers"])
        self.assertIn("active_run", payload["unsafe_cleanup_blockers"])
        self.assertTrue(payload["recommended_next_actions"])

    def test_maintenance_audit_reuses_dashboard_profile_warnings(self) -> None:
        profile_path = self.root / "dashboards" / "build_profile.json"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(
            json.dumps(
                {
                    "schema_version": "build_profile_v1",
                    "warnings": [
                        {
                            "code": "resource_scan_skipped_payload",
                            "message": "Generated payload resources were skipped.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        repo = self.tmp_root

        def fake_git(git_repo: Path, *args: str) -> str:
            if args == ("worktree", "list", "--porcelain"):
                return f"worktree {repo}\nHEAD abc123\nbranch refs/heads/main\n"
            if args == ("-c", "status.refreshIndex=false", "status", "--porcelain"):
                return ""
            if args == ("branch", "--format=%(refname:short)"):
                return "main\n"
            if args == ("branch", "--merged", "main", "--format=%(refname:short)"):
                return "main\n"
            raise AssertionError(f"unexpected git call: {git_repo} {args}")

        with patch("research_cockpit.maintenance._git_output", side_effect=fake_git):
            payload = maintenance_audit_payload(self.root, repo=repo, min_size_bytes=1)

        self.assertEqual(payload["dashboard_performance_warnings"][0]["code"], "resource_scan_skipped_payload")
        self.assertEqual(payload["dashboard_performance_warnings"][0]["source"], "build_profile")

    def test_retention_and_maintenance_audit_manifest_metadata(self) -> None:
        manifest = {item["name"]: item for item in agent_command_manifest()}

        self.assertFalse(manifest["artifact-retention-audit"]["mutating"])
        self.assertFalse(manifest["maintenance-audit"]["mutating"])
        self.assertIn("maintenance", manifest["artifact-retention-audit"]["workflow_tags"])
        self.assertIn("maintenance", manifest["maintenance-audit"]["workflow_tags"])
        self.assertIn("--repo", manifest["artifact-retention-audit"]["supported_flags"])
        self.assertIn("--min-size-gb", manifest["artifact-retention-audit"]["supported_flags"])
        self.assertIn("--repo", manifest["maintenance-audit"]["supported_flags"])
        self.assertIn("--base", manifest["maintenance-audit"]["supported_flags"])
        self.assertIn("--min-size-gb", manifest["maintenance-audit"]["supported_flags"])

    def test_run_metadata_write_support_and_context_output(self) -> None:
        resources_file = self.tmp_root / "resources.json"
        resources_file.write_text(json.dumps({"gpu_ids": ["1"], "ports": [9001]}), encoding="utf-8")
        output_retention_file = self.tmp_root / "output_retention.json"
        output_retention_file.write_text(
            json.dumps({"class": "reproducible_output", "reason": "Can regenerate from config."}),
            encoding="utf-8",
        )
        create_out = subprocess.run(
            [
                *cli_command("create-run"),
                "--root",
                str(self.root),
                "--id",
                "run_metadata",
                "--experiment",
                "exp_t5",
                "--status",
                "running",
                "--resources-json",
                '{"gpu_ids":["0"],"ports":[7860]}',
                "--output-retention-json",
                '{"class":"disposable_cache","reason":"Warm cache."}',
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(create_out.returncode, 0, create_out.stdout + create_out.stderr)

        update_out = subprocess.run(
            [
                *cli_command("update-run"),
                "--root",
                str(self.root),
                "--id",
                "run_metadata",
                "--resources-file",
                str(resources_file),
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(update_out.returncode, 0, update_out.stdout + update_out.stderr)

        complete_out = subprocess.run(
            [
                *cli_command("complete-run"),
                "--root",
                str(self.root),
                "--id",
                "run_metadata",
                "--output-retention-file",
                str(output_retention_file),
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(complete_out.returncode, 0, complete_out.stdout + complete_out.stderr)

        saved = load_yaml(self.root / "runs" / "run_metadata.yaml")
        context = run_context_payload(self.root, run_id="run_metadata")
        compact = run_context_payload(self.root, run_id="run_metadata", compact=True)

        self.assertEqual(saved["resources"]["gpu_ids"], ["1"])
        self.assertEqual(saved["output_retention"]["class"], "reproducible_output")
        self.assertEqual(context["run"]["resources"]["ports"], [9001])
        self.assertEqual(context["run"]["output_retention"]["reason"], "Can regenerate from config.")
        self.assertEqual(compact["resources"]["gpu_ids"], ["1"])
        self.assertEqual(compact["output_retention"]["class"], "reproducible_output")

    def test_artifact_retention_write_support_and_context_output(self) -> None:
        artifact_file = self.tmp_root / "artifact_retention.yaml"
        save_yaml(
            artifact_file,
            {
                "id": "artifact_retention_write",
                "title": "Retention write",
                "status": "done",
                "path": "artifacts/retention_write",
                "artifact_kind": "portable_review_bundle",
                "retention": {"class": "portable_review_bundle", "reason": "Small review bundle."},
                "link_to": ["exp_t5"],
            },
        )
        create_out = subprocess.run(
            [
                *cli_command("create-artifact"),
                "--root",
                str(self.root),
                "--file",
                str(artifact_file),
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(create_out.returncode, 0, create_out.stdout + create_out.stderr)

        metadata_file = self.tmp_root / "artifact_metadata.yaml"
        save_yaml(
            metadata_file,
            {
                "artifact_kind": "reproducible_output",
                "retention": {
                    "class": "reproducible_output",
                    "reason": "Regenerable payload.",
                    "regenerate_command": "python run.py",
                },
            },
        )
        update_out = subprocess.run(
            [
                *cli_command("update-node-fields"),
                "--root",
                str(self.root),
                "--id",
                "artifact_retention_write",
                "--metadata-file",
                str(metadata_file),
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        context = context_payload(self.root, node_id="exp_t5", with_artifacts=True)
        artifact_node = {
            item["id"]: item
            for item in context["artifacts"]["nodes"]
        }["artifact_retention_write"]
        saved = load_yaml(self.root / "graph" / "nodes" / "artifact_retention_write.yaml")

        self.assertEqual(update_out.returncode, 0, update_out.stdout + update_out.stderr)
        self.assertEqual(saved["artifact_kind"], "reproducible_output")
        self.assertEqual(saved["retention"]["class"], "reproducible_output")
        self.assertEqual(artifact_node["artifact_kind"], "reproducible_output")
        self.assertEqual(artifact_node["retention"]["regenerate_command"], "python run.py")

    def test_retention_metadata_rejects_invalid_shapes(self) -> None:
        bad_run = subprocess.run(
            [
                *cli_command("create-run"),
                "--root",
                str(self.root),
                "--id",
                "run_bad_metadata",
                "--experiment",
                "exp_t5",
                "--resources-json",
                '["gpu0"]',
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        bad_artifact_file = self.tmp_root / "bad_artifact_retention.yaml"
        save_yaml(
            bad_artifact_file,
            {
                "id": "artifact_bad_retention",
                "title": "Bad retention",
                "retention": {"class": "not_a_retention_class"},
            },
        )
        bad_artifact = subprocess.run(
            [
                *cli_command("create-artifact"),
                "--root",
                str(self.root),
                "--file",
                str(bad_artifact_file),
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(bad_run.returncode, 0)
        self.assertIn("resources must be a mapping", bad_run.stdout + bad_run.stderr)
        self.assertNotEqual(bad_artifact.returncode, 0)
        self.assertIn("Invalid retention.class", bad_artifact.stdout + bad_artifact.stderr)

    def test_metadata_write_manifest_flags(self) -> None:
        manifest = {item["name"]: item for item in agent_command_manifest()}

        for command_name in ("create-run", "update-run", "complete-run"):
            with self.subTest(command_name=command_name):
                self.assertIn("--resources-json", manifest[command_name]["supported_flags"])
                self.assertIn("--resources-file", manifest[command_name]["supported_flags"])
                self.assertIn("--output-retention-json", manifest[command_name]["supported_flags"])
                self.assertIn("--output-retention-file", manifest[command_name]["supported_flags"])
                self.assertIn("resources", manifest[command_name]["fields_supported"])
                self.assertIn("output_retention", manifest[command_name]["fields_supported"])
        self.assertIn("--metadata-file", manifest["update-node-fields"]["supported_flags"])
        self.assertIn("artifact_kind", manifest["update-node-fields"]["fields_supported"])
        self.assertIn("retention", manifest["update-node-fields"]["fields_supported"])

    def test_list_agent_commands_manifest_marks_mutating_commands(self) -> None:
        manifest = agent_command_manifest()
        by_name = {item["name"]: item for item in manifest}

        self.assertFalse(by_name["validate"]["mutating"])
        self.assertTrue(by_name["repair-interaction-log"]["mutating"])
        self.assertTrue(by_name["repair-interaction-log"]["supports_dry_run"])
        self.assertTrue(by_name["repair-interaction-log"]["supports_show_diff"])
        self.assertFalse(by_name["repair-interaction-log"]["writes_truth_source"])
        self.assertIn("maintenance", by_name["repair-interaction-log"]["workflow_tags"])
        self.assertFalse(by_name["search"]["mutating"])
        self.assertFalse(by_name["smoke"]["mutating"])
        self.assertFalse(by_name["commands"]["mutating"])
        self.assertTrue(by_name["commands"]["supports_compact"])
        self.assertFalse(by_name["commands"]["supports_root"])
        self.assertIn("--root", by_name["commands"]["unsupported_flags"])
        self.assertNotIn("--root", by_name["commands"]["supported_flags"])
        self.assertIn("read", by_name["commands"]["workflow_tags"])
        self.assertTrue(by_name["init"]["mutating"])
        self.assertFalse(by_name["ui"]["mutating"])
        self.assertEqual(by_name["init"]["capability_file"], "capabilities/integrations.md")
        self.assertEqual(by_name["ui"]["capability_file"], "capabilities/ui-dashboard.md")
        self.assertTrue(by_name["record-finding"]["mutating"])
        self.assertTrue(by_name["record-finding"]["supports_json"])
        self.assertTrue(by_name["record-finding"]["supports_dry_run"])
        self.assertTrue(by_name["record-finding"]["supports_no_build"])
        self.assertIn("evidence_path", by_name["record-finding"]["fields_supported"])
        self.assertIn("evidence_links", by_name["record-finding"]["fields_supported"])
        self.assertNotIn("evidence_artifact_id", by_name["record-finding"]["fields_supported"])
        self.assertTrue(by_name["update-finding"]["supports_json"])
        self.assertTrue(by_name["create-artifact"]["supports_dry_run"])
        self.assertIn("evidence", by_name["create-artifact"]["workflow_tags"])
        self.assertEqual(by_name["create-artifact"]["file_schema"], "artifact_v1")
        self.assertIn("link_to:", by_name["create-artifact"]["example_file"])
        self.assertIn("--print-schema", by_name["create-artifact"]["schema_command"])
        self.assertTrue(by_name["link-artifact"]["supports_no_build"])
        self.assertFalse(by_name["context"]["mutating"])
        self.assertTrue(by_name["context"]["supports_json"])
        self.assertTrue(by_name["context"]["supports_compact"])
        self.assertIn("focus", by_name["context"]["workflow_tags"])
        self.assertIn("--assignment", by_name["bootstrap"]["supported_flags"])
        for command_name in (
            "apply-graph-plan",
            "create-workstream",
            "update-status",
            "create-run",
            "update-run",
            "complete-run",
            "record-gate-result",
            "ingest-artifact",
            "record-finding",
            "complete-experiment",
        ):
            with self.subTest(command_name=command_name):
                self.assertIn("--assignment", by_name[command_name]["supported_flags"])
                self.assertIn("--coordinator", by_name[command_name]["supported_flags"])
        self.assertTrue(by_name["node-context"]["supports_compact"])
        self.assertIn("read", by_name["node-context"]["workflow_tags"])
        self.assertTrue(by_name["add-node"]["supports_json"])
        self.assertTrue(by_name["add-node"]["supports_dry_run"])
        self.assertTrue(by_name["add-node"]["supports_no_build"])
        self.assertTrue(by_name["add-node"]["supports_show_diff"])
        self.assertIn("graph", by_name["add-node"]["workflow_tags"])
        self.assertTrue(by_name["add-node"]["writes_truth_source"])
        self.assertTrue(by_name["add-node"]["writes_generated_files"])
        self.assertTrue(by_name["add-node"]["can_batch"])
        self.assertTrue(by_name["update-status"]["supports_no_build"])
        self.assertTrue(by_name["update-status"]["supports_json"])
        self.assertTrue(by_name["update-status"]["supports_dry_run"])
        self.assertTrue(by_name["set-focus"]["supports_json"])
        self.assertTrue(by_name["set-focus"]["supports_dry_run"])
        self.assertTrue(by_name["set-focus"]["supports_compact"])
        self.assertTrue(by_name["set-agent-focus"]["supports_dry_run"])
        self.assertTrue(by_name["set-agent-focus"]["supports_compact"])
        self.assertIn("agent_focuses", by_name["set-agent-focus"]["fields_supported"])
        self.assertTrue(by_name["set-cursor"]["supports_dry_run"])
        self.assertTrue(by_name["set-cursor"]["supports_compact"])
        self.assertIn("--assignment", by_name["set-cursor"]["supported_flags"])
        self.assertIn("assignment.current_node", by_name["set-cursor"]["fields_supported"])
        self.assertTrue(by_name["apply-graph-plan"]["supports_json"])
        self.assertTrue(by_name["apply-graph-plan"]["supports_dry_run"])
        self.assertTrue(by_name["apply-graph-plan"]["supports_no_build"])
        self.assertTrue(by_name["apply-graph-plan"]["can_batch"])
        self.assertEqual(by_name["apply-graph-plan"]["file_schema"], "graph_plan_v1")
        self.assertIn("nodes:", by_name["apply-graph-plan"]["example_file"])
        self.assertIn("--print-schema", by_name["apply-graph-plan"]["schema_command"])
        self.assertEqual(by_name["apply-graph-plan"]["status_aliases"], {"option": {"planned": "open"}})
        self.assertTrue(by_name["create-workstream"]["supports_json"])
        self.assertEqual(by_name["create-workstream"]["file_schema"], "workstream_v1")
        self.assertIn("followup_options:", by_name["create-workstream"]["example_file"])
        self.assertIn("status: active", by_name["create-workstream"]["example_file"])
        self.assertIn("hypothesis:", by_name["create-workstream"]["example_file"])
        self.assertIn("summary:", by_name["create-workstream"]["example_file"])
        self.assertIn("success_criteria:", by_name["create-workstream"]["example_file"])
        self.assertIn("metrics:", by_name["create-workstream"]["example_file"])
        self.assertIn("hypothesis", by_name["create-workstream"]["fields_supported"])
        self.assertIn("success_criteria", by_name["create-workstream"]["fields_supported"])
        self.assertIn("metrics", by_name["create-workstream"]["fields_supported"])
        self.assertIn("option -> problem -> option", by_name["create-workstream"]["hierarchy_guidance"])
        self.assertEqual(by_name["create-workstream"]["status_aliases"], {"option": {"planned": "open"}})
        self.assertTrue(by_name["sync-focus-actions"]["supports_dry_run"])
        self.assertTrue(by_name["sync-focus-actions"]["supports_compact"])
        self.assertTrue(by_name["complete-experiment"]["mutating"])
        self.assertTrue(by_name["complete-experiment"]["supports_json"])
        self.assertTrue(by_name["complete-experiment"]["supports_dry_run"])
        self.assertTrue(by_name["complete-experiment"]["supports_no_build"])
        self.assertTrue(by_name["complete-experiment"]["supports_compact"])
        self.assertEqual(by_name["complete-experiment"]["batch_policy"]["mode"], "serial_no_build")
        self.assertTrue(by_name["complete-experiment"]["batch_policy"]["use_no_build"])
        self.assertIn(
            "smoke --root <root> --json",
            " ".join(by_name["complete-experiment"]["batch_policy"]["finish_commands"]),
        )
        self.assertIn("evidence_path", by_name["complete-experiment"]["fields_supported"])
        self.assertNotIn("next_actions", by_name["complete-experiment"]["fields_supported"])
        self.assertNotIn("evidence_artifact_id", by_name["complete-experiment"]["fields_supported"])
        self.assertTrue(by_name["complete-experiments"]["can_batch"])
        self.assertTrue(by_name["complete-experiments"]["supports_dry_run"])
        self.assertEqual(by_name["complete-experiments"]["file_schema"], "experiment_completion_v1")
        self.assertIn("experiments:", by_name["complete-experiments"]["example_file"])
        self.assertIn("evidence.links", by_name["complete-experiments"]["fields_supported"])
        self.assertNotIn("next_actions", by_name["complete-experiments"]["fields_supported"])
        self.assertNotIn("evidence.artifact_id", by_name["complete-experiments"]["fields_supported"])
        self.assertNotIn("evidence.title", by_name["complete-experiments"]["fields_supported"])
        self.assertIn("evidence:", by_name["complete-experiments"]["example_file"])
        self.assertNotIn("title: First result bundle", by_name["complete-experiments"]["example_file"])
        self.assertTrue(by_name["finalize-workstream"]["supports_json"])
        self.assertTrue(by_name["finalize-workstream"]["supports_dry_run"])
        self.assertEqual(by_name["finalize-workstream"]["file_schema"], "finalize_workstream_v1")
        self.assertIn("summary_target:", by_name["finalize-workstream"]["example_file"])
        self.assertIn("--id", by_name["option-workstream-context"]["target_aliases"])
        self.assertEqual(by_name["option-workstream-context"]["primary_target"], "--id")
        self.assertTrue(by_name["option-workstream-context"]["supports_compact"])
        self.assertTrue(by_name["agent-session-context"]["supports_compact"])
        self.assertEqual(by_name["agent-session-context"]["primary_target"], "--assignment")
        self.assertIn("--agent", by_name["agent-session-context"]["target_aliases"])
        self.assertIn("finalize file directory", by_name["finalize-workstream"]["path_resolution"])
        self.assertTrue(by_name["update-node-fields"]["mutating"])
        self.assertTrue(by_name["update-node-fields"]["supports_json"])
        self.assertTrue(by_name["update-node-fields"]["supports_dry_run"])
        self.assertTrue(by_name["update-node-fields"]["supports_no_build"])
        self.assertTrue(by_name["update-node-fields"]["supports_compact"])
        self.assertIn("--root", by_name["update-node-fields"]["supported_flags"])
        self.assertIn("--compact", by_name["update-node-fields"]["supported_flags"])
        self.assertNotIn("--compact", by_name["update-node-fields"]["unsupported_flags"])
        self.assertIn("--compact", by_name["update-decision-evidence"]["unsupported_flags"])
        self.assertIn("question", by_name["update-node-fields"]["fields_supported"])
        self.assertIn("supporting_experiments", by_name["update-node-fields"]["fields_supported"])
        self.assertIn("ready_for_agent", by_name["update-node-fields"]["fields_supported"])
        self.assertIn("assignment-view", by_name)
        self.assertFalse(by_name["assignment-view"]["mutating"])
        self.assertIn("depends_on", by_name["assignment-view"]["fields_supported"])
        self.assertIn("lint", by_name)
        self.assertFalse(by_name["lint"]["mutating"])
        self.assertIn("semantic", by_name["lint"]["fields_supported"])
        self.assertEqual(by_name["lint"]["command"], "research-cockpit lint --semantic")
        self.assertIn("--semantic", by_name["lint"]["supported_flags"])
        self.assertIn("close-current-experiment", by_name)
        self.assertTrue(by_name["close-current-experiment"]["mutating"])
        self.assertIn("next_focus", by_name["close-current-experiment"]["fields_supported"])
        self.assertFalse(by_name["close-current-experiment"]["supports_show_diff"])
        self.assertIn("--show-diff", by_name["close-current-experiment"]["unsupported_flags"])
        self.assertIn("create-followup-experiment", by_name)
        self.assertIn("derived_from", by_name["create-followup-experiment"]["fields_supported"])
        self.assertIn("coordinator_set_focus", by_name["create-followup-experiment"]["fields_supported"])
        self.assertNotIn("set_focus", by_name["create-followup-experiment"]["fields_supported"])
        self.assertTrue(by_name["create-followup-experiment"]["supports_show_diff"])
        self.assertIn("--assignment", by_name["create-followup-experiment"]["supported_flags"])
        self.assertIn("--coordinator", by_name["create-followup-experiment"]["supported_flags"])
        self.assertIn("--show-diff", by_name["create-followup-experiment"]["supported_flags"])
        self.assertIn("single queued gate", by_name["create-followup-experiment"]["hierarchy_guidance"])
        self.assertIn("update-workstream-fields", by_name)
        self.assertIn("agent_workstream.status", by_name["update-workstream-fields"]["fields_supported"])
        self.assertTrue(by_name["claim-option"]["supports_json"])
        self.assertTrue(by_name["claim-option"]["supports_dry_run"])
        self.assertTrue(by_name["claim-workstream"]["supports_dry_run"])
        self.assertTrue(by_name["start-agent-session"]["supports_dry_run"])
        self.assertIn("git_branch", by_name["start-agent-session"]["fields_supported"])
        self.assertIn("sparse_worktree.command_plan", by_name["start-agent-session"]["fields_supported"])
        self.assertIn("--sparse", by_name["start-agent-session"]["supported_flags"])
        self.assertIn("--sparse-profile", by_name["start-agent-session"]["supported_flags"])
        self.assertTrue(by_name["report-option-workstream"]["supports_json"])
        self.assertTrue(by_name["report-option-workstream"]["supports_dry_run"])
        self.assertTrue(by_name["import-worktree-findings"]["supports_dry_run"])
        self.assertIn("workstream_report", by_name["import-worktree-findings"]["fields_supported"])
        self.assertTrue(by_name["ingest-artifact"]["supports_dry_run"])
        self.assertTrue(by_name["ingest-artifact"]["supports_compact"])
        self.assertIn("run_id", by_name["ingest-artifact"]["fields_supported"])
        self.assertIn("agent", by_name["ingest-artifact"]["fields_supported"])
        self.assertIn("create-run", by_name)
        self.assertTrue(by_name["create-run"]["mutating"])
        self.assertTrue(by_name["create-run"]["supports_json"])
        self.assertTrue(by_name["create-run"]["supports_dry_run"])
        self.assertTrue(by_name["create-run"]["supports_no_build"])
        self.assertTrue(by_name["create-run"]["supports_compact"])
        self.assertTrue(by_name["create-run"]["supports_show_diff"])
        self.assertIn("tmux_session", by_name["create-run"]["fields_supported"])
        self.assertIn("progress_file", by_name["create-run"]["fields_supported"])
        self.assertTrue(by_name["update-run"]["mutating"])
        self.assertTrue(by_name["update-run"]["supports_dry_run"])
        self.assertTrue(by_name["update-run"]["supports_no_build"])
        self.assertTrue(by_name["complete-run"]["mutating"])
        self.assertTrue(by_name["complete-run"]["supports_dry_run"])
        self.assertTrue(by_name["list-runs"]["supports_json"])
        self.assertTrue(by_name["list-runs"]["supports_compact"])
        self.assertFalse(by_name["list-runs"]["mutating"])
        self.assertTrue(by_name["run-context"]["supports_json"])
        self.assertTrue(by_name["run-context"]["supports_compact"])
        self.assertFalse(by_name["run-context"]["mutating"])
        self.assertTrue(by_name["promote-decision"]["supports_json"])
        self.assertTrue(by_name["promote-decision"]["supports_dry_run"])
        self.assertTrue(by_name["accept-decision"]["supports_json"])
        self.assertTrue(by_name["accept-decision"]["supports_dry_run"])
        self.assertTrue(by_name["apply-suggestion"]["supports_json"])
        self.assertTrue(by_name["apply-suggestion"]["supports_dry_run"])
        self.assertTrue(by_name["update-decision-checklist"]["mutating"])
        self.assertTrue(by_name["update-decision-checklist"]["supports_json"])
        self.assertTrue(by_name["update-decision-checklist"]["supports_dry_run"])
        self.assertTrue(by_name["update-decision-checklist"]["supports_no_build"])
        self.assertIn("next_required_actions", by_name["update-decision-checklist"]["fields_supported"])
        self.assertTrue(by_name["update-decision-evidence"]["supports_json"])
        self.assertTrue(by_name["update-decision-evidence"]["supports_dry_run"])
        self.assertIn("evidence_summary", by_name["update-decision-evidence"]["fields_supported"])
        self.assertTrue(by_name["cleanup-suggestion-lifecycle"]["supports_dry_run"])
        self.assertTrue(by_name["update-suggestion-state"]["supports_json"])
        self.assertTrue(by_name["update-suggestion-state"]["supports_dry_run"])
        self.assertTrue(by_name["build"]["mutating"])
        self.assertTrue(by_name["build"]["supports_json"])
        self.assertTrue(by_name["build"]["supports_watch"])
        self.assertTrue(by_name["build"]["writes_generated_files"])
        self.assertFalse(by_name["build"]["writes_truth_source"])
        self.assertEqual(by_name["build"]["batch_policy"]["mode"], "generated_build")
        self.assertIn("--watch", by_name["build"]["supported_flags"])
        self.assertIn("--interval", by_name["build"]["supported_flags"])
        self.assertIn("--max-iterations", by_name["build"]["supported_flags"])
        self.assertIn("--profile", by_name["build"]["supported_flags"])
        self.assertIn("--profile-output", by_name["build"]["supported_flags"])
        self.assertIn("--skip-resource-search", by_name["build"]["supported_flags"])
        self.assertTrue(by_name["validate"]["safe_in_plan_mode"])
        self.assertEqual(by_name["validate"]["batch_policy"]["mode"], "read_only")
        self.assertFalse(by_name["update-node-fields"]["safe_in_plan_mode"])
        for command in manifest:
            if command["mutating"]:
                self.assertTrue(command["requires_serial_mutation"], command["name"])
                self.assertIn("changed after command planning", command["conflict_policy"])
        self.assertTrue(all(item["command"].startswith("research-cockpit ") for item in manifest))
        self.assertTrue(all("plugin_command" not in item for item in manifest))

    def test_list_agent_commands_cli_outputs_json(self) -> None:
        command = "commands"

        out = subprocess.run(
            [*cli_command(command), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0)
        payload = json.loads(out.stdout)
        self.assertIn("commands", payload)
        self.assertIn("init", {item["name"] for item in payload["commands"]})
        self.assertIn("ui", {item["name"] for item in payload["commands"]})
        self.assertIn("commands", {item["name"] for item in payload["commands"]})
        self.assertIn("record-finding", {item["name"] for item in payload["commands"]})
        self.assertIn("complete-experiment", {item["name"] for item in payload["commands"]})
        self.assertIn("update-node-fields", {item["name"] for item in payload["commands"]})
        self.assertIn("apply-graph-plan", {item["name"] for item in payload["commands"]})
        self.assertIn("create-workstream", {item["name"] for item in payload["commands"]})
        self.assertIn("sync-focus-actions", {item["name"] for item in payload["commands"]})
        self.assertIn("start-agent-session", {item["name"] for item in payload["commands"]})
        self.assertIn("agent-session-context", {item["name"] for item in payload["commands"]})

    def test_list_agent_commands_filters_by_name_and_workflow(self) -> None:
        by_name_out = subprocess.run(
            [*cli_command("commands"), "--json", "--compact", "--name", "context"],
            capture_output=True,
            text=True,
            check=False,
        )
        workflow_out = subprocess.run(
            [*cli_command("commands"), "--json", "--compact", "--workflow", "evidence"],
            capture_output=True,
            text=True,
            check=False,
        )
        by_name_payload = json.loads(by_name_out.stdout)
        workflow_payload = json.loads(workflow_out.stdout)

        self.assertEqual(by_name_out.returncode, 0, by_name_out.stderr or by_name_out.stdout)
        self.assertEqual([item["name"] for item in by_name_payload["commands"]], ["context"])
        self.assertTrue(by_name_payload["commands"][0]["supports_compact"])
        self.assertEqual(workflow_out.returncode, 0, workflow_out.stderr or workflow_out.stdout)
        self.assertIn("complete-experiments", {item["name"] for item in workflow_payload["commands"]})
        self.assertIn("create-artifact", {item["name"] for item in workflow_payload["commands"]})
        self.assertIn("ingest-artifact", {item["name"] for item in workflow_payload["commands"]})
        self.assertIn("start-agent-session", {item["name"] for item in workflow_payload["commands"]})
        self.assertTrue(all("evidence" in item["workflow_tags"] for item in workflow_payload["commands"]))

    def test_workflow_metrics_detects_manual_truth_patch_even_after_build(self) -> None:
        metrics = workflow_metrics(
            [{"command": cli_command("build"), "passed": True}],
            files_changed=[
                "research_cockpit/graph/nodes/problem_x.yaml",
                "research_cockpit/dashboards/graph_view.json",
            ],
        )

        self.assertTrue(metrics["manual_yaml_patch_detected"])
        self.assertEqual(metrics["truth_source_changed_files"], ["research_cockpit/graph/nodes/problem_x.yaml"])
        self.assertEqual(metrics["explained_truth_source_changes"], [])

    def test_workflow_metrics_treats_truth_mutation_as_explained_change(self) -> None:
        metrics = workflow_metrics(
            [{"command": cli_command("update-status"), "passed": True}],
            files_changed=["research_cockpit/graph/nodes/problem_x.yaml"],
        )

        self.assertFalse(metrics["manual_yaml_patch_detected"])
        self.assertEqual(metrics["explained_truth_source_changes"], ["research_cockpit/graph/nodes/problem_x.yaml"])

    def test_documented_flags_match_help_and_manifest(self) -> None:
        flag_fields = {
            "--compact": "supports_compact",
            "--dry-run": "supports_dry_run",
            "--show-diff": "supports_show_diff",
            "--no-build": "supports_no_build",
        }
        manifest = {item["name"]: item for item in agent_command_manifest()}
        docs = [ROOT_DIR / "README.md", ROOT_DIR / "SKILL.md", *(ROOT_DIR / "capabilities").glob("*.md")]
        documented: dict[str, set[str]] = {}
        pattern = re.compile(r"research-cockpit\s+([a-z0-9-]+)([^\n`]*)")
        for path in docs:
            text = path.read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                command = match.group(1)
                if command not in manifest:
                    continue
                command_text = match.group(0)
                for flag in flag_fields:
                    if flag in command_text:
                        documented.setdefault(command, set()).add(flag)

        for command, flags in documented.items():
            with self.subTest(command=command):
                help_out = subprocess.run(
                    [*cli_command(command), "--help"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(help_out.returncode, 0, help_out.stderr or help_out.stdout)
                for flag in flags:
                    self.assertIn(flag, help_out.stdout)
                    self.assertTrue(manifest[command].get(flag_fields[flag]), f"{command} missing {flag_fields[flag]}")

    def test_launcher_output_conventions_document_standard_files_and_ingest_paths(self) -> None:
        doc = ROOT_DIR / "docs" / "launcher-output-conventions.md"
        self.assertTrue(doc.exists())
        text = doc.read_text(encoding="utf-8")

        for filename in ("run_record.txt", "progress.json", "gate_result.json", "artifact_manifest.json"):
            self.assertIn(filename, text)
        for command in ("create-run", "update-run", "complete-run", "ingest-gate-result", "ingest-artifact"):
            self.assertIn(f"research-cockpit {command}", text)
        for launcher_mode in ("shell", "Python", "scheduler", "manual"):
            self.assertIn(launcher_mode, text)
        self.assertIn("artifact_manifest_v1", text)
        self.assertIn("launcher_run_record_v1", text)

        capability = (ROOT_DIR / "capabilities" / "experiment-tracking.md").read_text(encoding="utf-8")
        skill = (ROOT_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("docs/launcher-output-conventions.md", capability)
        self.assertIn("docs/launcher-output-conventions.md", skill)

    def test_launcher_templates_cover_modes_and_write_standard_files(self) -> None:
        template_dir = ROOT_DIR / "templates" / "launcher"
        required_modes = ("dry-run", "smoke-gate", "full-run", "artifact-capture", "validate-build", "next-action-update")
        standard_files = ("run_record.txt", "progress.json", "gate_result.json", "artifact_manifest.json")

        readme = (template_dir / "README.md").read_text(encoding="utf-8")
        python_template = (template_dir / "run_launcher.py").read_text(encoding="utf-8")
        shell_template = (template_dir / "run_launcher.sh").read_text(encoding="utf-8")
        manual_template = (template_dir / "manual_run_checklist.md").read_text(encoding="utf-8")
        for mode in required_modes:
            self.assertIn(mode, readme)
            self.assertIn(mode, python_template)
            self.assertIn(mode, shell_template)
        for filename in standard_files:
            self.assertIn(filename, readme)
            self.assertIn(filename, manual_template)
        self.assertIn("MONITOR_COMMAND", shell_template)
        self.assertIn("STOP_COMMAND", shell_template)
        self.assertIn('case "$MODE"', shell_template)
        for expected_default in (
            'DEFAULT_GATE_TYPE="dry_run"',
            'DEFAULT_GATE_TYPE="smoke_check"',
            'DEFAULT_GATE_TYPE="full_run"',
            'DEFAULT_GATE_TYPE="artifact_capture"',
            'DEFAULT_GATE_TYPE="validation_check"',
            'DEFAULT_GATE_TYPE="handoff_check"',
            'DEFAULT_NEXT_ALLOWED_ACTION="next_action_update"',
        ):
            self.assertIn(expected_default, shell_template)

        run_dir = self.tmp_root / "launcher_run"
        out = subprocess.run(
            [
                sys.executable,
                str(template_dir / "run_launcher.py"),
                "--run-dir",
                str(run_dir),
                "--experiment-id",
                "exp_t5",
                "--run-id",
                "run_template",
                "--mode",
                "smoke-gate",
                "--command",
                "python train.py --smoke",
                "--status",
                "completed",
                "--link",
                "metrics=outputs/metrics.json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        for filename in standard_files:
            self.assertTrue((run_dir / filename).exists(), filename)

        run_record = (run_dir / "run_record.txt").read_text(encoding="utf-8")
        progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
        gate = json.loads((run_dir / "gate_result.json").read_text(encoding="utf-8"))
        manifest = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))

        self.assertIn("monitor_command: tail -f logs/run.log", run_record)
        self.assertIn("stop_command:", run_record)
        self.assertEqual(progress["current_stage"], "smoke_gate")
        self.assertEqual(progress["status"], "completed")
        self.assertEqual(gate["gate_type"], "smoke_check")
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["next_allowed_action"], "full_run")
        self.assertEqual(manifest["schema_version"], "artifact_manifest_v1")
        self.assertEqual(manifest["links"]["metrics"], "outputs/metrics.json")

    def test_list_agent_commands_compact_json_returns_short_discovery_payload(self) -> None:
        out = subprocess.run(
            [*cli_command("commands"), "--json", "--compact"],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)
        by_name = {item["name"]: item for item in payload["commands"]}

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        self.assertIn("--print-schema", by_name["create-workstream"]["schema_command"])
        self.assertEqual(by_name["create-workstream"]["status_aliases"], {"option": {"planned": "open"}})
        self.assertNotIn("example_file", by_name["create-workstream"])
        self.assertNotIn("python_module_command", by_name["create-workstream"])
        self.assertNotIn("cwd", by_name["create-workstream"])
        self.assertNotIn("fields_supported", by_name["create-workstream"])
        self.assertNotIn("example_file", by_name["complete-experiments"])

    def test_file_commands_expose_schema_help_and_print_schema(self) -> None:
        expectations = {
            "apply-graph-plan": "nodes:",
            "create-workstream": "followup_options:",
            "complete-experiments": "experiments:",
            "create-artifact": "link_to:",
            "finalize-workstream": "summary_target:",
        }

        for command, marker in expectations.items():
            with self.subTest(command=command):
                help_out = subprocess.run(
                    [*cli_command(command), "--help"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                schema_out = subprocess.run(
                    [*cli_command(command), "--print-schema"],
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(help_out.returncode, 0, help_out.stderr or help_out.stdout)
                self.assertIn("File schema v1", help_out.stdout)
                self.assertIn(marker, help_out.stdout)
                self.assertEqual(schema_out.returncode, 0, schema_out.stderr or schema_out.stdout)
                self.assertIn(marker, schema_out.stdout)
                self.assertNotIn("--file", schema_out.stderr)

    def test_high_level_commands_support_compact_json(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_cache",
                "type": "artifact",
                "title": "Cache",
                "status": "done",
            },
        )
        record_finding(
            self.root,
            experiment_id="exp_t5",
            statement="Original finding.",
            confidence="medium",
            rebuild_dashboard=False,
        )
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["next_actions"] = ["Compact sync action."]
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        graph_plan = self.tmp_root / "graph_plan.yaml"
        save_yaml(
            graph_plan,
            {
                "nodes": [
                    {
                        "id": "problem_compact",
                        "type": "problem",
                        "title": "Compact problem",
                        "status": "active",
                    }
                ]
            },
        )
        workstream_plan = self.tmp_root / "workstream.yaml"
        save_yaml(
            workstream_plan,
            {
                "problem": {"id": "problem_ws_compact", "title": "Compact workstream"},
                "active_option": {"id": "option_ws_compact", "title": "Compact option"},
                "experiments": [{"id": "experiment_ws_compact", "title": "Compact experiment"}],
            },
        )
        findings_plan = self.tmp_root / "findings.yaml"
        save_yaml(
            findings_plan,
            {
                "defaults": {"confidence": "medium"},
                "experiments": [{"id": "exp_t5", "finding": "Compact batch finding."}],
            },
        )
        commands = [
            [
                *cli_command("apply-graph-plan"),
                "--root",
                str(self.root),
                "--file",
                str(graph_plan),
                "--dry-run",
                "--json",
                "--compact",
            ],
            [
                *cli_command("create-workstream"),
                "--root",
                str(self.root),
                "--file",
                str(workstream_plan),
                "--dry-run",
                "--json",
                "--compact",
            ],
            [
                *cli_command("create-artifact"),
                "--root",
                str(self.root),
                "--id",
                "artifact_compact",
                "--title",
                "Compact artifact",
                "--link-to",
                "option_t5",
                "--dry-run",
                "--json",
                "--compact",
            ],
            [
                *cli_command("complete-experiments"),
                "--root",
                str(self.root),
                "--file",
                str(findings_plan),
                "--dry-run",
                "--json",
                "--compact",
            ],
            [
                *cli_command("update-finding"),
                "--root",
                str(self.root),
                "--experiment",
                "exp_t5",
                "--finding-id",
                "exp_t5_finding_001",
                "--statement",
                "Compact finding update.",
                "--dry-run",
                "--json",
                "--compact",
            ],
            [
                *cli_command("set-focus"),
                "--root",
                str(self.root),
                "--focus-node",
                "exp_t5",
                "--dry-run",
                "--json",
                "--compact",
            ],
            [
                *cli_command("set-agent-focus"),
                "--root",
                str(self.root),
                "--agent",
                "agent_compact",
                "--node",
                "exp_t5",
                "--dry-run",
                "--json",
                "--compact",
            ],
            [
                *cli_command("sync-focus-actions"),
                "--root",
                str(self.root),
                "--from-node",
                "problem_text",
                "--dry-run",
                "--json",
                "--compact",
            ],
            [
                *cli_command("update-node-fields"),
                "--root",
                str(self.root),
                "--id",
                "problem_text",
                "--summary",
                "Compact problem summary.",
                "--dry-run",
                "--json",
                "--compact",
            ],
            [
                *cli_command("finalize-workstream"),
                "--root",
                str(self.root),
                "--option",
                "option_t5",
                "--status",
                "promising",
                "--dry-run",
                "--json",
                "--compact",
            ],
        ]

        for command in commands:
            with self.subTest(command=command[3]):
                out = subprocess.run(command, capture_output=True, text=True, check=False)
                payload = json.loads(out.stdout)

                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                self.assertTrue(payload["ok"])
                self.assertIn("command", payload)
                self.assertIn("target", payload)
                self.assertTrue(payload["dry_run"])
                self.assertTrue(payload["would_change"])
                self.assertIn("changed_files_count", payload)
                self.assertIn("research-cockpit validate", payload["verify_commands"][0])
                self.assertNotIn("before", payload)
                self.assertNotIn("after", payload)

    def test_skill_smoke_test_payload_runs_read_only_workflow(self) -> None:
        payload = skill_smoke_test_payload(root=self.root, query="t5", python_executable=sys.executable)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["root"], str(self.root))
        by_name = {item["name"]: item for item in payload["checks"]}
        self.assertTrue(by_name["validate_cockpit"]["passed"])
        self.assertTrue(by_name["agent_bootstrap"]["passed"])
        self.assertTrue(by_name["list_agent_commands"]["passed"])
        self.assertTrue(by_name["search_knowledge"]["passed"])
        self.assertTrue(by_name["suggest_next_actions"]["passed"])
        self.assertGreaterEqual(by_name["list_agent_commands"]["summary"]["command_count"], 1)

    def test_skill_smoke_test_uses_python_environment_override(self) -> None:
        with patch.dict(os.environ, {"RESEARCH_COCKPIT_PYTHON": sys.executable}):
            payload = skill_smoke_test_payload(root=self.root, query="t5")

        self.assertEqual(payload["python"], sys.executable)
        self.assertTrue(payload["ok"])

    def test_skill_smoke_test_dependency_check_reports_missing_modules(self) -> None:
        missing = missing_modules_for_python(
            sys.executable,
            {"sys": "stdlib", "definitely_missing_module_for_test": "example-package"},
        )

        self.assertEqual(missing, ["definitely_missing_module_for_test"])

    def test_create_note_generates_note_links_node_and_rebuilds_dashboard(self) -> None:
        note_path = create_note(self.root, node_id="problem_text")

        data = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")

        self.assertEqual(note_path, self.root / "notes" / "problems" / "problem_text.md")
        self.assertTrue(note_path.exists())
        self.assertIn("Problem", note_path.read_text(encoding="utf-8"))
        self.assertEqual(data["links"]["notes"], "notes/problems/problem_text.md")
        self.assertTrue((self.root / "dashboards" / "linked_resources.json").exists())

    def test_create_note_rejects_existing_without_overwrite_and_allows_overwrite(self) -> None:
        note_path = create_note(self.root, node_id="option_t5", rebuild_dashboard=False)

        with self.assertRaises(FileExistsError):
            create_note(self.root, node_id="option_t5", rebuild_dashboard=False)

        note_path.write_text("old text\n", encoding="utf-8")
        overwritten = create_note(self.root, node_id="option_t5", overwrite=True, rebuild_dashboard=False)

        self.assertEqual(overwritten, note_path)
        self.assertIn("Option", overwritten.read_text(encoding="utf-8"))

    def test_create_note_rejects_unsupported_node_type(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            create_note(self.root, node_id="stage_text", rebuild_dashboard=False)

        self.assertIn("stage", str(ctx.exception))

    def test_create_note_rejects_malformed_interaction_log_without_note_side_effect(self) -> None:
        (self.root / "graph" / "interaction_log.yaml").write_text("events:\n  - [bad\n", encoding="utf-8")
        note_path = self.root / "notes" / "problems" / "problem_text.md"

        with self.assertRaises(MutationError):
            create_note(self.root, node_id="problem_text", rebuild_dashboard=False)

        data = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        self.assertFalse(note_path.exists())
        self.assertNotIn("links", data)

    def test_validate_cockpit_cli_reports_success_and_failure(self) -> None:
        command = "validate"

        ok = subprocess.run(
            [*cli_command(command), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ok.returncode, 0)
        self.assertIn("OK", ok.stdout)
        ok_json = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ok_json.returncode, 0)
        ok_payload = json.loads(ok_json.stdout)
        self.assertTrue(ok_payload["valid"])
        self.assertTrue(ok_payload["ok"])

        bad = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        bad["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", bad)
        failed = subprocess.run(
            [*cli_command(command), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(failed.returncode, 1)
        self.assertIn("invalid status", failed.stdout)

        bad["status"] = "active"
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", bad)
        save_yaml(self.root / "graph" / "interaction_log.yaml", {"events": ["bad event"]})
        bad_log = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(bad_log.returncode, 1)
        bad_log_payload = json.loads(bad_log.stdout)
        self.assertFalse(bad_log_payload["ok"])
        self.assertTrue(any("interaction_log.yaml" in error and "events[1]" in error for error in bad_log_payload["errors"]))

        write_malformed_interaction_log(self.root)
        malformed_log = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(malformed_log.returncode, 1)
        malformed_payload = json.loads(malformed_log.stdout)
        self.assertTrue(any("YAML parse error" in error for error in malformed_payload["errors"]))

    def test_repair_interaction_log_dry_run_and_execute_schema_repair(self) -> None:
        log_path = self.root / "graph" / "interaction_log.yaml"
        save_yaml(log_path, {"events": [{"kind": "ok"}, "bad event", {"kind": "still_ok"}]})
        before = log_path.read_text(encoding="utf-8")

        dry_run = repair_interaction_log(self.root, dry_run=True, show_diff=True)
        self.assertTrue(dry_run["would_change"])
        self.assertFalse(dry_run["changed"])
        self.assertEqual(dry_run["dropped_event_count"], 1)
        self.assertIn("- bad event", dry_run["diff"])
        self.assertEqual(before, log_path.read_text(encoding="utf-8"))

        out = subprocess.run(
            [*cli_command("repair-interaction-log"), "--root", str(self.root), "--json", "--show-diff", "--backup"],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)
        repaired = load_yaml(log_path)

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        self.assertTrue(payload["changed"])
        self.assertEqual(payload["kept_event_count"], 2)
        self.assertEqual(payload["dropped_event_count"], 1)
        self.assertTrue(Path(payload["backup_path"]).exists())
        self.assertEqual([event["kind"] for event in repaired["events"]], ["ok", "still_ok"])

    def test_repair_interaction_log_rejects_yaml_parse_error(self) -> None:
        log_path = self.root / "graph" / "interaction_log.yaml"
        log_path.write_text("events:\n- kind: broken\n  command: [\n", encoding="utf-8")
        before = log_path.read_text(encoding="utf-8")

        out = subprocess.run(
            [*cli_command("repair-interaction-log"), "--root", str(self.root), "--json", "--show-diff"],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("cannot be repaired automatically", payload["error"])
        self.assertEqual(before, log_path.read_text(encoding="utf-8"))

    def test_repair_interaction_log_rereads_after_waiting_for_lock(self) -> None:
        log_path = self.root / "graph" / "interaction_log.yaml"
        save_yaml(log_path, {"events": [{"kind": "ok"}, "bad event"]})

        with mutation_lock(self.root):
            proc = subprocess.Popen(
                [*cli_command("repair-interaction-log"), "--root", str(self.root), "--json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            time.sleep(0.5)
            log = load_yaml(log_path)
            log["events"].append({"kind": "while_waiting"})
            save_yaml(log_path, log)

        stdout, stderr = proc.communicate(timeout=10)
        payload = json.loads(stdout)
        repaired = load_yaml(log_path)

        self.assertEqual(proc.returncode, 0, stderr or stdout)
        self.assertTrue(payload["changed"])
        self.assertEqual([event["kind"] for event in repaired["events"]], ["ok", "while_waiting"])

    def test_cli_help_documents_status_and_decision_checklist_constraints(self) -> None:
        status_help = subprocess.run(
            [*cli_command("update-status"), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        checklist_help = subprocess.run(
            [*cli_command("update-decision-checklist"), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(status_help.returncode, 0)
        self.assertEqual(checklist_help.returncode, 0)
        self.assertIn("Experiment nodes only", status_help.stdout)
        self.assertIn("Existing option node id", checklist_help.stdout)

    def test_suggest_next_actions_cli_outputs_text_json_and_filters(self) -> None:
        command = "suggest-next-actions"

        text = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--limit", "2"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(text.returncode, 0)
        self.assertIn("run_experiment", text.stdout)

        json_out = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--json",
                "--kind",
                "run_experiment",
                "--focus-only",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(json_out.returncode, 0)
        suggestions = json.loads(json_out.stdout)
        self.assertEqual({item["kind"] for item in suggestions}, {"run_experiment"})
        self.assertTrue(all(item["is_focus_related"] for item in suggestions))
        self.assertTrue(all(item["id"] == item["display_id"] for item in suggestions))
        self.assertTrue(all(item["key"] == item["suggestion_id"] for item in suggestions))

        selected = select_suggestions(suggestions, kinds=["run_experiment"], limit=1, focus_only=True)
        self.assertEqual(len(selected), 1)

    def test_suggest_next_actions_dedupes_normalized_focus_actions(self) -> None:
        current = load_yaml(self.root / "current_state.yaml")
        current["next_actions"] = ["Review graph plan."]
        save_yaml(self.root / "current_state.yaml", current)
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["next_actions"] = ["  review   graph plan  "]
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)

        out = subprocess.run(
            [
                *cli_command("suggest-next-actions"),
                "--root",
                str(self.root),
                "--kind",
                "focus_next_action",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        suggestions = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(len(suggestions), 1)
        self.assertTrue(suggestions[0]["queued_in_current"])
        self.assertTrue(suggestions[0]["queued_in_node"])

    def test_sync_focus_actions_replaces_and_appends_from_node(self) -> None:
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["next_actions"] = ["Review graph plan.", "Record decision."]
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        current = load_yaml(self.root / "current_state.yaml")
        current["next_actions"] = ["Old action."]
        save_yaml(self.root / "current_state.yaml", current)

        dry_run = sync_focus_actions(
            self.root,
            from_node="problem_text",
            mode="replace",
            dry_run=True,
            show_diff=True,
        )
        self.assertTrue(dry_run["would_change"])
        self.assertIn("diff", dry_run)
        self.assertEqual(load_yaml(self.root / "current_state.yaml")["next_actions"], ["Old action."])

        replaced = sync_focus_actions(self.root, from_node="problem_text", mode="replace", rebuild_dashboard=False)
        self.assertTrue(replaced["changed"])
        self.assertEqual(
            load_yaml(self.root / "current_state.yaml")["next_actions"],
            ["Review graph plan.", "Record decision."],
        )

        problem["next_actions"] = ["Record decision.", "Draft ADR."]
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        appended = sync_focus_actions(self.root, from_node="problem_text", mode="append", rebuild_dashboard=False)

        self.assertTrue(appended["changed"])
        self.assertEqual(
            load_yaml(self.root / "current_state.yaml")["next_actions"],
            ["Review graph plan.", "Record decision.", "Draft ADR."],
        )

    def test_apply_and_update_suggestion_accept_stable_suggestion_id(self) -> None:
        first = apply_suggestion(self.root, suggestion_id="next_action_001", target="current", rebuild_dashboard=False)
        stable_id = first["suggestion"]["suggestion_id"]
        second = apply_suggestion(self.root, suggestion_id=stable_id, target="current", rebuild_dashboard=False)
        completed = update_suggestion_state(
            self.root,
            suggestion_id=stable_id,
            state="completed",
            reason="Handled via stable id.",
            rebuild_dashboard=False,
        )
        current = load_yaml(self.root / "current_state.yaml")

        self.assertFalse(second["changed"])
        self.assertEqual(completed["suggestion"]["suggestion_id"], stable_id)
        self.assertEqual(current["suggestion_lifecycle"][stable_id]["state"], "completed")

    def test_suggest_next_actions_cli_filters_lifecycle_state(self) -> None:
        command = "suggest-next-actions"
        update_suggestion_state(
            self.root,
            suggestion_id="next_action_001",
            state="dismissed",
            reason="Will not run this now.",
            rebuild_dashboard=False,
        )

        default_out = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        inactive_out = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--include-inactive",
                "--state",
                "dismissed",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(default_out.returncode, 0)
        self.assertEqual(inactive_out.returncode, 0)
        default_suggestions = json.loads(default_out.stdout)
        inactive_suggestions = json.loads(inactive_out.stdout)
        self.assertNotIn("dismissed", {item["lifecycle_state"] for item in default_suggestions})
        self.assertEqual({item["lifecycle_state"] for item in inactive_suggestions}, {"dismissed"})

    def test_suggest_next_actions_cli_fails_on_invalid_cockpit(self) -> None:
        command = "suggest-next-actions"
        bad = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        bad["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", bad)

        failed = subprocess.run(
            [*cli_command(command), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(failed.returncode, 1)
        self.assertIn("invalid status", failed.stdout)

    def test_semantic_lint_reports_stale_focus_and_state_drift(self) -> None:
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["result_summary"] = "Finished."
        experiment["findings"] = [
            {
                "id": "exp_t5_finding_001",
                "statement": "Done.",
                "confidence": "medium",
                "evidence": ["exp_t5"],
            }
        ]
        experiment["next_actions"] = ["Create follow-up gate."]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "exp_t5"
        current["current_focus_path"] = ["stage_text", "problem_text", "option_t5", "exp_t5"]
        current["next_actions"] = ["Continue exp_t5."]
        current["agent_focuses"] = {
            "agent_t5": {
                "current_focus_node": "exp_t5",
                "current_focus_path": ["stage_text", "problem_text", "option_t5", "exp_t5"],
                "current_option": "option_t5",
                "next_actions": ["Continue exp_t5."],
            }
        }
        save_yaml(self.root / "current_state.yaml", current)
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["next_actions"] = ["Review done branch."]
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)

        payload = semantic_lint(self.root)
        out = subprocess.run(
            [*cli_command("lint"), "--root", str(self.root), "--semantic", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        cli_payload = json.loads(out.stdout)

        warning_ids = {warning["id"] for warning in payload["warnings"]}
        self.assertFalse(payload["ok"])
        self.assertIn("current_focus_terminal", warning_ids)
        self.assertIn("agent_focus_terminal", warning_ids)
        self.assertIn("next_action_references_terminal_node", warning_ids)
        self.assertIn("current_next_actions_diverge_from_focus_node", warning_ids)
        self.assertIn("terminal_node_has_next_actions", warning_ids)
        followup_warning = next(
            warning for warning in payload["warnings"] if warning["id"] == "terminal_node_has_next_actions"
        )
        self.assertIn("migrate-terminal-next-actions", followup_warning["command"])
        self.assertIn("--followup-id <followup_experiment_id>", followup_warning["command"])
        self.assertIn("--dry-run --json --show-diff", followup_warning["command"])
        self.assertNotIn("create-followup-experiment", followup_warning["command"])
        self.assertEqual(out.returncode, 1)
        self.assertEqual(cli_payload["warning_count"], len(payload["warnings"]))

    def test_semantic_lint_reports_terminal_parent_with_active_descendants(self) -> None:
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["status"] = "resolved"
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)

        payload = semantic_lint(self.root)
        out = subprocess.run(
            [*cli_command("lint"), "--root", str(self.root), "--semantic", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        cli_payload = json.loads(out.stdout)

        warning = next(
            warning
            for warning in payload["warnings"]
            if warning["id"] == "terminal_parent_has_active_descendants"
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(warning["node_id"], "problem_text")
        self.assertEqual(warning["parent_status"], "resolved")
        self.assertEqual(
            warning["blocking_descendants"],
            [
                {
                    "id": "option_t5",
                    "type": "option",
                    "status": "active",
                    "path": ["stage_text", "problem_text", "option_t5"],
                },
                {
                    "id": "exp_t5",
                    "type": "experiment",
                    "status": "planned",
                    "path": ["stage_text", "problem_text", "option_t5", "exp_t5"],
                },
            ],
        )
        self.assertIn("close-branch", warning["command"])
        self.assertEqual(out.returncode, 1)
        self.assertIn("terminal_parent_has_active_descendants", {item["id"] for item in cli_payload["warnings"]})

    def test_semantic_lint_warns_on_missing_retention_metadata(self) -> None:
        output_path = self.root / "artifacts" / "run_retention_missing"
        output_path.mkdir(parents=True, exist_ok=True)
        (output_path / "metrics.json").write_text("{}", encoding="utf-8")
        save_yaml(
            self.root / "runs" / "run_missing_retention.yaml",
            {
                "run_id": "run_missing_retention",
                "status": "completed",
                "experiment_id": "exp_t5",
                "output_root": "artifacts/run_retention_missing",
            },
        )
        artifact_path = self.root / "artifacts" / "artifact_retention_missing"
        artifact_path.mkdir(parents=True, exist_ok=True)
        (artifact_path / "payload.bin").write_bytes(b"large")
        write_node(
            self.root,
            {
                "id": "artifact_retention_missing",
                "type": "artifact",
                "title": "Large artifact without retention",
                "status": "done",
                "path": "artifacts/artifact_retention_missing",
            },
        )

        validation_errors = validate_cockpit(self.root)
        payload = semantic_lint(self.root, artifact_min_size_bytes=1)

        self.assertEqual(validation_errors, [])
        self.assertTrue(payload["valid"])
        warning_ids = {warning["id"] for warning in payload["warnings"]}
        self.assertIn("run_completed_without_retention_policy", warning_ids)
        self.assertIn("artifact_missing_retention_policy", warning_ids)
        run_warning = next(
            warning for warning in payload["warnings"] if warning["id"] == "run_completed_without_retention_policy"
        )
        artifact_warning = next(
            warning for warning in payload["warnings"] if warning["id"] == "artifact_missing_retention_policy"
        )
        self.assertEqual(run_warning["run_id"], "run_missing_retention")
        self.assertEqual(artifact_warning["node_id"], "artifact_retention_missing")
        self.assertIn("complete-run", run_warning["command"])
        self.assertIn("update-node-fields", artifact_warning["command"])

    def test_semantic_lint_does_not_suggest_invalid_followup_for_failed_experiment(self) -> None:
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "failed"
        experiment["next_actions"] = ["Investigate failure."]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)

        payload = semantic_lint(self.root)
        warning = next(
            warning for warning in payload["warnings"] if warning["id"] == "terminal_node_has_next_actions"
        )

        self.assertIsNone(warning.get("command"))

    def test_semantic_lint_does_not_substring_match_terminal_node_ids(self) -> None:
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        write_node(
            self.root,
            {
                "id": "exp_t5_followup",
                "type": "experiment",
                "title": "T5 follow-up",
                "status": "planned",
                "parent": "option_t5",
            },
        )
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "option_t5"
        current["next_actions"] = ["Run exp_t5_followup."]
        save_yaml(self.root / "current_state.yaml", current)

        payload = semantic_lint(self.root)

        terminal_reference_warnings = [
            warning
            for warning in payload["warnings"]
            if warning["id"] == "next_action_references_terminal_node"
        ]
        self.assertEqual(terminal_reference_warnings, [])

    def test_bootstrap_and_context_include_semantic_warnings(self) -> None:
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "exp_t5"
        current["current_focus_path"] = ["stage_text", "problem_text", "option_t5", "exp_t5"]
        save_yaml(self.root / "current_state.yaml", current)

        bootstrap = agent_bootstrap_payload(self.root)
        context = context_payload(self.root, node_id="exp_t5", with_bootstrap=True, compact=True)

        self.assertIn("semantic_warnings", bootstrap)
        self.assertTrue(bootstrap["semantic_warnings"])
        self.assertTrue(context["semantic_warnings"])
        self.assertTrue(context["bootstrap"]["semantic_warnings"])

    def test_search_knowledge_cli_outputs_json_and_filters(self) -> None:
        command = "search"
        note_path = self.root / "notes" / "problems" / "problem_text.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("# Search Note\nNeedle note for T5 branch.\n", encoding="utf-8")
        resource_path = self.root / "resources" / "problem_context.txt"
        resource_path.parent.mkdir(parents=True, exist_ok=True)
        resource_path.write_text("Needle resource context for T5 branch.\n", encoding="utf-8")
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["summary"] = "Needle YAML problem summary."
        problem["links"] = {
            "notes": "notes/problems/problem_text.md",
            "context": "resources/problem_context.txt",
        }
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)

        json_out = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--query", "needle", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(json_out.returncode, 0)
        results = json.loads(json_out.stdout)
        self.assertGreaterEqual(len(results), 2)
        self.assertIn("snippet", results[0])

        note_only = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--query", "needle", "--source", "note", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        resource_only = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--query",
                "needle",
                "--source",
                "resource",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        node_problem = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--query",
                "needle",
                "--source",
                "node",
                "--node-type",
                "problem",
                "--limit",
                "1",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        focus_only = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--query",
                "needle",
                "--focus-only",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        empty = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--query", "missing-needle", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(note_only.returncode, 0)
        self.assertEqual({item["source"] for item in json.loads(note_only.stdout)}, {"note"})
        self.assertEqual(resource_only.returncode, 0)
        resource_results = json.loads(resource_only.stdout)
        self.assertEqual({item["source"] for item in resource_results}, {"resource"})
        self.assertEqual(resource_results[0]["path"], "resources/problem_context.txt")
        self.assertEqual(node_problem.returncode, 0)
        node_results = json.loads(node_problem.stdout)
        self.assertEqual(len(node_results), 1)
        self.assertEqual(node_results[0]["node_type"], "problem")
        self.assertEqual(focus_only.returncode, 0)
        self.assertTrue(all(item["is_focus_related"] for item in json.loads(focus_only.stdout)))
        self.assertEqual(empty.returncode, 0)
        self.assertEqual(json.loads(empty.stdout), [])

    def test_search_knowledge_cli_fails_on_invalid_cockpit(self) -> None:
        command = "search"
        bad = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        bad["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", bad)

        failed = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--query", "t5"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(failed.returncode, 1)
        self.assertIn("invalid status", failed.stdout)

    def test_apply_suggestion_adds_current_action_without_duplicates(self) -> None:
        result = apply_suggestion(self.root, suggestion_id="next_action_001", target="current", rebuild_dashboard=False)
        first = load_yaml(self.root / "current_state.yaml")
        result_again = apply_suggestion(self.root, suggestion_id="next_action_001", target="current", rebuild_dashboard=False)
        second = load_yaml(self.root / "current_state.yaml")

        self.assertEqual(result["target"], "current")
        self.assertTrue(result["changed"])
        self.assertFalse(result_again["changed"])
        self.assertEqual(first["next_actions"], second["next_actions"])
        self.assertEqual(first["next_actions"].count(result["suggestion"]["action"]), 1)
        events = interaction_events(self.root)
        self.assertEqual([event["kind"] for event in events[-2:]], ["apply_suggestion", "apply_suggestion"])
        self.assertEqual(events[-2]["target"], "current")
        self.assertTrue(events[-2]["changed"])
        self.assertFalse(events[-1]["changed"])

    def test_apply_suggestion_adds_source_node_action_and_rebuilds_dashboard(self) -> None:
        result = apply_suggestion(self.root, suggestion_id="next_action_001", target="node")
        source_id = result["suggestion"]["source_node_id"]
        source = load_yaml(self.root / "graph" / "nodes" / f"{source_id}.yaml")

        self.assertEqual(result["target"], "node")
        self.assertIn(result["suggestion"]["action"], source["next_actions"])
        self.assertTrue((self.root / "dashboards" / "next_action_suggestions.json").exists())
        event = interaction_events(self.root)[-1]
        self.assertEqual(event["kind"], "apply_suggestion")
        self.assertEqual(event["node_id"], source_id)
        self.assertEqual(event["target"], "node")
        self.assertTrue(event["changed"])

    def test_apply_suggestion_rejects_unknown_id_and_invalid_target(self) -> None:
        with self.assertRaises(ValueError) as missing:
            apply_suggestion(self.root, suggestion_id="missing_suggestion", target="current", rebuild_dashboard=False)
        self.assertIn("missing_suggestion", str(missing.exception))

        with self.assertRaises(ValueError) as bad_target:
            apply_suggestion(self.root, suggestion_id="next_action_001", target="invalid", rebuild_dashboard=False)
        self.assertIn("target", str(bad_target.exception))

    def test_apply_suggestion_cli_reports_errors(self) -> None:
        command = "apply-suggestion"

        ok = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--id", "next_action_001", "--no-build"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ok.returncode, 0)
        self.assertIn("Queued", ok.stdout)

        failed = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--id", "missing_suggestion", "--no-build"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(failed.returncode, 1)
        self.assertIn("missing_suggestion", failed.stdout)

    def test_apply_suggestion_cli_dry_run_json_previews_without_writing(self) -> None:
        command = "apply-suggestion"
        current_path = self.root / "current_state.yaml"
        before = current_path.read_text(encoding="utf-8")

        out = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--id",
                "next_action_001",
                "--target",
                "current",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after = current_path.read_text(encoding="utf-8")

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["target"], "current")
        self.assertTrue(payload["would_change"])
        self.assertEqual(payload["before"]["next_actions"], [])
        self.assertEqual(len(payload["after"]["next_actions"]), 1)
        self.assertEqual(before, after)
        self.assertFalse((self.root / "graph" / "interaction_log.yaml").exists())
        self.assertFalse((self.root / "dashboards").exists())

    def test_update_suggestion_state_writes_dismissed_completed_and_active(self) -> None:
        dismissed = update_suggestion_state(
            self.root,
            suggestion_id="next_action_001",
            state="dismissed",
            reason="Not useful now.",
            rebuild_dashboard=False,
        )
        current = load_yaml(self.root / "current_state.yaml")
        key = dismissed["suggestion"]["key"]

        self.assertEqual(current["suggestion_lifecycle"][key]["state"], "dismissed")
        self.assertEqual(current["suggestion_lifecycle"][key]["reason"], "Not useful now.")

        completed = update_suggestion_state(
            self.root,
            suggestion_id=key,
            state="completed",
            reason="Done manually.",
            rebuild_dashboard=False,
        )
        current = load_yaml(self.root / "current_state.yaml")
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(current["suggestion_lifecycle"][key]["state"], "completed")

        restored = update_suggestion_state(
            self.root,
            suggestion_id=key,
            state="active",
            rebuild_dashboard=False,
        )
        current = load_yaml(self.root / "current_state.yaml")
        self.assertEqual(restored["state"], "active")
        self.assertNotIn(key, current.get("suggestion_lifecycle", {}))

    def test_update_suggestion_state_rebuilds_dashboard_and_cli_reports_errors(self) -> None:
        command = "update-suggestion-state"

        ok = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--id",
                "next_action_001",
                "--state",
                "completed",
                "--reason",
                "Handled outside cockpit.",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ok.returncode, 0)
        self.assertIn("completed", ok.stdout)
        self.assertTrue((self.root / "dashboards" / "next_action_suggestions.json").exists())

        failed = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--id",
                "missing_suggestion",
                "--state",
                "dismissed",
                "--no-build",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(failed.returncode, 1)
        self.assertIn("missing_suggestion", failed.stdout)

    def test_update_suggestion_state_cli_dry_run_json_diff_does_not_write(self) -> None:
        command = "update-suggestion-state"
        before = (self.root / "current_state.yaml").read_text(encoding="utf-8")
        out = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--id",
                "next_action_001",
                "--state",
                "dismissed",
                "--reason",
                "Preview dismissal.",
                "--dry-run",
                "--show-diff",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after = (self.root / "current_state.yaml").read_text(encoding="utf-8")
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(payload["dry_run"])
        self.assertFalse(payload["changed"])
        self.assertTrue(payload["would_change"])
        self.assertEqual(payload["state"], "dismissed")
        self.assertIn("diff", payload)
        self.assertEqual(before, after)
        self.assertFalse((self.root / "graph" / "interaction_log.yaml").exists())

    def test_update_suggestion_state_rejects_malformed_interaction_log_without_write(self) -> None:
        before = (self.root / "current_state.yaml").read_text(encoding="utf-8")
        write_malformed_interaction_log(self.root)

        with self.assertRaises(MutationError):
            update_suggestion_state(
                self.root,
                suggestion_id="next_action_001",
                state="completed",
                rebuild_dashboard=False,
            )

        self.assertEqual(before, (self.root / "current_state.yaml").read_text(encoding="utf-8"))

    def test_cleanup_suggestion_lifecycle_dry_run_and_age_filter(self) -> None:
        current = load_yaml(self.root / "current_state.yaml")
        current["suggestion_lifecycle"] = {
            "old_completed": {
                "state": "completed",
                "reason": "Resolved old suggestion.",
                "updated_at": "2000-01-01",
                "action": "Old action",
                "kind": "run_experiment",
                "source_node_id": "exp_old",
            },
            "fresh_dismissed": {
                "state": "dismissed",
                "reason": "Recent dismissal.",
                "updated_at": str(date.today()),
                "action": "Fresh action",
                "kind": "record_finding",
                "source_node_id": "exp_old",
            },
            "bad_date": {
                "state": "dismissed",
                "reason": "Bad date stays when age filtered.",
                "updated_at": "not-a-date",
                "action": "Bad date action",
                "kind": "record_finding",
                "source_node_id": "exp_old",
            },
        }
        save_yaml(self.root / "current_state.yaml", current)

        dry_run = cleanup_suggestion_lifecycle(
            self.root,
            dry_run=True,
            older_than_days=1,
            rebuild_dashboard=False,
        )
        after_dry_run = load_yaml(self.root / "current_state.yaml")
        cleaned = cleanup_suggestion_lifecycle(
            self.root,
            older_than_days=1,
            rebuild_dashboard=False,
        )
        after_clean = load_yaml(self.root / "current_state.yaml")

        self.assertEqual(dry_run["candidate_count"], 1)
        self.assertFalse(dry_run["changed"])
        self.assertIn("old_completed", after_dry_run["suggestion_lifecycle"])
        self.assertEqual(cleaned["removed_count"], 1)
        self.assertTrue(cleaned["changed"])
        self.assertNotIn("old_completed", after_clean["suggestion_lifecycle"])
        self.assertIn("fresh_dismissed", after_clean["suggestion_lifecycle"])
        self.assertIn("bad_date", after_clean["suggestion_lifecycle"])
        self.assertEqual(after_clean["updated_at"], str(date.today()))

    def test_cleanup_suggestion_lifecycle_cli_json_and_noop(self) -> None:
        command = "cleanup-suggestion-lifecycle"
        current = load_yaml(self.root / "current_state.yaml")
        current["suggestion_lifecycle"] = {
            "old_completed": {
                "state": "completed",
                "reason": "Resolved old suggestion.",
                "updated_at": "2000-01-01",
                "action": "Old action",
                "kind": "run_experiment",
                "source_node_id": "exp_old",
            },
            "old_dismissed": {
                "state": "dismissed",
                "reason": "Dismissed old suggestion.",
                "updated_at": "2000-01-01",
                "action": "Dismissed action",
                "kind": "record_finding",
                "source_node_id": "exp_old",
            },
        }
        save_yaml(self.root / "current_state.yaml", current)

        dry_run = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--dry-run", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        cleaned = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--state",
                "completed",
                "--no-build",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        noop = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--state",
                "completed",
                "--no-build",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after_clean = load_yaml(self.root / "current_state.yaml")

        self.assertEqual(dry_run.returncode, 0)
        payload = json.loads(dry_run.stdout)
        self.assertEqual(payload["candidate_count"], 2)
        self.assertFalse(payload["changed"])
        self.assertEqual(cleaned.returncode, 0)
        self.assertIn("Removed 1", cleaned.stdout)
        self.assertNotIn("old_completed", after_clean["suggestion_lifecycle"])
        self.assertIn("old_dismissed", after_clean["suggestion_lifecycle"])
        self.assertEqual(noop.returncode, 0)
        self.assertIn("No orphan", noop.stdout)

    def test_cleanup_suggestion_lifecycle_cli_show_diff_previews_and_writes(self) -> None:
        command = "cleanup-suggestion-lifecycle"
        current = load_yaml(self.root / "current_state.yaml")
        current["suggestion_lifecycle"] = {
            "old_completed": {
                "state": "completed",
                "reason": "Resolved old suggestion.",
                "updated_at": "2000-01-01",
                "action": "Old action",
                "kind": "run_experiment",
                "source_node_id": "exp_old",
            }
        }
        save_yaml(self.root / "current_state.yaml", current)
        before = (self.root / "current_state.yaml").read_text(encoding="utf-8")

        dry_run = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--dry-run", "--show-diff", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        after_dry_run = (self.root / "current_state.yaml").read_text(encoding="utf-8")
        payload = json.loads(dry_run.stdout)
        cleaned = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--show-diff", "--json", "--no-build"],
            capture_output=True,
            text=True,
            check=False,
        )
        cleaned_payload = json.loads(cleaned.stdout)
        after_clean = load_yaml(self.root / "current_state.yaml")

        self.assertEqual(dry_run.returncode, 0, dry_run.stdout + dry_run.stderr)
        self.assertTrue(payload["dry_run"])
        self.assertFalse(payload["changed"])
        self.assertEqual(payload["candidate_count"], 1)
        self.assertIn("diff", payload)
        self.assertEqual(before, after_dry_run)
        self.assertEqual(cleaned.returncode, 0, cleaned.stdout + cleaned.stderr)
        self.assertTrue(cleaned_payload["changed"])
        self.assertIn("diff", cleaned_payload)
        self.assertNotIn("old_completed", after_clean.get("suggestion_lifecycle", {}))
        self.assertFalse((self.root / "dashboards").exists())

    def test_record_finding_appends_finding_and_rebuilds_dashboard(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_cache",
                "type": "artifact",
                "title": "Feature cache",
                "status": "active",
            },
        )

        record_finding(
            self.root,
            experiment_id="exp_t5",
            statement="T5 improves replace following.",
            confidence="medium",
            outcome="positive",
            metrics=["replace_following"],
            artifacts=["artifact_cache"],
            summary="Improved edit following.",
        )

        data = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        context = json.loads((self.root / "dashboards" / "focus_context_pack.json").read_text(encoding="utf-8"))

        self.assertEqual(data["result_summary"], "Improved edit following.")
        self.assertEqual(data["findings"][0]["id"], "exp_t5_finding_001")
        self.assertEqual(data["findings"][0]["evidence"], ["exp_t5"])
        self.assertEqual(data["findings"][0]["linked_artifacts"], ["artifact_cache"])
        self.assertEqual(context["local_neighbors"]["experiments"][0]["findings"][0]["statement"], "T5 improves replace following.")
        events = interaction_events(self.root)
        self.assertEqual(events[-1]["kind"], "record_finding")
        self.assertEqual(events[-1]["node_id"], "exp_t5")
        self.assertEqual(events[-1]["experiment_id"], "exp_t5")
        self.assertEqual(events[-1]["finding_id"], "exp_t5_finding_001")
        self.assertEqual(events[-1]["before"]["finding_count"], 0)
        self.assertEqual(events[-1]["after"]["finding_count"], 1)

    def test_create_and_link_artifact_support_resource_fields_and_reverse_links(self) -> None:
        result = create_artifact(
            self.root,
            artifact_id="artifact_results",
            title="Result bundle",
            status="done",
            summary="Collected outputs.",
            path="outputs/run_a",
            links={"metrics": "outputs/run_a/metrics.json"},
            link_to=["option_t5"],
            rebuild_dashboard=False,
        )
        artifact = load_yaml(self.root / "graph" / "nodes" / "artifact_results.yaml")
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")

        self.assertTrue(result["changed"])
        self.assertEqual(artifact["path"], "outputs/run_a")
        self.assertEqual(artifact["links"]["metrics"], "outputs/run_a/metrics.json")
        self.assertEqual(option["linked_artifacts"], ["artifact_results"])
        self.assertEqual(interaction_events(self.root)[-1]["kind"], "create_artifact")

        link_result = link_artifact(
            self.root,
            artifact_id="artifact_results",
            to_nodes=["exp_t5", "option_t5"],
            path="outputs/run_b",
            links={"review": "notes/review.md"},
            rebuild_dashboard=False,
        )
        artifact = load_yaml(self.root / "graph" / "nodes" / "artifact_results.yaml")
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")

        self.assertTrue(link_result["changed"])
        self.assertEqual(artifact["path"], "outputs/run_b")
        self.assertEqual(artifact["links"]["review"], "notes/review.md")
        self.assertEqual(experiment["linked_artifacts"], ["artifact_results"])
        self.assertEqual(option["linked_artifacts"], ["artifact_results"])

    def test_ingest_artifact_copies_worktree_output_to_stable_store(self) -> None:
        source = self.tmp_root / "worktrees" / "agent_t5" / ".agent_runs" / "run_001"
        (source / "figures").mkdir(parents=True)
        (source / "metrics.json").write_text('{"score": 0.91}', encoding="utf-8")
        (source / "figures" / "curve.txt").write_text("curve", encoding="utf-8")

        result = ingest_artifact(
            self.root,
            node_id="exp_t5",
            source_dir=source,
            run_id="run_001",
            agent_id="agent_t5",
            links={"metrics": "metrics.json"},
            rebuild_dashboard=False,
        )
        target = self.root / "artifacts" / "exp_t5" / "run_001"
        artifact = load_yaml(self.root / "graph" / "nodes" / "artifact_exp_t5_run_001.yaml")
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        manifest = json.loads((target / "_research_cockpit_ingest.json").read_text(encoding="utf-8"))

        self.assertTrue(result["changed"])
        self.assertEqual(result["artifact_id"], "artifact_exp_t5_run_001")
        self.assertEqual(result["stable_path"], "artifacts/exp_t5/run_001")
        self.assertEqual(result["source_path_resolved"], str(source.resolve()))
        self.assertEqual(result["resolved_inputs"]["manifest_source_path"], "worktrees/agent_t5/.agent_runs/run_001")
        self.assertTrue((target / "metrics.json").exists())
        self.assertTrue((target / "figures" / "curve.txt").exists())
        self.assertEqual(artifact["title"], "Artifact for exp_t5 run_001")
        self.assertEqual(artifact["status"], "done")
        self.assertEqual(artifact["path"], "artifacts/exp_t5/run_001")
        self.assertEqual(artifact["links"]["metrics"], "artifacts/exp_t5/run_001/metrics.json")
        self.assertEqual(artifact["agent"], "agent_t5")
        self.assertEqual(experiment["linked_artifacts"], ["artifact_exp_t5_run_001"])
        self.assertEqual(manifest["node_id"], "exp_t5")
        self.assertEqual(manifest["run_id"], "run_001")
        self.assertEqual(manifest["artifact_id"], "artifact_exp_t5_run_001")
        self.assertEqual(manifest["agent_id"], "agent_t5")
        self.assertFalse(Path(manifest["source_path"]).is_absolute())
        self.assertEqual(manifest["source_path"], "worktrees/agent_t5/.agent_runs/run_001")
        self.assertEqual(manifest["source_file_count"], 2)
        self.assertIn("source_git", manifest)
        self.assertTrue(any(
            row.get("target") == "artifacts/exp_t5/run_001"
            and row.get("exists") is True
            for row in result["resource_rows"]
        ))
        self.assertTrue(any(
            row.get("target") == "artifacts/exp_t5/run_001/metrics.json"
            and row.get("exists") is True
            for row in result["resource_rows"]
        ))
        self.assertEqual(interaction_events(self.root)[-1]["kind"], "ingest_artifact")

    def test_assignment_scope_guard_applies_to_run_gate_and_artifact_links(self) -> None:
        session = self.start_t5_assignment()
        self.write_other_scope_branch(
            option_id="option_other_link_scope",
            experiment_id="exp_other_link_scope",
            option_title="Other link scope option",
            experiment_title="Other link scope experiment",
        )
        source = self.tmp_root / "worktrees" / "agent_t5" / ".agent_runs" / "run_scope"
        source.mkdir(parents=True)
        (source / "metrics.json").write_text('{"score": 0.5}', encoding="utf-8")

        in_scope_run = create_run(
            self.root,
            run_id="run_scope_in",
            experiment_id="exp_t5",
            assignment_id=session["assignment_id"],
            rebuild_dashboard=False,
        )
        completed = complete_run(
            self.root,
            run_id="run_scope_in",
            assignment_id=session["assignment_id"],
            rebuild_dashboard=False,
        )
        with self.assertRaises(AssignmentScopeError) as run_ctx:
            create_run(
                self.root,
                run_id="run_scope_out",
                experiment_id="exp_other_link_scope",
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        create_run(
            self.root,
            run_id="run_scope_rebind",
            experiment_id="exp_other_link_scope",
            rebuild_dashboard=False,
        )
        before_rebind = (self.root / "runs" / "run_scope_rebind.yaml").read_text(encoding="utf-8")
        with self.assertRaises(AssignmentScopeError) as rebind_ctx:
            update_run(
                self.root,
                run_id="run_scope_rebind",
                experiment_id="exp_t5",
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        after_rebind = (self.root / "runs" / "run_scope_rebind.yaml").read_text(encoding="utf-8")
        with self.assertRaises(AssignmentScopeError) as gate_ctx:
            record_gate_result(
                self.root,
                gate_id="gate_scope_out",
                experiment_id="exp_other_link_scope",
                gate_type="smoke_check",
                passed=False,
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        with self.assertRaises(AssignmentScopeError) as artifact_ctx:
            ingest_artifact(
                self.root,
                node_id="exp_other_link_scope",
                source_dir=source,
                run_id="run_scope",
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )

        self.assertEqual(in_scope_run["experiment_id"], "exp_t5")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(run_ctx.exception.payload["error"], "node_out_of_assignment_scope")
        self.assertEqual(rebind_ctx.exception.payload["error"], "node_out_of_assignment_scope")
        self.assertEqual(rebind_ctx.exception.payload["node_id"], "exp_other_link_scope")
        self.assertEqual(before_rebind, after_rebind)
        self.assertEqual(gate_ctx.exception.payload["error"], "node_out_of_assignment_scope")
        self.assertEqual(artifact_ctx.exception.payload["error"], "node_out_of_assignment_scope")
        self.assertFalse((self.root / "runs" / "run_scope_out.yaml").exists())
        self.assertFalse((self.root / "gate_results" / "gate_scope_out.yaml").exists())
        self.assertFalse((self.root / "graph" / "nodes" / "artifact_exp_other_link_scope_run_scope.yaml").exists())
        self.assertFalse((self.root / "artifacts" / "exp_other_link_scope" / "run_scope").exists())

    def test_ingest_artifact_cli_dry_run_compact_does_not_copy(self) -> None:
        source = self.tmp_root / "worktrees" / "agent_t5" / ".agent_runs" / "run_002"
        source.mkdir(parents=True)
        (source / "metrics.json").write_text("{}", encoding="utf-8")

        dry_run = subprocess.run(
            [
                *cli_command("ingest-artifact"),
                "--root",
                str(self.root),
                "--node",
                "exp_t5",
                "--from",
                str(source),
                "--run-id",
                "run_002",
                "--link",
                "metrics=metrics.json",
                "--dry-run",
                "--show-diff",
                "--json",
                "--compact",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(dry_run.stdout)

        self.assertEqual(dry_run.returncode, 0, dry_run.stdout + dry_run.stderr)
        self.assertTrue(payload["dry_run"])
        self.assertTrue(payload["would_change"])
        self.assertEqual(payload["created"], ["artifact_exp_t5_run_002"])
        self.assertEqual(payload["updated"], ["exp_t5"])
        self.assertEqual(payload["resolved_inputs"]["source_path_resolved"], str(source.resolve()))
        self.assertEqual(payload["resolved_inputs"]["manifest_source_path"], "worktrees/agent_t5/.agent_runs/run_002")
        self.assertEqual(payload["resolved_inputs"]["stable_path"], "artifacts/exp_t5/run_002")
        self.assertIn("diff", payload)
        self.assertFalse((self.root / "artifacts" / "exp_t5" / "run_002").exists())
        self.assertFalse((self.root / "graph" / "nodes" / "artifact_exp_t5_run_002.yaml").exists())

    def test_ingest_artifact_rejects_unsafe_or_unstable_inputs(self) -> None:
        source = self.tmp_root / "worktrees" / "agent_t5" / ".agent_runs" / "run_safe"
        source.mkdir(parents=True)
        (source / "metrics.json").write_text("{}", encoding="utf-8")
        write_node(
            self.root,
            {
                "id": "artifact_existing",
                "type": "artifact",
                "title": "Existing",
                "status": "done",
            },
        )
        (self.root / "artifacts" / "exp_t5" / "run_exists").mkdir(parents=True)
        file_source = self.tmp_root / "file_source.txt"
        file_source.write_text("not a directory", encoding="utf-8")

        cases = [
            {"source_dir": self.tmp_root / "missing_source", "run_id": "run_missing_source"},
            {"source_dir": file_source, "run_id": "run_file_source"},
            {"source_dir": source, "run_id": "run_exists"},
            {"source_dir": source, "run_id": "run_duplicate", "artifact_id": "artifact_existing"},
            {"source_dir": source, "run_id": "run_bad_artifact_id", "artifact_id": "../artifact_bad"},
            {"source_dir": source, "run_id": "run_bad_artifact_space", "artifact_id": "artifact bad"},
            {"source_dir": source, "run_id": "run_bad_artifact_dot", "artifact_id": "artifact."},
            {"source_dir": source, "run_id": "run_bad_artifact_reserved", "artifact_id": "CON"},
            {"source_dir": source, "run_id": "run_artifact_node", "node_id": "artifact_existing"},
            {"source_dir": self.root, "run_id": "run_root_source"},
            {"source_dir": source, "run_id": "run_absolute_link", "links": {"bad": str((source / "metrics.json").resolve())}},
            {"source_dir": source, "run_id": "run_escape_link", "links": {"bad": "../metrics.json"}},
            {"source_dir": source, "run_id": "run_missing_link", "links": {"bad": "missing.json"}},
            {"source_dir": source, "run_id": "../escape_run"},
        ]
        for case in cases:
            with self.subTest(run_id=case["run_id"]):
                with self.assertRaises((ValueError, FileNotFoundError, FileExistsError)):
                    ingest_artifact(
                        self.root,
                        node_id=case.get("node_id", "exp_t5"),
                        source_dir=case["source_dir"],
                        run_id=case["run_id"],
                        artifact_id=case.get("artifact_id"),
                        links=case.get("links"),
                        rebuild_dashboard=False,
                    )
        self.assertFalse((self.root / "graph" / "nodes" / "artifact_exp_t5_run_missing_link.yaml").exists())

    def test_ingest_artifact_does_not_delete_existing_target_on_copy_race(self) -> None:
        source = self.tmp_root / "worktrees" / "agent_t5" / ".agent_runs" / "run_race"
        source.mkdir(parents=True)
        (source / "metrics.json").write_text("{}", encoding="utf-8")
        target = self.root / "artifacts" / "exp_t5" / "run_race"
        marker = target / "winner.txt"
        original_rename = Path.rename

        def race_during_rename(self_path: Path, target_path: Path) -> Path:
            if Path(target_path) == target:
                target.mkdir(parents=True)
                marker.write_text("already ingested", encoding="utf-8")
                raise FileExistsError(str(target_path))
            return original_rename(self_path, target_path)

        with patch.object(Path, "rename", race_during_rename):
            with self.assertRaises(FileExistsError):
                ingest_artifact(
                    self.root,
                    node_id="exp_t5",
                    source_dir=source,
                    run_id="run_race",
                    rebuild_dashboard=False,
                )

        self.assertTrue(marker.exists())
        self.assertFalse((self.root / "graph" / "nodes" / "artifact_exp_t5_run_race.yaml").exists())

    def test_ingest_artifact_removes_copied_target_on_stale_yaml_conflict(self) -> None:
        source = self.tmp_root / "worktrees" / "agent_t5" / ".agent_runs" / "run_conflict"
        source.mkdir(parents=True)
        (source / "metrics.json").write_text("{}", encoding="utf-8")
        target = self.root / "artifacts" / "exp_t5" / "run_conflict"
        experiment_path = self.root / "graph" / "nodes" / "exp_t5.yaml"

        def copy_then_change_yaml(source_dir: Path, target_dir: Path, manifest: dict[str, object]) -> None:
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_dir, target_dir)
            (target_dir / "_research_cockpit_ingest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            experiment = load_yaml(experiment_path)
            experiment["summary"] = "Concurrent update."
            save_yaml(experiment_path, experiment)

        with patch("research_cockpit.commands.ingest_artifact._copy_to_stable_store", copy_then_change_yaml):
            with self.assertRaises(MutationError) as ctx:
                ingest_artifact(
                    self.root,
                    node_id="exp_t5",
                    source_dir=source,
                    run_id="run_conflict",
                    rebuild_dashboard=False,
                )

        self.assertIn("Mutation conflict", str(ctx.exception))
        self.assertFalse(target.exists())
        self.assertFalse((self.root / "graph" / "nodes" / "artifact_exp_t5_run_conflict.yaml").exists())
        self.assertEqual(load_yaml(experiment_path)["summary"], "Concurrent update.")

    def test_ingest_artifact_manifest_uses_hint_for_external_source(self) -> None:
        external_parent = ROOT_DIR / ".test_tmp" / f"external_source_{uuid.uuid4().hex}"
        self.addCleanup(shutil.rmtree, external_parent, ignore_errors=True)
        source = external_parent / ".agent_runs" / "run_external"
        source.mkdir(parents=True)
        (source / "metrics.json").write_text("{}", encoding="utf-8")

        ingest_artifact(
            self.root,
            node_id="exp_t5",
            source_dir=source,
            run_id="run_external",
            rebuild_dashboard=False,
        )
        manifest = json.loads(
            (self.root / "artifacts" / "exp_t5" / "run_external" / "_research_cockpit_ingest.json")
            .read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["source_path"], "run_external")
        self.assertEqual(manifest["source_path_base"], "external_hint")
        self.assertNotIn("..", manifest["source_path"])

    def test_ingest_artifact_rejects_symlinks_in_source_tree(self) -> None:
        source = self.tmp_root / "worktrees" / "agent_t5" / ".agent_runs" / "run_symlink"
        source.mkdir(parents=True)
        link_like_file = source / "metrics.json"
        link_like_file.write_text("{}", encoding="utf-8")
        original_is_symlink = Path.is_symlink

        def fake_is_symlink(path: Path) -> bool:
            return Path(path) == link_like_file or original_is_symlink(path)

        with patch.object(Path, "is_symlink", fake_is_symlink):
            with self.assertRaises(ValueError):
                ingest_artifact(
                    self.root,
                    node_id="exp_t5",
                    source_dir=source,
                    run_id="run_symlink",
                    rebuild_dashboard=False,
                )
        self.assertFalse((self.root / "artifacts" / "exp_t5" / "run_symlink").exists())
        self.assertFalse((self.root / "graph" / "nodes" / "artifact_exp_t5_run_symlink.yaml").exists())

    def test_ingest_artifact_rejects_symlink_created_during_copy(self) -> None:
        source = self.tmp_root / "worktrees" / "agent_t5" / ".agent_runs" / "run_late_symlink"
        source.mkdir(parents=True)
        (source / "metrics.json").write_text("{}", encoding="utf-8")
        target = self.root / "artifacts" / "exp_t5" / "run_late_symlink"
        original_is_symlink = Path.is_symlink

        def fake_copytree(source_dir: Path, target_dir: Path, *args: object, **kwargs: object) -> Path:
            target_dir = Path(target_dir)
            target_dir.mkdir(parents=True)
            (target_dir / "late_symlink.txt").write_text("outside", encoding="utf-8")
            return target_dir

        def fake_is_symlink(path: Path) -> bool:
            return Path(path).name == "late_symlink.txt" or original_is_symlink(path)

        with patch("research_cockpit.commands.ingest_artifact.shutil.copytree", fake_copytree):
            with patch.object(Path, "is_symlink", fake_is_symlink):
                with self.assertRaises(ValueError) as ctx:
                    ingest_artifact(
                        self.root,
                        node_id="exp_t5",
                        source_dir=source,
                        run_id="run_late_symlink",
                        rebuild_dashboard=False,
                    )

        self.assertIn("symlinks", str(ctx.exception))
        self.assertFalse(target.exists())
        self.assertFalse((self.root / "graph" / "nodes" / "artifact_exp_t5_run_late_symlink.yaml").exists())

    def test_import_worktree_findings_imports_artifacts_and_experiment_evidence(self) -> None:
        start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=self.tmp_root / "worktrees" / "agent_t5",
            rebuild_dashboard=False,
        )
        source_root = self.tmp_root / "worktree_research_cockpit"
        shutil.copytree(self.root, source_root)
        source_exp = load_yaml(source_root / "graph" / "nodes" / "exp_t5.yaml")
        source_exp["result_summary"] = "Imported result."
        source_exp["next_actions"] = ["Review imported result."]
        source_exp["linked_artifacts"] = ["artifact_imported_results"]
        source_exp["findings"] = [
            {
                "id": "exp_t5_finding_imported",
                "statement": "Imported finding.",
                "confidence": "medium",
                "outcome": "positive",
                "linked_artifacts": ["artifact_imported_results"],
            }
        ]
        save_yaml(source_root / "graph" / "nodes" / "exp_t5.yaml", source_exp)
        write_node(
            source_root,
            {
                "id": "artifact_imported_results",
                "type": "artifact",
                "title": "Imported results",
                "status": "done",
                "path": "outputs/imported",
            },
        )

        dry = import_worktree_findings(
            self.root,
            from_root=source_root,
            agent_id="agent_t5",
            option_id="option_t5",
            dry_run=True,
            show_diff=True,
        )
        result = import_worktree_findings(
            self.root,
            from_root=source_root,
            agent_id="agent_t5",
            option_id="option_t5",
            rebuild_dashboard=False,
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        artifact = load_yaml(self.root / "graph" / "nodes" / "artifact_imported_results.yaml")

        self.assertTrue(dry["preflight_ok"])
        self.assertIn("diff", dry)
        self.assertTrue(result["changed"])
        self.assertIn("artifact_imported_results", result["imported_artifacts"])
        self.assertEqual(experiment["result_summary"], "Imported result.")
        self.assertEqual(experiment["findings"][0]["statement"], "Imported finding.")
        self.assertEqual(artifact["path"], "outputs/imported")
        self.assertEqual(interaction_events(self.root)[-1]["kind"], "import_worktree_findings")

    def test_import_worktree_findings_allows_stale_source_without_session_metadata(self) -> None:
        source_root = self.tmp_root / "stale_worktree_research_cockpit"
        shutil.copytree(self.root, source_root)
        start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=self.tmp_root / "worktrees" / "agent_t5",
            rebuild_dashboard=False,
        )
        source_exp = load_yaml(source_root / "graph" / "nodes" / "exp_t5.yaml")
        source_exp["result_summary"] = "Imported from stale source."
        save_yaml(source_root / "graph" / "nodes" / "exp_t5.yaml", source_exp)

        result = import_worktree_findings(
            self.root,
            from_root=source_root,
            agent_id="agent_t5",
            option_id="option_t5",
            rebuild_dashboard=False,
        )

        self.assertTrue(result["changed"])
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        self.assertEqual(experiment["result_summary"], "Imported from stale source.")

    def test_create_artifact_cli_dry_run_diff_and_bad_link_target(self) -> None:
        (self.tmp_root / "outputs" / "preview").mkdir(parents=True)
        (self.tmp_root / "outputs" / "preview" / "metrics.json").write_text("{}", encoding="utf-8")
        dry_run = subprocess.run(
            [
                *cli_command("create-artifact"),
                "--root",
                str(self.root),
                "--id",
                "artifact_preview",
                "--title",
                "Preview",
                "--path",
                "outputs/preview",
                "--link",
                "metrics=outputs/preview/metrics.json",
                "--link-to",
                "exp_t5",
                "--dry-run",
                "--show-diff",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(dry_run.stdout)

        self.assertEqual(dry_run.returncode, 0, dry_run.stdout + dry_run.stderr)
        self.assertTrue(payload["dry_run"])
        self.assertTrue(payload["would_change"])
        self.assertIn("diff", payload)
        path_row = [row for row in payload["resource_rows"] if row["kind"] == "path"][0]
        metric_row = [row for row in payload["resource_rows"] if row["label"] == "metrics"][0]
        self.assertTrue(path_row["exists"])
        self.assertEqual(path_row["resolution_base"], "root_parent")
        self.assertTrue(path_row["resolved_target"].endswith("outputs/preview"))
        self.assertTrue(metric_row["exists"])
        self.assertFalse((self.root / "graph" / "nodes" / "artifact_preview.yaml").exists())

        failed = subprocess.run(
            [
                *cli_command("create-artifact"),
                "--root",
                str(self.root),
                "--id",
                "artifact_bad",
                "--title",
                "Bad",
                "--link-to",
                "missing_node",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(failed.returncode, 1)
        self.assertIn("missing_node", failed.stdout)

    def test_create_artifact_cli_accepts_file_input_to_shorten_long_commands(self) -> None:
        artifact_file = self.tmp_root / "artifact.yaml"
        save_yaml(
            artifact_file,
            {
                "id": "artifact_file_bundle",
                "title": "File bundle",
                "status": "done",
                "summary": "Created from an input file.",
                "path": "outputs/file_bundle",
                "links": {
                    "cached": "outputs/file_bundle/cached.json",
                    "fresh": "outputs/file_bundle/fresh.json",
                },
                "link_to": ["option_t5", "exp_t5"],
            },
        )

        dry_run = subprocess.run(
            [
                *cli_command("create-artifact"),
                "--root",
                str(self.root),
                "--file",
                str(artifact_file),
                "--dry-run",
                "--show-diff",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(dry_run.stdout)

        self.assertEqual(dry_run.returncode, 0, dry_run.stdout + dry_run.stderr)
        self.assertTrue(payload["dry_run"])
        self.assertTrue(payload["would_change"])
        self.assertEqual(payload["after"]["links"]["cached"], "outputs/file_bundle/cached.json")
        self.assertIn("diff", payload)
        self.assertFalse((self.root / "graph" / "nodes" / "artifact_file_bundle.yaml").exists())

        out = subprocess.run(
            [
                *cli_command("create-artifact"),
                "--root",
                str(self.root),
                "--file",
                str(artifact_file),
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        artifact = load_yaml(self.root / "graph" / "nodes" / "artifact_file_bundle.yaml")
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(artifact["path"], "outputs/file_bundle")
        self.assertEqual(option["linked_artifacts"], ["artifact_file_bundle"])
        self.assertEqual(experiment["linked_artifacts"], ["artifact_file_bundle"])
        self.assertFalse((self.root / "dashboards").exists())

    def test_record_finding_rejects_non_experiment_and_unknown_artifact(self) -> None:
        with self.assertRaises(ValueError) as non_experiment:
            record_finding(
                self.root,
                experiment_id="option_t5",
                statement="Not an experiment.",
                confidence="weak",
            )
        self.assertIn("experiment", str(non_experiment.exception))

        with self.assertRaises(ValueError) as missing_artifact:
            record_finding(
                self.root,
                experiment_id="exp_t5",
                statement="Missing artifact.",
                confidence="weak",
                artifacts=["missing_artifact"],
            )
        self.assertIn("missing_artifact", str(missing_artifact.exception))

    def test_record_finding_cli_accepts_artifact_id_and_rejects_legacy_artifact(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_cache",
                "type": "artifact",
                "title": "Feature cache",
                "status": "active",
            },
        )

        artifact_id = subprocess.run(
            [
                *cli_command("record-finding"),
                "--root",
                str(self.root),
                "--experiment",
                "exp_t5",
                "--statement",
                "Artifact id alias works.",
                "--confidence",
                "medium",
                "--artifact-id",
                "artifact_cache",
                "--no-build",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        legacy = subprocess.run(
            [
                *cli_command("record-finding"),
                "--root",
                str(self.root),
                "--experiment",
                "exp_t5",
                "--statement",
                "Legacy artifact flag works.",
                "--confidence",
                "medium",
                "--artifact",
                "artifact_cache",
                "--no-build",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        failed = subprocess.run(
            [
                *cli_command("record-finding"),
                "--root",
                str(self.root),
                "--experiment",
                "exp_t5",
                "--statement",
                "Bad artifact path.",
                "--confidence",
                "medium",
                "--artifact-id",
                "notes/results.md",
                "--no-build",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        data = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")

        self.assertEqual(artifact_id.returncode, 0, artifact_id.stdout + artifact_id.stderr)
        self.assertNotEqual(legacy.returncode, 0)
        self.assertIn("unrecognized arguments", legacy.stderr)
        self.assertEqual(data["findings"][0]["linked_artifacts"], ["artifact_cache"])
        self.assertEqual(len(data["findings"]), 1)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("artifact node id", failed.stdout.lower())

    def test_record_finding_cli_creates_inline_evidence_artifact(self) -> None:
        out = subprocess.run(
            [
                *cli_command("record-finding"),
                "--root",
                str(self.root),
                "--experiment",
                "exp_t5",
                "--statement",
                "Inline evidence can be recorded in one command.",
                "--confidence",
                "medium",
                "--evidence-path",
                "outputs/record_finding",
                "--evidence-link",
                "metrics=outputs/record_finding/metrics.json",
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        artifact = load_yaml(self.root / "graph" / "nodes" / "artifact_exp_t5_finding_001.yaml")

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(payload["created_artifacts"], ["artifact_exp_t5_finding_001"])
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(experiment["linked_artifacts"], ["artifact_exp_t5_finding_001"])
        self.assertEqual(experiment["findings"][0]["linked_artifacts"], ["artifact_exp_t5_finding_001"])
        self.assertEqual(artifact["title"], "Evidence for exp_t5_finding_001")
        self.assertEqual(artifact["status"], "done")
        self.assertEqual(artifact["path"], "outputs/record_finding")
        self.assertEqual(artifact["links"]["metrics"], "outputs/record_finding/metrics.json")

    def test_update_finding_rewrites_statement_and_links_artifact(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_cache",
                "type": "artifact",
                "title": "Feature cache",
                "status": "active",
            },
        )
        record_finding(
            self.root,
            experiment_id="exp_t5",
            statement="English finding.",
            confidence="medium",
            outcome="mixed",
            metrics=["old_metric"],
            rebuild_dashboard=False,
        )

        result = update_finding(
            self.root,
            experiment_id="exp_t5",
            finding_id="exp_t5_finding_001",
            statement="中文 finding。",
            confidence="strong",
            outcome="positive",
            metrics=["new_metric"],
            artifact_ids=["artifact_cache"],
            replace_metrics=True,
            rebuild_dashboard=False,
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        finding = experiment["findings"][0]

        self.assertTrue(result["changed"])
        self.assertEqual(finding["statement"], "中文 finding。")
        self.assertEqual(finding["confidence"], "strong")
        self.assertEqual(finding["outcome"], "positive")
        self.assertEqual(finding["metrics"], ["new_metric"])
        self.assertEqual(finding["linked_artifacts"], ["artifact_cache"])
        self.assertEqual(experiment["linked_artifacts"], ["artifact_cache"])
        self.assertEqual(result["added_experiment_artifacts"], ["artifact_cache"])
        self.assertIn("created_at", finding)
        self.assertIn("updated_at", finding)
        self.assertEqual(interaction_events(self.root)[-1]["kind"], "update_finding")

    def test_update_finding_cli_dry_run_diff_and_bad_artifact(self) -> None:
        record_finding(
            self.root,
            experiment_id="exp_t5",
            statement="Original finding.",
            confidence="medium",
            rebuild_dashboard=False,
        )
        before = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")
        dry_run = subprocess.run(
            [
                *cli_command("update-finding"),
                "--root",
                str(self.root),
                "--experiment",
                "exp_t5",
                "--finding-id",
                "exp_t5_finding_001",
                "--statement",
                "Preview finding.",
                "--dry-run",
                "--show-diff",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")
        payload = json.loads(dry_run.stdout)

        self.assertEqual(dry_run.returncode, 0, dry_run.stdout + dry_run.stderr)
        self.assertTrue(payload["dry_run"])
        self.assertIn("diff", payload)
        self.assertEqual(before, after)

        failed = subprocess.run(
            [
                *cli_command("update-finding"),
                "--root",
                str(self.root),
                "--experiment",
                "exp_t5",
                "--finding-id",
                "exp_t5_finding_001",
                "--artifact-id",
                "missing_artifact",
                "--replace-artifacts",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(failed.returncode, 1)
        self.assertIn("missing_artifact", failed.stdout)

    def test_complete_experiment_records_finding_marks_done_and_clears_actions(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_cache",
                "type": "artifact",
                "title": "Feature cache",
                "status": "active",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["next_actions"] = ["Compare cache footprint."]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)

        result = complete_experiment(
            self.root,
            experiment_id="exp_t5",
            finding="T5 improves replace following.",
            confidence="strong",
            outcome="positive",
            metrics=["replace_following"],
            artifact_ids=["artifact_cache"],
            result_summary="Improved edit following.",
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")

        self.assertTrue(result["changed"])
        self.assertEqual(experiment["status"], "done")
        self.assertEqual(experiment["result_summary"], "Improved edit following.")
        self.assertEqual(experiment["findings"][0]["statement"], "T5 improves replace following.")
        self.assertEqual(experiment["findings"][0]["linked_artifacts"], ["artifact_cache"])
        self.assertEqual(experiment["findings"][0]["evidence"], ["exp_t5", "artifact_cache"])
        self.assertEqual(experiment["linked_artifacts"], ["artifact_cache"])
        self.assertNotIn("next_actions", experiment)
        self.assertEqual(result["removed_next_actions"], ["Compare cache footprint."])
        self.assertEqual(option["status"], "active")
        self.assertEqual(problem["status"], "active")
        self.assertNotIn("current_best_option", problem)
        self.assertTrue((self.root / "dashboards" / "focus_context_pack.json").exists())
        self.assertEqual(interaction_events(self.root)[-1]["kind"], "complete_experiment")

    def test_complete_experiment_warns_when_assignment_cursor_becomes_terminal(self) -> None:
        session = self.start_t5_assignment()
        set_cursor(
            self.root,
            assignment_id=session["assignment_id"],
            node_id="exp_t5",
            rebuild_dashboard=False,
        )

        result = complete_experiment(
            self.root,
            experiment_id="exp_t5",
            finding="Completed assignment cursor target.",
            confidence="medium",
            assignment_id=session["assignment_id"],
            rebuild_dashboard=False,
        )

        self.assertIn(f"assignment_cursor_is_terminal:{session['assignment_id']}", result["warnings"])
        self.assertIn("set-cursor", " ".join(result["recommended_commands"]))
        self.assertIn(session["assignment_id"], " ".join(result["recommended_commands"]))

    def test_assignment_scope_guard_allows_in_scope_and_rejects_out_of_scope_completion(self) -> None:
        session = self.start_t5_assignment()
        self.write_other_scope_branch(
            option_id="option_other_scope",
            experiment_id="exp_other_scope",
        )
        before_other = (self.root / "graph" / "nodes" / "exp_other_scope.yaml").read_text(encoding="utf-8")

        in_scope = complete_experiment(
            self.root,
            experiment_id="exp_t5",
            finding="In scope finding.",
            confidence="medium",
            assignment_id=session["assignment_id"],
            rebuild_dashboard=False,
        )
        with self.assertRaises(AssignmentScopeError) as ctx:
            complete_experiment(
                self.root,
                experiment_id="exp_other_scope",
                finding="Out of scope finding.",
                confidence="medium",
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        after_other = (self.root / "graph" / "nodes" / "exp_other_scope.yaml").read_text(encoding="utf-8")
        coordinator = complete_experiment(
            self.root,
            experiment_id="exp_other_scope",
            finding="Coordinator override finding.",
            confidence="medium",
            assignment_id=session["assignment_id"],
            coordinator=True,
            rebuild_dashboard=False,
        )

        self.assertTrue(in_scope["changed"])
        self.assertEqual(ctx.exception.payload["error"], "node_out_of_assignment_scope")
        self.assertEqual(ctx.exception.payload["assignment_id"], session["assignment_id"])
        self.assertEqual(ctx.exception.payload["node_id"], "exp_other_scope")
        self.assertEqual(ctx.exception.payload["allowed_root"], "option_t5")
        self.assertEqual(before_other, after_other)
        self.assertTrue(coordinator["changed"])

    def test_assignment_scope_guard_rejects_out_of_scope_artifact_links(self) -> None:
        session = self.start_t5_assignment()
        write_node(
            self.root,
            {
                "id": "option_other_artifact_scope",
                "type": "option",
                "title": "Other artifact scope option",
                "status": "active",
                "parent": "problem_text",
            },
        )
        write_node(
            self.root,
            {
                "id": "artifact_other_scope",
                "type": "artifact",
                "title": "Other branch artifact",
                "status": "done",
                "parent": "option_other_artifact_scope",
            },
        )
        before = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")

        with self.assertRaises(AssignmentScopeError) as finding_ctx:
            record_finding(
                self.root,
                experiment_id="exp_t5",
                statement="Out of scope artifact should fail.",
                confidence="medium",
                artifacts=["artifact_other_scope"],
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        with self.assertRaises(AssignmentScopeError) as complete_ctx:
            complete_experiment(
                self.root,
                experiment_id="exp_t5",
                finding="Out of scope artifact should fail.",
                confidence="medium",
                artifact_ids=["artifact_other_scope"],
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        after = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")

        self.assertEqual(finding_ctx.exception.payload["error"], "node_out_of_assignment_scope")
        self.assertEqual(finding_ctx.exception.payload["node_id"], "artifact_other_scope")
        self.assertEqual(complete_ctx.exception.payload["error"], "node_out_of_assignment_scope")
        self.assertEqual(complete_ctx.exception.payload["node_id"], "artifact_other_scope")
        self.assertEqual(before, after)

    def test_assignment_scope_guard_applies_to_artifact_and_finding_mutations(self) -> None:
        session = self.start_t5_assignment()
        self.write_other_scope_branch(
            option_id="option_other_artifact_cmd_scope",
            experiment_id="exp_other_artifact_cmd_scope",
            option_title="Other artifact command scope option",
            experiment_title="Other artifact command scope experiment",
        )
        write_node(
            self.root,
            {
                "id": "artifact_other_cmd_scope",
                "type": "artifact",
                "title": "Other command scope artifact",
                "status": "done",
                "parent": "option_other_artifact_cmd_scope",
            },
        )
        record_finding(
            self.root,
            experiment_id="exp_t5",
            statement="Seed finding.",
            confidence="medium",
            assignment_id=session["assignment_id"],
            rebuild_dashboard=False,
        )
        created = create_artifact(
            self.root,
            artifact_id="artifact_in_scope_cmd",
            title="In-scope command artifact",
            status="done",
            link_to=["exp_t5"],
            assignment_id=session["assignment_id"],
            rebuild_dashboard=False,
        )
        before_exp = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")
        with self.assertRaises(AssignmentScopeError) as create_ctx:
            create_artifact(
                self.root,
                artifact_id="artifact_out_scope_cmd",
                title="Out-scope command artifact",
                status="done",
                link_to=["exp_other_artifact_cmd_scope"],
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        with self.assertRaises(AssignmentScopeError) as link_artifact_ctx:
            link_artifact(
                self.root,
                artifact_id="artifact_other_cmd_scope",
                to_nodes=["exp_t5"],
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        with self.assertRaises(AssignmentScopeError) as link_node_ctx:
            link_artifact(
                self.root,
                artifact_id="artifact_in_scope_cmd",
                to_nodes=["exp_other_artifact_cmd_scope"],
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        with self.assertRaises(AssignmentScopeError) as finding_ctx:
            update_finding(
                self.root,
                experiment_id="exp_t5",
                finding_id="finding_exp_t5_001",
                artifact_ids=["artifact_other_cmd_scope"],
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        after_exp = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")

        self.assertTrue(created["changed"])
        self.assertEqual(create_ctx.exception.payload["node_id"], "exp_other_artifact_cmd_scope")
        self.assertEqual(link_artifact_ctx.exception.payload["node_id"], "artifact_other_cmd_scope")
        self.assertEqual(link_node_ctx.exception.payload["node_id"], "exp_other_artifact_cmd_scope")
        self.assertEqual(finding_ctx.exception.payload["node_id"], "artifact_other_cmd_scope")
        self.assertFalse((self.root / "graph" / "nodes" / "artifact_out_scope_cmd.yaml").exists())
        self.assertEqual(before_exp, after_exp)

    def test_assignment_scope_guard_applies_to_graph_plan_and_node_field_refs(self) -> None:
        session = self.start_t5_assignment()
        self.write_other_scope_branch(
            option_id="option_other_ref_scope",
            experiment_id="exp_other_ref_scope",
            option_title="Other ref scope option",
            experiment_title="Other ref scope experiment",
        )
        write_node(
            self.root,
            {
                "id": "artifact_other_ref_scope",
                "type": "artifact",
                "title": "Other ref scope artifact",
                "status": "done",
                "parent": "option_other_ref_scope",
            },
        )
        before = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")

        created = add_node_result(
            self.root,
            node_id="exp_t5_child_scope",
            node_type="experiment",
            title="In-scope child experiment",
            parent="option_t5",
            assignment_id=session["assignment_id"],
            rebuild_dashboard=False,
        )
        with self.assertRaises(AssignmentScopeError) as add_ctx:
            add_node_result(
                self.root,
                node_id="exp_other_child_scope",
                node_type="experiment",
                title="Out-scope child experiment",
                parent="option_other_ref_scope",
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        with self.assertRaises(AssignmentScopeError) as fields_ctx:
            update_node_fields(
                self.root,
                node_id="exp_t5",
                list_appends={"linked_artifacts": ["artifact_other_ref_scope"]},
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        with self.assertRaises(AssignmentScopeError) as plan_ctx:
            apply_graph_plan(
                self.root,
                plan={"updates": [{"id": "exp_t5", "fields": {"linked_artifacts": ["artifact_other_ref_scope"]}}]},
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        with self.assertRaises(AssignmentScopeError) as artifact_add_ctx:
            add_node_result(
                self.root,
                node_id="artifact_orphan_scope",
                node_type="artifact",
                title="Orphan scope artifact",
                parent="option_t5",
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        with self.assertRaises(AssignmentScopeError) as artifact_plan_ctx:
            apply_graph_plan(
                self.root,
                plan={
                    "nodes": [
                        {
                            "id": "artifact_plan_orphan_scope",
                            "type": "artifact",
                            "title": "Orphan plan artifact",
                            "parent": "option_t5",
                        }
                    ]
                },
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        after = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")
        linked_plan = apply_graph_plan(
            self.root,
            plan={
                "nodes": [
                    {
                        "id": "artifact_plan_linked_scope",
                        "type": "artifact",
                        "title": "Linked plan artifact",
                    }
                ],
                "updates": [
                    {
                        "id": "exp_t5",
                        "fields": {"linked_artifacts": ["artifact_plan_linked_scope"]},
                    }
                ],
            },
            assignment_id=session["assignment_id"],
            rebuild_dashboard=False,
        )

        self.assertTrue(created["changed"])
        self.assertEqual(add_ctx.exception.payload["node_id"], "exp_other_child_scope")
        self.assertEqual(fields_ctx.exception.payload["node_id"], "artifact_other_ref_scope")
        self.assertEqual(plan_ctx.exception.payload["node_id"], "artifact_other_ref_scope")
        self.assertEqual(artifact_add_ctx.exception.payload["error"], "artifact_not_linked_in_assignment_scope")
        self.assertEqual(artifact_plan_ctx.exception.payload["error"], "artifact_not_linked_in_assignment_scope")
        self.assertFalse((self.root / "graph" / "nodes" / "exp_other_child_scope.yaml").exists())
        self.assertFalse((self.root / "graph" / "nodes" / "artifact_orphan_scope.yaml").exists())
        self.assertFalse((self.root / "graph" / "nodes" / "artifact_plan_orphan_scope.yaml").exists())
        self.assertEqual(before, after)
        self.assertTrue(linked_plan["changed"])
        self.assertIn("artifact_plan_linked_scope", load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")["linked_artifacts"])

    def test_assignment_scope_guard_applies_to_batch_completion_and_gate_ingest(self) -> None:
        session = self.start_t5_assignment()
        self.write_other_scope_branch(
            option_id="option_other_batch_scope",
            experiment_id="exp_other_batch_scope",
            option_title="Other batch scope option",
            experiment_title="Other batch scope experiment",
        )
        gate_path = self.root / "artifacts" / "exp_other_batch_scope" / "run_gate" / "gate_result.json"
        gate_path.parent.mkdir(parents=True)
        gate_path.write_text(json.dumps({"gate_type": "smoke_check", "passed": True}), encoding="utf-8")
        before_other = (self.root / "graph" / "nodes" / "exp_other_batch_scope.yaml").read_text(encoding="utf-8")

        in_scope = complete_experiments(
            self.root,
            plan={
                "experiments": [
                    {
                        "id": "exp_t5",
                        "finding": "Batch in scope finding.",
                        "confidence": "medium",
                    }
                ]
            },
            assignment_id=session["assignment_id"],
            rebuild_dashboard=False,
        )
        with self.assertRaises(AssignmentScopeError) as complete_ctx:
            complete_experiments(
                self.root,
                plan={
                    "experiments": [
                        {
                            "id": "exp_other_batch_scope",
                            "finding": "Batch out of scope finding.",
                            "confidence": "medium",
                        }
                    ]
                },
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        with self.assertRaises(AssignmentScopeError) as gate_ctx:
            ingest_gate_result(
                self.root,
                gate_id="gate_other_batch_scope",
                gate_result_file="artifacts/exp_other_batch_scope/run_gate/gate_result.json",
                experiment_id="exp_other_batch_scope",
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        after_other = (self.root / "graph" / "nodes" / "exp_other_batch_scope.yaml").read_text(encoding="utf-8")

        self.assertEqual(in_scope["experiment_ids"], ["exp_t5"])
        self.assertEqual(complete_ctx.exception.payload["node_id"], "exp_other_batch_scope")
        self.assertEqual(gate_ctx.exception.payload["node_id"], "exp_other_batch_scope")
        self.assertEqual(before_other, after_other)
        self.assertFalse((self.root / "gate_results" / "gate_other_batch_scope.yaml").exists())

    def test_assignment_scope_guard_cli_returns_structured_error_before_write(self) -> None:
        session = self.start_t5_assignment()
        self.write_other_scope_branch(
            option_id="option_other_cli_scope",
            experiment_id="exp_other_cli_scope",
            option_title="Other CLI scope option",
            experiment_title="Other CLI scope experiment",
        )
        before = (self.root / "graph" / "nodes" / "exp_other_cli_scope.yaml").read_text(encoding="utf-8")

        out = subprocess.run(
            [
                *cli_command("complete-experiment"),
                "--root",
                str(self.root),
                "--assignment",
                session["assignment_id"],
                "--id",
                "exp_other_cli_scope",
                "--finding",
                "Out of scope CLI finding.",
                "--confidence",
                "medium",
                "--json",
                "--no-build",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)
        after = (self.root / "graph" / "nodes" / "exp_other_cli_scope.yaml").read_text(encoding="utf-8")

        self.assertEqual(out.returncode, 1)
        self.assertEqual(payload["error"], "node_out_of_assignment_scope")
        self.assertEqual(payload["assignment_id"], session["assignment_id"])
        self.assertEqual(payload["node_id"], "exp_other_cli_scope")
        self.assertEqual(before, after)

    def test_complete_experiment_rejects_next_actions_on_done_node(self) -> None:
        with self.assertRaisesRegex(ValueError, "create-followup-experiment"):
            complete_experiment(
                self.root,
                experiment_id="exp_t5",
                finding="T5 improves replace following.",
                confidence="strong",
                next_actions=["Compare cache footprint."],
                rebuild_dashboard=False,
            )

    def test_complete_experiment_warns_when_completed_node_is_still_focus(self) -> None:
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "exp_t5"
        current["current_focus_path"] = ["stage_text", "problem_text", "option_t5", "exp_t5"]
        current["agent_focuses"] = {
            "agent_t5": {
                "current_focus_node": "exp_t5",
                "current_focus_path": ["stage_text", "problem_text", "option_t5", "exp_t5"],
                "current_option": "option_t5",
                "next_actions": ["Run exp_t5."],
            }
        }
        save_yaml(self.root / "current_state.yaml", current)

        result = complete_experiment(
            self.root,
            experiment_id="exp_t5",
            finding="Focus completion warning.",
            confidence="medium",
            rebuild_dashboard=False,
        )

        self.assertIn("current_focus_node_is_terminal", result["warnings"])
        self.assertIn("agent_focus_is_terminal:agent_t5", result["warnings"])
        self.assertIn("set-focus", " ".join(result["recommended_commands"]))
        self.assertIn("set-agent-focus", " ".join(result["recommended_commands"]))

    def test_complete_experiment_compact_keeps_recommended_commands(self) -> None:
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "exp_t5"
        current["current_focus_path"] = ["stage_text", "problem_text", "option_t5", "exp_t5"]
        current["agent_focuses"] = {
            "agent_t5": {
                "current_focus_node": "exp_t5",
                "current_focus_path": ["stage_text", "problem_text", "option_t5", "exp_t5"],
                "current_option": "option_t5",
            }
        }
        save_yaml(self.root / "current_state.yaml", current)

        out = subprocess.run(
            [
                *cli_command("complete-experiment"),
                "--root",
                str(self.root),
                "--id",
                "exp_t5",
                "--finding",
                "Compact warning.",
                "--confidence",
                "medium",
                "--json",
                "--compact",
                "--no-build",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("current_focus_node_is_terminal", payload["warnings"])
        self.assertIn("set-focus", " ".join(payload["recommended_commands"]))
        self.assertIn("set-agent-focus", " ".join(payload["recommended_commands"]))

    def test_complete_experiment_creates_inline_evidence_artifact(self) -> None:
        result = complete_experiment(
            self.root,
            experiment_id="exp_t5",
            finding="Inline evidence supports the conclusion.",
            confidence="strong",
            outcome="positive",
            evidence_path="outputs/run_inline",
            evidence_links={"metrics": "outputs/run_inline/metrics.json", "plot": "outputs/run_inline/loss.png"},
            rebuild_dashboard=False,
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        artifact = load_yaml(self.root / "graph" / "nodes" / f"{result['created_artifacts'][0]}.yaml")

        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["created_artifacts"], ["artifact_exp_t5_finding_001"])
        self.assertEqual(experiment["linked_artifacts"], ["artifact_exp_t5_finding_001"])
        self.assertEqual(experiment["findings"][0]["linked_artifacts"], ["artifact_exp_t5_finding_001"])
        self.assertEqual(artifact["title"], "Evidence for exp_t5_finding_001")
        self.assertEqual(artifact["status"], "done")
        self.assertEqual(artifact["path"], "outputs/run_inline")
        self.assertEqual(artifact["links"]["metrics"], "outputs/run_inline/metrics.json")

    def test_complete_experiment_warns_without_evidence_artifact(self) -> None:
        result = complete_experiment(
            self.root,
            experiment_id="exp_t5",
            finding="Conclusion without linked evidence.",
            confidence="medium",
            rebuild_dashboard=False,
        )

        self.assertEqual(result["warnings"], ["missing_evidence_artifact"])

    def test_complete_experiment_links_existing_and_inline_evidence(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_existing",
                "type": "artifact",
                "title": "Existing bundle",
                "status": "done",
            },
        )

        result = complete_experiment(
            self.root,
            experiment_id="exp_t5",
            finding="Both evidence records are relevant.",
            confidence="medium",
            artifact_ids=["artifact_existing"],
            evidence_path="outputs/new_bundle",
            rebuild_dashboard=False,
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")

        self.assertEqual(result["linked_artifacts"], ["artifact_existing", "artifact_exp_t5_finding_001"])
        self.assertEqual(experiment["linked_artifacts"], ["artifact_existing", "artifact_exp_t5_finding_001"])
        self.assertEqual(experiment["findings"][0]["linked_artifacts"], ["artifact_existing", "artifact_exp_t5_finding_001"])

    def test_complete_experiment_dry_run_no_build_and_rejects_invalid_inputs(self) -> None:
        before = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")
        dry_run = complete_experiment(
            self.root,
            experiment_id="exp_t5",
            finding="Dry run finding.",
            confidence="medium",
            dry_run=True,
        )
        after_dry_run = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")

        self.assertTrue(dry_run["dry_run"])
        self.assertTrue(dry_run["would_change"])
        self.assertFalse(dry_run["changed"])
        self.assertEqual(before, after_dry_run)
        self.assertFalse((self.root / "graph" / "interaction_log.yaml").exists())

        no_build = complete_experiment(
            self.root,
            experiment_id="exp_t5",
            finding="No build finding.",
            confidence="medium",
            rebuild_dashboard=False,
        )
        self.assertTrue(no_build["changed"])
        self.assertFalse((self.root / "dashboards" / "focus_context_pack.json").exists())

        with self.assertRaises(ValueError) as non_experiment:
            complete_experiment(
                self.root,
                experiment_id="option_t5",
                finding="Bad type.",
                confidence="medium",
                rebuild_dashboard=False,
            )
        self.assertIn("experiment", str(non_experiment.exception))

        with self.assertRaises(ValueError) as missing_artifact:
            complete_experiment(
                self.root,
                experiment_id="exp_t5",
                finding="Missing artifact.",
                confidence="medium",
                artifact_ids=["missing_artifact"],
                rebuild_dashboard=False,
            )
        self.assertIn("artifact node id", str(missing_artifact.exception).lower())

        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "failed"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        with self.assertRaises(ValueError) as failed:
            complete_experiment(
                self.root,
                experiment_id="exp_t5",
                finding="Cannot complete failed.",
                confidence="medium",
                rebuild_dashboard=False,
            )
        self.assertIn("failed", str(failed.exception))

    def test_complete_experiment_cli_json_no_build(self) -> None:
        out = subprocess.run(
            [
                *cli_command("complete-experiment"),
                "--root",
                str(self.root),
                "--id",
                "exp_t5",
                "--finding",
                "CLI completion works.",
                "--confidence",
                "medium",
                "--outcome",
                "mixed",
                "--result-summary",
                "CLI summary.",
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(payload["changed"])
        self.assertEqual(payload["experiment_id"], "exp_t5")
        self.assertEqual(experiment["status"], "done")
        self.assertNotIn("next_actions", experiment)
        self.assertFalse((self.root / "dashboards").exists())

    def test_complete_experiment_cli_rejects_next_action(self) -> None:
        out = subprocess.run(
            [
                *cli_command("complete-experiment"),
                "--root",
                str(self.root),
                "--id",
                "exp_t5",
                "--finding",
                "CLI completion works.",
                "--confidence",
                "medium",
                "--next-action",
                "Review next branch.",
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 1)
        self.assertIn("create-followup-experiment", out.stdout)

    def test_complete_experiment_cli_compact_json(self) -> None:
        out = subprocess.run(
            [
                *cli_command("complete-experiment"),
                "--root",
                str(self.root),
                "--id",
                "exp_t5",
                "--finding",
                "Compact completion works.",
                "--confidence",
                "medium",
                "--no-build",
                "--json",
                "--compact",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(payload["command"], "research-cockpit complete-experiment")
        self.assertEqual(payload["target"], "exp_t5")
        self.assertTrue(payload["changed"])
        self.assertEqual(payload["updated"], ["exp_t5"])
        self.assertIn("research-cockpit validate", payload["verify_commands"][0])

    def test_complete_experiment_rejects_malformed_interaction_log_without_partial_write(self) -> None:
        experiment_path = self.root / "graph" / "nodes" / "exp_t5.yaml"
        before = experiment_path.read_text(encoding="utf-8")
        write_malformed_interaction_log(self.root)

        with self.assertRaises(MutationError) as ctx:
            complete_experiment(
                self.root,
                experiment_id="exp_t5",
                finding="Should not write.",
                confidence="medium",
                rebuild_dashboard=False,
            )

        self.assertIn("interaction_log.yaml", str(ctx.exception))
        self.assertEqual(before, experiment_path.read_text(encoding="utf-8"))
        cli_error = subprocess.run(
            [
                *cli_command("complete-experiment"),
                "--root",
                str(self.root),
                "--id",
                "exp_t5",
                "--finding",
                "Still should not write.",
                "--confidence",
                "medium",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(cli_error.stdout)
        self.assertEqual(cli_error.returncode, 1)
        self.assertFalse(payload["partial_success"])
        self.assertFalse(payload["rolled_back"])
        self.assertEqual(before, experiment_path.read_text(encoding="utf-8"))

    def test_update_status_dry_run_rejects_malformed_interaction_log_without_write(self) -> None:
        node_path = self.root / "graph" / "nodes" / "option_t5.yaml"
        before = node_path.read_text(encoding="utf-8")
        write_malformed_interaction_log(self.root)

        out = subprocess.run(
            [
                *cli_command("update-status"),
                "--root",
                str(self.root),
                "--id",
                "option_t5",
                "--status",
                "promising",
                "--dry-run",
                "--show-diff",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 1)
        assert_mutation_json_failed_without_writes(self, payload)
        self.assertEqual(before, node_path.read_text(encoding="utf-8"))

    def test_create_workstream_dry_run_rejects_malformed_interaction_log_without_write(self) -> None:
        plan_path = self.tmp_root / "workstream_bad_log.yaml"
        save_yaml(
            plan_path,
            {
                "problem": {"id": "problem_bad_log_preview", "title": "Bad log preview"},
                "active_option": {"id": "option_bad_log_preview", "title": "Bad log option"},
            },
        )
        write_malformed_interaction_log(self.root)

        out = subprocess.run(
            [
                *cli_command("create-workstream"),
                "--root",
                str(self.root),
                "--file",
                str(plan_path),
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 1)
        assert_mutation_json_failed_without_writes(self, payload)
        self.assertFalse((self.root / "graph" / "nodes" / "problem_bad_log_preview.yaml").exists())

    def test_complete_experiment_reports_partial_success_when_build_fails(self) -> None:
        with patch("research_cockpit.commands.build_dashboard.build_dashboard", side_effect=RuntimeError("build exploded")):
            with self.assertRaises(MutationError) as ctx:
                complete_experiment(
                    self.root,
                    experiment_id="exp_t5",
                    finding="Build can fail after truth write.",
                    confidence="medium",
                )

        payload = ctx.exception.payload
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        self.assertTrue(payload["partial_success"])
        self.assertFalse(payload["rolled_back"])
        self.assertIn("research-cockpit build", payload["recovery_commands"][0])
        self.assertEqual(experiment["status"], "done")

    def test_parallel_complete_experiment_commands_serialize_interaction_log_writes(self) -> None:
        write_node(
            self.root,
            {
                "id": "exp_t5_b",
                "type": "experiment",
                "title": "Second ablation",
                "status": "planned",
                "parent": "option_t5",
            },
        )
        commands = [
            [
                *cli_command("complete-experiment"),
                "--root",
                str(self.root),
                "--id",
                "exp_t5",
                "--finding",
                "First parallel finding.",
                "--confidence",
                "medium",
                "--no-build",
                "--json",
            ],
            [
                *cli_command("complete-experiment"),
                "--root",
                str(self.root),
                "--id",
                "exp_t5_b",
                "--finding",
                "Second parallel finding.",
                "--confidence",
                "medium",
                "--no-build",
                "--json",
            ],
        ]

        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for command in commands]
        results = [process.communicate(timeout=20) + (process.returncode,) for process in processes]

        for stdout, stderr, returncode in results:
            self.assertEqual(returncode, 0, stdout + stderr)
        events = interaction_events(self.root)
        self.assertEqual(len(events), 2)
        self.assertTrue(all(isinstance(event, dict) for event in events))
        self.assertEqual({event["node_id"] for event in events}, {"exp_t5", "exp_t5_b"})

    def test_complete_experiments_batches_defaults_and_validates_without_partial_writes(self) -> None:
        write_node(
            self.root,
            {
                "id": "exp_t5_b",
                "type": "experiment",
                "title": "Second ablation",
                "status": "queued",
                "parent": "option_t5",
            },
        )
        write_node(
            self.root,
            {
                "id": "artifact_bundle",
                "type": "artifact",
                "title": "Bundle",
                "status": "done",
            },
        )
        exp_a_before = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        exp_a_before["next_actions"] = ["Review aggregate."]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", exp_a_before)

        result = complete_experiments(
            self.root,
            plan={
                "defaults": {
                    "confidence": "medium",
                    "outcome": "mixed",
                    "artifact_ids": ["artifact_bundle"],
                },
                "experiments": [
                    {
                        "id": "exp_t5",
                        "finding": "First finding.",
                        "metrics": ["m1"],
                        "result_summary": "First summary.",
                    },
                    {
                        "id": "exp_t5_b",
                        "finding": "Second finding.",
                        "confidence": "strong",
                        "outcome": "positive",
                        "metrics": ["m2"],
                    },
                ],
            },
            rebuild_dashboard=False,
        )
        exp_a = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        exp_b = load_yaml(self.root / "graph" / "nodes" / "exp_t5_b.yaml")

        self.assertTrue(result["changed"])
        self.assertEqual(result["experiment_ids"], ["exp_t5", "exp_t5_b"])
        self.assertEqual(exp_a["status"], "done")
        self.assertEqual(exp_b["status"], "done")
        self.assertEqual(exp_a["findings"][0]["linked_artifacts"], ["artifact_bundle"])
        self.assertEqual(exp_a["linked_artifacts"], ["artifact_bundle"])
        self.assertEqual(exp_b["findings"][0]["confidence"], "strong")
        self.assertEqual(exp_b["findings"][0]["outcome"], "positive")
        self.assertNotIn("next_actions", exp_a)
        self.assertEqual(result["completed_experiments"][0]["removed_next_actions"], ["Review aggregate."])
        self.assertFalse((self.root / "dashboards").exists())

        with self.assertRaisesRegex(ValueError, "create-followup-experiment"):
            complete_experiments(
                self.root,
                plan={
                    "defaults": {
                        "confidence": "medium",
                        "next_actions": ["Review aggregate."],
                    },
                    "experiments": [
                        {"id": "exp_t5", "finding": "Should not write."},
                    ],
                },
                rebuild_dashboard=False,
            )

        with self.assertRaisesRegex(ValueError, "create-followup-experiment"):
            complete_experiments(
                self.root,
                plan={
                    "defaults": {"confidence": "medium"},
                    "experiments": [
                        {
                            "id": "exp_t5",
                            "finding": "Should not write.",
                            "next_actions": ["Review aggregate."],
                        },
                    ],
                },
                rebuild_dashboard=False,
            )

        before = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")
        with self.assertRaises(ValueError) as missing:
            complete_experiments(
                self.root,
                plan={
                    "defaults": {"confidence": "medium"},
                    "experiments": [
                        {"id": "exp_t5", "finding": "Should not write."},
                        {"id": "missing_exp", "finding": "Bad."},
                    ],
                },
                rebuild_dashboard=False,
            )
        after = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")
        self.assertIn("missing_exp", str(missing.exception))
        self.assertEqual(before, after)

    def test_complete_experiments_creates_inline_evidence_artifacts(self) -> None:
        write_node(
            self.root,
            {
                "id": "exp_t5_b",
                "type": "experiment",
                "title": "Second ablation",
                "status": "queued",
                "parent": "option_t5",
            },
        )

        result = complete_experiments(
            self.root,
            plan={
                "defaults": {"confidence": "medium"},
                "experiments": [
                    {
                        "id": "exp_t5",
                        "finding": "First finding with evidence.",
                        "evidence": {
                            "path": "outputs/exp_t5",
                            "links": {"metrics": "outputs/exp_t5/metrics.json"},
                        },
                    },
                    {
                        "id": "exp_t5_b",
                        "finding": "Second finding with evidence.",
                        "evidence": {
                            "path": "outputs/exp_t5_b",
                        },
                    },
                ],
            },
            rebuild_dashboard=False,
        )
        exp_a = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        exp_b = load_yaml(self.root / "graph" / "nodes" / "exp_t5_b.yaml")
        artifact_a = load_yaml(self.root / "graph" / "nodes" / "artifact_exp_t5_finding_001.yaml")
        artifact_b = load_yaml(self.root / "graph" / "nodes" / "artifact_exp_t5_b_finding_001.yaml")

        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["created_artifacts"], ["artifact_exp_t5_finding_001", "artifact_exp_t5_b_finding_001"])
        self.assertEqual(exp_a["linked_artifacts"], ["artifact_exp_t5_finding_001"])
        self.assertEqual(exp_b["linked_artifacts"], ["artifact_exp_t5_b_finding_001"])
        self.assertEqual(exp_a["findings"][0]["linked_artifacts"], ["artifact_exp_t5_finding_001"])
        self.assertEqual(artifact_a["links"]["metrics"], "outputs/exp_t5/metrics.json")
        self.assertEqual(artifact_b["title"], "Evidence for exp_t5_b_finding_001")

    def test_complete_experiments_rejects_duplicate_evidence_artifact_without_partial_write(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_exp_t5_finding_001",
                "type": "artifact",
                "title": "Existing evidence bundle",
                "status": "done",
            },
        )
        write_node(
            self.root,
            {
                "id": "exp_t5_b",
                "type": "experiment",
                "title": "Second ablation",
                "status": "queued",
                "parent": "option_t5",
            },
        )
        before = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")

        with self.assertRaises(ValueError) as duplicate:
            complete_experiments(
                self.root,
                plan={
                    "defaults": {"confidence": "medium"},
                    "experiments": [
                        {
                            "id": "exp_t5",
                            "finding": "First finding.",
                            "evidence": {"path": "outputs/a"},
                        },
                        {
                            "id": "exp_t5_b",
                            "finding": "Second finding.",
                            "evidence": {"path": "outputs/b"},
                        },
                    ],
                },
                rebuild_dashboard=False,
            )

        self.assertIn("already exists", str(duplicate.exception))
        self.assertEqual(before, (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8"))
        self.assertFalse((self.root / "graph" / "nodes" / "artifact_exp_t5_b_finding_001.yaml").exists())

    def test_complete_experiments_rejects_invalid_evidence_links_schema(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            complete_experiments(
                self.root,
                plan={
                    "experiments": [
                        {
                            "id": "exp_t5",
                            "finding": "Finding with invalid evidence links.",
                            "confidence": "medium",
                            "evidence": {"links": ["metrics=outputs/run/metrics.json"]},
                        }
                    ]
                },
                rebuild_dashboard=False,
            )

        self.assertIn("evidence.links must be a mapping", str(ctx.exception))
        self.assertNotIn("findings", load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml"))

    def test_complete_experiments_rejects_removed_evidence_metadata_fields(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            complete_experiments(
                self.root,
                plan={
                    "experiments": [
                        {
                            "id": "exp_t5",
                            "finding": "Finding with custom artifact metadata.",
                            "confidence": "medium",
                            "evidence": {"artifact_id": "artifact_manual", "path": "outputs/run"},
                        }
                    ]
                },
                rebuild_dashboard=False,
            )

        self.assertIn("unsupported evidence field", str(ctx.exception))
        self.assertIn("artifact_id", str(ctx.exception))
        self.assertNotIn("findings", load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml"))

    def test_complete_experiments_cli_dry_run_diff_no_build(self) -> None:
        plan_path = self.tmp_root / "findings.yaml"
        save_yaml(
            plan_path,
            {
                "defaults": {"confidence": "medium"},
                "experiments": [
                    {
                        "id": "exp_t5",
                        "finding": "CLI batch finding.",
                        "result_summary": "Batch summary.",
                    }
                ],
            },
        )
        before = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")
        dry_run = subprocess.run(
            [
                *cli_command("complete-experiments"),
                "--root",
                str(self.root),
                "--file",
                str(plan_path),
                "--dry-run",
                "--show-diff",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after_dry = (self.root / "graph" / "nodes" / "exp_t5.yaml").read_text(encoding="utf-8")
        payload = json.loads(dry_run.stdout)

        self.assertEqual(dry_run.returncode, 0, dry_run.stdout + dry_run.stderr)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["experiment_ids"], ["exp_t5"])
        self.assertIn("diff", payload)
        self.assertEqual(before, after_dry)

        out = subprocess.run(
            [
                *cli_command("complete-experiments"),
                "--root",
                str(self.root),
                "--file",
                str(plan_path),
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        written = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(written["status"], "done")
        self.assertFalse((self.root / "dashboards").exists())

    def test_set_baseline_writes_dry_run_clear_and_manifest(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_baseline",
                "type": "artifact",
                "title": "Baseline bundle",
                "status": "done",
            },
        )
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "accepted",
                "parent": "option_t5",
            },
        )

        dry_run = set_baseline(
            self.root,
            node_id="problem_text",
            option_id="option_t5",
            decision_id="decision_t5",
            artifacts=["artifact_baseline"],
            reason="Default baseline for follow-up agents.",
            dry_run=True,
            show_diff=True,
        )
        after_dry_run = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")

        self.assertTrue(dry_run["would_change"])
        self.assertFalse(dry_run["changed"])
        self.assertIn("diff", dry_run)
        self.assertNotIn("baseline", after_dry_run)

        result = set_baseline(
            self.root,
            node_id="problem_text",
            option_id="option_t5",
            decision_id="decision_t5",
            artifacts=["artifact_baseline"],
            reason="Default baseline for follow-up agents.",
            rebuild_dashboard=False,
        )
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        manifest = agent_command_manifest()

        self.assertTrue(result["changed"])
        self.assertEqual(problem["baseline"]["option"], "option_t5")
        self.assertEqual(problem["baseline"]["decision"], "decision_t5")
        self.assertEqual(problem["baseline"]["artifacts"], ["artifact_baseline"])
        self.assertEqual(interaction_events(self.root)[-1]["kind"], "set_baseline")
        command = [item for item in manifest if item["name"] == "set-baseline"][0]
        self.assertEqual(
            command["fields_supported"],
            ["baseline.option", "baseline.decision", "baseline.artifacts", "baseline.reason"],
        )

        clear = set_baseline(self.root, node_id="problem_text", clear=True, rebuild_dashboard=False)
        cleared = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        self.assertTrue(clear["changed"])
        self.assertNotIn("baseline", cleared)

        cli_out = subprocess.run(
            [
                *cli_command("set-baseline"),
                "--root",
                str(self.root),
                "--node",
                "problem_text",
                "--option",
                "option_t5",
                "--decision",
                "decision_t5",
                "--artifact",
                "artifact_baseline",
                "--reason",
                "CLI baseline.",
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        cli_problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        cli_payload = json.loads(cli_out.stdout)

        self.assertEqual(cli_out.returncode, 0, cli_out.stderr or cli_out.stdout)
        self.assertTrue(cli_payload["changed"])
        self.assertEqual(cli_problem["baseline"]["reason"], "CLI baseline.")

    def test_set_baseline_rejects_bad_refs_and_cross_problem_option(self) -> None:
        write_node(
            self.root,
            {
                "id": "problem_other",
                "type": "problem",
                "title": "Other problem",
                "status": "active",
                "parent": "stage_text",
                "children": ["option_other"],
            },
        )
        write_node(
            self.root,
            {
                "id": "option_other",
                "type": "option",
                "title": "Other option",
                "status": "open",
                "parent": "problem_other",
            },
        )
        write_node(
            self.root,
            {
                "id": "decision_other",
                "type": "decision",
                "title": "Use other option",
                "status": "accepted",
                "parent": "option_other",
            },
        )
        write_node(
            self.root,
            {
                "id": "option_orphan",
                "type": "option",
                "title": "Orphan option",
                "status": "open",
            },
        )

        with self.assertRaises(ValueError) as missing_option:
            set_baseline(self.root, node_id="problem_text", option_id="missing_option", rebuild_dashboard=False)
        self.assertIn("missing_option", str(missing_option.exception))

        with self.assertRaises(ValueError) as wrong_type:
            set_baseline(self.root, node_id="problem_text", option_id="exp_t5", rebuild_dashboard=False)
        self.assertIn("must be option", str(wrong_type.exception))

        with self.assertRaises(ValueError) as wrong_branch:
            set_baseline(self.root, node_id="problem_text", option_id="option_other", rebuild_dashboard=False)
        self.assertIn("same problem", str(wrong_branch.exception))

        with self.assertRaises(ValueError) as orphan_problem_branch:
            set_baseline(self.root, node_id="problem_text", option_id="option_orphan", rebuild_dashboard=False)
        self.assertIn("same problem", str(orphan_problem_branch.exception))

        with self.assertRaises(ValueError) as orphan_stage_branch:
            set_baseline(self.root, node_id="stage_text", option_id="option_orphan", rebuild_dashboard=False)
        self.assertIn("stage", str(orphan_stage_branch.exception))

        with self.assertRaises(ValueError) as missing_artifact:
            set_baseline(
                self.root,
                node_id="problem_text",
                option_id="option_t5",
                artifacts=["missing_artifact"],
                rebuild_dashboard=False,
            )
        self.assertIn("missing_artifact", str(missing_artifact.exception))

        with self.assertRaises(ValueError) as wrong_decision_type:
            set_baseline(
                self.root,
                node_id="problem_text",
                option_id="option_t5",
                decision_id="option_t5",
                rebuild_dashboard=False,
            )
        self.assertIn("expected 'decision'", str(wrong_decision_type.exception))

        with self.assertRaises(ValueError) as wrong_artifact_type:
            set_baseline(
                self.root,
                node_id="problem_text",
                option_id="option_t5",
                artifacts=["option_t5"],
                rebuild_dashboard=False,
            )
        self.assertIn("expected 'artifact'", str(wrong_artifact_type.exception))

        before = (self.root / "graph" / "nodes" / "problem_text.yaml").read_text(encoding="utf-8")
        with self.assertRaises(ValueError) as wrong_decision_branch:
            set_baseline(
                self.root,
                node_id="problem_text",
                option_id="option_t5",
                decision_id="decision_other",
                rebuild_dashboard=False,
            )
        after = (self.root / "graph" / "nodes" / "problem_text.yaml").read_text(encoding="utf-8")
        self.assertIn("baseline.decision must belong to baseline.option", str(wrong_decision_branch.exception))
        self.assertEqual(before, after)

    def test_validate_rejects_non_list_baseline_artifacts(self) -> None:
        problem_path = self.root / "graph" / "nodes" / "problem_text.yaml"
        problem = load_yaml(problem_path)
        problem["baseline"] = {"option": "option_t5", "artifacts": ""}
        save_yaml(problem_path, problem)

        failed = subprocess.run(
            [*cli_command("validate"), "--root", str(self.root), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(failed.stdout)

        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertFalse(payload["valid"])
        self.assertTrue(any("baseline.artifacts must be a list" in error for error in payload["errors"]))

    def test_validate_strict_lifecycle_rejects_terminal_parent_with_active_descendants(self) -> None:
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["status"] = "resolved"
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)

        compatible = subprocess.run(
            [*cli_command("validate"), "--root", str(self.root), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        strict = subprocess.run(
            [*cli_command("validate"), "--root", str(self.root), "--strict-lifecycle", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        compatible_payload = json.loads(compatible.stdout)
        strict_payload = json.loads(strict.stdout)

        self.assertEqual(compatible.returncode, 0, compatible.stdout + compatible.stderr)
        self.assertTrue(compatible_payload["valid"])
        self.assertNotIn("lifecycle_errors", compatible_payload)
        self.assertEqual(strict.returncode, 1, strict.stdout + strict.stderr)
        self.assertFalse(strict_payload["valid"])
        self.assertEqual(strict_payload["strict_lifecycle"], True)
        self.assertEqual(strict_payload["lifecycle_errors"][0]["error"], "terminal_parent_has_active_descendants")
        self.assertEqual(strict_payload["lifecycle_errors"][0]["node_id"], "problem_text")
        self.assertEqual(strict_payload["lifecycle_errors"][0]["target_status"], "resolved")
        self.assertEqual(
            [item["id"] for item in strict_payload["lifecycle_errors"][0]["blocking_descendants"]],
            ["option_t5", "exp_t5"],
        )
        self.assertTrue(
            any("terminal_parent_has_active_descendants" in error for error in strict_payload["errors"])
        )

    def test_effective_baseline_prefers_explicit_then_inherited_then_problem_fallback(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_baseline",
                "type": "artifact",
                "title": "Baseline bundle",
                "status": "done",
            },
        )
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "accepted",
                "parent": "option_t5",
            },
        )
        current_fallback = resolve_effective_baseline(
            load_nodes(self.root),
            "exp_t5",
            load_yaml(self.root / "current_state.yaml"),
        )
        self.assertEqual(current_fallback["source_kind"], "current_state_fallback")
        self.assertEqual(current_fallback["option"]["id"], "option_t5")

        problem_path = self.root / "graph" / "nodes" / "problem_text.yaml"
        problem = load_yaml(problem_path)
        problem["current_best_option"] = "option_t5"
        problem["resolved_by"] = "decision_t5"
        save_yaml(problem_path, problem)

        fallback = resolve_effective_baseline(load_nodes(self.root), "exp_t5", load_yaml(self.root / "current_state.yaml"))

        self.assertEqual(fallback["source_kind"], "problem_fallback")
        self.assertEqual(fallback["option"]["id"], "option_t5")
        self.assertEqual(fallback["decision"]["id"], "decision_t5")

        set_baseline(
            self.root,
            node_id="problem_text",
            option_id="option_t5",
            decision_id="decision_t5",
            artifacts=["artifact_baseline"],
            reason="Inherited by experiments.",
            rebuild_dashboard=False,
        )
        inherited = resolve_effective_baseline(load_nodes(self.root), "exp_t5", load_yaml(self.root / "current_state.yaml"))

        self.assertEqual(inherited["source_kind"], "inherited")
        self.assertEqual(inherited["source_node_id"], "problem_text")
        self.assertEqual(inherited["artifacts"][0]["id"], "artifact_baseline")

        set_baseline(
            self.root,
            node_id="exp_t5",
            option_id="option_t5",
            reason="Experiment override.",
            rebuild_dashboard=False,
        )
        explicit = resolve_effective_baseline(load_nodes(self.root), "exp_t5", load_yaml(self.root / "current_state.yaml"))

        self.assertEqual(explicit["source_kind"], "explicit")
        self.assertEqual(explicit["source_node_id"], "exp_t5")
        self.assertEqual(explicit["reason"], "Experiment override.")

    def test_context_and_node_context_include_effective_baseline_without_accepted_history(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_baseline",
                "type": "artifact",
                "title": "Baseline bundle",
                "status": "done",
            },
        )
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "accepted",
                "parent": "option_t5",
            },
        )
        write_node(
            self.root,
            {
                "id": "decision_old",
                "type": "decision",
                "title": "Old accepted branch",
                "status": "accepted",
                "parent": "option_t5",
            },
        )
        set_baseline(
            self.root,
            node_id="problem_text",
            option_id="option_t5",
            decision_id="decision_t5",
            artifacts=["artifact_baseline"],
            reason="Use this baseline.",
            rebuild_dashboard=False,
        )

        payload = context_payload(self.root, node_id="exp_t5", with_artifacts=True, compact=True)
        node_payload = node_context_payload(self.root, node_id="exp_t5", compact=True)

        self.assertEqual(payload["effective_baseline"]["option"]["id"], "option_t5")
        self.assertEqual(payload["effective_baseline"]["decision"]["id"], "decision_t5")
        self.assertIn("artifact_baseline", payload["artifacts"]["artifact_ids"])
        self.assertEqual(node_payload["effective_baseline"]["option"]["id"], "option_t5")
        self.assertNotIn("accepted_decisions", payload)
        self.assertNotIn("decision_old", json.dumps(payload["effective_baseline"]))

    def test_global_context_keeps_current_state_baseline_fallback(self) -> None:
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        context = build_agent_context(self.root, nodes)
        bootstrap = agent_bootstrap_payload(self.root, build=False)

        self.assertEqual(context["effective_baseline"]["source_kind"], "current_state_fallback")
        self.assertEqual(context["effective_baseline"]["option"]["id"], current["current_option"])
        self.assertEqual(bootstrap["focus"]["effective_baseline"]["source_kind"], "current_state_fallback")
        self.assertEqual(bootstrap["focus"]["effective_baseline"]["option"]["id"], current["current_option"])

    def test_context_for_stage_does_not_use_current_problem_fallback(self) -> None:
        problem_path = self.root / "graph" / "nodes" / "problem_text.yaml"
        problem = load_yaml(problem_path)
        problem["current_best_option"] = "option_t5"
        save_yaml(problem_path, problem)

        payload = context_payload(self.root, node_id="stage_text", compact=True)
        node_payload = node_context_payload(self.root, node_id="stage_text", compact=True)

        self.assertEqual(payload["effective_baseline"]["source_kind"], "current_state_fallback")
        self.assertEqual(payload["effective_baseline"]["source_node_id"], "current_state")
        self.assertEqual(node_payload["effective_baseline"]["source_kind"], "current_state_fallback")
        self.assertEqual(node_payload["effective_baseline"]["source_node_id"], "current_state")

    def test_baseline_overview_and_accepted_rows_are_compact(self) -> None:
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "accepted",
                "parent": "option_t5",
                "supporting_experiments": ["exp_t5"],
                "evidence_strength": "medium",
            },
        )
        write_node(
            self.root,
            {
                "id": "artifact_missing_for_row_count",
                "type": "artifact",
                "title": "Row count artifact",
                "status": "done",
            },
        )
        option_path = self.root / "graph" / "nodes" / "option_t5.yaml"
        option = load_yaml(option_path)
        option["status"] = "accepted"
        option["linked_artifacts"] = ["artifact_missing_for_row_count"]
        save_yaml(option_path, option)
        set_baseline(
            self.root,
            node_id="problem_text",
            option_id="option_t5",
            decision_id="decision_t5",
            reason="Default accepted branch.",
            rebuild_dashboard=False,
        )
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        baseline_rows = build_baseline_overview_rows(nodes, current)
        option_rows = build_accepted_option_rows(nodes, current)
        decision_rows = build_accepted_decision_rows(nodes)

        self.assertEqual(baseline_rows[0]["problem_id"], "problem_text")
        self.assertEqual(baseline_rows[0]["baseline_option_id"], "option_t5")
        self.assertEqual(option_rows, [row for row in option_rows if row["status"] == "accepted"])
        self.assertEqual(option_rows[0]["finding_count"], 0)
        self.assertFalse(option_rows[0]["is_current_best"])
        self.assertTrue(option_rows[0]["is_current_option"])
        self.assertEqual(decision_rows, [row for row in decision_rows if row["status"] == "accepted"])
        self.assertEqual(decision_rows[0]["supporting_experiment_count"], 1)
        self.assertIn("--option option_t5", build_set_baseline_command("problem_text", "option_t5"))

        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["current_best_option"] = "option_t5"
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        option_rows = build_accepted_option_rows(load_nodes(self.root), current)
        self.assertTrue(option_rows[0]["is_current_best"])

    def test_build_set_baseline_command_shell_quotes_values(self) -> None:
        command = build_set_baseline_command(
            "problem weird",
            "option;rm",
            decision_id="decision$HOME",
            artifacts=["artifact one"],
            reason='Use $BASE; echo "bad"',
        )

        self.assertEqual(
            command,
            "research-cockpit set-baseline --node 'problem weird' "
            "--option 'option;rm' --decision 'decision$HOME' "
            "--artifact 'artifact one' --reason 'Use $BASE; echo \"bad\"'",
        )

    def test_baseline_overview_does_not_use_global_current_option_for_every_problem(self) -> None:
        stage = load_yaml(self.root / "graph" / "nodes" / "stage_text.yaml")
        stage["children"] = ["problem_text", "problem_other"]
        save_yaml(self.root / "graph" / "nodes" / "stage_text.yaml", stage)
        write_node(
            self.root,
            {
                "id": "problem_other",
                "type": "problem",
                "title": "Other problem",
                "status": "active",
                "parent": "stage_text",
                "children": ["option_other"],
            },
        )
        write_node(
            self.root,
            {
                "id": "option_other",
                "type": "option",
                "title": "Other option",
                "status": "open",
                "parent": "problem_other",
            },
        )
        rows = {
            row["problem_id"]: row
            for row in build_baseline_overview_rows(load_nodes(self.root), load_yaml(self.root / "current_state.yaml"))
        }

        self.assertEqual(rows["problem_text"]["source_kind"], "none")
        self.assertEqual(rows["problem_text"]["baseline_option_id"], "")
        self.assertEqual(rows["problem_other"]["source_kind"], "none")
        self.assertEqual(rows["problem_other"]["baseline_option_id"], "")

    def test_update_node_fields_updates_problem_fields_and_rebuilds_dashboard(self) -> None:
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative",
                "status": "open",
                "parent": "problem_text",
            },
        )

        result = update_node_fields(
            self.root,
            node_id="problem_text",
            current_best_option="option_alt",
            replace_next_actions=["Audit table.", "Write final summary."],
        )
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")

        self.assertTrue(result["changed"])
        self.assertEqual(problem["current_best_option"], "option_alt")
        self.assertEqual(problem["next_actions"], ["Audit table.", "Write final summary."])
        self.assertTrue((self.root / "dashboards" / "graph_view.json").exists())
        self.assertEqual(interaction_events(self.root)[-1]["kind"], "update_node_fields")

    def test_update_node_fields_supports_rich_scalar_list_and_ref_fields(self) -> None:
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative",
                "status": "open",
                "parent": "problem_text",
            },
        )
        write_node(
            self.root,
            {
                "id": "artifact_report",
                "type": "artifact",
                "title": "Report",
                "status": "active",
            },
        )
        write_node(
            self.root,
            {
                "id": "decision_ref",
                "type": "decision",
                "title": "Decision",
                "status": "proposed",
                "parent": "option_t5",
            },
        )

        result = update_node_fields(
            self.root,
            node_id="problem_text",
            scalar_updates={
                "question": "Which branch should we test next?",
                "hypothesis": "Shorter branch setup improves throughput.",
                "priority": "high",
            },
            list_appends={
                "tags": ["timeline-control", "timeline-control"],
                "success_criteria": ["Agent can update graph without patching YAML."],
                "metrics": ["command_count"],
                "pros": ["Fewer rebuilds."],
                "cons": ["Needs plan validation."],
                "next_actions": ["Review graph plan."],
                "supporting_experiments": ["exp_t5"],
                "supporting_decisions": ["decision_ref"],
                "linked_artifacts": ["artifact_report"],
                "alternatives_considered": ["option_alt"],
                "derived_from": ["option_t5"],
            },
            rebuild_dashboard=False,
            show_diff=True,
        )
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")

        self.assertTrue(result["changed"])
        self.assertIn("diff", result)
        self.assertEqual(problem["question"], "Which branch should we test next?")
        self.assertEqual(problem["hypothesis"], "Shorter branch setup improves throughput.")
        self.assertEqual(problem["priority"], "high")
        self.assertEqual(problem["tags"], ["timeline-control"])
        self.assertEqual(problem["supporting_experiments"], ["exp_t5"])
        self.assertEqual(problem["supporting_decisions"], ["decision_ref"])
        self.assertEqual(problem["linked_artifacts"], ["artifact_report"])
        self.assertEqual(problem["alternatives_considered"], ["option_alt"])

        with self.assertRaises(ValueError) as bad_ref:
            update_node_fields(
                self.root,
                node_id="problem_text",
                list_appends={"linked_artifacts": ["option_t5"]},
                rebuild_dashboard=False,
            )
        self.assertIn("expected 'artifact'", str(bad_ref.exception))

    def test_update_node_fields_cli_rejects_next_action_replace_conflict(self) -> None:
        out = subprocess.run(
            [
                *cli_command("update-node-fields"),
                "--root",
                str(self.root),
                "--id",
                "problem_text",
                "--next-action",
                "Append action.",
                "--replace-next-actions",
                "Replace action.",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 1)
        self.assertIn("--next-action", out.stdout + out.stderr)

    def test_update_node_fields_dry_run_cli_and_rejects_cross_problem_option(self) -> None:
        write_node(
            self.root,
            {
                "id": "problem_other",
                "type": "problem",
                "title": "Other problem",
                "status": "active",
                "parent": "stage_text",
                "children": ["option_other"],
            },
        )
        write_node(
            self.root,
            {
                "id": "option_other",
                "type": "option",
                "title": "Other option",
                "status": "open",
                "parent": "problem_other",
            },
        )

        dry_run = update_node_fields(
            self.root,
            node_id="problem_text",
            current_best_option="option_t5",
            dry_run=True,
        )
        problem_after_dry_run = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")

        self.assertTrue(dry_run["would_change"])
        self.assertFalse(dry_run["changed"])
        self.assertNotIn("current_best_option", problem_after_dry_run)

        with self.assertRaises(ValueError) as wrong_parent:
            update_node_fields(
                self.root,
                node_id="problem_text",
                current_best_option="option_other",
                rebuild_dashboard=False,
            )
        self.assertIn("child option", str(wrong_parent.exception))

        with self.assertRaises(ValueError) as wrong_type:
            update_node_fields(
                self.root,
                node_id="option_t5",
                current_best_option="option_t5",
                rebuild_dashboard=False,
            )
        self.assertIn("problem", str(wrong_type.exception))

        cli_out = subprocess.run(
            [
                *cli_command("update-node-fields"),
                "--root",
                str(self.root),
                "--id",
                "problem_text",
                "--replace-next-actions",
                "Review branch.",
                "--replace-next-actions",
                "Record decision.",
                "--no-build",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        cli_payload = json.loads(cli_out.stdout)
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")

        self.assertEqual(cli_out.returncode, 0, cli_out.stdout + cli_out.stderr)
        self.assertTrue(cli_payload["changed"])
        self.assertEqual(problem["next_actions"], ["Review branch.", "Record decision."])
        self.assertFalse((self.root / "dashboards").exists())

    def test_update_node_fields_clear_next_actions_and_compact_json(self) -> None:
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["next_actions"] = ["Old action."]
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)

        out = subprocess.run(
            [
                *cli_command("update-node-fields"),
                "--root",
                str(self.root),
                "--id",
                "problem_text",
                "--clear-next-actions",
                "--next-action",
                "Review branch.",
                "--next-action",
                "Record decision.",
                "--no-build",
                "--json",
                "--compact",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(problem["next_actions"], ["Review branch.", "Record decision."])
        self.assertEqual(payload["command"], "research-cockpit update-node-fields")
        self.assertEqual(payload["target"], "problem_text")
        self.assertEqual(payload["updated"], ["problem_text"])

    def test_apply_graph_plan_creates_batched_subtree_and_syncs_parent_children(self) -> None:
        plan = {
            "nodes": [
                {
                    "id": "problem_batch",
                    "type": "problem",
                    "title": "Batch problem",
                    "parent": "stage_text",
                    "status": "active",
                    "fields": {
                        "current_best_option": "option_batch_active",
                        "question": "Can one plan create the branch?",
                    },
                },
                {
                    "id": "option_batch_active",
                    "type": "option",
                    "title": "Active batch option",
                    "parent": "problem_batch",
                    "status": "active",
                    "fields": {
                        "supporting_experiments": ["exp_batch_1", "exp_batch_2", "exp_batch_3"],
                    },
                },
                {"id": "exp_batch_1", "type": "experiment", "title": "Batch exp 1", "parent": "option_batch_active"},
                {"id": "exp_batch_2", "type": "experiment", "title": "Batch exp 2", "parent": "option_batch_active"},
                {"id": "exp_batch_3", "type": "experiment", "title": "Batch exp 3", "parent": "option_batch_active"},
                {"id": "option_batch_follow_1", "type": "option", "title": "Follow option 1", "parent": "problem_batch"},
                {"id": "option_batch_follow_2", "type": "option", "title": "Follow option 2", "parent": "problem_batch"},
            ]
        }

        result = apply_graph_plan(self.root, plan=plan, rebuild_dashboard=False, show_diff=True)
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_batch.yaml")
        option = load_yaml(self.root / "graph" / "nodes" / "option_batch_active.yaml")

        self.assertTrue(result["changed"])
        self.assertEqual(len(result["created_nodes"]), 7)
        self.assertIn("diff", result)
        self.assertEqual(problem["current_best_option"], "option_batch_active")
        self.assertEqual(
            problem["children"],
            ["option_batch_active", "option_batch_follow_1", "option_batch_follow_2"],
        )
        self.assertEqual(option["children"], ["exp_batch_1", "exp_batch_2", "exp_batch_3"])
        self.assertEqual(option["supporting_experiments"], ["exp_batch_1", "exp_batch_2", "exp_batch_3"])
        self.assertFalse((self.root / "dashboards").exists())

    def test_apply_graph_plan_dry_run_and_invalid_plan_do_not_write(self) -> None:
        dry_run = apply_graph_plan(
            self.root,
            plan={
                "nodes": [
                    {
                        "id": "problem_preview",
                        "type": "problem",
                        "title": "Preview problem",
                        "parent": "stage_text",
                    }
                ]
            },
            dry_run=True,
            show_diff=True,
        )

        self.assertTrue(dry_run["would_change"])
        self.assertFalse(dry_run["changed"])
        self.assertTrue(dry_run["preflight_ok"])
        self.assertFalse((self.root / "graph" / "nodes" / "problem_preview.yaml").exists())

        with self.assertRaises(ValueError):
            apply_graph_plan(
                self.root,
                plan={
                    "nodes": [
                        {
                            "id": "problem_invalid_plan",
                            "type": "problem",
                            "title": "Invalid plan",
                            "parent": "stage_text",
                            "fields": {"supporting_experiments": ["missing_exp"]},
                        }
                    ]
                },
                rebuild_dashboard=False,
            )
        self.assertFalse((self.root / "graph" / "nodes" / "problem_invalid_plan.yaml").exists())

        with self.assertRaisesRegex(ValueError, "fields must include"):
            apply_graph_plan(
                self.root,
                plan={"updates": [{"id": "exp_t5", "fields": {}}]},
                dry_run=True,
            )

    def test_create_workstream_creates_branch_without_focus_or_old_option_changes(self) -> None:
        before_current = load_yaml(self.root / "current_state.yaml")
        before_old_option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        result = create_workstream(
            self.root,
            workstream={
                "problem": {
                    "id": "problem_workstream",
                    "title": "Workstream problem",
                    "parent": "stage_text",
                    "question": "Which branch is fastest?",
                },
                "active_option": {
                    "id": "option_workstream_active",
                    "title": "Active workstream option",
                    "hypothesis": "A file plan is faster.",
                },
                "experiments": [
                    {"id": "exp_workstream_1", "title": "Run first check"},
                    {"id": "exp_workstream_2", "title": "Run second check"},
                ],
                "followup_options": [
                    {"id": "option_workstream_follow", "title": "Follow-up option"},
                ],
            },
            rebuild_dashboard=False,
        )
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_workstream.yaml")
        option = load_yaml(self.root / "graph" / "nodes" / "option_workstream_active.yaml")

        self.assertTrue(result["changed"])
        self.assertEqual(problem["current_best_option"], "option_workstream_active")
        self.assertEqual(problem["question"], "Which branch is fastest?")
        self.assertEqual(option["status"], "active")
        self.assertEqual(option["supporting_experiments"], ["exp_workstream_1", "exp_workstream_2"])
        self.assertEqual(load_yaml(self.root / "current_state.yaml"), before_current)
        self.assertEqual(load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml"), before_old_option)

    def test_create_workstream_json_preserves_lifecycle_guard_error(self) -> None:
        plan_path = self.tmp_root / "terminal_workstream.yaml"
        save_yaml(
            plan_path,
            {
                "problem": {
                    "id": "problem_terminal_workstream",
                    "title": "Terminal workstream",
                    "parent": "stage_text",
                    "status": "resolved",
                },
                "active_option": {
                    "id": "option_terminal_workstream",
                    "title": "Active child option",
                },
            },
        )

        out = subprocess.run(
            [
                *cli_command("create-workstream"),
                "--root",
                str(self.root),
                "--file",
                str(plan_path),
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertEqual(payload["error"], "terminal_parent_has_active_descendants")
        self.assertEqual(payload["node_id"], "problem_terminal_workstream")
        self.assertEqual(payload["blocking_descendants"][0]["id"], "option_terminal_workstream")
        self.assertFalse((self.root / "graph" / "nodes" / "problem_terminal_workstream.yaml").exists())
        self.assertFalse((self.root / "graph" / "nodes" / "option_terminal_workstream.yaml").exists())

    def test_create_workstream_can_create_nested_branch_under_option(self) -> None:
        session = start_agent_session(
            self.root,
            option_id="option_t5",
            label="t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=self.tmp_root / "worktrees" / "agent_t5",
            rebuild_dashboard=False,
        )
        result = create_workstream(
            self.root,
            workstream={
                "problem": {
                    "id": "problem_nested_worktree",
                    "title": "Nested worktree follow-up",
                    "parent": "option_t5",
                    "summary": "Scope follow-up work from the completed run.",
                    "derived_from": ["exp_t5"],
                },
                "active_option": {
                    "id": "option_nested_route",
                    "title": "Nested follow-up route",
                },
                "experiments": [
                    {"id": "exp_nested_gate", "title": "Run nested gate"},
                ],
            },
            assignment_id=session["assignment_id"],
            rebuild_dashboard=False,
        )

        problem = load_yaml(self.root / "graph" / "nodes" / "problem_nested_worktree.yaml")
        option = load_yaml(self.root / "graph" / "nodes" / "option_nested_route.yaml")
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_nested_gate.yaml")
        parent = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")

        self.assertTrue(result["changed"])
        self.assertEqual(problem["parent"], "option_t5")
        self.assertEqual(problem["derived_from"], ["exp_t5"])
        self.assertEqual(problem["children"], ["option_nested_route"])
        self.assertEqual(option["parent"], "problem_nested_worktree")
        self.assertEqual(option["supporting_experiments"], ["exp_nested_gate"])
        self.assertEqual(experiment["parent"], "option_nested_route")
        self.assertIn("problem_nested_worktree", parent["children"])

    def test_create_workstream_rejects_out_of_scope_branch_without_writing(self) -> None:
        session = self.start_t5_assignment()

        with self.assertRaises(AssignmentScopeError) as ctx:
            create_workstream(
                self.root,
                workstream={
                    "problem": {
                        "id": "problem_out_of_scope_workstream",
                        "title": "Out of scope workstream",
                        "parent": "stage_text",
                    },
                    "active_option": {
                        "id": "option_out_of_scope_workstream",
                        "title": "Out of scope option",
                    },
                    "experiments": [
                        {"id": "exp_out_of_scope_workstream", "title": "Out of scope experiment"},
                    ],
                },
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )

        self.assertEqual(ctx.exception.payload["error"], "node_out_of_assignment_scope")
        self.assertEqual(ctx.exception.payload["node_id"], "problem_out_of_scope_workstream")
        self.assertFalse((self.root / "graph" / "nodes" / "problem_out_of_scope_workstream.yaml").exists())
        self.assertFalse((self.root / "graph" / "nodes" / "option_out_of_scope_workstream.yaml").exists())

    def test_create_workstream_normalizes_planned_followup_option_to_open(self) -> None:
        result = create_workstream(
            self.root,
            workstream={
                "problem": {
                    "id": "problem_planned_alias",
                    "title": "Alias problem",
                    "parent": "stage_text",
                },
                "active_option": {
                    "id": "option_planned_alias_active",
                    "title": "Active route",
                },
                "followup_options": [
                    {
                        "id": "option_planned_alias_follow",
                        "title": "Follow-up route",
                        "status": "planned",
                    }
                ],
            },
            rebuild_dashboard=False,
        )
        followup = load_yaml(self.root / "graph" / "nodes" / "option_planned_alias_follow.yaml")

        self.assertEqual(followup["status"], "open")
        self.assertEqual(
            result["status_aliases"],
            [
                {
                    "node_id": "option_planned_alias_follow",
                    "type": "option",
                    "from": "planned",
                    "to": "open",
                }
            ],
        )
        self.assertEqual(
            result["normalized_statuses"],
            [
                {
                    "node_id": "option_planned_alias_follow",
                    "node_type": "option",
                    "input_status": "planned",
                    "stored_status": "open",
                }
            ],
        )

    def test_update_workstream_fields_updates_safe_nested_allowlist(self) -> None:
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["agent_workstream"] = {"status": "in_progress", "owner": "agent_old"}
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)

        result = update_workstream_fields(
            self.root,
            option_id="option_t5",
            status="reported",
            objective="Summarize downstream results.",
            owner="agent_t5",
            report_to_problem="problem_text",
            rebuild_dashboard=False,
        )
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")

        self.assertTrue(result["changed"])
        self.assertEqual(option["agent_workstream"]["status"], "reported")
        self.assertEqual(option["agent_workstream"]["objective"], "Summarize downstream results.")
        self.assertEqual(option["agent_workstream"]["owner"], "agent_t5")
        self.assertEqual(option["agent_workstream"]["report_to_problem"], "problem_text")

    def test_create_followup_experiment_derives_from_source_and_can_set_focus(self) -> None:
        source = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        source["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", source)

        result = create_followup_experiment(
            self.root,
            from_experiment="exp_t5",
            node_id="exp_t5_followup",
            title="T5 follow-up gate",
            priority="high",
            next_action="Run follow-up gate.",
            set_focus_to_created=True,
            rebuild_dashboard=False,
        )
        followup = load_yaml(self.root / "graph" / "nodes" / "exp_t5_followup.yaml")
        current = load_yaml(self.root / "current_state.yaml")

        self.assertTrue(result["changed"])
        self.assertEqual(followup["status"], "queued")
        self.assertEqual(followup["parent"], "option_t5")
        self.assertEqual(followup["priority"], "high")
        self.assertEqual(followup["derived_from"], ["exp_t5"])
        self.assertIn("Validate follow-up against exp_t5.", followup["success_criteria"])
        self.assertEqual(followup["next_actions"], ["Run follow-up gate."])
        self.assertEqual(current["current_focus_node"], "exp_t5_followup")
        self.assertEqual(current["next_actions"], ["Run follow-up gate."])

    def test_create_followup_experiment_rejects_non_active_source_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be done or running"):
            create_followup_experiment(
                self.root,
                from_experiment="exp_t5",
                node_id="exp_t5_followup",
                title="T5 follow-up gate",
                rebuild_dashboard=False,
            )

    def test_create_followup_experiment_respects_assignment_scope(self) -> None:
        session = self.start_t5_assignment()
        source = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        source["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", source)
        self.write_other_scope_branch(
            option_id="option_other_followup_scope",
            experiment_id="exp_other_followup_scope",
            option_title="Other follow-up scope option",
            experiment_title="Other follow-up scope experiment",
            experiment_status="queued",
        )

        ok = create_followup_experiment(
            self.root,
            from_experiment="exp_t5",
            node_id="exp_t5_scoped_followup",
            title="Scoped follow-up",
            assignment_id=session["assignment_id"],
            rebuild_dashboard=False,
        )
        with self.assertRaises(AssignmentScopeError) as out_of_scope_source:
            create_followup_experiment(
                self.root,
                from_experiment="exp_other_followup_scope",
                node_id="exp_other_scoped_followup",
                title="Out-of-scope follow-up",
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        with self.assertRaises(AssignmentScopeError) as out_of_scope_parent:
            create_followup_experiment(
                self.root,
                from_experiment="exp_t5",
                parent="option_other_followup_scope",
                node_id="exp_t5_bad_parent_followup",
                title="Bad parent follow-up",
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        source["status"] = "queued"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", source)
        with self.assertRaises(AssignmentScopeError) as out_of_scope_parent_before_status:
            create_followup_experiment(
                self.root,
                from_experiment="exp_t5",
                parent="option_other_followup_scope",
                node_id="exp_t5_bad_parent_status_followup",
                title="Bad parent beats bad status",
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )

        self.assertTrue(ok["changed"])
        self.assertEqual(out_of_scope_source.exception.payload["node_id"], "exp_other_followup_scope")
        self.assertEqual(out_of_scope_parent.exception.payload["node_id"], "option_other_followup_scope")
        self.assertEqual(out_of_scope_parent_before_status.exception.payload["node_id"], "option_other_followup_scope")
        self.assertFalse((self.root / "graph" / "nodes" / "exp_other_scoped_followup.yaml").exists())
        self.assertFalse((self.root / "graph" / "nodes" / "exp_t5_bad_parent_followup.yaml").exists())
        self.assertFalse((self.root / "graph" / "nodes" / "exp_t5_bad_parent_status_followup.yaml").exists())

    def test_followup_and_migrate_assignment_scope_cli_errors_are_structured(self) -> None:
        session = self.start_t5_assignment()
        source = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        source["status"] = "done"
        source["next_actions"] = ["Run cache validation gate."]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", source)
        self.write_other_scope_branch(
            option_id="option_other_cli_followup_scope",
            experiment_id="exp_other_cli_followup_scope",
            option_title="Other CLI follow-up scope option",
            experiment_title="Other CLI follow-up scope experiment",
            experiment_status="queued",
            experiment_next_actions=["Run other gate."],
        )
        write_node(
            self.root,
            {
                "id": "exp_t5_cli_queued_scope",
                "type": "experiment",
                "title": "Queued in-scope CLI scope experiment",
                "status": "queued",
                "parent": "option_t5",
                "next_actions": ["Run queued gate."],
            },
        )

        cases = [
            (
                [
                    *cli_command("create-followup-experiment"),
                    "--root",
                    str(self.root),
                    "--assignment",
                    session["assignment_id"],
                    "--from",
                    "exp_other_cli_followup_scope",
                    "--id",
                    "exp_other_cli_followup",
                    "--title",
                    "Out of scope follow-up",
                    "--json",
                    "--no-build",
                ],
                "node_out_of_assignment_scope",
                "exp_other_cli_followup_scope",
                self.root / "graph" / "nodes" / "exp_other_cli_followup.yaml",
            ),
            (
                [
                    *cli_command("create-followup-experiment"),
                    "--root",
                    str(self.root),
                    "--assignment",
                    session["assignment_id"],
                    "--from",
                    "exp_t5_cli_queued_scope",
                    "--parent",
                    "option_other_cli_followup_scope",
                    "--id",
                    "exp_t5_cli_queued_bad_parent_followup",
                    "--title",
                    "Bad parent beats bad status",
                    "--json",
                    "--no-build",
                ],
                "node_out_of_assignment_scope",
                "option_other_cli_followup_scope",
                self.root / "graph" / "nodes" / "exp_t5_cli_queued_bad_parent_followup.yaml",
            ),
            (
                [
                    *cli_command("create-followup-experiment"),
                    "--root",
                    str(self.root),
                    "--assignment",
                    session["assignment_id"],
                    "--from",
                    "exp_t5",
                    "--id",
                    "exp_t5_forbidden_focus_followup",
                    "--title",
                    "Forbidden focus follow-up",
                    "--set-focus",
                    "--json",
                    "--no-build",
                ],
                "assignment_set_focus_forbidden",
                "exp_t5",
                self.root / "graph" / "nodes" / "exp_t5_forbidden_focus_followup.yaml",
            ),
            (
                [
                    *cli_command("migrate-terminal-next-actions"),
                    "--root",
                    str(self.root),
                    "--assignment",
                    session["assignment_id"],
                    "--id",
                    "exp_other_cli_followup_scope",
                    "--followup-id",
                    "exp_other_cli_migrate_followup",
                    "--title",
                    "Out of scope migrate follow-up",
                    "--json",
                    "--no-build",
                ],
                "node_out_of_assignment_scope",
                "exp_other_cli_followup_scope",
                self.root / "graph" / "nodes" / "exp_other_cli_migrate_followup.yaml",
            ),
            (
                [
                    *cli_command("migrate-terminal-next-actions"),
                    "--root",
                    str(self.root),
                    "--assignment",
                    session["assignment_id"],
                    "--id",
                    "exp_t5_cli_queued_scope",
                    "--parent",
                    "option_other_cli_followup_scope",
                    "--followup-id",
                    "exp_t5_cli_queued_bad_parent_migrate",
                    "--title",
                    "Bad parent beats bad status",
                    "--json",
                    "--no-build",
                ],
                "node_out_of_assignment_scope",
                "option_other_cli_followup_scope",
                self.root / "graph" / "nodes" / "exp_t5_cli_queued_bad_parent_migrate.yaml",
            ),
            (
                [
                    *cli_command("migrate-terminal-next-actions"),
                    "--root",
                    str(self.root),
                    "--assignment",
                    session["assignment_id"],
                    "--id",
                    "exp_t5",
                    "--followup-id",
                    "exp_t5_forbidden_focus_migrate",
                    "--title",
                    "Forbidden focus migrate",
                    "--set-focus",
                    "--json",
                    "--no-build",
                ],
                "assignment_set_focus_forbidden",
                "exp_t5",
                self.root / "graph" / "nodes" / "exp_t5_forbidden_focus_migrate.yaml",
            ),
        ]

        for command, expected_error, expected_node, forbidden_path in cases:
            with self.subTest(expected_error=expected_error, expected_node=expected_node):
                out = subprocess.run(command, capture_output=True, text=True, check=False)
                payload = json.loads(out.stdout)

                self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
                self.assertEqual(payload["error"], expected_error)
                self.assertEqual(payload["assignment_id"], session["assignment_id"])
                self.assertEqual(payload["node_id"], expected_node)
                self.assertFalse(forbidden_path.exists())

    def test_migrate_terminal_next_actions_dry_run_shows_followup_and_cleanup(self) -> None:
        source = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        source["status"] = "done"
        source["next_actions"] = ["Run cache validation gate."]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", source)

        result = migrate_terminal_next_actions(
            self.root,
            node_id="exp_t5",
            followup_id="exp_t5_cache_gate",
            title="T5 cache validation gate",
            dry_run=True,
            show_diff=True,
        )
        after_source = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")

        self.assertTrue(result["dry_run"])
        self.assertTrue(result["would_change"])
        self.assertFalse(result["changed"])
        self.assertEqual(result["strategy"], "single_followup_experiment")
        self.assertEqual(result["moved_next_actions"], ["Run cache validation gate."])
        self.assertEqual(result["created_nodes"], ["exp_t5_cache_gate"])
        self.assertEqual(result["updated_nodes"], ["exp_t5"])
        self.assertIn("exp_t5_cache_gate.yaml", result["diff"])
        self.assertEqual(after_source["next_actions"], ["Run cache validation gate."])
        self.assertFalse((self.root / "graph" / "nodes" / "exp_t5_cache_gate.yaml").exists())

    def test_migrate_terminal_next_actions_creates_followup_and_clears_source(self) -> None:
        source = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        source["status"] = "done"
        source["next_actions"] = ["Run cache validation gate."]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", source)

        result = migrate_terminal_next_actions(
            self.root,
            node_id="exp_t5",
            followup_id="exp_t5_cache_gate",
            title="T5 cache validation gate",
            priority="high",
            rebuild_dashboard=False,
        )
        after_source = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        followup = load_yaml(self.root / "graph" / "nodes" / "exp_t5_cache_gate.yaml")

        self.assertTrue(result["changed"])
        self.assertNotIn("next_actions", after_source)
        self.assertEqual(followup["status"], "queued")
        self.assertEqual(followup["parent"], "option_t5")
        self.assertEqual(followup["priority"], "high")
        self.assertEqual(followup["derived_from"], ["exp_t5"])
        self.assertEqual(followup["next_actions"], ["Run cache validation gate."])
        self.assertIn("Validate follow-up against exp_t5.", followup["success_criteria"])

    def test_migrate_terminal_next_actions_respects_assignment_scope(self) -> None:
        session = self.start_t5_assignment()
        source = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        source["status"] = "done"
        source["next_actions"] = ["Run cache validation gate."]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", source)
        self.write_other_scope_branch(
            option_id="option_other_migrate_scope",
            experiment_id="exp_other_migrate_scope",
            option_title="Other migrate scope option",
            experiment_title="Other migrate scope experiment",
            experiment_status="queued",
            experiment_next_actions=["Run other gate."],
        )

        ok = migrate_terminal_next_actions(
            self.root,
            node_id="exp_t5",
            followup_id="exp_t5_cache_gate_scoped",
            title="T5 cache validation gate",
            assignment_id=session["assignment_id"],
            rebuild_dashboard=False,
        )
        with self.assertRaises(AssignmentScopeError) as out_of_scope:
            migrate_terminal_next_actions(
                self.root,
                node_id="exp_other_migrate_scope",
                followup_id="exp_other_cache_gate_scoped",
                title="Other cache validation gate",
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )
        write_node(
            self.root,
            {
                "id": "exp_t5_invalid_migrate_scope",
                "type": "experiment",
                "title": "Invalid migrate scope experiment",
                "status": "queued",
                "parent": "option_t5",
                "next_actions": ["Run invalid gate."],
            },
        )
        with self.assertRaises(AssignmentScopeError) as out_of_scope_parent_before_status:
            migrate_terminal_next_actions(
                self.root,
                node_id="exp_t5_invalid_migrate_scope",
                parent="option_other_migrate_scope",
                followup_id="exp_t5_invalid_migrate_followup",
                title="Bad parent beats bad status",
                assignment_id=session["assignment_id"],
                rebuild_dashboard=False,
            )

        self.assertTrue(ok["changed"])
        self.assertIn("set-cursor", ok["recommended_commands"]["set_cursor"])
        self.assertIn("exp_t5_cache_gate_scoped", ok["recommended_commands"]["set_cursor"])
        self.assertEqual(out_of_scope.exception.payload["node_id"], "exp_other_migrate_scope")
        self.assertEqual(out_of_scope_parent_before_status.exception.payload["node_id"], "option_other_migrate_scope")
        self.assertFalse((self.root / "graph" / "nodes" / "exp_other_cache_gate_scoped.yaml").exists())
        self.assertFalse((self.root / "graph" / "nodes" / "exp_t5_invalid_migrate_followup.yaml").exists())

    def test_migrate_terminal_next_actions_guides_larger_work_to_workstream(self) -> None:
        source = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        source["status"] = "done"
        source["next_actions"] = ["Run cache gate.", "Design longer branch."]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", source)

        result = migrate_terminal_next_actions(self.root, node_id="exp_t5", dry_run=True)

        self.assertFalse(result["would_change"])
        self.assertEqual(result["strategy"], "create_workstream_guidance")
        self.assertIn("create-workstream", result["recommended_commands"]["create_workstream"])
        self.assertIn("Use create-workstream", result["guidance"])

    def test_migrate_terminal_next_actions_guides_non_experiment_terminal_node(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_old",
                "type": "artifact",
                "title": "Old artifact",
                "status": "deprecated",
                "next_actions": ["Replace archived bundle."],
            },
        )

        result = migrate_terminal_next_actions(
            self.root,
            node_id="artifact_old",
            followup_id="artifact_old_followup",
            title="Artifact follow-up",
            dry_run=True,
        )
        artifact = load_yaml(self.root / "graph" / "nodes" / "artifact_old.yaml")
        context = build_agent_context(self.root, load_nodes(self.root))
        semantic = semantic_lint(self.root)

        self.assertFalse(result["would_change"])
        self.assertEqual(result["strategy"], "create_workstream_guidance")
        self.assertIn("create-workstream", result["recommended_commands"]["create_workstream"])
        self.assertEqual(artifact["next_actions"], ["Replace archived bundle."])
        stale_ids = {item["node_id"] for item in context["next_action_scopes"]["stale_terminal_node_next_actions"]}
        self.assertIn("artifact_old", stale_ids)
        self.assertIn(
            "terminal_node_has_next_actions",
            {warning["id"] for warning in semantic["warnings"]},
        )

    def test_migrate_terminal_next_actions_guides_non_done_terminal_experiment(self) -> None:
        source = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        source["status"] = "failed"
        source["next_actions"] = ["Investigate failed experiment."]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", source)

        result = migrate_terminal_next_actions(
            self.root,
            node_id="exp_t5",
            followup_id="exp_t5_failed_followup",
            title="Failed experiment follow-up",
            dry_run=True,
        )
        after_source = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")

        self.assertFalse(result["would_change"])
        self.assertEqual(result["strategy"], "create_workstream_guidance")
        self.assertEqual(after_source["next_actions"], ["Investigate failed experiment."])
        self.assertFalse((self.root / "graph" / "nodes" / "exp_t5_failed_followup.yaml").exists())

    def test_migrate_terminal_next_actions_cli_compact_and_manifest(self) -> None:
        source = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        source["status"] = "done"
        source["next_actions"] = ["Run cache validation gate."]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", source)

        out = subprocess.run(
            [
                *cli_command("migrate-terminal-next-actions"),
                "--root",
                str(self.root),
                "--id",
                "exp_t5",
                "--followup-id",
                "exp_t5_cache_gate",
                "--title",
                "T5 cache validation gate",
                "--dry-run",
                "--json",
                "--compact",
                "--show-diff",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)
        by_name = {item["name"]: item for item in agent_command_manifest()}

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(payload["command"], "research-cockpit migrate-terminal-next-actions")
        self.assertTrue(payload["would_change"])
        self.assertEqual(payload["created"], ["exp_t5_cache_gate"])
        self.assertEqual(payload["updated"], ["exp_t5"])
        self.assertIn("migrate-terminal-next-actions", by_name)
        self.assertTrue(by_name["migrate-terminal-next-actions"]["supports_dry_run"])
        self.assertTrue(by_name["migrate-terminal-next-actions"]["supports_no_build"])
        self.assertIn("--assignment", by_name["migrate-terminal-next-actions"]["supported_flags"])
        self.assertIn("--coordinator", by_name["migrate-terminal-next-actions"]["supported_flags"])
        self.assertIn("create-workstream", by_name["migrate-terminal-next-actions"]["hierarchy_guidance"])

    def test_close_current_experiment_completes_and_moves_global_and_agent_focus(self) -> None:
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "exp_t5"
        current["current_focus_path"] = ["stage_text", "problem_text", "option_t5", "exp_t5"]
        current["next_actions"] = ["Run exp_t5."]
        current["agent_focuses"] = {
            "agent_t5": {
                "current_focus_node": "exp_t5",
                "current_focus_path": ["stage_text", "problem_text", "option_t5", "exp_t5"],
                "current_option": "option_t5",
            }
        }
        save_yaml(self.root / "current_state.yaml", current)
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["next_actions"] = ["Review option branch."]
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)

        result = close_current_experiment(
            self.root,
            experiment_id="exp_t5",
            finding="Closed via workflow.",
            confidence="medium",
            next_focus="option_t5",
            sync_agent="agent_t5",
            rebuild_dashboard=False,
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        current = load_yaml(self.root / "current_state.yaml")

        self.assertTrue(result["changed"])
        self.assertEqual(experiment["status"], "done")
        self.assertEqual(current["current_focus_node"], "option_t5")
        self.assertEqual(current["agent_focuses"]["agent_t5"]["current_focus_node"], "option_t5")
        self.assertEqual(current["next_actions"], ["Review option branch."])
        self.assertEqual(current["agent_focuses"]["agent_t5"]["next_actions"], ["Review option branch."])
        self.assertEqual(result["completed_experiment"]["experiment_id"], "exp_t5")
        self.assertNotIn("current_focus_node_is_terminal", result["warnings"])
        self.assertNotIn("agent_focus_is_terminal:agent_t5", result["warnings"])
        self.assertNotIn("set-focus", " ".join(result["recommended_commands"]))
        self.assertNotIn("set-agent-focus", " ".join(result["recommended_commands"]))

    def test_close_current_experiment_rejects_terminal_next_focus(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be the experiment being closed"):
            close_current_experiment(
                self.root,
                experiment_id="exp_t5",
                finding="Do not refocus closed experiment.",
                confidence="medium",
                next_focus="exp_t5",
                rebuild_dashboard=False,
            )

        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["status"] = "resolved"
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)

        with self.assertRaisesRegex(ValueError, "terminal status"):
            close_current_experiment(
                self.root,
                experiment_id="exp_t5",
                finding="Do not focus resolved problem.",
                confidence="medium",
                next_focus="problem_text",
                rebuild_dashboard=False,
            )

    def test_apply_graph_plan_reports_normalized_statuses_for_option_alias(self) -> None:
        result = apply_graph_plan(
            self.root,
            plan={
                "nodes": [
                    {
                        "id": "option_plan_alias",
                        "type": "option",
                        "title": "Alias option",
                        "parent": "problem_text",
                        "status": "planned",
                    }
                ]
            },
            dry_run=True,
        )

        self.assertEqual(
            result["normalized_statuses"],
            [
                {
                    "node_id": "option_plan_alias",
                    "node_type": "option",
                    "input_status": "planned",
                    "stored_status": "open",
                }
            ],
        )

    def test_apply_graph_plan_updates_status_and_assignment_fields(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_t5_gate",
                "type": "artifact",
                "title": "T5 gate bundle",
                "status": "done",
            },
        )

        result = apply_graph_plan(
            self.root,
            plan={
                "updates": [
                    {
                        "id": "exp_t5",
                        "status": "queued",
                        "fields": {
                            "priority": "high",
                            "order": "p2.2",
                            "owner": "agent_t5",
                            "ready_for_agent": True,
                            "depends_on": ["problem_text"],
                            "blocked_by": ["problem_text"],
                            "handoff_context": "Run the queued T5 gate.",
                            "linked_artifact": "artifact_t5_gate",
                        },
                    }
                ]
            },
            rebuild_dashboard=False,
        )

        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")

        self.assertTrue(result["changed"])
        self.assertEqual(result["updated_nodes"], ["exp_t5"])
        self.assertEqual(experiment["status"], "queued")
        self.assertEqual(experiment["priority"], "high")
        self.assertEqual(experiment["order"], "p2.2")
        self.assertEqual(experiment["owner"], "agent_t5")
        self.assertIs(experiment["ready_for_agent"], True)
        self.assertEqual(experiment["depends_on"], ["problem_text"])
        self.assertEqual(experiment["blocked_by"], ["problem_text"])
        self.assertEqual(experiment["handoff_context"], "Run the queued T5 gate.")
        self.assertEqual(experiment["linked_artifacts"], ["artifact_t5_gate"])

    def test_apply_graph_plan_rejects_direct_decision_acceptance(self) -> None:
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Adopt T5",
                "status": "proposed",
                "parent": "option_t5",
            },
        )

        with self.assertRaisesRegex(ValueError, "accept-decision"):
            apply_graph_plan(
                self.root,
                plan={"updates": [{"id": "decision_t5", "status": "accepted"}]},
                dry_run=True,
            )

    def test_apply_graph_plan_rejects_terminal_parent_with_active_descendants(self) -> None:
        plan_file = self.tmp_root / "resolve_problem.yaml"
        save_yaml(
            plan_file,
            {
                "updates": [
                    {
                        "id": "problem_text",
                        "status": "resolved",
                    }
                ]
            },
        )
        problem_path = self.root / "graph" / "nodes" / "problem_text.yaml"
        before = problem_path.read_text(encoding="utf-8")

        out = subprocess.run(
            [
                *cli_command("apply-graph-plan"),
                "--root",
                str(self.root),
                "--file",
                str(plan_file),
                "--dry-run",
                "--json",
                "--show-diff",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertEqual(payload["error"], "terminal_parent_has_active_descendants")
        self.assertEqual(payload["node_id"], "problem_text")
        self.assertEqual(payload["target_status"], "resolved")
        self.assertEqual([item["id"] for item in payload["blocking_descendants"]], ["option_t5", "exp_t5"])
        self.assertEqual(before, problem_path.read_text(encoding="utf-8"))

    def test_assignment_view_lists_high_priority_agent_tasks(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_t5_gate",
                "type": "artifact",
                "title": "T5 gate bundle",
                "status": "done",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment.update({
            "status": "queued",
            "priority": "high",
            "order": "p2.2",
            "owner": "agent_t5",
            "ready_for_agent": True,
            "depends_on": ["problem_text"],
            "blocked_by": ["problem_text"],
            "handoff_context": "Run the queued T5 gate.",
            "linked_artifacts": ["artifact_t5_gate"],
            "next_actions": ["Run gate and record one finding."],
        })
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        write_node(
            self.root,
            {
                "id": "exp_medium",
                "type": "experiment",
                "title": "Medium task",
                "status": "queued",
                "priority": "medium",
                "parent": "option_t5",
            },
        )

        payload = assignment_view_payload(self.root)

        self.assertEqual(payload["count"], 1)
        row = payload["assignments"][0]
        self.assertEqual(row["id"], "exp_t5")
        self.assertEqual(row["parent_option"]["id"], "option_t5")
        self.assertEqual(row["owner"], "agent_t5")
        self.assertIs(row["ready_for_agent"], True)
        self.assertEqual(row["depends_on"][0]["id"], "problem_text")
        self.assertEqual(row["blocked_by"][0]["id"], "problem_text")
        self.assertEqual(row["key_artifacts"][0]["id"], "artifact_t5_gate")
        self.assertEqual(row["next_action"], "Run gate and record one finding.")

    def test_apply_graph_plan_print_schema_lists_status_and_assignment_fields(self) -> None:
        result = subprocess.run(
            [
                *cli_command("apply-graph-plan"),
                "--print-schema",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("status: queued", result.stdout)
        self.assertIn("ready_for_agent", result.stdout)
        self.assertIn("updates[*].fields", result.stdout)

    def test_promote_decision_creates_proposed_decision(self) -> None:
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative",
                "status": "open",
                "parent": "problem_text",
            },
        )

        promote_decision(
            self.root,
            decision_id="decision_t5",
            option_id="option_t5",
            title="Adopt T5",
            summary="T5 is promising.",
            status="proposed",
            supporting_experiments=["exp_t5"],
            alternatives=["option_alt"],
            consequences=["Regenerate cache."],
            next_required_actions=["Run CLAP ablation."],
            evidence_strength="medium",
        )

        decision = load_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml")

        self.assertEqual(decision["type"], "decision")
        self.assertEqual(decision["status"], "proposed")
        self.assertEqual(decision["decision_status"], "proposed")
        self.assertEqual(decision["parent"], "option_t5")
        self.assertEqual(decision["derived_from"], ["option_t5"])
        self.assertEqual(decision["supporting_experiments"], ["exp_t5"])
        self.assertEqual(decision["alternatives_considered"], ["option_alt"])

    def test_promote_decision_cli_dry_run_json_previews_without_writing(self) -> None:
        command = "promote-decision"
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative",
                "status": "open",
                "parent": "problem_text",
            },
        )

        out = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--id",
                "decision_t5",
                "--option",
                "option_t5",
                "--title",
                "Adopt T5",
                "--summary",
                "T5 is promising.",
                "--status",
                "proposed",
                "--supporting-experiment",
                "exp_t5",
                "--alternative",
                "option_alt",
                "--consequence",
                "Regenerate cache.",
                "--next-required-action",
                "Run CLAP ablation.",
                "--evidence-strength",
                "medium",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["decision_id"], "decision_t5")
        self.assertEqual(payload["option_id"], "option_t5")
        self.assertEqual(payload["decision"]["status"], "proposed")
        self.assertEqual(payload["decision"]["supporting_experiments"], ["exp_t5"])
        self.assertFalse((self.root / "graph" / "nodes" / "decision_t5.yaml").exists())
        self.assertFalse((self.root / "dashboards").exists())

    def test_promote_decision_cli_dry_run_gate_failure_does_not_write(self) -> None:
        command = "promote-decision"

        out = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--id",
                "decision_accept_bad",
                "--option",
                "option_t5",
                "--title",
                "Accept T5",
                "--summary",
                "Accept without evidence.",
                "--status",
                "accepted",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 1)
        self.assertIn("not ready", out.stdout)
        self.assertFalse((self.root / "graph" / "nodes" / "decision_accept_bad.yaml").exists())
        self.assertFalse((self.root / "dashboards").exists())

    def test_promote_decision_auto_evidence_merges_experiments_and_preserves_explicit_strength(self) -> None:
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["outcome"] = "positive"
        experiment["findings"] = [
            {
                "id": "exp_t5_finding_001",
                "statement": "T5 improves replace following.",
                "confidence": "medium",
                "outcome": "positive",
            }
        ]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)

        promote_decision(
            self.root,
            decision_id="decision_auto_t5",
            option_id="option_t5",
            title="Auto evidence T5",
            summary="T5 is promising.",
            status="proposed",
            supporting_experiments=["exp_t5"],
            evidence_strength="strong",
            auto_evidence=True,
            rebuild_dashboard=False,
        )

        decision = load_yaml(self.root / "graph" / "nodes" / "decision_auto_t5.yaml")

        self.assertEqual(decision["supporting_experiments"], ["exp_t5"])
        self.assertEqual(decision["evidence_strength"], "strong")
        self.assertIn("T5 improves replace following.", decision["evidence_summary"])

    def test_update_decision_evidence_refreshes_existing_decision_and_rebuilds_dashboard(self) -> None:
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
                "supporting_experiments": [],
                "evidence_strength": "none",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["result_summary"] = "Improves edit following."
        experiment["outcome"] = "positive"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)

        result = update_decision_evidence(self.root, decision_id="decision_t5")
        decision = load_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml")

        self.assertEqual(result["decision_id"], "decision_t5")
        self.assertEqual(decision["status"], "proposed")
        self.assertEqual(decision["supporting_experiments"], ["exp_t5"])
        self.assertEqual(decision["evidence_strength"], "medium")
        self.assertIn("1 experiment", decision["evidence_summary"])
        self.assertTrue((self.root / "dashboards" / "next_action_suggestions.json").exists())

    def test_update_decision_evidence_uses_zh_locale_from_current_state(self) -> None:
        current = load_yaml(self.root / "current_state.yaml")
        current["language"] = "zh"
        save_yaml(self.root / "current_state.yaml", current)
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["outcome"] = "positive"
        experiment["findings"] = [
            {
                "id": "exp_t5_finding_001",
                "statement": "保持用户原文。",
                "confidence": "medium",
                "outcome": "positive",
            }
        ]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)

        update_decision_evidence(self.root, decision_id="decision_t5", rebuild_dashboard=False)
        decision = load_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml")

        self.assertIn("1 个实验", decision["evidence_summary"])
        self.assertIn("1 条 finding", decision["evidence_summary"])
        self.assertIn("保持用户原文。", decision["evidence_summary"])

    def test_update_decision_evidence_rejects_bad_decision_inputs(self) -> None:
        with self.assertRaises(ValueError) as missing:
            update_decision_evidence(self.root, decision_id="missing_decision", rebuild_dashboard=False)
        self.assertIn("missing_decision", str(missing.exception))

        with self.assertRaises(ValueError) as wrong_type:
            update_decision_evidence(self.root, decision_id="option_t5", rebuild_dashboard=False)
        self.assertIn("decision", str(wrong_type.exception))

    def test_update_decision_evidence_cli_reports_success_and_failure(self) -> None:
        command = "update-decision-evidence"
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
            },
        )

        ok = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--id", "decision_t5", "--no-build"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ok.returncode, 0)
        self.assertIn("Updated evidence", ok.stdout)

        failed = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--id", "missing_decision", "--no-build"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(failed.returncode, 1)
        self.assertIn("missing_decision", failed.stdout)

    def test_update_decision_evidence_cli_dry_run_json_diff_does_not_write(self) -> None:
        command = "update-decision-evidence"
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
                "evidence_strength": "none",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["outcome"] = "positive"
        experiment["result_summary"] = "Improves edit following."
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        before = (self.root / "graph" / "nodes" / "decision_t5.yaml").read_text(encoding="utf-8")

        out = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--id",
                "decision_t5",
                "--dry-run",
                "--show-diff",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after = (self.root / "graph" / "nodes" / "decision_t5.yaml").read_text(encoding="utf-8")
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(payload["dry_run"])
        self.assertFalse(payload["changed"])
        self.assertTrue(payload["would_change"])
        self.assertEqual(payload["decision_id"], "decision_t5")
        self.assertEqual(payload["before"]["evidence_strength"], "none")
        self.assertEqual(payload["after"]["evidence_strength"], "medium")
        self.assertIn("bundle", payload)
        self.assertIn("diff", payload)
        self.assertEqual(before, after)

    def test_update_decision_evidence_rejects_malformed_interaction_log_without_write(self) -> None:
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
            },
        )
        before = (self.root / "graph" / "nodes" / "decision_t5.yaml").read_text(encoding="utf-8")
        write_malformed_interaction_log(self.root)

        with self.assertRaises(MutationError):
            update_decision_evidence(self.root, decision_id="decision_t5", rebuild_dashboard=False)

        self.assertEqual(before, (self.root / "graph" / "nodes" / "decision_t5.yaml").read_text(encoding="utf-8"))

    def test_update_decision_checklist_appends_fields_and_dedupes(self) -> None:
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative",
                "status": "open",
                "parent": "problem_text",
            },
        )
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
                "alternatives_considered": ["option_alt"],
                "consequences": ["Update docs."],
                "next_required_actions": ["Run smoke test."],
            },
        )

        result = update_decision_checklist(
            self.root,
            decision_id="decision_t5",
            alternatives=["option_alt"],
            consequences=["Update docs.", "Notify downstream agent."],
            next_required_actions=["Run smoke test.", "Review checklist."],
            evidence_summary="Evidence is ready for review.",
        )
        decision = load_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml")

        self.assertEqual(result["added"]["alternatives_considered"], [])
        self.assertEqual(decision["alternatives_considered"], ["option_alt"])
        self.assertEqual(decision["consequences"], ["Update docs.", "Notify downstream agent."])
        self.assertEqual(decision["next_required_actions"], ["Run smoke test.", "Review checklist."])
        self.assertEqual(decision["evidence_summary"], "Evidence is ready for review.")
        self.assertEqual(decision["status"], "proposed")
        self.assertTrue((self.root / "dashboards" / "decision_acceptance_checklists.json").exists())

    def test_update_decision_checklist_rejects_bad_inputs(self) -> None:
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
            },
        )

        with self.assertRaises(ValueError) as missing:
            update_decision_checklist(self.root, decision_id="missing_decision", rebuild_dashboard=False)
        self.assertIn("missing_decision", str(missing.exception))

        with self.assertRaises(ValueError) as wrong_type:
            update_decision_checklist(self.root, decision_id="option_t5", rebuild_dashboard=False)
        self.assertIn("decision", str(wrong_type.exception))

        with self.assertRaises(ValueError) as missing_alt:
            update_decision_checklist(
                self.root,
                decision_id="decision_t5",
                alternatives=["missing_option"],
                rebuild_dashboard=False,
            )
        self.assertIn("missing_option", str(missing_alt.exception))

        with self.assertRaises(ValueError) as wrong_alt_type:
            update_decision_checklist(
                self.root,
                decision_id="decision_t5",
                alternatives=["problem_text"],
                rebuild_dashboard=False,
            )
        self.assertIn("option", str(wrong_alt_type.exception))

    def test_update_decision_checklist_cli_reports_success_and_failure(self) -> None:
        command = "update-decision-checklist"
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative",
                "status": "open",
                "parent": "problem_text",
            },
        )
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
            },
        )

        ok = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--id",
                "decision_t5",
                "--alternative",
                "option_alt",
                "--consequence",
                "Update docs.",
                "--next-required-action",
                "Review checklist.",
                "--no-build",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        failed = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--id",
                "decision_t5",
                "--alternative",
                "missing_option",
                "--no-build",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(ok.returncode, 0)
        self.assertIn("Updated decision checklist", ok.stdout)
        self.assertFalse((self.root / "dashboards" / "decision_acceptance_checklists.json").exists())
        self.assertEqual(failed.returncode, 1)
        self.assertIn("missing_option", failed.stdout)

    def test_update_decision_checklist_cli_dry_run_json_diff_does_not_write(self) -> None:
        command = "update-decision-checklist"
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative",
                "status": "rejected",
                "parent": "problem_text",
            },
        )
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
            },
        )
        before = (self.root / "graph" / "nodes" / "decision_t5.yaml").read_text(encoding="utf-8")

        out = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--id",
                "decision_t5",
                "--alternative",
                "option_alt",
                "--consequence",
                "Update docs.",
                "--next-required-action",
                "Review checklist.",
                "--dry-run",
                "--show-diff",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after = (self.root / "graph" / "nodes" / "decision_t5.yaml").read_text(encoding="utf-8")
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(payload["dry_run"])
        self.assertFalse(payload["changed"])
        self.assertTrue(payload["would_change"])
        self.assertEqual(payload["decision_id"], "decision_t5")
        self.assertEqual(payload["added"]["alternatives_considered"], ["option_alt"])
        self.assertIn("diff", payload)
        self.assertEqual(before, after)

    def test_update_decision_checklist_rejects_malformed_interaction_log_without_write(self) -> None:
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
            },
        )
        before = (self.root / "graph" / "nodes" / "decision_t5.yaml").read_text(encoding="utf-8")
        write_malformed_interaction_log(self.root)

        with self.assertRaises(MutationError):
            update_decision_checklist(
                self.root,
                decision_id="decision_t5",
                consequences=["Should not write."],
                rebuild_dashboard=False,
            )

        self.assertEqual(before, (self.root / "graph" / "nodes" / "decision_t5.yaml").read_text(encoding="utf-8"))

    def test_check_decision_acceptance_cli_json_reports_ready_and_failures(self) -> None:
        command = "check-decision-acceptance"
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
            },
        )

        payload = decision_acceptance_payload(self.root, "decision_t5")
        failed = subprocess.run(
            [*cli_command(command), "--root", str(self.root), "--id", "decision_t5", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertFalse(payload["ready"])
        self.assertEqual(failed.returncode, 1)
        self.assertFalse(json.loads(failed.stdout)["ready"])

    def test_accept_decision_updates_existing_decision_option_and_problem(self) -> None:
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative",
                "status": "rejected",
                "parent": "problem_text",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["result_summary"] = "Improves edit following."
        experiment["outcome"] = "positive"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
                "supporting_experiments": ["exp_t5"],
                "evidence_strength": "medium",
                "evidence_summary": "1 experiment; outcome positive",
                "alternatives_considered": ["option_alt"],
                "consequences": ["Update focus."],
                "next_required_actions": ["Run CLAP ablation."],
            },
        )

        accept_decision(self.root, decision_id="decision_t5", rebuild_dashboard=False)

        decision = load_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml")
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        self.assertEqual(decision["status"], "accepted")
        self.assertEqual(decision["decision_status"], "accepted")
        self.assertEqual(option["status"], "accepted")
        self.assertEqual(option["decision_state"], "accepted")
        self.assertEqual(problem["status"], "resolved")
        self.assertEqual(problem["resolved_by"], "decision_t5")
        event = interaction_events(self.root)[-1]
        self.assertEqual(event["kind"], "accept_decision")
        self.assertEqual(event["node_id"], "decision_t5")
        self.assertEqual(event["decision_id"], "decision_t5")
        self.assertEqual(event["option_id"], "option_t5")
        self.assertEqual(event["problem_id"], "problem_text")
        self.assertFalse(event["forced"])

    def test_accept_decision_cli_dry_run_json_previews_without_writing(self) -> None:
        command = "accept-decision"
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative",
                "status": "rejected",
                "parent": "problem_text",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["result_summary"] = "Improves edit following."
        experiment["outcome"] = "positive"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
                "supporting_experiments": ["exp_t5"],
                "evidence_strength": "medium",
                "evidence_summary": "1 experiment; outcome positive",
                "alternatives_considered": ["option_alt"],
                "consequences": ["Update focus."],
                "next_required_actions": ["Run CLAP ablation."],
            },
        )
        decision_path = self.root / "graph" / "nodes" / "decision_t5.yaml"
        option_path = self.root / "graph" / "nodes" / "option_t5.yaml"
        problem_path = self.root / "graph" / "nodes" / "problem_text.yaml"
        before = {
            "decision": decision_path.read_text(encoding="utf-8"),
            "option": option_path.read_text(encoding="utf-8"),
            "problem": problem_path.read_text(encoding="utf-8"),
        }

        out = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--id",
                "decision_t5",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after = {
            "decision": decision_path.read_text(encoding="utf-8"),
            "option": option_path.read_text(encoding="utf-8"),
            "problem": problem_path.read_text(encoding="utf-8"),
        }

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["decision_id"], "decision_t5")
        self.assertFalse(payload["forced"])
        self.assertEqual(payload["before"]["decision_status"], "proposed")
        self.assertEqual(payload["after"]["decision_status"], "accepted")
        self.assertEqual(payload["after"]["problem_status"], "resolved")
        self.assertEqual(before, after)
        self.assertFalse((self.root / "graph" / "interaction_log.yaml").exists())
        self.assertFalse((self.root / "dashboards").exists())

    def test_accept_decision_cli_dry_run_not_ready_does_not_write(self) -> None:
        command = "accept-decision"
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
            },
        )
        decision_path = self.root / "graph" / "nodes" / "decision_t5.yaml"
        before = decision_path.read_text(encoding="utf-8")

        out = subprocess.run(
            [
                *cli_command(command),
                "--root",
                str(self.root),
                "--id",
                "decision_t5",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after = decision_path.read_text(encoding="utf-8")

        self.assertEqual(out.returncode, 1)
        payload = json.loads(out.stdout)
        self.assertFalse(payload["ready"])
        self.assertFalse(payload["changed"])
        self.assertIn("blocking_failures", payload)
        self.assertIn("not ready", payload["error"])
        self.assertEqual(before, after)
        self.assertFalse((self.root / "graph" / "interaction_log.yaml").exists())
        self.assertFalse((self.root / "dashboards").exists())

    def test_accept_decision_rejects_not_ready_unless_forced(self) -> None:
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
            },
        )

        with self.assertRaises(ValueError) as not_ready:
            accept_decision(self.root, decision_id="decision_t5", rebuild_dashboard=False)
        self.assertIn("not ready", str(not_ready.exception))
        self.assertEqual(interaction_events(self.root), [])

        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        accept_decision(self.root, decision_id="decision_t5", force_accept=True, rebuild_dashboard=False)
        decision = load_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml")
        self.assertEqual(decision["status"], "accepted")
        event = interaction_events(self.root)[-1]
        self.assertEqual(event["kind"], "accept_decision")
        self.assertTrue(event["forced"])

    def test_accept_decision_rejects_terminal_parent_with_active_descendants(self) -> None:
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative",
                "status": "open",
                "parent": "problem_text",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["result_summary"] = "Improves edit following."
        experiment["outcome"] = "positive"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
                "supporting_experiments": ["exp_t5"],
                "evidence_strength": "medium",
                "evidence_summary": "1 experiment; outcome positive",
                "alternatives_considered": ["option_alt"],
                "consequences": ["Update focus."],
                "next_required_actions": ["Run CLAP ablation."],
            },
        )
        problem_path = self.root / "graph" / "nodes" / "problem_text.yaml"
        before = problem_path.read_text(encoding="utf-8")

        out = subprocess.run(
            [
                *cli_command("accept-decision"),
                "--root",
                str(self.root),
                "--id",
                "decision_t5",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertEqual(payload["error"], "terminal_parent_has_active_descendants")
        self.assertEqual(payload["node_id"], "problem_text")
        self.assertEqual(payload["target_status"], "resolved")
        self.assertEqual([item["id"] for item in payload["blocking_descendants"]], ["option_alt"])
        self.assertEqual(before, problem_path.read_text(encoding="utf-8"))

    def test_promote_accepted_decision_updates_option_and_problem(self) -> None:
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative",
                "status": "rejected",
                "parent": "problem_text",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["result_summary"] = "Improves edit following."
        experiment["outcome"] = "positive"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        promote_decision(
            self.root,
            decision_id="decision_accept_t5",
            option_id="option_t5",
            title="Accept T5",
            summary="Accept T5 as current branch.",
            status="accepted",
            supporting_experiments=["exp_t5"],
            alternatives=["option_alt"],
            consequences=["Update focus."],
            next_required_actions=["Run CLAP ablation."],
            auto_evidence=True,
        )

        decision = load_yaml(self.root / "graph" / "nodes" / "decision_accept_t5.yaml")
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")

        self.assertEqual(decision["status"], "accepted")
        self.assertEqual(option["status"], "accepted")
        self.assertEqual(option["decision_state"], "accepted")
        self.assertEqual(problem["status"], "resolved")
        self.assertEqual(problem["resolved_by"], "decision_accept_t5")

    def test_promote_accepted_decision_requires_quality_gate(self) -> None:
        with self.assertRaises(ValueError) as not_ready:
            promote_decision(
                self.root,
                decision_id="decision_accept_bad",
                option_id="option_t5",
                title="Accept T5",
                summary="Accept without evidence.",
                status="accepted",
            )
        self.assertIn("not ready", str(not_ready.exception))

    def test_promote_accepted_decision_rejects_terminal_parent_with_active_descendants(self) -> None:
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative",
                "status": "open",
                "parent": "problem_text",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["result_summary"] = "Improves edit following."
        experiment["outcome"] = "positive"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        problem_path = self.root / "graph" / "nodes" / "problem_text.yaml"
        before = problem_path.read_text(encoding="utf-8")

        out = subprocess.run(
            [
                *cli_command("promote-decision"),
                "--root",
                str(self.root),
                "--id",
                "decision_accept_t5",
                "--option",
                "option_t5",
                "--title",
                "Accept T5",
                "--summary",
                "Accept T5 as current branch.",
                "--status",
                "accepted",
                "--supporting-experiment",
                "exp_t5",
                "--alternative",
                "option_alt",
                "--consequence",
                "Update focus.",
                "--next-required-action",
                "Run CLAP ablation.",
                "--auto-evidence",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertEqual(payload["error"], "terminal_parent_has_active_descendants")
        self.assertEqual(payload["node_id"], "problem_text")
        self.assertEqual(payload["target_status"], "resolved")
        self.assertEqual([item["id"] for item in payload["blocking_descendants"]], ["option_alt"])
        self.assertEqual(before, problem_path.read_text(encoding="utf-8"))
        self.assertFalse((self.root / "graph" / "nodes" / "decision_accept_t5.yaml").exists())

    def test_update_status_rejects_direct_decision_acceptance(self) -> None:
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
            },
        )

        with self.assertRaises(ValueError) as direct_accept:
            update_status(self.root, node_id="decision_t5", status="accepted")
        self.assertIn("research-cockpit accept-decision", str(direct_accept.exception))

    def test_promote_decision_rejects_bad_references(self) -> None:
        with self.assertRaises(ValueError) as unknown_option:
            promote_decision(
                self.root,
                decision_id="decision_bad",
                option_id="missing_option",
                title="Bad",
                summary="Bad.",
            )
        self.assertIn("missing_option", str(unknown_option.exception))

        with self.assertRaises(ValueError) as unknown_experiment:
            promote_decision(
                self.root,
                decision_id="decision_bad",
                option_id="option_t5",
                title="Bad",
                summary="Bad.",
                supporting_experiments=["missing_exp"],
            )
        self.assertIn("missing_exp", str(unknown_experiment.exception))


if __name__ == "__main__":
    unittest.main()
