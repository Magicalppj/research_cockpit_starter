from __future__ import annotations

import json
import os
import shutil
import subprocess
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
existing_pythonpath = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = str(SRC_DIR) if not existing_pythonpath else str(SRC_DIR) + os.pathsep + existing_pythonpath

from research_cockpit.model import load_nodes, load_yaml, save_yaml
from research_cockpit.commands.accept_decision import accept_decision
from research_cockpit.commands.add_node import add_node
from research_cockpit.commands.agent_bootstrap import agent_bootstrap_payload, format_dependency_error, missing_runtime_dependencies
from research_cockpit.commands.apply_graph_plan import apply_graph_plan
from research_cockpit.commands.apply_suggestion import apply_suggestion
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.commands.check_decision_acceptance import decision_acceptance_payload
from research_cockpit.commands.claim_option import claim_option
from research_cockpit.commands.cleanup_suggestion_lifecycle import cleanup_suggestion_lifecycle
from research_cockpit.commands.complete_experiment import complete_experiment
from research_cockpit.commands.complete_experiments import complete_experiments
from research_cockpit.commands.context import context_payload
from research_cockpit.commands.create_artifact import create_artifact
from research_cockpit.commands.create_workstream import create_workstream
from research_cockpit.commands.create_note import create_note
from research_cockpit.commands.finalize_workstream import finalize_workstream
from research_cockpit.commands.link_artifact import link_artifact
from research_cockpit.commands.list_agent_commands import agent_command_manifest
from research_cockpit.commands.node_context import node_context_payload
from research_cockpit.commands.option_workstream_context import option_workstream_context_payload
from research_cockpit.commands.promote_decision import promote_decision
from research_cockpit.commands.record_finding import record_finding
from research_cockpit.commands.report_option_workstream import report_option_workstream
from research_cockpit.commands.set_focus import set_focus
from research_cockpit.commands.skill_smoke_test import missing_modules_for_python, skill_smoke_test_payload
from research_cockpit.commands.sync_focus_actions import sync_focus_actions
from research_cockpit.commands.suggest_next_actions import select_suggestions
from research_cockpit.commands.update_decision_evidence import update_decision_evidence
from research_cockpit.commands.update_decision_checklist import update_decision_checklist
from research_cockpit.commands.update_node_fields import update_node_fields
from research_cockpit.commands.update_finding import update_finding
from research_cockpit.commands.update_suggestion_state import update_suggestion_state
from research_cockpit.commands.update_status import update_status


def write_node(root: Path, data: dict) -> None:
    save_yaml(root / "graph" / "nodes" / f"{data['id']}.yaml", data)


def interaction_events(root: Path) -> list[dict]:
    return load_yaml(root / "graph" / "interaction_log.yaml").get("events", [])


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
        self.assertIn("active_option_workstreams", context)
        self.assertIn("option_workstream_context", focus_context)
        self.assertIn("saved_graph_views", context)
        self.assertIn("saved_graph_views", focus_context)
        self.assertIn("stage_text", nodes)
        self.assertIn("metadata", context)
        self.assertIn("metadata", focus_context)
        self.assertIsInstance(context["metadata"]["worktree_dirty"], bool)

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

    def test_option_workstream_context_payload_summarizes_option(self) -> None:
        payload = option_workstream_context_payload(self.root, option_id="option_t5")

        self.assertEqual(payload["option"]["id"], "option_t5")
        self.assertEqual(payload["subtree"]["experiment_ids"], ["exp_t5"])
        self.assertIn("context", payload["suggested_commands"])

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

    def test_list_agent_commands_manifest_marks_mutating_commands(self) -> None:
        manifest = agent_command_manifest()
        by_name = {item["name"]: item for item in manifest}

        self.assertFalse(by_name["validate"]["mutating"])
        self.assertFalse(by_name["search"]["mutating"])
        self.assertFalse(by_name["smoke"]["mutating"])
        self.assertTrue(by_name["init"]["mutating"])
        self.assertFalse(by_name["ui"]["mutating"])
        self.assertEqual(by_name["init"]["capability_file"], "capabilities/integrations.md")
        self.assertEqual(by_name["ui"]["capability_file"], "capabilities/ui-dashboard.md")
        self.assertTrue(by_name["record-finding"]["mutating"])
        self.assertTrue(by_name["record-finding"]["supports_json"])
        self.assertTrue(by_name["record-finding"]["supports_dry_run"])
        self.assertTrue(by_name["record-finding"]["supports_no_build"])
        self.assertTrue(by_name["update-finding"]["supports_json"])
        self.assertTrue(by_name["create-artifact"]["supports_dry_run"])
        self.assertEqual(by_name["create-artifact"]["file_schema"], "artifact_v1")
        self.assertIn("link_to:", by_name["create-artifact"]["example_file"])
        self.assertIn("--print-schema", by_name["create-artifact"]["schema_command"])
        self.assertTrue(by_name["link-artifact"]["supports_no_build"])
        self.assertFalse(by_name["context"]["mutating"])
        self.assertTrue(by_name["context"]["supports_json"])
        self.assertTrue(by_name["add-node"]["supports_json"])
        self.assertTrue(by_name["add-node"]["supports_dry_run"])
        self.assertTrue(by_name["add-node"]["supports_no_build"])
        self.assertTrue(by_name["add-node"]["writes_truth_source"])
        self.assertTrue(by_name["add-node"]["writes_generated_files"])
        self.assertTrue(by_name["add-node"]["can_batch"])
        self.assertTrue(by_name["update-status"]["supports_no_build"])
        self.assertTrue(by_name["update-status"]["supports_json"])
        self.assertTrue(by_name["update-status"]["supports_dry_run"])
        self.assertTrue(by_name["set-focus"]["supports_json"])
        self.assertTrue(by_name["set-focus"]["supports_dry_run"])
        self.assertTrue(by_name["apply-graph-plan"]["supports_json"])
        self.assertTrue(by_name["apply-graph-plan"]["supports_dry_run"])
        self.assertTrue(by_name["apply-graph-plan"]["supports_no_build"])
        self.assertTrue(by_name["apply-graph-plan"]["can_batch"])
        self.assertEqual(by_name["apply-graph-plan"]["file_schema"], "graph_plan_v1")
        self.assertIn("nodes:", by_name["apply-graph-plan"]["example_file"])
        self.assertIn("--print-schema", by_name["apply-graph-plan"]["schema_command"])
        self.assertTrue(by_name["create-workstream"]["supports_json"])
        self.assertEqual(by_name["create-workstream"]["file_schema"], "workstream_v1")
        self.assertIn("followup_options:", by_name["create-workstream"]["example_file"])
        self.assertIn("status: active", by_name["create-workstream"]["example_file"])
        self.assertTrue(by_name["sync-focus-actions"]["supports_dry_run"])
        self.assertTrue(by_name["complete-experiment"]["mutating"])
        self.assertTrue(by_name["complete-experiment"]["supports_json"])
        self.assertTrue(by_name["complete-experiment"]["supports_dry_run"])
        self.assertTrue(by_name["complete-experiment"]["supports_no_build"])
        self.assertTrue(by_name["complete-experiments"]["can_batch"])
        self.assertTrue(by_name["complete-experiments"]["supports_dry_run"])
        self.assertEqual(by_name["complete-experiments"]["file_schema"], "experiment_completion_v1")
        self.assertIn("experiments:", by_name["complete-experiments"]["example_file"])
        self.assertTrue(by_name["finalize-workstream"]["supports_json"])
        self.assertTrue(by_name["finalize-workstream"]["supports_dry_run"])
        self.assertTrue(by_name["update-node-fields"]["mutating"])
        self.assertTrue(by_name["update-node-fields"]["supports_json"])
        self.assertTrue(by_name["update-node-fields"]["supports_dry_run"])
        self.assertTrue(by_name["update-node-fields"]["supports_no_build"])
        self.assertIn("question", by_name["update-node-fields"]["fields_supported"])
        self.assertIn("supporting_experiments", by_name["update-node-fields"]["fields_supported"])
        self.assertTrue(by_name["claim-option"]["supports_json"])
        self.assertTrue(by_name["claim-option"]["supports_dry_run"])
        self.assertTrue(by_name["report-option-workstream"]["supports_json"])
        self.assertTrue(by_name["report-option-workstream"]["supports_dry_run"])
        self.assertTrue(by_name["promote-decision"]["supports_json"])
        self.assertTrue(by_name["promote-decision"]["supports_dry_run"])
        self.assertTrue(by_name["accept-decision"]["supports_json"])
        self.assertTrue(by_name["accept-decision"]["supports_dry_run"])
        self.assertTrue(by_name["apply-suggestion"]["supports_json"])
        self.assertTrue(by_name["apply-suggestion"]["supports_dry_run"])
        self.assertTrue(by_name["update-decision-checklist"]["mutating"])
        self.assertTrue(by_name["update-decision-checklist"]["supports_no_build"])
        self.assertTrue(by_name["cleanup-suggestion-lifecycle"]["supports_dry_run"])
        self.assertTrue(by_name["build"]["mutating"])
        self.assertTrue(by_name["build"]["writes_generated_files"])
        self.assertFalse(by_name["build"]["writes_truth_source"])
        self.assertTrue(by_name["validate"]["safe_in_plan_mode"])
        self.assertFalse(by_name["update-node-fields"]["safe_in_plan_mode"])
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
        self.assertIn("record-finding", {item["name"] for item in payload["commands"]})
        self.assertIn("complete-experiment", {item["name"] for item in payload["commands"]})
        self.assertIn("update-node-fields", {item["name"] for item in payload["commands"]})
        self.assertIn("apply-graph-plan", {item["name"] for item in payload["commands"]})
        self.assertIn("create-workstream", {item["name"] for item in payload["commands"]})
        self.assertIn("sync-focus-actions", {item["name"] for item in payload["commands"]})

    def test_list_agent_commands_compact_json_omits_long_examples(self) -> None:
        out = subprocess.run(
            [*cli_command("commands"), "--json", "--compact"],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)
        by_name = {item["name"]: item for item in payload["commands"]}

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        self.assertEqual(by_name["create-workstream"]["file_schema"], "workstream_v1")
        self.assertIn("--print-schema", by_name["create-workstream"]["schema_command"])
        self.assertNotIn("example_file", by_name["create-workstream"])
        self.assertNotIn("example_file", by_name["complete-experiments"])

    def test_file_commands_expose_schema_help_and_print_schema(self) -> None:
        expectations = {
            "apply-graph-plan": "nodes:",
            "create-workstream": "followup_options:",
            "complete-experiments": "experiments:",
            "create-artifact": "link_to:",
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

    def test_create_artifact_cli_dry_run_diff_and_bad_link_target(self) -> None:
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

    def test_record_finding_cli_accepts_artifact_id_alias_and_legacy_artifact(self) -> None:
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
        self.assertEqual(legacy.returncode, 0, legacy.stdout + legacy.stderr)
        self.assertEqual(data["findings"][0]["linked_artifacts"], ["artifact_cache"])
        self.assertEqual(data["findings"][1]["linked_artifacts"], ["artifact_cache"])
        self.assertEqual(failed.returncode, 1)
        self.assertIn("artifact node id", failed.stdout.lower())

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

    def test_complete_experiment_records_finding_marks_done_and_appends_actions(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_cache",
                "type": "artifact",
                "title": "Feature cache",
                "status": "active",
            },
        )

        result = complete_experiment(
            self.root,
            experiment_id="exp_t5",
            finding="T5 improves replace following.",
            confidence="strong",
            outcome="positive",
            metrics=["replace_following"],
            artifact_ids=["artifact_cache"],
            result_summary="Improved edit following.",
            next_actions=["Compare cache footprint.", "Compare cache footprint."],
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")

        self.assertTrue(result["changed"])
        self.assertEqual(experiment["status"], "done")
        self.assertEqual(experiment["result_summary"], "Improved edit following.")
        self.assertEqual(experiment["findings"][0]["statement"], "T5 improves replace following.")
        self.assertEqual(experiment["findings"][0]["linked_artifacts"], ["artifact_cache"])
        self.assertEqual(experiment["next_actions"], ["Compare cache footprint."])
        self.assertEqual(option["status"], "active")
        self.assertEqual(problem["status"], "active")
        self.assertNotIn("current_best_option", problem)
        self.assertTrue((self.root / "dashboards" / "focus_context_pack.json").exists())
        self.assertEqual(interaction_events(self.root)[-1]["kind"], "complete_experiment")

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
                "--next-action",
                "Review next branch.",
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
        self.assertEqual(experiment["next_actions"], ["Review next branch."])
        self.assertFalse((self.root / "dashboards").exists())

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

        result = complete_experiments(
            self.root,
            plan={
                "defaults": {
                    "confidence": "medium",
                    "outcome": "mixed",
                    "artifact_ids": ["artifact_bundle"],
                    "next_actions": ["Review aggregate."],
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
        self.assertEqual(exp_b["findings"][0]["confidence"], "strong")
        self.assertEqual(exp_b["findings"][0]["outcome"], "positive")
        self.assertEqual(exp_a["next_actions"], ["Review aggregate."])
        self.assertFalse((self.root / "dashboards").exists())

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
