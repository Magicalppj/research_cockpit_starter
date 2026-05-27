from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import unittest
import uuid
from datetime import date
from pathlib import Path
import sys
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT_DIR
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))
DEV_SCRIPTS_DIR = ROOT_DIR / "dev" / "scripts"
sys.path.insert(0, str(DEV_SCRIPTS_DIR))
existing_pythonpath = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = str(SRC_DIR) if not existing_pythonpath else str(SRC_DIR) + os.pathsep + existing_pythonpath

from research_cockpit.model import ValidationError, load_nodes, load_yaml, save_yaml
from research_cockpit.baselines import (
    build_accepted_decision_rows,
    build_accepted_option_rows,
    build_baseline_overview_rows,
    build_set_baseline_command,
    resolve_effective_baseline,
)
from research_cockpit.commands.accept_decision import accept_decision
from research_cockpit.commands.add_node import add_node
from research_cockpit.commands.agent_bootstrap import agent_bootstrap_payload, format_dependency_error, missing_runtime_dependencies
from research_cockpit.commands.apply_graph_plan import apply_graph_plan
from research_cockpit.commands.apply_suggestion import apply_suggestion
from research_cockpit.commands.agent_session_context import agent_session_context_payload
from research_cockpit.commands.assignment_view import assignment_view_payload
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.commands.check_decision_acceptance import decision_acceptance_payload
from research_cockpit.commands.claim_option import claim_option
from research_cockpit.commands.cleanup_suggestion_lifecycle import cleanup_suggestion_lifecycle
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
from research_cockpit.commands.link_artifact import link_artifact
from research_cockpit.commands.list_runs import list_runs_payload
from research_cockpit.commands.lint_semantic import semantic_lint
from research_cockpit.commands.list_agent_commands import agent_command_manifest
from research_cockpit.commands.node_context import node_context_payload
from research_cockpit.commands.option_workstream_context import compact_option_workstream_context, option_workstream_context_payload
from research_cockpit.commands.promote_decision import promote_decision
from research_cockpit.commands.record_finding import record_finding
from research_cockpit.commands.repair_interaction_log import repair_interaction_log
from research_cockpit.commands.report_option_workstream import report_option_workstream
from research_cockpit.commands.run_context import run_context_payload
from research_cockpit.commands.set_agent_focus import set_agent_focus
from research_cockpit.commands.set_baseline import set_baseline
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
from research_cockpit.context_packs import build_agent_context
from research_cockpit.graph_views import upsert_graph_view
from research_cockpit.mutation_lock import MutationError, mutation_lock
from research_cockpit.mutation_runtime import finish_mutation
from research_cockpit.resources import build_link_rows
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
        self.assertEqual(len(payload["written_files"]), 12)

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
        self.assertEqual(payload["agent_focus"]["current_focus_node"], "exp_t5")
        self.assertEqual(payload["option_context"]["hierarchy_policy"]["workstream_file_hint"]["problem.parent"], "option_t5")
        self.assertIn("create_child_workstream", payload["option_context"]["suggested_commands"])

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
        start_agent_session(
            self.root,
            option_id="option_t5",
            agent_id="agent_t5",
            objective="Run T5 branch",
            branch="agent/option_t5",
            worktree=worktree,
            rebuild_dashboard=False,
        )
        set_agent_focus(self.root, agent_id="agent_t5", node_id="exp_t5", rebuild_dashboard=False)

        build_dashboard(self.root)
        rows = json.loads((self.root / "dashboards" / "option_workstreams.json").read_text(encoding="utf-8"))

        self.assertEqual(rows[0]["session_id"], "session_agent_t5_option_t5")
        self.assertEqual(rows[0]["git_branch"], "agent/option_t5")
        self.assertEqual(rows[0]["worktree_label"], "agent_t5")
        self.assertEqual(rows[0]["agent_focus_node"], "exp_t5")

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
        hierarchy = payload["mutation_guidance"]["hierarchy_policy"]
        self.assertEqual(hierarchy["default_branch_shape"], "option -> problem -> option -> experiment/decision")
        self.assertIn("create-workstream", hierarchy["recommended_command"])
        self.assertIsInstance(payload["git"]["worktree_dirty"], bool)

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
        self.assertEqual(by_name["agent-session-context"]["primary_target"], "--agent")
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
        self.assertTrue(by_name["create-followup-experiment"]["supports_show_diff"])
        self.assertIn("--show-diff", by_name["create-followup-experiment"]["supported_flags"])
        self.assertIn("single queued gate", by_name["create-followup-experiment"]["hierarchy_guidance"])
        self.assertIn("update-workstream-fields", by_name)
        self.assertIn("agent_workstream.status", by_name["update-workstream-fields"]["fields_supported"])
        self.assertTrue(by_name["claim-option"]["supports_json"])
        self.assertTrue(by_name["claim-option"]["supports_dry_run"])
        self.assertTrue(by_name["claim-workstream"]["supports_dry_run"])
        self.assertTrue(by_name["start-agent-session"]["supports_dry_run"])
        self.assertIn("git_branch", by_name["start-agent-session"]["fields_supported"])
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
        self.assertTrue(by_name["validate"]["safe_in_plan_mode"])
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
        self.assertIn("create-followup-experiment", followup_warning["command"])
        self.assertEqual(out.returncode, 1)
        self.assertEqual(cli_payload["warning_count"], len(payload["warnings"]))

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

    def test_create_workstream_can_create_nested_branch_under_option(self) -> None:
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

        accept_decision(self.root, decision_id="decision_t5", force_accept=True, rebuild_dashboard=False)
        decision = load_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml")
        self.assertEqual(decision["status"], "accepted")
        event = interaction_events(self.root)[-1]
        self.assertEqual(event["kind"], "accept_decision")
        self.assertTrue(event["forced"])

    def test_promote_accepted_decision_updates_option_and_problem(self) -> None:
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
