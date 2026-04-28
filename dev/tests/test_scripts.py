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

ROOT_DIR = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT_DIR / "skills" / "research-cockpit"
sys.path.insert(0, str(SKILL_ROOT))

from cockpit.model import load_nodes, load_yaml, save_yaml
from scripts.accept_decision import accept_decision
from scripts.add_node import add_node
from scripts.agent_bootstrap import agent_bootstrap_payload, format_dependency_error, missing_runtime_dependencies
from scripts.apply_suggestion import apply_suggestion
from scripts.build_dashboard import build_dashboard
from scripts.check_decision_acceptance import decision_acceptance_payload
from scripts.claim_option import claim_option
from scripts.cleanup_suggestion_lifecycle import cleanup_suggestion_lifecycle
from scripts.create_note import create_note
from scripts.list_agent_commands import agent_command_manifest
from scripts.option_workstream_context import option_workstream_context_payload
from scripts.promote_decision import promote_decision
from scripts.record_finding import record_finding
from scripts.report_option_workstream import report_option_workstream
from scripts.set_focus import set_focus
from scripts.skill_smoke_test import missing_modules_for_python, skill_smoke_test_payload
from scripts.suggest_next_actions import select_suggestions
from scripts.update_decision_evidence import update_decision_evidence
from scripts.update_decision_checklist import update_decision_checklist
from scripts.update_suggestion_state import update_suggestion_state
from scripts.update_status import update_status


def write_node(root: Path, data: dict) -> None:
    save_yaml(root / "graph" / "nodes" / f"{data['id']}.yaml", data)


def interaction_events(root: Path) -> list[dict]:
    return load_yaml(root / "graph" / "interaction_log.yaml").get("events", [])


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

    def test_option_workstream_context_cli_outputs_json(self) -> None:
        script = SKILL_ROOT / "scripts" / "option_workstream_context.py"

        result = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), "--option", "option_t5", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["option"]["id"], "option_t5")
        self.assertEqual(payload["upstream_problem"]["id"], "problem_text")

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

    def test_option_workstream_context_payload_summarizes_option(self) -> None:
        payload = option_workstream_context_payload(self.root, option_id="option_t5")

        self.assertEqual(payload["option"]["id"], "option_t5")
        self.assertEqual(payload["subtree"]["experiment_ids"], ["exp_t5"])
        self.assertIn("context", payload["suggested_commands"])

    def test_agent_bootstrap_payload_reports_context_without_building_by_default(self) -> None:
        payload = agent_bootstrap_payload(self.root, build=False)

        self.assertTrue(payload["validation"]["ok"])
        self.assertEqual(payload["focus"]["current_focus_node"], "problem_text")
        self.assertFalse(payload["context_paths"]["agent_context_pack"]["exists"])
        self.assertEqual(payload["skill"]["path"], ".")
        self.assertTrue(payload["skill"]["exists"])
        self.assertIn("top_suggestions", payload)
        self.assertIn("search_summary", payload)
        self.assertIsInstance(payload["git"]["worktree_dirty"], bool)

    def test_agent_bootstrap_cli_json_builds_when_requested(self) -> None:
        script = SKILL_ROOT / "scripts" / "agent_bootstrap.py"

        default_out = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        build_out = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), "--json", "--build"],
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

    def test_agent_bootstrap_dependency_error_is_clear(self) -> None:
        missing = missing_runtime_dependencies({"definitely_missing_module_for_test": "example-package"})

        self.assertEqual(missing, ["definitely_missing_module_for_test"])
        message = format_dependency_error(missing)
        self.assertIn("definitely_missing_module_for_test", message)
        self.assertIn("pip install -r requirements.txt", message)

    def test_list_agent_commands_manifest_marks_mutating_scripts(self) -> None:
        manifest = agent_command_manifest()
        by_name = {item["name"]: item for item in manifest}

        self.assertFalse(by_name["validate_cockpit.py"]["mutating"])
        self.assertFalse(by_name["search_knowledge.py"]["mutating"])
        self.assertFalse(by_name["skill_smoke_test.py"]["mutating"])
        self.assertTrue(by_name["record_finding.py"]["mutating"])
        self.assertTrue(by_name["record_finding.py"]["supports_no_build"])
        self.assertTrue(by_name["update_decision_checklist.py"]["mutating"])
        self.assertTrue(by_name["update_decision_checklist.py"]["supports_no_build"])
        self.assertTrue(by_name["cleanup_suggestion_lifecycle.py"]["supports_dry_run"])
        self.assertTrue(by_name["build_dashboard.py"]["writes_generated_files"])

    def test_list_agent_commands_cli_outputs_json(self) -> None:
        script = SKILL_ROOT / "scripts" / "list_agent_commands.py"

        out = subprocess.run(
            [sys.executable, str(script), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0)
        payload = json.loads(out.stdout)
        self.assertIn("commands", payload)
        self.assertIn("record_finding.py", {item["name"] for item in payload["commands"]})

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
        script = SKILL_ROOT / "scripts" / "validate_cockpit.py"

        ok = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ok.returncode, 0)
        self.assertIn("OK", ok.stdout)

        bad = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        bad["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", bad)
        failed = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(failed.returncode, 1)
        self.assertIn("invalid status", failed.stdout)

    def test_suggest_next_actions_cli_outputs_text_json_and_filters(self) -> None:
        script = SKILL_ROOT / "scripts" / "suggest_next_actions.py"

        text = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), "--limit", "2"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(text.returncode, 0)
        self.assertIn("run_experiment", text.stdout)

        json_out = subprocess.run(
            [
                sys.executable,
                str(script),
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

        selected = select_suggestions(suggestions, kinds=["run_experiment"], limit=1, focus_only=True)
        self.assertEqual(len(selected), 1)

    def test_suggest_next_actions_cli_filters_lifecycle_state(self) -> None:
        script = SKILL_ROOT / "scripts" / "suggest_next_actions.py"
        update_suggestion_state(
            self.root,
            suggestion_id="next_action_001",
            state="dismissed",
            reason="Will not run this now.",
            rebuild_dashboard=False,
        )

        default_out = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        inactive_out = subprocess.run(
            [
                sys.executable,
                str(script),
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
        script = SKILL_ROOT / "scripts" / "suggest_next_actions.py"
        bad = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        bad["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", bad)

        failed = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(failed.returncode, 1)
        self.assertIn("invalid status", failed.stdout)

    def test_search_knowledge_cli_outputs_json_and_filters(self) -> None:
        script = SKILL_ROOT / "scripts" / "search_knowledge.py"
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
            [sys.executable, str(script), "--root", str(self.root), "--query", "needle", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(json_out.returncode, 0)
        results = json.loads(json_out.stdout)
        self.assertGreaterEqual(len(results), 2)
        self.assertIn("snippet", results[0])

        note_only = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), "--query", "needle", "--source", "note", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        resource_only = subprocess.run(
            [
                sys.executable,
                str(script),
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
                sys.executable,
                str(script),
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
                sys.executable,
                str(script),
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
            [sys.executable, str(script), "--root", str(self.root), "--query", "missing-needle", "--json"],
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
        script = SKILL_ROOT / "scripts" / "search_knowledge.py"
        bad = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        bad["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", bad)

        failed = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), "--query", "t5"],
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
        script = SKILL_ROOT / "scripts" / "apply_suggestion.py"

        ok = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), "--id", "next_action_001", "--no-build"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ok.returncode, 0)
        self.assertIn("Queued", ok.stdout)

        failed = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), "--id", "missing_suggestion", "--no-build"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(failed.returncode, 1)
        self.assertIn("missing_suggestion", failed.stdout)

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
        script = SKILL_ROOT / "scripts" / "update_suggestion_state.py"

        ok = subprocess.run(
            [
                sys.executable,
                str(script),
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
                sys.executable,
                str(script),
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
        script = SKILL_ROOT / "scripts" / "cleanup_suggestion_lifecycle.py"
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
            [sys.executable, str(script), "--root", str(self.root), "--dry-run", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        cleaned = subprocess.run(
            [
                sys.executable,
                str(script),
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
                sys.executable,
                str(script),
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

    def test_update_decision_evidence_rejects_bad_decision_inputs(self) -> None:
        with self.assertRaises(ValueError) as missing:
            update_decision_evidence(self.root, decision_id="missing_decision", rebuild_dashboard=False)
        self.assertIn("missing_decision", str(missing.exception))

        with self.assertRaises(ValueError) as wrong_type:
            update_decision_evidence(self.root, decision_id="option_t5", rebuild_dashboard=False)
        self.assertIn("decision", str(wrong_type.exception))

    def test_update_decision_evidence_cli_reports_success_and_failure(self) -> None:
        script = SKILL_ROOT / "scripts" / "update_decision_evidence.py"
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
            [sys.executable, str(script), "--root", str(self.root), "--id", "decision_t5", "--no-build"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ok.returncode, 0)
        self.assertIn("Updated evidence", ok.stdout)

        failed = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), "--id", "missing_decision", "--no-build"],
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
        script = SKILL_ROOT / "scripts" / "update_decision_checklist.py"
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
                sys.executable,
                str(script),
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
                sys.executable,
                str(script),
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
        script = SKILL_ROOT / "scripts" / "check_decision_acceptance.py"
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
            [sys.executable, str(script), "--root", str(self.root), "--id", "decision_t5", "--json"],
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
        self.assertIn("accept_decision.py", str(direct_accept.exception))

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
