from __future__ import annotations

import json
import shutil
import subprocess
import unittest
import uuid
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from cockpit.model import load_nodes, load_yaml, save_yaml
from scripts.add_node import add_node
from scripts.build_dashboard import build_dashboard
from scripts.create_note import create_note
from scripts.promote_decision import promote_decision
from scripts.record_finding import record_finding
from scripts.set_focus import set_focus
from scripts.suggest_next_actions import select_suggestions
from scripts.update_status import update_status


def write_node(root: Path, data: dict) -> None:
    save_yaml(root / "graph" / "nodes" / f"{data['id']}.yaml", data)


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
        }

        self.assertEqual({path.name for path in paths}, expected)
        context = json.loads((self.root / "dashboards" / "agent_context_pack.json").read_text(encoding="utf-8"))
        focus_context = json.loads((self.root / "dashboards" / "focus_context_pack.json").read_text(encoding="utf-8"))
        matrix = json.loads((self.root / "dashboards" / "experiment_matrix.json").read_text(encoding="utf-8"))
        links = json.loads((self.root / "dashboards" / "linked_resources.json").read_text(encoding="utf-8"))
        suggestions = json.loads((self.root / "dashboards" / "next_action_suggestions.json").read_text(encoding="utf-8"))
        nodes = load_nodes(self.root)

        self.assertEqual(context["linked_nodes"][0]["id"], "stage_text")
        self.assertIn("suggested_next_actions", context)
        self.assertIn("suggested_next_actions", focus_context)
        self.assertEqual(focus_context["focus_node"]["id"], "problem_text")
        self.assertEqual(matrix[0]["id"], "exp_t5")
        self.assertIsInstance(links, list)
        self.assertIsInstance(suggestions, list)
        self.assertIn("stage_text", nodes)

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
        script = ROOT_DIR / "scripts" / "validate_cockpit.py"

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
        script = ROOT_DIR / "scripts" / "suggest_next_actions.py"

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

    def test_suggest_next_actions_cli_fails_on_invalid_cockpit(self) -> None:
        script = ROOT_DIR / "scripts" / "suggest_next_actions.py"
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

    def test_promote_accepted_decision_updates_option_and_problem(self) -> None:
        promote_decision(
            self.root,
            decision_id="decision_accept_t5",
            option_id="option_t5",
            title="Accept T5",
            summary="Accept T5 as current branch.",
            status="accepted",
            supporting_experiments=["exp_t5"],
            consequences=["Update focus."],
        )

        decision = load_yaml(self.root / "graph" / "nodes" / "decision_accept_t5.yaml")
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")

        self.assertEqual(decision["status"], "accepted")
        self.assertEqual(option["status"], "accepted")
        self.assertEqual(option["decision_state"], "accepted")
        self.assertEqual(problem["status"], "resolved")
        self.assertEqual(problem["resolved_by"], "decision_accept_t5")

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
